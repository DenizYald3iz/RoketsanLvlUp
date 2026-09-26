"""Stage 1 — LOCATE: what does the image cover, and which vehicles are in it (with lat/lon)."""
from collections import Counter
from ..config import CFG
from ..geo import (
    image_data_url,
    pixel_to_latlon,
    haversine_m,
    bearing_deg,
)
from ..registry import ToolContext, stage_tool
from ._stub import todo


@stage_tool("LOCATE", writes="image_info")
def get_image_info(*, ctx: ToolContext) -> dict:
    """
    Görüntünün çekim saati, piksel boyutu, köşe koordinatları,
    merkez koordinatı, en yakın bölge ve merkez üsse olan
    mesafe/yön bilgisini döndürür.

    LOCATE aşamasında ilk çağrılması gereken tool'dur.
    """

    # Görüntü metadata'sını al
    meta = ctx.data.meta.get(ctx.image_id)

    if meta is None:
        return {
            "error": f"Metadata bulunamadı: {ctx.image_id}"
        }

    width = int(meta["width_px"])
    height = int(meta["height_px"])

    # Görüntünün merkez pikseli
    center_x = width / 2.0
    center_y = height / 2.0

    # Merkez pikseli WGS84 koordinatına çevir
    center_lat, center_lon = pixel_to_latlon(
        center_x,
        center_y,
        meta,
    )

    zones_data = ctx.data.zones

    # Merkez üs bilgisi
    base = zones_data["base"]

    base_lat = float(base["lat"])
    base_lon = float(base["lon"])

    # Görüntü merkezinin üsse uzaklığı
    base_dist_m = haversine_m(
        center_lat,
        center_lon,
        base_lat,
        base_lon,
    )

    # Görüntü merkezinden üs yönüne bearing
    base_bearing = bearing_deg(
        center_lat,
        center_lon,
        base_lat,
        base_lon,
    )

    # Görüntü merkezine en yakın bölgeyi bul
    nearest_zone = None
    nearest_zone_dist_m = None

    for zone in zones_data.get("zones", []):
        zone_lat, zone_lon = zone["center"]

        dist_m = haversine_m(
            center_lat,
            center_lon,
            float(zone_lat),
            float(zone_lon),
        )

        if (
            nearest_zone_dist_m is None
            or dist_m < nearest_zone_dist_m
        ):
            nearest_zone_dist_m = dist_m
            nearest_zone = zone

    zone_result = None

    if nearest_zone is not None:
        zone_result = {
            "name": nearest_zone["name"],
            "distance_m": round(nearest_zone_dist_m, 1),
        }

    return {
        "image_id": ctx.image_id,
        "capture_time": meta["capture_time"],

        "size_px": {
            "width": width,
            "height": height,
        },

        "corner_coordinates": meta["corner_coordinates"],

        "center": {
            "x_px": round(center_x, 2),
            "y_px": round(center_y, 2),
            "lat": round(center_lat, 7),
            "lon": round(center_lon, 7),
        },

        "zone": zone_result,

        "base": {
            "name": base.get("name", "Merkez Us"),
            "distance_m": round(base_dist_m, 1),
            "bearing_deg": round(base_bearing, 1),
        },
    }


@stage_tool("LOCATE", writes="detections")
def get_detections(min_conf: float = 0.5, *, ctx: ToolContext) -> dict:
    """
    1. gün modelinin bu görüntüdeki araç tespitlerini döndürür.

    Sadece conf >= min_conf olan tespitler alınır.
    Her tespitin kutu merkezi WGS84 lat/lon koordinatına çevrilir.

    Her tespit:
    {det_id, label, conf, cx, cy, lat, lon}

    Dönüş:
    {count, by_label, detections:[...]}
    """

    if not 0.0 <= min_conf <= 1.0:
        return {
            "error": "min_conf 0.0 ile 1.0 arasında olmalıdır."
        }

    meta = ctx.data.meta.get(ctx.image_id)

    if meta is None:
        return {
            "error": f"Metadata bulunamadı: {ctx.image_id}"
        }

    boxes = ctx.data.boxes

    if boxes is None or boxes.empty:
        return {
            "image_id": ctx.image_id,
            "min_conf": min_conf,
            "count": 0,
            "by_label": {},
            "detections": [],
        }

    image_boxes = boxes[
        boxes["image_id"] == ctx.image_id
    ].copy()

    if image_boxes.empty:
        return {
            "image_id": ctx.image_id,
            "min_conf": min_conf,
            "count": 0,
            "by_label": {},
            "detections": [],
        }

    # Önce tüm detection'ları confidence'a göre sırala.
    # Böylece det_id threshold değişse bile mümkün olduğunca stabil kalır.
    image_boxes = image_boxes.sort_values(
        "conf",
        ascending=False,
    ).reset_index(drop=True)

    image_boxes["det_index"] = range(
        1,
        len(image_boxes) + 1,
    )

    # Threshold uygula.
    filtered = image_boxes[
        image_boxes["conf"] >= min_conf
    ].copy()

    detections = []

    for _, row in filtered.iterrows():
        cx = float(row["cx"])
        cy = float(row["cy"])

        lat, lon = pixel_to_latlon(
            cx,
            cy,
            meta,
        )

        det_id = f"D{int(row['det_index']):03d}"

        detections.append(
            {
                "det_id": det_id,
                "label": str(row["label"]),
                "conf": round(float(row["conf"]), 5),
                "cx": round(cx, 2),
                "cy": round(cy, 2),
                "lat": round(float(lat), 7),
                "lon": round(float(lon), 7),
            }
        )

    by_label = dict(
        Counter(
            det["label"]
            for det in detections
        )
    )

    return {
        "image_id": ctx.image_id,
        "min_conf": min_conf,
        "count": len(detections),
        "by_label": by_label,
        "detections": detections,
    }


@stage_tool("LOCATE", "MOTION")
def view_image(
    crop_x: int | None = None,
    crop_y: int | None = None,
    crop_w: int | None = None,
    crop_h: int | None = None,
    *,
    ctx: ToolContext,
) -> dict:
    """
    Görüntüyü veya piksel cinsinden belirtilen kırpımı modele gösterir.

    Crop kullanılacaksa crop_x, crop_y, crop_w ve crop_h birlikte verilmelidir.
    Pahalıdır; sadece görsel doğrulama gerektiğinde kullan.
    """

    values = (crop_x, crop_y, crop_w, crop_h)
    supplied = [v is not None for v in values]

    # Crop parametrelerinin bir kısmı verilmişse hata
    if any(supplied) and not all(supplied):
        return {
            "error": (
                "Crop için crop_x, crop_y, crop_w ve crop_h "
                "birlikte verilmelidir."
            )
        }

    crop = None

    if all(supplied):
        meta = ctx.data.meta.get(ctx.image_id)

        if meta is None:
            return {
                "error": f"Metadata bulunamadı: {ctx.image_id}"
            }

        width = int(meta["width_px"])
        height = int(meta["height_px"])

        x = int(crop_x)
        y = int(crop_y)
        w = int(crop_w)
        h = int(crop_h)

        if x < 0 or y < 0:
            return {
                "error": "crop_x ve crop_y negatif olamaz."
            }

        if w <= 0 or h <= 0:
            return {
                "error": "crop_w ve crop_h pozitif olmalıdır."
            }

        if x >= width or y >= height:
            return {
                "error": "Crop başlangıç noktası görüntü dışında."
            }

        # Crop görüntü sınırını aşıyorsa kırp
        w = min(w, width - x)
        h = min(h, height - y)

        crop = (x, y, w, h)

    url = image_data_url(
        ctx.data.image_path(ctx.image_id),
        CFG.image_max_side,
        crop,
    )

    return {
        "_image": url,
        "shown": ctx.image_id,
        "crop": crop,
    }