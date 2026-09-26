"""Hareket kaydi eslestirme ve hareket profili cikarma.

Iki tool:
    find_candidate_tracks — bir koordinat + saat icin olasi track_id'ler
    get_motion_profile    — bir track_id'nin hizi, yonu, usse gore davranisi

Onemli kabuller (gorev tanimi):
  * Hangi kaydin hangi araca ait oldugu VERILMEZ. Birebir esitlik yok;
    makul mesafe siniri icindeki en yakin nokta eslesmedir.
  * Kayit, ait oldugu goruntunun cekim aninda biter. Goruntudeki aracin
    konumu, kaydin cekim saatindeki noktasidir.
  * Park halindeki araclarin kaydi hic olmayabilir; eslesmeme normaldir.
"""
from __future__ import annotations

from typing import Dict, Optional

import config
import data_loader as dl
from tools.confidence import margin_confidence
from tools.geo import bearing_deg, compass_label, haversine_distance, nearest_zone


def find_candidate_tracks(
    lat: float,
    lon: float,
    time: str,
    radius_m: float = config.TRACK_MATCH_RADIUS_M,
    limit: int = 5,
) -> Dict[str, object]:
    """Verilen konum ve saatte yakindaki hareket kayitlarini dondurur.

    Args:
        lat, lon: tespitin koordinati (pixel_to_geo ciktisi).
        time:     goruntunun cekim saati, "HH:MM". Kayitlar 5 dk adimli.
        radius_m: eslesme ust siniri.
        limit:    en fazla kac aday donsun.

    Donen:
        {"candidates": [...], "best": {...}|None, "ambiguous": bool,
         "searched_time": str, "note": str}

    Bos "candidates" bir hata degil: arac park halinde olabilir (kaydi yok)
    ya da kayitli arac cekim aninda karenin disinda kalmistir.
    """
    ds = dl.load()
    # Mekansal izgara: yaricap hucre boyunu asmazsa yalnizca ilgili hucreler
    # gezilir. Sonuc lineer taramayla ayni; sadece daha az nokta dokunulur.
    points = ds.points_near(lat, lon, time, radius_m)
    if not points and not ds.points_at(time):
        return {
            "candidates": [], "best": None, "ambiguous": False,
            "searched_time": time,
            "note": f"{time} icin hicbir hareket kaydi yok. "
                    f"Kayitlar 5 dakikalik adimlarla geliyor; saati kontrol et.",
        }

    scored = []
    for p in points:
        d = haversine_distance(lat, lon, p.lat, p.lon)
        if d <= radius_m:
            scored.append(
                {"track_id": p.track_id, "lat": p.lat, "lon": p.lon,
                 "distance_m": round(d, 1)}
            )
    # Beraberlik kirma: esit mesafede track_id'ye gore sirala. Aksi halde
    # sonuc CSV satir sirasina bagli kalir ve veri yeniden siralanirsa degisir.
    scored.sort(key=lambda c: (c["distance_m"], c["track_id"]))
    top = scored[:limit]

    if not top:
        # En yakini yine de soyle ki agent "cok mu uzakta" gorebilsin.
        # Burada TAM listeye bakilir: en yakin komsu izgara disinda olabilir.
        points = ds.points_at(time)
        nearest = min(
            points,
            key=lambda p: (haversine_distance(lat, lon, p.lat, p.lon), p.track_id),
        )
        nd = haversine_distance(lat, lon, nearest.lat, nearest.lon)
        return {
            "candidates": [], "best": None, "ambiguous": False,
            "searched_time": time,
            "note": f"{radius_m:.0f} m icinde kayit yok. En yakin kayit "
                    f"{nearest.track_id}, {nd:.0f} m uzakta. Arac park halinde "
                    f"olup kaydi bulunmuyor olabilir.",
        }

    # Belirsizlik iki testten biriyle yanar:
    #  (a) oran  — ikinci aday birincinin 1.5 katindan yakin
    #  (b) mutlak — ikinci aday 3 m icinde (oran testi burada ise yaramaz,
    #      cunku gercek eslesme 0.2 m'deyken 0.3 m bile "2 kat uzak" sayilir)
    ambiguous = len(top) > 1 and (
        top[1]["distance_m"] <= top[0]["distance_m"] * config.TRACK_AMBIGUITY_RATIO
        or top[1]["distance_m"] <= config.TRACK_AMBIGUITY_ABS_M
    )

    # --- guven: iki bagimsiz kaynaktan ---
    # (a) esige uzaklik: 39 m'lik eslesme 40 m esikte sinirda demektir
    prox = margin_confidence(float(top[0]["distance_m"]), radius_m)
    # (b) ayirt edicilik: ikinci aday ne kadar uzakta
    if len(top) > 1:
        d0, d1 = float(top[0]["distance_m"]), float(top[1]["distance_m"])
        separation = (d1 - d0) / max(d1, 1e-6)
        distinct = round(max(0.0, min(1.0, separation)), 3)
    else:
        distinct = 1.0
    confidence = round(min(float(prox["confidence"]), distinct), 3)

    note = "Tek net eslesme."
    if ambiguous:
        note = (
            f"Belirsiz: {top[0]['track_id']} ({top[0]['distance_m']} m) ve "
            f"{top[1]['track_id']} ({top[1]['distance_m']} m) benzer uzaklikta. "
            f"Hareket profillerine bakip ayirt et."
        )
    if prox["at_cliff"]:
        note += (
            f" En iyi eslesme {radius_m:.0f} m esiginin SINIRINDA "
            f"({top[0]['distance_m']} m); birkac metrelik tespit hatasi "
            f"bu eslesmeyi degistirir."
        )

    return {
        "candidates": top,
        "best": top[0],
        "ambiguous": ambiguous,
        "confidence": confidence,
        "confidence_basis": {
            "proximity": prox["confidence"],
            "distinctness": distinct,
            "margin_m": prox["margin"],
            "at_threshold_cliff": prox["at_cliff"],
        },
        "searched_time": time,
        "note": note,
    }


# Siniflandirma esikleri. Tek yerde dursunlar ki guven hesabi da ayni
# sayilari kullansin — esigi degistirip guveni unutmak kolay bir hata.
STOPPED_SPEED_MPS = 0.5
BASE_TREND_M = 150.0


def _classify_movement(distance_change_m: float, speed_mps: float) -> str:
    """Usse gore net davranis. Esikler 30 dakikalik pencereye gore."""
    if speed_mps < STOPPED_SPEED_MPS:
        return "stationary"
    if distance_change_m < -BASE_TREND_M:
        return "approaching_base"
    if distance_change_m > BASE_TREND_M:
        return "departing_base"
    return "lateral"


def _classification_confidence(
    movement: str, speed: float, delta_m: float, points_used: int
) -> Dict[str, object]:
    """Siniflandirma ne kadar net? Esige uzakliktan hesaplanir.

    0.51 m/s hizla 'hareketli' demek ile 9 m/s ile demek ayni kesinlikte
    degil. Bu fonksiyon o farki sayiya ceviriyor.
    """
    if movement == "stationary":
        # Esigin ne kadar altinda: 0.5'te sinirda, 0'a yakinsa kesin.
        base = margin_confidence(speed, STOPPED_SPEED_MPS,
                                 scale=STOPPED_SPEED_MPS, direction="below")
    elif movement in ("approaching_base", "departing_base"):
        base = margin_confidence(abs(delta_m), BASE_TREND_M,
                                 scale=BASE_TREND_M, direction="above")
    else:  # lateral — iki esigin de disinda kalmak, ortada olmak demek
        base = margin_confidence(BASE_TREND_M - abs(delta_m), 0.0,
                                 scale=BASE_TREND_M, direction="above")

    # Az nokta = zayif kanit. 30 dk penceresi 7 nokta demek; azalirsa ceza.
    sample = round(min(1.0, points_used / 7.0), 3)
    conf = round(min(float(base["confidence"]), sample), 3)
    return {
        "confidence": conf,
        "threshold_margin": base["margin"],
        "at_threshold_cliff": base["at_cliff"],
        "sample_adequacy": sample,
        "points_used": points_used,
    }


def get_motion_profile(
    track_id: str,
    at_time: Optional[str] = None,
    window_min: int = 30,
) -> Dict[str, object]:
    """Bir aracin hareketini kaydin TAMAMINDAN okur.

    Hiz ve yonu tek bir adimdan cikarma: araclar donus yapar, durur, uste
    dolasir. Bu yuzden hem son adimin hizi hem de pencere ortalamasi doner.

    Args:
        track_id:   kaydin kimligi.
        at_time:    hangi ana kadar bakilacak ("HH:MM"). None ise kaydin sonu.
        window_min: hiz/yon ortalamasinin alindigi pencere (dakika).

    Donen: MotionProfile + insan okunur "summary".
    """
    ds = dl.load()
    pts = ds.track(track_id)

    if at_time is not None:
        cutoff = dl.to_minutes(at_time)
        pts = [p for p in pts if p.minutes <= cutoff]
        if not pts:
            first = ds.track(track_id)[0]
            return {
                "track_id": track_id,
                "error": f"{track_id} kaydi {at_time} tarihinden once baslamiyor "
                         f"(ilk kayit {first.time}).",
            }

    end = pts[-1]
    window_start_min = end.minutes - window_min
    window = [p for p in pts if p.minutes >= window_start_min] or pts[-2:]
    start = window[0]

    # --- hiz: pencere boyunca kat edilen yol / gecen sure ---
    path_m = sum(
        haversine_distance(a.lat, a.lon, b.lat, b.lon)
        for a, b in zip(window, window[1:])
    )
    elapsed_s = max((end.minutes - start.minutes) * 60, 1)
    speed = path_m / elapsed_s

    recent_speed = 0.0
    if len(pts) >= 2:
        prev = pts[-2]
        step_m = haversine_distance(prev.lat, prev.lon, end.lat, end.lon)
        recent_speed = step_m / max((end.minutes - prev.minutes) * 60, 1)

    # --- usse gore ---
    base = ds.base
    d_end = haversine_distance(end.lat, end.lon, base.lat, base.lon)
    d_start = haversine_distance(start.lat, start.lon, base.lat, base.lon)
    delta_m = d_end - d_start

    movement = _classify_movement(delta_m, speed)
    cls = _classification_confidence(movement, speed, delta_m, len(window))

    # --- yon: pencerenin net yer degistirmesi (tek adim gurultulu) ---
    # Duran araca yon atfetme: park halindeki bir aracin kaydi da birkac
    # metre oynar (GPS gurultusu) ve bu sahte bir istikamet uretir. Esik
    # sabit bir mesafe degil, pencereyle olcekli olmali.
    net_m = haversine_distance(start.lat, start.lon, end.lat, end.lon)
    jitter_floor = max(config.STOPPED_STEP_M,
                       config.STOPPED_STEP_M * (end.minutes - start.minutes)
                       / config.TRACK_STEP_MIN / 2)
    heading = (
        round(bearing_deg(start.lat, start.lon, end.lat, end.lon), 1)
        if movement != "stationary" and net_m > jitter_floor else None
    )

    # --- kaydin sonunda kac dakikadir duruyor ---
    stopped_min = 0
    for a, b in zip(reversed(pts[:-1]), reversed(pts)):
        if haversine_distance(a.lat, a.lon, b.lat, b.lon) <= config.STOPPED_STEP_M:
            stopped_min += b.minutes - a.minutes
        else:
            break

    ctx = nearest_zone(end.lat, end.lon)

    summary = _summarize(track_id, end, speed, recent_speed, heading, movement,
                         d_end, delta_m, stopped_min, ctx, window, window_min)

    return {
        "track_id": track_id,
        "at_time": end.time,
        "position": {"lat": end.lat, "lon": end.lon},
        "speed_mps": round(speed, 2),
        "speed_kmh": round(speed * 3.6, 1),
        "recent_speed_mps": round(recent_speed, 2),
        "heading_deg": heading,
        "heading_label": compass_label(heading) if heading is not None else None,
        "distance_to_base_km": round(d_end / 1000.0, 3),
        "distance_change_km": round(delta_m / 1000.0, 3),
        "movement": movement,
        "stopped_minutes": stopped_min,
        "path_length_km": round(path_m / 1000.0, 3),
        "window_min": window_min,
        "window_start": start.time,
        "points_used": len(window),
        "zone": ctx["zone"],
        "direction_from_base": ctx["direction_from_base"],
        "confidence": cls["confidence"],
        "confidence_basis": {
            "threshold_margin": cls["threshold_margin"],
            "at_threshold_cliff": cls["at_threshold_cliff"],
            "sample_adequacy": cls["sample_adequacy"],
            "points_used": cls["points_used"],
        },
        "summary": summary + (
            f" (Siniflandirma esige yakin, guven {cls['confidence']}.)"
            if cls["at_threshold_cliff"] else ""
        ),
    }


def _summarize(track_id, end, speed, recent_speed, heading, movement,
               d_end, delta_m, stopped_min, ctx, window, window_min) -> str:
    """Modelin dogrudan gerekceye yazabilecegi tek cumlelik ozet."""
    if movement == "stationary":
        return (
            f"{track_id} {end.time} itibariyla {ctx['zone']} bolgesinde duruyor; "
            f"{stopped_min} dakikadir hareketsiz, usse {d_end/1000:.1f} km."
        )
    yon = compass_label(heading) if heading is not None else "belirsiz"
    if movement == "approaching_base":
        hareket = f"usse yaklasiyor ({abs(delta_m)/1000:.1f} km yaklasti)"
    elif movement == "departing_base":
        hareket = f"ussen uzaklasiyor ({delta_m/1000:.1f} km uzaklasti)"
    else:
        hareket = "usse mesafesini koruyarak yanal hareket ediyor"
    return (
        f"{track_id} son {window_min} dakikada ~{speed:.1f} m/s hizla {yon} "
        f"yonunde ilerledi; {hareket}. {end.time} itibariyla {ctx['zone']} "
        f"bolgesinde, usse {d_end/1000:.1f} km."
    )


def tracks_in_image(image_id: str, radius_m: float = config.TRACK_MATCH_RADIUS_M) -> Dict[str, object]:
    """Cekim aninda karenin icinde kalan tum hareket kayitlari.

    Tespit listesi olmadan "bu karede kayitli kac arac var" sorusuna bakmak
    icin. Tespitle eslestirme yine find_candidate_tracks'in isi.
    """
    ds = dl.load()
    meta = ds.image(image_id)
    inside = [
        {"track_id": p.track_id, "lat": p.lat, "lon": p.lon}
        for p in ds.points_at(meta.capture_time)
        if meta.contains(p.lat, p.lon)
    ]
    return {
        "image_id": image_id,
        "capture_time": meta.capture_time,
        "tracks_inside": inside,
        "count": len(inside),
    }
