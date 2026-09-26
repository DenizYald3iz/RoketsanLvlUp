"""Stage 2 — TRACKS: which movement records belong to the detected vehicles."""
from ..geo import haversine_m
from ..registry import ToolContext, stage_tool


def _frame(ctx: ToolContext):
    m = ctx.data.meta[ctx.image_id]
    c = m["corner_coordinates"]
    t = ctx.data.tracks
    now = t[t.time == m["capture_time"]]
    lat0, lat1, lon0, lon1 = c["bottom_left"][0], c["top_left"][0], c["top_left"][1], c["top_right"][1]
    inside = now.lat.between(lat0, lat1) & now.lon.between(lon0, lon1)
    return now, inside, (lat0, lat1, lon0, lon1)


@stage_tool("TRACKS", writes="matches")
def match_tracks(max_dist_m: float = 20.0, *, ctx: ToolContext) -> dict:
    """Tespitleri, çekim saatindeki (time == capture_time) track noktalarıyla eşleştirir: en yakın çiftler
    önce, her track/tespit en fazla bir kez, mesafe <= max_dist_m. Park halindeki araçların kaydı olmayabilir.
    Dönüş: {matches:[{det_id, label, track_id, dist_m}], unmatched_detections:[det_id],
            unmatched_tracks_in_frame:[track_id] (çerçevede olup tespit edilmemiş araçlar)}."""
    dets = ctx.evidence.get("detections", {}).get("detections")
    if dets is None:
        return {"error": "Önce get_image_info / get_detections çağrılmalı."}
    now, inside, _ = _frame(ctx)
    pairs = sorted((haversine_m(d["lat"], d["lon"], r.lat, r.lon), d["det_id"], d["label"], r.track_id)
                   for d in dets for r in now.itertuples())
    used_d, used_t, matches = set(), set(), []
    for dist, did, label, tid in pairs:
        if dist > max_dist_m:
            break
        if did not in used_d and tid not in used_t:
            used_d.add(did), used_t.add(tid)
            matches.append({"det_id": did, "label": label, "track_id": tid, "dist_m": round(dist, 1)})
    return {"matches": matches,
            "unmatched_detections": [d["det_id"] for d in dets if d["det_id"] not in used_d],
            "unmatched_tracks_in_frame": sorted(set(now[inside].track_id) - used_t)}


@stage_tool("TRACKS", writes="nearby_tracks")
def list_tracks_near(radius_m: float = 1000.0, *, ctx: ToolContext) -> dict:
    """Çekim anında görüntü çerçevesinin DIŞINDA ama çerçeveye radius_m içinde olan track'ler
    (görüntüye girmemiş/çıkmış araçlar). Dönüş: {tracks:[{track_id, dist_to_frame_m, lat, lon}]}."""
    now, inside, (lat0, lat1, lon0, lon1) = _frame(ctx)
    out = []
    for r in now[~inside].itertuples():
        d = haversine_m(r.lat, r.lon, min(max(r.lat, lat0), lat1), min(max(r.lon, lon0), lon1))
        if d <= radius_m:
            out.append({"track_id": r.track_id, "dist_to_frame_m": round(d), "lat": r.lat, "lon": r.lon})
    return {"tracks": sorted(out, key=lambda x: x["dist_to_frame_m"])}
