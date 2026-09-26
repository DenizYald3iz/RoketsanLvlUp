"""Saha raporlarini sorgulama ve kendi tespitinle karsilastirma.

Uc tool:
    query_reports                    — bir konum/saat civarindaki raporlar
    check_report_consistency         — rapor ile kendi bulgun uyusuyor mu
    cross_report_contradiction_check — raporlar birbiriyle celisiyor mu

Temel kural (gorev tanimi): "Bazi raporlar dogru, bazilari hatali veya
ilgisizdir. Bunlar isaretlenmez." Yani hicbir rapor pesinen dogru degil.
Celiski varsa RAPOR degil, kendi tespitin esas alinir.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

import config
import data_loader as dl
from tools.confidence import margin_confidence
from tools.geo import (
    _normalize,
    find_zone_in_text,
    haversine_distance,
    resolve_zone_name,
)

# --- Serbest metin ayristirma --------------------------------------------
# "39.9374N 32.8483E" / "39.92087N 32.89536E"
_COORD_RE = re.compile(
    r"(\d{1,3}\.\d+)\s*N\s+(\d{1,3}\.\d+)\s*E", re.IGNORECASE
)

_NUM_WORDS = {"bir": 1, "iki": 2, "uc": 3, "dort": 4, "bes": 5,
              "alti": 6, "yedi": 7, "sekiz": 8, "dokuz": 9, "on": 10}

# Arac turleri. Sira onemli: "agir arac" ve "binek arac", ciplak "arac"tan
# once denenir, yoksa hepsi "arac" olur.
_VEHICLE_WORDS = [
    ("panelvan", "panelvan"),
    ("otobus", "otobus"),
    ("kamyon", "kamyon"),
    ("otomobil", "otomobil"),
    ("binek arac", "otomobil"),
    ("agir arac", "agir_arac"),
    ("agir bir arac", "agir_arac"),
    ("arac", "arac"),
]

_COLORS = ["mavi", "kirmizi", "sari", "beyaz", "siyah", "yesil", "gri"]

# Rapor "dost/teyitli" mi? Bu ifadeler varsa arac bize baglidir.
_FRIENDLY_PAT = re.compile(
    r"dost (devriye )?unsur|bize bagli|planli ikmal|kimlik teyidi|teyitlidir|"
    r"bize bagli devriyedir|gelisi onceden bildirilmis|planli tatbikat",
)
# Rapor kendisi "dogrulanmamis" diyorsa agirligi dusuktur.
_UNVERIFIED_PAT = re.compile(
    r"dogrulanmamis|dogrulanamadi|ihbar alindi|bir ihbara gore|"
    r"yonunde ihbar|bir kaynak|bildirildi",
)

# Hareket ifadeleri -> normalize hareket
_MOVEMENT_PATS = [
    (r"usse dogru ilerleyen|usse gelen|usse yaklas", "approaching_base"),
    (r"bolgeden uzaklas|ussen uzaklas", "departing"),
    (r"transit geciyor|konvoyu ilerliyor|ilerliyor", "transit"),
    (r"yerinden ayrilmadi|hareketsiz|park halinde|durdugu|bekliyor|"
     r"beklemede|uzun suredir", "stationary"),
]

# Konum tasimayan cevresel gurultu.
_ENVIRONMENT_PAT = re.compile(
    r"hava acik|gorus mesafesi|lojistik konvoyu|planli tatbikat",
)
# Bolge duzeyinde "her sey normal" bildirimleri.
_ALL_CLEAR_PAT = re.compile(
    r"trafik akisi normal|olagandisi bir durum bildirmedi|"
    r"kayda deger bir hareketlilik bulunmuyor|hareketleri olagan",
)
# Gozlem bosluqu: devriye sessiz, o bolgede goz yok.
_COMMS_LOSS_PAT = re.compile(r"telsiz baglantisi")
# "Agir arac hareketi yok" — olumsuz iddia. Agir arac tespitini yalanlar.
_NEGATED_HEAVY_PAT = re.compile(r"agir arac hareketi yok")

# "Olagan trafik N arac" / "genellikle N arac" -> taban cizgisi, GOZLEM DEGIL.
_BASELINE_RE = re.compile(
    r"(?:olagan trafik|genellikle)\s*(\d+)\s*arac", re.IGNORECASE
)
_DENSITY_PAT = re.compile(r"beklenmedik bir yogunluk|olagandan yogun")


def _extract_count(norm: str) -> Optional[int]:
    """Metinden GOZLENEN arac sayisini cikarir.

    Iki tuzak var:
      * 'bir saatten uzun suredir' — buradaki 'bir' sayi degil. Bu yuzden
        sayi ancak bir arac ismiyle birlikte geciyorsa kabul edilir.
      * 'olagan trafik 4 arac civaridir' — bu taban cizgisi, gozlem degil.
        Baseline ifadesi metinden cikarilip oyle aranir.
    """
    norm = _BASELINE_RE.sub(" ", norm)

    veh = r"(?:kamyon|otomobil|panelvan|otobus|agir arac|binek arac|arac)"
    adj = r"(?:(?:mavi|kirmizi|sari|beyaz|siyah|yesil|gri|yuklu|agir|"
    adj += r"uzeri ortulu|araclik)\s+)*"

    m = re.search(rf"(\d+)\s*{adj}{veh}", norm)
    if m:
        return int(m.group(1))
    words = "|".join(_NUM_WORDS)
    m = re.search(rf"\b({words})\s+{adj}{veh}", norm)
    if m:
        return _NUM_WORDS[m.group(1)]
    return None


def _classify_kind(norm: str, has_pos: bool, friendly: bool,
                   unverified: bool, vehicle: Optional[str]) -> str:
    """Raporu turune gore etiketler. Agent'in agirlik vermesi bu etikete bakar.

    sighting        — konumlu somut gozlem; en degerlisi
    friendly_id     — arac bize bagli/teyitli; dikkat gereksinimini dusurur
    density_anomaly — "olagandan yogun", taban cizgisiyle birlikte gelir
    rumor           — dogrulanmamis ihbar; tek basina kanit degil
    all_clear       — "normal seyrediyor" tarzi olumsuz gozlem; zayif
    comms_loss      — devriye sessiz; o bolgede gozlem boslugu var
    environment     — hava/lojistik gibi konudisi
    """
    if _ENVIRONMENT_PAT.search(norm) and not has_pos:
        return "environment"
    if _COMMS_LOSS_PAT.search(norm):
        return "comms_loss"
    if _DENSITY_PAT.search(norm):
        return "density_anomaly"
    if friendly and has_pos:
        return "friendly_id"
    if _ALL_CLEAR_PAT.search(norm) or _NEGATED_HEAVY_PAT.search(norm):
        return "all_clear"
    if unverified and vehicle is None:
        return "rumor"
    if vehicle is not None and has_pos:
        return "rumor" if unverified else "sighting"
    return "rumor" if unverified else "other"


@lru_cache(maxsize=1024)
def _parse_report_cached(text: str) -> Tuple[Tuple[str, object], ...]:
    """parse_report'un onbellekli cekirdegi. Sozluk hashlenemedigi icin
    ciftler demeti olarak saklanir; parse_report bunu sozluge cevirir."""
    return tuple(_parse_report_impl(text).items())


def parse_report(text: str) -> Dict[str, object]:
    """Serbest metin raporu yapisal bir iddiaya cevirir (onbellekli sarmalayici).

    Ayni raporlar zincir boyunca defalarca ayristiriliyordu; profilde en
    buyuk tek maliyet kalemi buydu (22.000 regex cagrisi). Rapor metinleri
    degismedigi icin sonuc guvenle onbelleklenir. Her cagrida YENI bir
    sozluk doner, boylece cagiran taraf ciktiyi degistirse bile onbellek
    bozulmaz.
    """
    return dict(_parse_report_cached(text))


def _parse_report_impl(text: str) -> Dict[str, object]:
    """Serbest metin raporu yapisal bir iddiaya cevirir.

    Ayristirilamayan alan None doner — bu bir hata degil, rapor o bilgiyi
    tasimiyor demektir.

    Onemli: "count" GOZLENEN sayidir. "olagan trafik 4 arac" gibi taban
    cizgisi degerleri ayri alanda (baseline_count) durur.
    """
    norm = _normalize(text)

    lat = lon = None
    m = _COORD_RE.search(text)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))

    zone = None
    if lat is None:
        z = find_zone_in_text(text)
        if z:
            zone = z["zone"]
            lat, lon = z["lat"], z["lon"]  # type: ignore[assignment]

    vehicle = None
    for word, kind in _VEHICLE_WORDS:
        if word in norm:
            vehicle = kind
            break
    # "agir bir arac" — sifat araya girdigi icin bitisik eslesme kacar.
    if vehicle == "arac" and re.search(r"agir (bir )?arac", norm):
        vehicle = "agir_arac"

    movement = None
    for pat, kind in _MOVEMENT_PATS:
        if re.search(pat, norm):
            movement = kind
            break

    color = next((c for c in _COLORS if re.search(rf"\b{c}\b", norm)), None)
    baseline = _BASELINE_RE.search(norm)
    friendly = bool(_FRIENDLY_PAT.search(norm))
    unverified = bool(_UNVERIFIED_PAT.search(norm))
    has_pos = lat is not None
    negated_heavy = bool(_NEGATED_HEAVY_PAT.search(norm))

    kind = _classify_kind(norm, has_pos, friendly, unverified, vehicle)
    if negated_heavy:
        # Olumsuz iddia: "agir arac yok". Arac turunu iddia olarak tasima.
        vehicle = None

    return {
        "lat": lat,
        "lon": lon,
        "zone": zone,
        "kind": kind,
        "vehicle_type": vehicle,
        "count": _extract_count(norm),
        "movement": movement,
        "color": color,
        "loaded": "yuklu" in norm,
        "covered": "uzeri ortulu" in norm,
        "friendly": friendly,
        "unverified": unverified,
        "negated_heavy": negated_heavy,
        "noise": kind == "environment",
        "baseline_count": int(baseline.group(1)) if baseline else None,
        "density_anomaly": bool(_DENSITY_PAT.search(norm)),
        "has_position": has_pos,
    }


def _as_matched(rep: dl.Report, distance_m: Optional[float]) -> Dict[str, object]:
    return {
        "index": rep.index,
        "time": rep.time,
        "source": rep.source,
        "text": rep.text,
        "distance_m": None if distance_m is None else round(distance_m, 1),
        "claim": parse_report(rep.text),
    }


# --- Tool 1: sorgulama ----------------------------------------------------
def query_reports(
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    time: Optional[str] = None,
    radius_m: float = config.REPORT_MATCH_RADIUS_M,
    window_min: int = config.REPORT_TIME_WINDOW_MIN,
    source: Optional[str] = None,
    zone: Optional[str] = None,
    include_zone_level: bool = True,
    limit: int = 12,
) -> Dict[str, object]:
    """Bir konum ve/veya saat civarindaki saha raporlarini dondurur.

    Raporlar goruntuye bagli degildir: tum bolge icin tek havuzdur. Bu tool
    havuzu cekim saati ve konum uzerinden suzer.

    Args:
        lat, lon:  ilgilenilen konum. Verilmezse konum suzgeci uygulanmaz.
        time:      referans saat "HH:MM" (genelde cekim saati).
        radius_m:  koordinatli raporlar icin mesafe siniri.
        window_min: time +/- bu dakika araligi taranir.
        source:    "official" | "third_party" ile sinirla.
        zone:      bolge adiyla sinirla (koordinat yerine).
        include_zone_level: koordinati olmayip bolge adi gecen raporlar da
                   donsun mu. Bunlar zayif kanittir, "zone_level" isaretlenir.

    Donen: {"reports": [...], "counts": {...}, "note": str}
    """
    ds = dl.load()
    pool = ds.reports

    if time is not None:
        t = dl.to_minutes(time)
        pool = [r for r in pool if abs(r.minutes - t) <= window_min]
    if source is not None:
        if source not in ("official", "third_party"):
            raise ValueError("source 'official' ya da 'third_party' olmali")
        pool = [r for r in pool if r.source == source]

    zone_filter = None
    if zone is not None:
        z = resolve_zone_name(zone)
        if z is None:
            raise ValueError(f"Bilinmeyen bolge: {zone!r}")
        zone_filter = z["zone"]

    matched: List[Dict[str, object]] = []
    for rep in pool:
        claim = parse_report(rep.text)
        if claim["noise"]:
            continue

        if zone_filter is not None:
            hit_zone = claim["zone"] == zone_filter
            near = False
            if claim["lat"] is not None:
                zz = resolve_zone_name(zone_filter)
                near = haversine_distance(
                    claim["lat"], claim["lon"], zz["lat"], zz["lon"]  # type: ignore[arg-type]
                ) <= 2500
            if not (hit_zone or near):
                continue

        if lat is None or lon is None:
            matched.append(_as_matched(rep, None))
            continue

        if claim["lat"] is None:
            continue
        d = haversine_distance(lat, lon, claim["lat"], claim["lon"])  # type: ignore[arg-type]

        if claim["zone"] is not None:
            # Bolge merkezine dayali konum: kaba. Genis bir esik kullan.
            if include_zone_level and d <= 5000:
                item = _as_matched(rep, d)
                item["zone_level"] = True
                matched.append(item)
            continue

        if d <= radius_m:
            matched.append(_as_matched(rep, d))

    matched.sort(
        key=lambda r: (
            r.get("zone_level", False),
            r["distance_m"] if r["distance_m"] is not None else 0.0,
            abs(dl.to_minutes(r["time"]) - dl.to_minutes(time)) if time else 0,
        )
    )
    out = matched[:limit]

    counts = {
        "total": len(matched),
        "returned": len(out),
        "official": sum(1 for r in out if r["source"] == "official"),
        "third_party": sum(1 for r in out if r["source"] == "third_party"),
        "zone_level": sum(1 for r in out if r.get("zone_level")),
    }
    note = "Raporlar kanit degil iddiadir; kendi tespitinle karsilastir."
    if not out:
        note = (
            "Bu konum ve zaman penceresinde rapor yok. Rapor yoklugu, orada "
            "bir sey olmadigi anlamina gelmez."
        )
    return {"reports": out, "counts": counts, "note": note}


# --- Tool 2: rapor vs kendi tespitin -------------------------------------
_TYPE_FAMILY = {
    "kamyon": "heavy", "otobus": "heavy", "agir_arac": "heavy",
    "panelvan": "van", "otomobil": "light", "arac": None,
}


def _coord_precision_m(text: str) -> float:
    """Rapordaki koordinatin ondalik basamagindan konum hassasiyetini cikarir.

    Raporlar '39.9374N' (4 basamak) ya da '39.92087N' (5 basamak) yaziyor.
    4 basamak ~11 m, 5 basamak ~1.1 m eder. Konum karsilastirmasinin esigi
    uydurma bir sayi degil, bu hassasiyet olmali.
    """
    m = _COORD_RE.search(text)
    if not m:
        return float("inf")
    decimals = min(len(m.group(1).split(".")[1]), len(m.group(2).split(".")[1]))
    return 111_000.0 / (10 ** decimals)


def check_report_consistency(
    report_index: int,
    observed_count: Optional[int] = None,
    observed_movement: Optional[str] = None,
    observed_lat: Optional[float] = None,
    observed_lon: Optional[float] = None,
    observed_time: Optional[str] = None,
    position_tolerance_m: Optional[float] = None,
) -> Dict[str, object]:
    """Tek bir raporun iddiasini KENDI tespitinle karsilastirir.

    Uc eksende karsilastirir: KONUM, SAYI, HAREKET. Arac turu bilincli olarak
    disarida: gercek veride neredeyse her kare her turu iceriyor, bu yuzden
    "rapor kamyon diyor, karede kamyon var mi" testi hicbir sey ayirt etmiyor.

    Bu tool'un dayandigi tek varsayim su: verdigin observed_* degerleri bir
    goruntudeki tespitten ve o karenin cekim anindaki hareket kaydindan
    geliyor. O zemin saglamdir. Tool rapora hakemlik ETMEZ; sadece iddiayi
    senin bulgunla eksen eksen karsilastirip farki gosterir.

    Args:
        report_index: query_reports'un dondugu rapor indeksi.
        observed_*:   senin bulgun. Bilmedigini None birak — o eksen atlanir.
        observed_time: gozlemin saati "HH:MM" (goruntunun cekim saati). Verilirse
                      rapor saatiyle arasindaki fark hesaplanir; hareket
                      celiskisi bu farkla birlikte degerlendirilir.
        position_tolerance_m: konum esigi. Verilmezse raporun KENDI koordinat
                      hassasiyetinden hesaplanir (4 ondalik ~11 m, 5 ~1.1 m),
                      alt sinir 50 m.

    Donen:
        verdict   : consistent | partial | contradicts | unrelated
        agrees    : uyusan eksenler
        conflicts : celisen eksenler
        caveats   : bu karsilastirmanin zayif noktalari
        limits    : tool'un KARARA BAGLAYAMADIGI seyler
    """
    ds = dl.load()
    if not (0 <= report_index < len(ds.reports)):
        raise ValueError(
            f"report_index {report_index} araligin disinda (0..{len(ds.reports)-1})"
        )
    rep = next(r for r in ds.reports if r.index == report_index)
    claim = parse_report(rep.text)

    agrees: List[str] = []
    conflicts: List[str] = []
    caveats: List[str] = []
    limits: List[str] = []

    gap_min: Optional[int] = None
    if observed_time is not None:
        gap_min = abs(rep.minutes - dl.to_minutes(observed_time))

    # --- konum ---
    dist = None
    if observed_lat is not None and observed_lon is not None and claim["lat"] is not None:
        dist = haversine_distance(
            observed_lat, observed_lon, claim["lat"], claim["lon"]  # type: ignore[arg-type]
        )
        if claim["zone"] is not None:
            agrees.append(
                f"Rapor yalnizca bolge adi veriyor ({claim['zone']}); konum kabaca "
                f"ortusuyor ama bu zayif bir ortusmedir."
            )
            caveats.append(
                "Raporun konumu bolge MERKEZI, gercek koordinat degil. Konum "
                "ortusmesini kanit sayma."
            )
        else:
            tol = position_tolerance_m
            if tol is None:
                tol = max(_coord_precision_m(rep.text), 50.0)
            if dist <= tol:
                agrees.append(
                    f"Konum ortusuyor ({dist:.0f} m; raporun koordinat "
                    f"hassasiyetine gore esik {tol:.0f} m)."
                )
            else:
                conflicts.append(
                    f"Konum tutmuyor: rapor {dist:.0f} m uzagi isaret ediyor "
                    f"(esik {tol:.0f} m). Muhtemelen baska bir araci anlatiyor."
                )

    # NOT: Arac turu ekseni kaldirildi. Gercek tespit verisiyle olculdu:
    # neredeyse her karede her tur birden mevcut (heavy + light + van), bu
    # yuzden "rapor kamyon diyor, karede kamyon var mi" testi bos cikiyordu
    # (18 mevcut / 8 degil, ama karelerin cogu zaten hepsini iceriyor).
    # Tur karsilastirmasi ancak rapor TEK bir eslesmis tespitle kiyaslanirsa
    # anlamli olur; o da cagiran tarafin isi, bu tool'un degil.

    # --- sayi ---
    if observed_count is not None and claim["count"] is not None:
        c = int(claim["count"])
        if c == observed_count:
            agrees.append(f"Sayi ortusuyor ({c}).")
        elif abs(c - observed_count) <= 1:
            agrees.append(f"Sayi yakin (rapor {c}, tespit {observed_count}).")
        else:
            conflicts.append(
                f"Sayi uyusmuyor: rapor {c}, tespit {observed_count}."
            )
        limits.append(
            "Sayi farki tek basina raporu yalanlamaz: raporun kapsadigi alan "
            "senin karenden genis olabilir ve park halindeki araclarin hareket "
            "kaydi bulunmayabilir."
        )

    # --- hareket ---
    if observed_movement and claim["movement"]:
        obs, rc = observed_movement, claim["movement"]
        same = (
            obs == rc
            or (obs == "departing_base" and rc == "departing")
            or (obs == "lateral" and rc == "transit")
        )
        if same:
            agrees.append(f"Hareket ortusuyor ({rc}).")
        elif gap_min is not None and gap_min > config.MOVEMENT_CONFLICT_MAX_GAP_MIN:
            # Celiski saymiyoruz: aradaki surede arac gercekten hareket etmis olabilir.
            caveats.append(
                f"Rapor '{rc}' diyor, tespit '{obs}'. Ama aralarinda {gap_min} dk "
                f"var; bu surede arac hareket etmis olabilir. Celiski sayilmadi."
            )
        else:
            gap_txt = f" ({gap_min} dk arayla)" if gap_min is not None else ""
            conflicts.append(
                f"Hareket celisiyor: rapor '{rc}', tespit '{obs}'{gap_txt}."
            )
            if gap_min is None:
                caveats.append(
                    "observed_time verilmedi, zaman farki bilinmiyor. Hareket "
                    "zamanla degisir; fark buyukse bu celiski sayilmamali."
                )

    # --- karar ---
    if not agrees and not conflicts:
        verdict = "unrelated"
    elif conflicts and not agrees:
        verdict = "contradicts"
    elif conflicts:
        verdict = "partial"
    else:
        verdict = "consistent"

    # --- kaynaga dair, sayi uydurmadan ---
    if rep.source == "third_party":
        caveats.append(
            "Kaynak third_party. Veride hangi raporun dogru oldugu isaretli "
            "degil; bu yalnizca bir baglamdir, agirlik katsayisi degil."
        )
    if claim["unverified"]:
        caveats.append(
            "Rapor kendi ifadesiyle dogrulanmamis ('ihbar', 'bildirildi'). "
            "Tek basina kanit sayma."
        )
    if claim["friendly"]:
        caveats.append(
            "Rapor araci dost/teyitli unsur olarak tanimliyor. Konum ve zaman "
            "tutuyorsa dikkat gereksinimini dusurur; tutmuyorsa hicbir sey soylemez."
        )

    limits.append(
        "Bu tool raporun DOGRU olup olmadigini soylemez, yalnizca senin "
        "bulgunla ortusup ortusmedigini. Celiski varsa raporu degil tespitini esas al."
    )

    # --- guven: karsilastirmanin kendisi ne kadar saglam? ---
    # Bu, raporun DOGRULUGUNUN guveni degil; "bu karsilastirmaya ne kadar
    # guvenebilirim" sorusunun cevabi. Kac eksen karsilastirilabildi ve
    # konum ortusmesi esige ne kadar yakin?
    axes_compared = len(agrees) + len(conflicts)
    coverage = round(min(1.0, axes_compared / 3.0), 3)
    position_conf = 1.0
    if dist is not None and claim["zone"] is None:
        tol = position_tolerance_m or max(_coord_precision_m(rep.text), 50.0)
        position_conf = float(
            margin_confidence(dist, tol, scale=tol).get("confidence", 1.0)
        )
    elif claim["zone"] is not None:
        position_conf = 0.35  # bolge merkezi: kaba konum, zayif ortusme
    comparison_conf = round(min(coverage, position_conf), 3)

    return {
        "report_index": report_index,
        "report_time": rep.time,
        "source": rep.source,
        "text": rep.text,
        "claim": claim,
        "distance_m": None if dist is None else round(dist, 1),
        "minutes_apart": gap_min,
        "agrees": agrees,
        "conflicts": conflicts,
        "caveats": caveats,
        "limits": limits,
        "verdict": verdict,
        "confidence": comparison_conf,
        "confidence_basis": {
            "axes_compared": axes_compared,
            "axis_coverage": coverage,
            "position_confidence": round(position_conf, 3),
            "meaning": "Karsilastirmanin saglamligi; raporun dogrulugu DEGIL.",
        },
    }



def find_supporting_reports(
    report_index: int,
    radius_m: float = config.SAME_POINT_RADIUS_M,
    window_min: int = config.CORROBORATION_WINDOW_MIN,
) -> Dict[str, object]:
    """Bir raporun iddiasini AYNI NOKTADA destekleyen baska raporlar.

    Destekleme, bagimsizlik derecesine gore siniflanir:
      cross_source — farkli kaynak ayni seyi soyluyor: en guclu destek
      same_source  — ayni kaynak farkli metinle tekrar ediyor: zayif,
                     ayni gozlemcinin iki kez konusmasi olabilir
      duplicate    — birebir ayni metin: destek SAYILMAZ, sadece kopya

    Destekleme eksen bazindadir: iki rapor arac turunde anlasip sayida
    ayrilabilir. O durumda tur desteklenmis, sayi tartismali olur.
    """
    ds = dl.load()
    if not (0 <= report_index < len(ds.reports)):
        raise ValueError(
            f"report_index {report_index} araligin disinda (0..{len(ds.reports)-1})"
        )
    rep = next(r for r in ds.reports if r.index == report_index)
    claim = parse_report(rep.text)
    if claim["lat"] is None or claim["zone"] is not None:
        return {
            "report_index": report_index,
            "supports": [], "duplicates": [],
            "note": "Raporun kesin koordinati yok; ayni-nokta destegi aranamaz.",
        }

    supports: List[Dict[str, object]] = []
    duplicates: List[Dict[str, object]] = []
    for other in ds.reports:
        if other.index == report_index:
            continue
        if abs(other.minutes - rep.minutes) > window_min:
            continue
        oc = parse_report(other.text)
        if oc["lat"] is None or oc["zone"] is not None:
            continue
        sep = haversine_distance(
            claim["lat"], claim["lon"], oc["lat"], oc["lon"]  # type: ignore[arg-type]
        )
        if sep > radius_m:
            continue

        row = {
            "index": other.index, "time": other.time, "source": other.source,
            "text": other.text, "separation_m": round(sep, 1),
            "minutes_apart": abs(other.minutes - rep.minutes),
        }
        if other.text == rep.text:
            row["why"] = ("Birebir ayni metin. Tekrar, bagimsiz dogrulama "
                          "degildir; destek sayilmadi.")
            duplicates.append(row)
            continue

        # --- eksen eksen anlasma ---
        agreed: List[str] = []
        disputed: List[str] = []
        fa = _TYPE_FAMILY.get(str(claim["vehicle_type"]))
        fb = _TYPE_FAMILY.get(str(oc["vehicle_type"]))
        if claim["vehicle_type"] and oc["vehicle_type"]:
            if fa and fb and fa == fb:
                agreed.append("vehicle_type")
            elif fa and fb:
                disputed.append("vehicle_type")
        if claim["count"] is not None and oc["count"] is not None:
            if abs(int(claim["count"]) - int(oc["count"])) <= 1:
                agreed.append("count")
            else:
                disputed.append("count")
        if claim["movement"] and oc["movement"]:
            if claim["movement"] == oc["movement"]:
                agreed.append("movement")

        if not agreed:
            continue
        kind = "cross_source" if other.source != rep.source else "same_source"
        row["support_kind"] = kind
        row["agreed_on"] = agreed
        row["disputed_on"] = disputed
        row["why"] = (
            f"{'Farkli kaynak' if kind == 'cross_source' else 'Ayni kaynak'} "
            f"({other.source}), {sep:.0f} m, {row['minutes_apart']} dk arayla "
            f"{', '.join(agreed)} konusunda ayni seyi soyluyor"
            + (f"; {', '.join(disputed)} konusunda ayriliyor." if disputed else ".")
            + ("" if kind == "cross_source" else
               " Ayni kaynak oldugu icin bagimsiz dogrulama sayilmaz, zayif destektir.")
        )
        supports.append(row)

    supports.sort(key=lambda r: (r["support_kind"] != "cross_source",
                                 r["minutes_apart"]))
    cross = sum(1 for r in supports if r["support_kind"] == "cross_source")
    return {
        "report_index": report_index,
        "claim": claim,
        "supports": supports,
        "duplicates": duplicates,
        "cross_source_count": cross,
        "same_source_count": len(supports) - cross,
        "note": (
            "Ayni noktada destekleyen rapor yok."
            if not supports else
            f"{cross} capraz-kaynak, {len(supports) - cross} ayni-kaynak destek. "
            f"Capraz kaynak destegi, ayni kaynagin tekrarindan agirdir."
        ),
    }


# --- Tool 3: ayni noktayi anlatan raporlar birbirini tutuyor mu ----------
def cross_report_contradiction_check(
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    time: Optional[str] = None,
    radius_m: float = config.SAME_POINT_RADIUS_M,
    window_min: int = config.REPORT_TIME_WINDOW_MIN,
) -> Dict[str, object]:
    """AYNI NOKTAYI anlatan raporlar birbiriyle tutarli mi?

    Kapsam bilincli olarak dar. Iki rapor ancak koordinatlari, raporlarin
    kendi yazim hassasiyeti kadar yakinsa (varsayilan 30 m) "ayni noktayi
    anlatiyor" sayilir. Daha genis bir yaricap kullanmak, birkac km'lik bir
    bolgede farkli araclari ayni arac sanmak demektir.

    Bu tool HAKEMLIK YAPMAZ. Hangi raporun dogru oldugunu soyleyemez, cunku:
      * veride hangi raporun dogru oldugu isaretli degil;
      * hareket kayitlari da hakem olamaz — her kayit bir goruntunun cekim
        saatinde biten 2 saatlik penceredir, rastgele bir rapor saatinde
        elinizdeki kayit havuzu keyfi bir alt kumedir;
      * park halindeki araclarin kaydi hic bulunmayabilir.
    Yaptigi sey, ayni noktaya dair birbirini tutmayan iddialari yan yana
    koymaktir. Karar senin.

    Donen: {"conflicts": [...], "pairs_examined": int, "note": str}
    """
    q = query_reports(
        lat=lat, lon=lon, time=time,
        radius_m=max(radius_m, config.REPORT_MATCH_RADIUS_M),
        window_min=window_min, include_zone_level=False, limit=50,
    )
    reports = [
        r for r in q["reports"]  # type: ignore[union-attr]
        if r["claim"]["has_position"] and r["claim"]["zone"] is None
    ]

    conflicts: List[Dict[str, object]] = []
    pairs = 0
    for i in range(len(reports)):
        for j in range(i + 1, len(reports)):
            a, b = reports[i], reports[j]
            ca, cb = a["claim"], b["claim"]
            sep = haversine_distance(ca["lat"], ca["lon"], cb["lat"], cb["lon"])
            if sep > radius_m:
                continue
            pairs += 1
            gap = abs(dl.to_minutes(a["time"]) - dl.to_minutes(b["time"]))

            def _row(field, a_value, b_value, why):
                return {
                    "a_index": a["index"], "a_time": a["time"],
                    "a_source": a["source"], "a_text": a["text"],
                    "b_index": b["index"], "b_time": b["time"],
                    "b_source": b["source"], "b_text": b["text"],
                    "field": field, "a_value": a_value, "b_value": b_value,
                    "separation_m": round(sep, 1), "minutes_apart": gap,
                    "why_it_conflicts": why,
                }

            # Arac turu: bir arac tur degistirmez. Ama zaman gecerse ayni
            # noktada FARKLI bir arac bulunabilir.
            if (ca["vehicle_type"] and cb["vehicle_type"]
                    and gap <= config.CLAIM_CONFLICT_MAX_GAP_MIN):
                fa = _TYPE_FAMILY.get(ca["vehicle_type"])
                fb = _TYPE_FAMILY.get(cb["vehicle_type"])
                if fa and fb and fa != fb:
                    conflicts.append(_row(
                        "vehicle_type", ca["vehicle_type"], cb["vehicle_type"],
                        f"Ayni nokta ({sep:.0f} m), {gap} dk arayla farkli arac "
                        f"turu bildirilmis. Bir arac tur degistirmez; ya biri "
                        f"yanlis ya da iki farkli aractan soz ediliyor.",
                    ))

            if (ca["count"] is not None and cb["count"] is not None
                    and gap <= config.CLAIM_CONFLICT_MAX_GAP_MIN
                    and abs(int(ca["count"]) - int(cb["count"])) >= 2):
                conflicts.append(_row(
                    "count", ca["count"], cb["count"],
                    f"Ayni nokta ({sep:.0f} m), {gap} dk arayla arac sayisi "
                    f"{ca['count']} ve {cb['count']} olarak bildirilmis. "
                    f"Hangisinin dogru oldugu bu veriyle belirlenemez.",
                ))

            mv = {ca["movement"], cb["movement"]}
            if ("stationary" in mv
                    and mv & {"approaching_base", "departing", "transit"}
                    and gap <= config.MOVEMENT_CONFLICT_MAX_GAP_MIN):
                conflicts.append(_row(
                    "movement", ca["movement"], cb["movement"],
                    f"Ayni nokta ({sep:.0f} m), {gap} dk arayla biri duruyor "
                    f"digeri hareketli diyor. Bu kisa surede ikisi de dogru olamaz.",
                ))

    # Simetri: ayni makine anlasmayi da bulabilir. Celiskinin yoklugu kadar
    # VARLIGI da bilgi — ama sadece bagimsiz kaynaklardan gelirse.
    corroborations: List[Dict[str, object]] = []
    for r in reports:
        sup = find_supporting_reports(int(r["index"]), radius_m=radius_m,
                                      window_min=window_min)
        for x in sup["supports"]:  # type: ignore[union-attr]
            key = tuple(sorted((int(r["index"]), int(x["index"]))))
            if any(tuple(sorted((c["a_index"], c["b_index"]))) == key
                   for c in corroborations):
                continue
            corroborations.append({
                "a_index": int(r["index"]), "b_index": int(x["index"]),
                "support_kind": x["support_kind"], "agreed_on": x["agreed_on"],
                "disputed_on": x["disputed_on"],
                "separation_m": x["separation_m"],
                "minutes_apart": x["minutes_apart"], "why": x["why"],
            })

    conflicts.sort(key=lambda c: (c["minutes_apart"], c["separation_m"]))

    if not conflicts:
        note = (
            f"{pairs} rapor cifti ayni noktayi anlatiyor; aralarinda celiski yok. "
            f"Bu, raporlarin DOGRU oldugu anlamina gelmez — sadece birbirlerini "
            f"yalanlamiyorlar."
        )
    else:
        note = (
            f"{len(conflicts)} celisen iddia var ({pairs} ayni-nokta cifti "
            f"incelendi). Hangisinin dogru oldugunu bu veri soyleyemez; hakem "
            f"kendi tespitindir."
        )

    return {
        "conflicts": conflicts,
        "corroborations": corroborations,
        "pairs_examined": pairs,
        "radius_m": radius_m,
        "note": note,
    }
