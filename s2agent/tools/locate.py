"""Stage 1 — LOCATE: what does the image cover, and which vehicles are in it (with lat/lon)."""
from ..config import CFG
from ..detector import get_detector
from ..geo import describe_point, haversine_m, image_data_url, pixel_to_latlon
from ..registry import ToolContext, stage_tool


@stage_tool("LOCATE", writes="image_info")
def get_image_info(*, ctx: ToolContext) -> dict:
    """Görüntü yüklendiğinde İLK çağrılacak tool. Tek çağrıda döner:
    genel bilgi (çekim saati, piksel boyutu, yerdeki kapsam m, köşe/merkez koordinatı, en yakın bölge,
    üsse mesafe/yön) + görüntüdeki araç tespitleri (get_detections varsayılanlarıyla: tip, conf, lat/lon)."""
    m = ctx.data.meta.get(ctx.image_id)
    if m is None:
        return {"error": f"image_meta'da yok: {ctx.image_id}"}
    c = m["corner_coordinates"]
    lat, lon = pixel_to_latlon(m["width_px"] / 2, m["height_px"] / 2, m)
    dets = get_detections(ctx=ctx)
    return {"image_id": ctx.image_id, "capture_time": m["capture_time"],
            "size_px": [m["width_px"], m["height_px"]],
            "ground_m": [round(haversine_m(*c["top_left"], *c["top_right"])),
                         round(haversine_m(*c["top_left"], *c["bottom_left"]))],
            "corners": c, "center": [round(lat, 6), round(lon, 6)],
            **describe_point(ctx.data.zones, lat, lon),
            "vehicles": dets, "_evidence": {"detections": dets}}


def _iou(a, b) -> float:
    iw = max(0.0, min(a.x + a.w, b.x + b.w) - max(a.x, b.x))
    ih = max(0.0, min(a.y + a.h, b.y + b.h) - max(a.y, b.y))
    return iw * ih / (a.w * a.h + b.w * b.h - iw * ih + 1e-9)


@stage_tool("LOCATE", writes="detections")
def get_detections(min_conf: float = 0.3, *, ctx: ToolContext) -> dict:
    """Tespit modelinin bu görüntüdeki araçları (conf >= min_conf, çakışan kutular birleştirilmiş).
    Her tespit: {det_id, label, conf, cx, cy, w, h, lat, lon, alt_labels, type_uncertain}.
    alt_labels: aynı kutu için modelin verdiği diğer etiketler. type_uncertain=true ise tip güvenilir değil;
    raporla tip karşılaştırırken dikkate al. Dönüş: {count, by_label, detections:[...]}."""
    meta = ctx.data.meta.get(ctx.image_id)
    if meta is None:
        return {"error": f"image_meta'da yok: {ctx.image_id}"}
    df = get_detector().predict(ctx.image_id).sort_values("conf", ascending=False)
    groups: list[list] = []  # greedy NMS; suppressed boxes become alternative labels
    for r in df.itertuples():
        g = next((g for g in groups if _iou(g[0], r) >= 0.5), None)
        g.append(r) if g else groups.append([r])
    dets = []
    for g in groups:
        r = g[0]
        if r.conf < min_conf:
            continue
        alt = {}
        for o in g[1:]:
            if o.label != r.label and o.label not in alt:
                alt[o.label] = round(o.conf, 3)
        lat, lon = pixel_to_latlon(r.cx, r.cy, meta)
        dets.append({"det_id": f"D{len(dets):02d}", "label": r.label, "conf": round(r.conf, 3),
                     "cx": round(r.cx, 1), "cy": round(r.cy, 1), "w": r.w, "h": r.h,
                     "lat": round(lat, 6), "lon": round(lon, 6), "alt_labels": alt,
                     "type_uncertain": any(c >= 0.5 * r.conf for c in alt.values())})
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
