"""Stage 2 — TRACKS: which movement records belong to the detected vehicles."""
from ..registry import ToolContext, stage_tool
from ._stub import todo


@stage_tool("TRACKS", writes="matches")
def match_tracks(max_dist_m: float = 15.0, *, ctx: ToolContext) -> dict:
    """Tespitleri, çekim saatindeki (time == capture_time) track noktalarıyla en yakın-komşu
    eşleştirir (mesafe <= max_dist_m). Park halindeki araçların kaydı olmayabilir.
    Dönüş: {matches:[{det_id, track_id, dist_m}], unmatched_detections:[det_id], unmatched_tracks_in_frame:[track_id]}."""
    # TODO: ctx.evidence["detections"] + ctx.data.tracks[time == capture_time]; 1-1 greedy/Hungarian
    return todo(matches=[], unmatched_detections=[], unmatched_tracks_in_frame=[])


@stage_tool("TRACKS", writes="nearby_tracks")
def list_tracks_near(radius_m: float = 1000.0, *, ctx: ToolContext) -> dict:
    """Çekim saatinde görüntü çerçevesinin dışında ama radius_m içinde olan track'ler
    (görüntüye girmemiş/çıkmış araçlar). Dönüş: {tracks:[{track_id, dist_to_frame_m, lat, lon}]}."""
    # TODO
    return todo(tracks=[])
