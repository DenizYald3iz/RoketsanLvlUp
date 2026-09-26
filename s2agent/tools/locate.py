"""Stage 1 — LOCATE: what does the image cover, and which vehicles are in it (with lat/lon)."""
from ..config import CFG
from ..geo import image_data_url
from ..registry import ToolContext, stage_tool
from ._stub import todo


@stage_tool("LOCATE", writes="image_info")
def get_image_info(*, ctx: ToolContext) -> dict:
    """Görüntünün çekim saati, piksel boyutu, köşe koordinatları, merkez koordinatı,
    en yakın bölge ve merkez üsse mesafesi. Her görüntüde ilk çağrılacak tool."""
    # TODO: ctx.data.meta[ctx.image_id] + center via geo.pixel_to_latlon + zone/base distance
    return todo(image_id=ctx.image_id, capture_time=None, center=None, zone=None, base_dist_m=None)


@stage_tool("LOCATE", writes="detections")
def get_detections(min_conf: float = 0.5, *, ctx: ToolContext) -> dict:
    """1. gün modelinin bu görüntüdeki araç tespitleri, conf >= min_conf.
    Her tespit: {det_id, label, conf, cx, cy, lat, lon} (kutu merkezi → koordinat).
    Dönüş: {count, by_label, detections:[...]}."""
    # TODO: ctx.data.boxes filtered by image_id & conf; geo.pixel_to_latlon(cx, cy, meta)
    return todo(count=0, by_label={}, detections=[])


@stage_tool("LOCATE", "MOTION")
def view_image(crop_x: int | None = None, crop_y: int | None = None,
               crop_w: int | None = None, crop_h: int | None = None, *, ctx: ToolContext) -> dict:
    """Görüntüyü (ya da piksel cinsinden bir kırpımını) sana gösterir; bir sonraki mesajda görürsün.
    Pahalıdır: sadece tespit tipi/sayısı şüpheliyse kullan."""
    crop = (crop_x, crop_y, crop_w, crop_h) if None not in (crop_x, crop_y, crop_w, crop_h) else None
    url = image_data_url(ctx.data.image_path(ctx.image_id), CFG.image_max_side, crop)
    return {"_image": url, "shown": ctx.image_id, "crop": crop}
