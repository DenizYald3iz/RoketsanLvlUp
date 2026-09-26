"""Stage 1 — LOCATE: what does the image cover, and which vehicles are in it (with lat/lon)."""
from ..config import CFG
from ..detector import get_detector
from ..geo import image_data_url, pixel_to_latlon
from ..registry import ToolContext, stage_tool
from ._stub import todo


@stage_tool("LOCATE", writes="image_info")
def get_image_info(*, ctx: ToolContext) -> dict:
    """Görüntünün çekim saati, piksel boyutu, köşe koordinatları, merkez koordinatı,
    en yakın bölge ve merkez üsse mesafesi. Her görüntüde ilk çağrılacak tool."""
    # TODO: ctx.data.meta[ctx.image_id] + center via geo.pixel_to_latlon + zone/base distance
    return todo(image_id=ctx.image_id, capture_time=None, center=None, zone=None, base_dist_m=None)


CLASSES = ["car", "van", "truck", "bus"]


def _iou(a, b) -> float:
    ax2, ay2, bx2, by2 = a.x + a.w, a.y + a.h, b.x + b.w, b.y + b.h
    iw, ih = max(0.0, min(ax2, bx2) - max(a.x, b.x)), max(0.0, min(ay2, by2) - max(a.y, b.y))
    inter = iw * ih
    return inter / (a.w * a.h + b.w * b.h - inter + 1e-9)


def _nms(df, iou_thr: float):
    keep = []
    for r in df.sort_values("conf", ascending=False).itertuples():
        if all(_iou(r, k) < iou_thr for k in keep):
            keep.append(r)
    return keep


@stage_tool("LOCATE", writes="detections")
def get_detections(min_conf: float = 0.3, *, ctx: ToolContext) -> dict:
    """Tespit modelinin bu görüntüdeki araçları (conf >= min_conf, çakışan kutular elenmiş).
    Her tespit: {det_id, label, conf, cx, cy, w, h, lat, lon, probs, type_uncertain}.
    type_uncertain=true ise araç tipi güvenilir değil (sınıf olasılıkları yakın ya da etiketle çelişiyor);
    raporla tip karşılaştırırken bunu dikkate al. Dönüş: {count, by_label, detections:[...]}."""
    meta = ctx.data.meta.get(ctx.image_id)
    if meta is None:
        return {"error": f"image_meta'da yok: {ctx.image_id}"}
    df = get_detector().predict(ctx.image_id)
    df = df[df.conf >= min_conf]
    dets = []
    for i, r in enumerate(_nms(df, iou_thr=0.5)):
        lat, lon = pixel_to_latlon(r.cx, r.cy, meta)
        probs = {c: round(float(getattr(r, f"p_{c}")), 3) for c in CLASSES if getattr(r, f"p_{c}") == getattr(r, f"p_{c}")}
        ranked = sorted(probs.items(), key=lambda kv: -kv[1])
        uncertain = bool(ranked) and (ranked[0][0] != r.label or (len(ranked) > 1 and ranked[0][1] - ranked[1][1] < 0.2))
        dets.append({"det_id": f"D{i:02d}", "label": r.label, "conf": round(float(r.conf), 3),
                     "cx": round(float(r.cx), 1), "cy": round(float(r.cy), 1),
                     "w": round(float(r.w), 1), "h": round(float(r.h), 1),
                     "lat": round(lat, 6), "lon": round(lon, 6), "probs": probs, "type_uncertain": uncertain})
    by_label: dict[str, int] = {}
    for d in dets:
        by_label[d["label"]] = by_label.get(d["label"], 0) + 1
    return {"count": len(dets), "by_label": by_label, "detections": dets}


@stage_tool("LOCATE", "MOTION")
def view_image(crop_x: int | None = None, crop_y: int | None = None,
               crop_w: int | None = None, crop_h: int | None = None, *, ctx: ToolContext) -> dict:
    """Görüntüyü (ya da piksel cinsinden bir kırpımını) sana gösterir; bir sonraki mesajda görürsün.
    Pahalıdır: sadece tespit tipi/sayısı şüpheliyse kullan."""
    crop = (crop_x, crop_y, crop_w, crop_h) if None not in (crop_x, crop_y, crop_w, crop_h) else None
    url = image_data_url(ctx.data.image_path(ctx.image_id), CFG.image_max_side, crop)
    return {"_image": url, "shown": ctx.image_id, "crop": crop}
