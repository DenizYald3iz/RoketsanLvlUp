"""Stage 3 — MOTION: speed, heading, approach to base, ETA — from the whole 2h record, not one step."""
from ..registry import ToolContext, stage_tool
from ._stub import todo


@stage_tool("MOTION", writes="kinematics", key_by="track_id")
def get_track_kinematics(track_id: str, *, ctx: ToolContext) -> dict:
    """Bir track'in 2 saatlik kaydından hareket özeti:
    {avg_speed_mps, last30_speed_mps, heading_deg, base_dist_now_m, base_dist_60min_ago_m,
     closing_speed_mps (+ = üsse yaklaşıyor), eta_min (yaklaşmıyorsa null),
     stopped_min, loitering (üs çevresinde dolaşma), path_straightness (0-1)}."""
    # TODO: ctx.data.tracks[track_id] sorted by time; geo.haversine_m / bearing_deg; zones base
    return todo(track_id=track_id, avg_speed_mps=None, last30_speed_mps=None, heading_deg=None,
                base_dist_now_m=None, closing_speed_mps=None, eta_min=None, loitering=None)


@stage_tool("MOTION")
def get_track_points(track_id: str, last_n: int = 25, *, ctx: ToolContext) -> dict:
    """Bir track'in ham noktaları (time, lat, lon, base_dist_m), en yeni son. Özet yetmezse kullan."""
    # TODO
    return todo(track_id=track_id, points=[])
