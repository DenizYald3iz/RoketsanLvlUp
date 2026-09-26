"""Stage 3 — MOTION: speed, heading, approach to base, ETA — from the whole 2h record, not one step.
Tracks move in bursts (≈3/4 of 5-min steps are stops), so speeds/ETA use moving steps, not averages over stops."""
from ..geo import bearing_deg, compass, haversine_m
from ..registry import ToolContext, stage_tool

STEP_S = 300  # 5-min records
MOVING_MPS = 0.5  # below this a step counts as stopped
TREND_M = 100  # base-distance change over 60 min that counts as approaching/receding


def _points(track_id: str, ctx: ToolContext):
    t = ctx.data.tracks
    pts = t[t.track_id == track_id].sort_values("time")
    base = ctx.data.zones["base"]
    rows = [(r.time, r.lat, r.lon, haversine_m(r.lat, r.lon, base["lat"], base["lon"])) for r in pts.itertuples()]
    steps = [haversine_m(a[1], a[2], b[1], b[2]) for a, b in zip(rows, rows[1:])]
    return rows, steps, base


@stage_tool("MOTION", writes="kinematics", key_by="track_id")
def get_track_kinematics(track_id: str, *, ctx: ToolContext) -> dict:
    """Bir track'in 2 saatlik kaydından hareket özeti (hız/yön kaydın tamamından):
    avg_speed_mps (duruşlar dahil), moving_speed_mps (sadece hareketli adımlar), moving_ratio,
    last30_speed_mps, heading_deg/heading (son hareket yönü), heading_vs_base_deg (0 = tam üsse doğru),
    base_dist_now_m / _30min_ago_m / _60min_ago_m, trend (approaching|receding|stationary, son 60 dk),
    closing_speed_mps (son 30 dk hareketli adımlarda üsse yaklaşma hızı, + = yaklaşıyor), eta_min (trend approaching değilse null),
    stopped_now, stopped_for_min, path_m, straightness (0-1, düşük = dolaşma), loitering, min_base_dist_m."""
    rows, steps, base = _points(track_id, ctx)
    if len(rows) < 2:
        return {"error": f"track bulunamadı ya da tek nokta: {track_id}"}
    dist = [r[3] for r in rows]
    speeds = [d / STEP_S for d in steps]
    moving = [i for i, v in enumerate(speeds) if v >= MOVING_MPS]
    path = sum(steps)
    net = haversine_m(rows[0][1], rows[0][2], rows[-1][1], rows[-1][2])

    def ago(k):  # base distance k steps before the last point
        return dist[max(0, len(dist) - 1 - k)]

    # heading: last moving step
    heading = None
    if moving:
        i = moving[-1]
        heading = bearing_deg(rows[i][1], rows[i][2], rows[i + 1][1], rows[i + 1][2])
    to_base = bearing_deg(rows[-1][1], rows[-1][2], base["lat"], base["lon"])
    # closing speed while moving, last 30 min (fallback: last 60 min, then whole track)
    recent = [i for i in moving if i >= len(steps) - 6] or [i for i in moving if i >= len(steps) - 12] or moving
    closing = sum(dist[i] - dist[i + 1] for i in recent) / (len(recent) * STEP_S) if recent else 0.0
    delta60 = ago(12) - dist[-1]
    trend = "approaching" if delta60 > TREND_M else "receding" if delta60 < -TREND_M else "stationary"
    stopped_for = 0
    for v in reversed(speeds):
        if v >= MOVING_MPS:
            break
        stopped_for += 5
    straight = net / path if path > 0 else 1.0
    return {
        "track_id": track_id, "from": rows[0][0], "to": rows[-1][0],
        "avg_speed_mps": round(path / (len(steps) * STEP_S), 2),
        "moving_speed_mps": round(sum(speeds[i] for i in moving) / len(moving), 2) if moving else 0.0,
        "moving_ratio": round(len(moving) / len(steps), 2),
        "last30_speed_mps": round(sum(steps[-6:]) / (min(6, len(steps)) * STEP_S), 2),
        "heading_deg": round(heading) if heading is not None else None,
        "heading": compass(heading) if heading is not None else None,
        "heading_vs_base_deg": round(abs((heading - to_base + 180) % 360 - 180)) if heading is not None else None,
        "base_dist_now_m": round(dist[-1]), "base_dist_30min_ago_m": round(ago(6)), "base_dist_60min_ago_m": round(ago(12)),
        "trend": trend,
        "closing_speed_mps": round(closing, 2),
        "eta_min": round(dist[-1] / closing / 60, 1) if closing > 0.1 and trend == "approaching" else None,
        "stopped_now": speeds[-1] < MOVING_MPS, "stopped_for_min": stopped_for,
        "path_m": round(path), "straightness": round(straight, 2),
        "loitering": path > 1000 and straight < 0.3,
        "min_base_dist_m": round(min(dist)),
    }


@stage_tool("MOTION")
def get_track_points(track_id: str, last_n: int = 25, *, ctx: ToolContext) -> dict:
    """Bir track'in ham noktaları, en yeni en sonda: [time, lat, lon, base_dist_m, step_speed_mps].
    Özet (get_track_kinematics) yetmezse kullan."""
    rows, steps, _ = _points(track_id, ctx)
    if not rows:
        return {"error": f"track bulunamadı: {track_id}"}
    spd = [None] + [round(d / STEP_S, 2) for d in steps]
    pts = [[r[0], round(r[1], 6), round(r[2], 6), round(r[3]), s] for r, s in zip(rows, spd)][-last_n:]
    return {"track_id": track_id, "columns": ["time", "lat", "lon", "base_dist_m", "step_speed_mps"], "points": pts}
