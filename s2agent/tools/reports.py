"""Stage 4 — REPORTS: which field reports concern this image, and do they agree with our evidence?
Rule (task PDF): if a report contradicts our detections/tracks, trust our evidence and ignore the report."""
import re

from ..geo import dist_to_frame_m, frame_center, haversine_m, hhmm_to_min
from ..registry import ToolContext, stage_tool
from .locate import get_detections

# "39.9374N 32.8483E", also tolerates "39.9374 N, 32.8483 E" / "39.9374°N 32.8483°E"
_COORD = re.compile(r"(\d{1,2}\.\d+)\s*°?\s*N[\s,;]*(\d{1,3}\.\d+)\s*°?\s*E", re.IGNORECASE)
_TR = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")


def _norm(s: str) -> str:
    return s.translate(_TR).lower()


def parse_location(text: str, zones: dict) -> dict | None:
    """Report text → {loc_type: coord, lat, lon} | {loc_type: zone, zone, lat, lon} | None (no location)."""
    if m := _COORD.search(text):
        return {"loc_type": "coord", "lat": float(m[1]), "lon": float(m[2])}
    t = _norm(text)
    for z in zones["zones"]:
        if _norm(z["name"]) in t:
            return {"loc_type": "zone", "zone": z["name"], "lat": z["center"][0], "lon": z["center"][1]}
    return None


def nearest_zone(lat: float, lon: float, zones: dict) -> dict:
    return min(zones["zones"], key=lambda z: haversine_m(lat, lon, *z["center"]))


@stage_tool("REPORTS", writes="reports")
def find_reports(radius_m: float = 300.0, window_min: int = 120, *, ctx: ToolContext) -> dict:
    """Bu görüntüyle ilgili saha raporlarını bulur. Sadece çekimden en fazla window_min dakika ÖNCE yazılmış
    raporlara bakar (hareket kaydı da son 2 saati kapsar). İlgili sayılanlar:
      - coord: metindeki koordinat görüntü çerçevesine radius_m içinde (in_frame = çerçevenin içinde),
      - zone: metindeki bölge adı görüntünün bulunduğu bölge (image_zone).
    Konumsuz genel duyurular (hava durumu, tatbikat/dost unsur, konvoy) ayrı 'general' listesinde gelir.
    Dönüş: {image_zone, capture_time, reports:[{report_id, time, source, text, loc_type: coord|zone,
            dist_m (çerçeveye; zone için bölge merkezine), dt_min (çekimden kaç dk önce), in_frame,
            lat, lon (sadece coord)}],
            general:[{text, report_ids}]}.
    Sonra 'reports' içindeki HER report_id için compare_report çağır."""
    meta = ctx.data.meta[ctx.image_id]
    capture = hhmm_to_min(meta["capture_time"])
    image_zone = nearest_zone(*frame_center(meta), ctx.data.zones)["name"]

    reports, general = [], {}
    for r in ctx.data.reports:
        dt = capture - hhmm_to_min(r["time"])
        if not 0 <= dt <= window_min:
            continue
        loc = parse_location(r["text"], ctx.data.zones)
        if loc is None:
            general.setdefault(r["text"], []).append(r["report_id"])
            continue
        if loc["loc_type"] == "zone" and loc["zone"] != image_zone:
            continue
        dist = dist_to_frame_m(loc["lat"], loc["lon"], meta)
        if loc["loc_type"] == "coord" and dist > radius_m:
            continue
        where = {"lat": loc["lat"], "lon": loc["lon"]} if loc["loc_type"] == "coord" else {}
        reports.append({**r, "loc_type": loc["loc_type"], "dist_m": round(dist), "dt_min": dt,
                        "in_frame": loc["loc_type"] == "coord" and dist == 0, **where})

    # most specific first: in-frame coordinates, then nearby coordinates, then zone-wide; newest first within
    reports.sort(key=lambda x: (not x["in_frame"], x["loc_type"] != "coord", x["dt_min"]))
    return {"image_zone": image_zone, "capture_time": meta["capture_time"], "reports": reports,
            "general": [{"text": t, "report_ids": ids} for t, ids in general.items()]}


# ---------------------------------------------------------------- compare_report
# In this dataset a report's coordinate marks its subject vehicle *at capture time* (4-5 decimals → ≤ ~6 m),
# while the report's timestamp is earlier. So we compare against the image's detections and the end points
# (= capture-time positions) of the tracks that end in this image.
HI_CONF, LO_CONF = 0.5, 0.3  # detection is trusted / only weak support
SUBJECT_M = 15.0  # report coordinate ↔ its subject vehicle
AREA_M = 80.0  # "civarında / çevresinde" area used for counts (a 6-truck group spans ~70 m)
STOP_MPS, MOVE_MPS, CLOSE_MPS = 0.5, 1.0, 0.3
TREND_M = 100  # base-distance change over 60 min = approaching/receding (same rule as get_track_kinematics)
HEAVY = {"truck", "bus"}
_TYPES = [("agir arac", "heavy"), ("kamyon", "truck"), ("otobus", "bus"), ("panelvan", "van"),
          ("otomobil", "car"), ("arac", "any")]
_TYPE_TR = {"heavy": "ağır araç", "truck": "kamyon", "bus": "otobüs", "van": "panelvan", "car": "otomobil",
            "any": "araç"}


def parse_claim(text: str) -> dict:
    """Template-based parse of a report's claim. scope != 'vehicle' means it is not a checkable vehicle claim."""
    t = _norm(text)
    c: dict = {"scope": "vehicle", "type": None, "count": None, "min_count": None, "motion": None,
               "friendly": any(k in t for k in ("dost", "bize bagli", "ikmal araci", "kimlik teyid", "teyitli"))}
    for scope, keys in (("stale", ("dun gece", "dogrulanamadi", "dogrulanmamis")), ("comms", ("telsiz",)),
                        ("weather", ("hava acik", "gorus mesafesi")), ("blanket_friendly", ("tatbikat",)),
                        ("schedule", ("yola cikacak",)), ("no_heavy", ("agir arac hareketi yok",)),
                        ("no_activity", ("hareketlilik bulunmuyor",)),
                        ("normal", ("trafik akisi normal", "olagandisi bir durum"))):
        if any(k in t for k in keys):
            c["scope"] = scope
            return c
    c["type"] = next((ty for word, ty in _TYPES if word in t), None)
    if m := re.search(r"(?:olagan trafik|genellikle)\D{0,15}(\d+) arac", t):  # "usually ~4 vehicles" → more now
        c["type"], c["min_count"] = "any", int(m[1]) + 1
    elif m := re.search(r"(\d+) araclik", t) or re.search(r"(\d+)\s*(?:kamyon|otomobil|panelvan|otobus|agir arac|arac)", t):
        c["count"] = int(m[1])
    if "usse dogru" in t or "usse gelen" in t:
        c["motion"] = "approaching"
    elif "uzaklasiyor" in t:
        c["motion"] = "leaving"
    elif any(k in t for k in ("ilerliyor", "transit", "geciyor")):
        c["motion"] = "moving"
    elif any(k in t for k in ("durdu", "hareketsiz", "park", "beklemede", "bekliyor", "ayrilmadi")):
        c["motion"] = "stopped_long" if ("uzun sure" in t or "saatten uzun" in t) else "stopped"
    return c


def _type_ok(claimed: str, label: str) -> bool:
    return claimed == "any" or claimed == label or (claimed == "heavy" and label in HEAVY)


def _motion(pts: list[tuple[float, float]], base: tuple[float, float]) -> dict:
    """pts: 5-min spaced positions, last = capture time."""
    seg = [haversine_m(*a, *b) for a, b in zip(pts, pts[1:])]
    bd = [haversine_m(*p, *base) for p in pts]

    def speed(n: int) -> float:
        k = min(n, len(seg))
        return sum(seg[-k:]) / (k * 300) if k else 0.0

    k = min(6, len(bd) - 1)
    delta60 = bd[-1 - min(12, len(bd) - 1)] - bd[-1]
    return {"speed_30m_mps": round(speed(6), 1), "speed_60m_mps": round(speed(12), 1),
            "closing_30m_mps": round((bd[-1 - k] - bd[-1]) / (k * 300), 1) if k else 0.0,
            "trend_60m": "approaching" if delta60 > TREND_M else "receding" if delta60 < -TREND_M else "stationary",
            "base_dist_m": round(bd[-1])}


def _scene(ctx: ToolContext) -> tuple[list[dict], list[dict]]:
    """Detections (conf >= LO_CONF) and tracks ending at this image's capture time, with lat/lon."""
    meta = ctx.data.meta[ctx.image_id]
    # Same pipeline as LOCATE (detector + NMS). det_ids are assigned in conf order after the min_conf cut,
    # so they match the det_ids in evidence.detections.
    dets = [{**d, "ref": d["det_id"]} for d in get_detections(min_conf=LO_CONF, ctx=ctx)["detections"]]

    tr = ctx.data.tracks
    ends = tr.groupby("track_id")["time"].max()
    base = (ctx.data.zones["base"]["lat"], ctx.data.zones["base"]["lon"])
    tracks = []
    for tid in ends[ends == meta["capture_time"]].index:
        p = tr[tr.track_id == tid].sort_values("time")
        pts = list(zip(p.lat, p.lon))
        tracks.append({"track_id": tid, "lat": pts[-1][0], "lon": pts[-1][1], **_motion(pts, base),
                       "in_frame": dist_to_frame_m(*pts[-1], meta) <= 20})
    return dets, tracks


def _nearest(items: list[dict], lat: float, lon: float, max_m: float) -> dict | None:
    best = min(items, key=lambda x: haversine_m(lat, lon, x["lat"], x["lon"]), default=None)
    return best if best and haversine_m(lat, lon, best["lat"], best["lon"]) <= max_m else None


def _mot_txt(t: dict) -> str:
    return (f"{t['track_id']}: son 30 dk {t['speed_30m_mps']} m/s, son 60 dk {t['speed_60m_mps']} m/s, "
            f"üsse yaklaşma {t['closing_30m_mps']:+} m/s, 60 dk eğilim {t['trend_60m']}")


def _check_motion(claim: str, trk: dict | None, seen: bool) -> tuple[bool | None, str]:
    if trk is None:  # task: parked vehicles may have no movement record
        if not seen:
            return None, "konumda araç/track yok, hareket doğrulanamaz"
        return (claim.startswith("stopped"),
                "araç görüntüde var ama hareket kaydı yok → park halinde")
    v = trk["speed_60m_mps"] if claim == "stopped_long" else trk["speed_30m_mps"]
    cl, txt = trk["closing_30m_mps"], _mot_txt(trk)
    if claim.startswith("stopped"):
        return (True if v < STOP_MPS else False if v >= MOVE_MPS else None), txt
    if claim == "moving":
        return (True if v >= MOVE_MPS else False if v < STOP_MPS else None), txt
    trend = trk["trend_60m"]  # 30-min closing and 60-min trend can disagree; then the claim is only weak
    if claim == "approaching":
        if v < STOP_MPS or (cl <= 0 and trend != "approaching"):
            return False, txt
        return (True if v >= MOVE_MPS and cl > CLOSE_MPS else None), txt
    # leaving
    if v < STOP_MPS or cl > CLOSE_MPS or (cl >= 0 and trend == "approaching"):
        return False, txt
    return (True if cl < -CLOSE_MPS else None), txt


def _check_vehicle_claim(c: dict, lat: float, lon: float, dets: list, tracks: list,
                         det2trk: dict | None = None) -> tuple[list, list]:
    checks, related = [], []
    by_id = {t["track_id"]: t for t in tracks}

    def trk_of(d: dict) -> dict | None:
        """A detection's track: from match_tracks when available (a detection without a match has none)."""
        if det2trk is not None:
            return by_id.get(det2trk.get(d["ref"]))
        return _nearest(tracks, d["lat"], d["lon"], SUBJECT_M)

    area = [d for d in dets if haversine_m(lat, lon, d["lat"], d["lon"]) <= AREA_M]
    subj_det = _nearest(dets, lat, lon, SUBJECT_M)
    subj_trk = trk_of(subj_det) if subj_det else _nearest(tracks, lat, lon, SUBJECT_M)
    ty = c["type"] or "any"

    if c["min_count"]:  # "busier than the usual N"
        n = sum(d["conf"] >= HI_CONF for d in area)
        checks.append({"aspect": "count", "ok": n >= c["min_count"],
                       "ours": f"{AREA_M:.0f} m içinde {n} araç (conf≥{HI_CONF}); iddia >{c['min_count'] - 1}"})
        related += [d["ref"] for d in area if d["conf"] >= HI_CONF]
    elif c["count"]:
        match = [d for d in area if _type_ok(ty, d["label"])]
        n_hi, n_lo = sum(d["conf"] >= HI_CONF for d in match), len(match)
        need = c["count"] - (1 if c["count"] >= 4 else 0)  # detector may miss one in a large group
        ok = True if n_hi >= need else False if n_lo < need else None
        checks.append({"aspect": "count", "ok": ok,
                       "ours": f"{AREA_M:.0f} m içinde {_TYPE_TR[ty]}: {n_hi} (conf≥{HI_CONF}), {n_lo} (conf≥{LO_CONF}); "
                               f"iddia {c['count']}"})
        related += [d["ref"] for d in match]
        # single-vehicle claim: the vehicle AT the reported point must be of that type
        near_ok = [d for d in match if haversine_m(lat, lon, d["lat"], d["lon"]) <= SUBJECT_M]
        if c["count"] == 1 and c["type"] and subj_det and not near_ok and subj_det["conf"] >= HI_CONF:
            dm = haversine_m(lat, lon, subj_det["lat"], subj_det["lon"])
            checks.append({"aspect": "type", "ok": False,
                           "ours": f"rapor noktasındaki araç {subj_det['ref']} {subj_det['label']} ({dm:.0f} m, "
                                   f"conf {subj_det['conf']}); iddia {_TYPE_TR[ty]}"})
            related.append(subj_det["ref"])
    elif c["type"]:
        if subj_det is None:
            checks.append({"aspect": "type", "ok": False,
                           "ours": f"raporlanan konumun {SUBJECT_M:.0f} m içinde araç tespit edilmedi"})
        else:
            ok = _type_ok(ty, subj_det["label"])  # a low-conf detection neither confirms nor refutes the type
            checks.append({"aspect": "type", "ok": ok if subj_det["conf"] >= HI_CONF else None,
                           "ours": f"konumdaki araç {subj_det['label']} (conf {subj_det['conf']}); "
                                   f"iddia {_TYPE_TR[ty]}"})
            related.append(subj_det["ref"])

    group = []  # count claims ("5 trucks stopped") are about every counted vehicle, not just the one at the point
    if c["count"]:
        for d in area:
            if _type_ok(ty, d["label"]) and (t := trk_of(d)) and t not in group:
                group.append(t)
    if c["motion"] and group:
        res = [(t, *_check_motion(c["motion"], t, True)) for t in group]
        oks = [ok for _, ok, _ in res]
        if c["motion"].startswith("stopped"):  # one moving vehicle is enough to refute "they are stopped"
            ok = False if False in oks else True if all(oks) else None
        else:
            ok = True if True in oks else False if all(o is False for o in oks) else None
        txt = " | ".join(txt for _, _, txt in res)
        checks.append({"aspect": "motion", "ok": ok, "ours": f"sayılan araçların track'leri: {txt}; iddia {c['motion']}"})
        related += [t["track_id"] for t in group]
    elif c["motion"]:
        seen = subj_det is not None or bool(c["count"] and any(_type_ok(ty, d["label"]) for d in area))
        ok, txt = _check_motion(c["motion"], subj_trk, seen)
        checks.append({"aspect": "motion", "ok": ok, "ours": f"{txt}; iddia {c['motion']}"})
    if subj_trk and (subj_det is None or subj_det["ref"] in related):  # only the track of a vehicle we cited
        related.append(subj_trk["track_id"])
    return checks, related


def _check_scope_claim(c: dict, dets: list, tracks: list) -> tuple[str, str, list]:
    s = c["scope"]
    if s == "stale":
        return "irrelevant", "Dün geceye ait ya da doğrulanamamış ihbar; çekim anı hakkında bilgi vermiyor.", []
    if s == "comms":
        return "irrelevant", "Telsiz/iletişim durumu; araç gözlemi içermiyor.", []
    if s == "weather":
        return "irrelevant", "Hava durumu bilgisi; araç gözlemi içermiyor.", []
    if s == "blanket_friendly":
        return ("unverifiable", "Genel 'dost unsur' beyanı; belirli bir araca bağlanamaz, tek başına bir "
                                "aracı dost saymak için kullanılamaz.", [])
    if s == "schedule":
        return "unverifiable", "Planlanan konvoy duyurusu; konum/araç belirtmiyor.", []
    if s == "normal":
        return "unverifiable", "Genel 'durum normal' ifadesi; ölçülebilir bir iddia içermiyor.", []
    moving = [t for t in tracks if t["in_frame"] and t["speed_30m_mps"] >= MOVE_MPS]
    if s == "no_heavy":  # "no heavy-vehicle *movement*": a parked truck does not contradict it
        heavy = [d for d in dets if d["label"] in HEAVY and d["conf"] >= HI_CONF]
        pairs = [(d, t) for d in heavy if (t := _nearest(tracks, d["lat"], d["lon"], SUBJECT_M))
                 and t["speed_30m_mps"] >= MOVE_MPS]
        if pairs:
            txt = ", ".join(f"{d['label']} {d['ref']} ({t['track_id']} {t['speed_30m_mps']} m/s)" for d, t in pairs)
            return ("contradicts", f"Ağır araç hareketi yok deniyor; görüntüde hareketli ağır araç var: {txt}.",
                    [x for d, t in pairs for x in (d["ref"], t["track_id"])])
        return "consistent", (f"Görüntüde hareketli ağır araç yok ({len(heavy)} duran ağır araç)." if heavy
                              else "Görüntüde conf≥0.5 ağır araç tespiti yok."), [d["ref"] for d in heavy]
    # no_activity
    if moving:
        return ("contradicts", f"Hareketlilik yok deniyor; görüntüde {len(moving)} hareketli track var "
                               f"({', '.join(t['track_id'] for t in moving)}).", [t["track_id"] for t in moving])
    return "consistent", "Görüntüde hareketli (≥1 m/s) track yok.", []


@stage_tool("REPORTS", writes="report_checks", key_by="report_id")
def compare_report(report_id: str, *, ctx: ToolContext) -> dict:
    """Raporun iddiasını (araç tipi, sayı, durma/hareket/üsse yaklaşma, 'dost unsur' beyanı) bu görüntünün
    tespitleri ve çekim anındaki track'lerle karşılaştırır (LLM çağrısı yok, kural tabanlı).
    Dönüş: {report_id, source, claim:{type,count,motion,friendly,...},
            verdict: consistent|contradicts|unverifiable|irrelevant, checks:[{aspect, ok, ours}],
            related:[det/track id], det_tracks:{det_id: track_id|null (match_tracks'e göre; null = kaydı yok)}, reason}.
    contradicts → rapor yok sayılır (tespit esas). friendly=true ve consistent ise araç dost olabilir;
    friendly iddiası tek başına doğrulanamaz."""
    rep = next((r for r in ctx.data.reports if r["report_id"] == report_id), None)
    if rep is None:
        return {"error": f"rapor bulunamadı: {report_id}"}
    claim = parse_claim(rep["text"])
    loc = parse_location(rep["text"], ctx.data.zones)
    dets, tracks = _scene(ctx)
    out = {"report_id": report_id, "source": rep["source"], "time": rep["time"],
           "claim": {k: v for k, v in claim.items() if v not in (None, False) and k != "scope"}}

    if claim["scope"] != "vehicle":
        verdict, reason, related = _check_scope_claim(claim, dets, tracks)
        return {**out, "verdict": verdict, "checks": [], "related": related, "reason": reason}
    if loc is None or loc["loc_type"] != "coord":
        return {**out, "verdict": "unverifiable", "checks": [], "related": [],
                "reason": "Raporda koordinat yok; iddia belirli bir araca bağlanamıyor."}
    meta = ctx.data.meta[ctx.image_id]
    if (d := dist_to_frame_m(loc["lat"], loc["lon"], meta)) > AREA_M:
        return {**out, "verdict": "irrelevant", "checks": [], "related": [],
                "reason": f"Raporun konumu görüntü çerçevesinin {d:.0f} m dışında."}

    m = ctx.evidence.get("matches")
    det2trk = {x["det_id"]: x["track_id"] for x in m["matches"]} if m else None
    checks, related = _check_vehicle_claim(claim, loc["lat"], loc["lon"], dets, tracks, det2trk)
    related = list(dict.fromkeys(related))
    if det2trk is not None:  # make det↔track links explicit so they are not guessed
        out["det_tracks"] = {r: det2trk.get(r) for r in related if r.startswith("D")}
    oks = [ch["ok"] for ch in checks]
    verdict = ("contradicts" if False in oks else "consistent" if oks and all(oks) else "unverifiable")
    reason = "; ".join(f"{ch['aspect']}: {'uyumlu' if ch['ok'] else 'ÇELİŞKİ' if ch['ok'] is False else 'zayıf'} "
                       f"({ch['ours']})" for ch in checks) or "Kontrol edilebilir iddia yok."
    if claim["friendly"]:
        reason += "; dost beyanı " + {"contradicts": "tespitle çeliştiği için geçersiz",
                                      "consistent": "kimlik açısından doğrulanamaz, tespitle çelişmiyor"}.get(
            verdict, "tespitle desteklenmiyor (zayıf); dost kabul etmek için yeterli değil")
    return {**out, "verdict": verdict, "checks": checks, "related": related, "reason": reason}
