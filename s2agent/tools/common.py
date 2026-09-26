"""Tools available in every stage."""
from ..registry import ToolContext, stage_tool
from ._stub import todo


@stage_tool("*")
def zone_info(lat: float, lon: float, *, ctx: ToolContext) -> dict:
    """Bir koordinatın en yakın bölgesini (zones.json) ve merkez üsse mesafesini/yönünü döner.
    Dönüş: {zone, zone_dist_m, base_dist_m, bearing_from_base_deg}."""
    # TODO: ctx.data.zones + geo.haversine_m / geo.bearing_deg
    return todo(zone=None, zone_dist_m=None, base_dist_m=None, bearing_from_base_deg=None)
