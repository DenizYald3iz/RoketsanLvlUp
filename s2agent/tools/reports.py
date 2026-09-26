"""Stage 4 — REPORTS: which field reports concern this image, and do they agree with our evidence?
Rule (task PDF): if a report contradicts our detections/tracks, trust our evidence and ignore the report."""
import re

from ..geo import dist_to_frame_m, frame_center, haversine_m, hhmm_to_min
from ..registry import ToolContext, stage_tool
from ._stub import todo

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


@stage_tool("REPORTS", writes="report_checks", key_by="report_id")
def compare_report(report_id: str, *, ctx: ToolContext) -> dict:
    """Raporun iddiasını (araç tipi, sayı, hareket/durma, 'dost unsur' beyanı) kendi tespit ve
    track bulgularımızla karşılaştırır.
    Dönüş: {report_id, claim:{type,count,motion,friendly}, verdict: consistent|contradicts|unverifiable|irrelevant,
            related:[det_id/track_id], reason}."""
    # TODO: parse claim (regex or small LLM call) + compare with ctx.evidence
    return todo(report_id=report_id, claim={}, verdict="unverifiable", related=[], reason="not implemented")
