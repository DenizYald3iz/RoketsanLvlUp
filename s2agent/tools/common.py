"""Tools available in every stage."""
from ..geo import describe_point
from ..registry import ToolContext, stage_tool


@stage_tool("*")
def zone_info(lat: float, lon: float, *, ctx: ToolContext) -> dict:
    """Bir koordinatın en yakın bölgesini (zones.json) ve merkez üsse mesafesini/yönünü döner.
    Dönüş: {zone, zone_dist_m, base_dist_m, bearing_from_base_deg, direction_from_base}."""
    return describe_point(ctx.data.zones, lat, lon)
