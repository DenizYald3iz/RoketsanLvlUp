"""Upload → boxes → lat/lon → zone. Pure functions; server.py only does HTTP.

Zone rule (change here only): within CENTER_RADIUS_M of the base → "Merkez Us",
otherwise the zone whose bearing from the base is closest (8 compass sectors).
"""
import os
from pathlib import Path

import httpx

from s2agent.data import get_data
from s2agent.detector import get_detector, parse_prediction_string
from s2agent.geo import bearing_deg, compass, frame_center, haversine_m, pixel_to_latlon

CENTER_RADIUS_M = 1200
MIN_CONF = 0.3
NMS_IOU = 0.5


class NeedsMeta(Exception):
    """Uploaded image has no known corner coordinates."""


def layout() -> dict:
    z = get_data().zones
    b = z["base"]
    zones = [{"name": zz["name"], "center": zz["center"],
              "bearing": round(bearing_deg(b["lat"], b["lon"], *zz["center"]), 2)} for zz in z["zones"]]
    return {"base": b, "center_radius_m": CENTER_RADIUS_M, "zones": zones}


def assign_zone(lat: float, lon: float, lay: dict) -> str:
    b = lay["base"]
    if haversine_m(lat, lon, b["lat"], b["lon"]) <= CENTER_RADIUS_M:
        return b["name"]
    brg = bearing_deg(b["lat"], b["lon"], lat, lon)
    return min(lay["zones"], key=lambda z: abs((brg - z["bearing"] + 180) % 360 - 180))["name"]


def footprint(meta: dict) -> list[list[float]]:
    """[lon, lat] corners in tl, tr, br, bl order (MapLibre image-source order)."""
    c = meta["corner_coordinates"]
    return [[c[k][1], c[k][0]] for k in ("top_left", "top_right", "bottom_right", "bottom_left")]


def images() -> list[dict]:
    return [{"image_id": i, "capture_time": m["capture_time"], "footprint": footprint(m)}
            for i, m in sorted(get_data().meta.items())]


def raw_predict(image_id: str, file_path: Path):
    """Detector boxes for the uploaded file. http → GPU server gets the uploaded bytes; csv → lookup by id."""
    if os.getenv("DETECTOR", "csv").lower() == "http":
        with open(file_path, "rb") as f:
            r = httpx.post(os.environ["DETECTOR_URL"], files={"file": (file_path.name, f)},
                           data={"image_id": image_id}, timeout=60)
        r.raise_for_status()
        return parse_prediction_string(r.json().get("PredictionString", "none"))
    return get_detector().predict(image_id)


def _iou(a, b) -> float:
    iw = max(0.0, min(a.x + a.w, b.x + b.w) - max(a.x, b.x))
    ih = max(0.0, min(a.y + a.h, b.y + b.h) - max(a.y, b.y))
    return iw * ih / (a.w * a.h + b.w * b.h - iw * ih + 1e-9)


def nms(df, min_conf: float = MIN_CONF) -> list:
    keep = []
    for r in df.sort_values("conf", ascending=False).itertuples():
        if all(_iou(k, r) < NMS_IOU for k in keep):
            keep.append(r)
    return [r for r in keep if r.conf >= min_conf]


def analyze(file_path: Path, image_id: str, min_conf: float = MIN_CONF) -> dict:
    meta = get_data().meta.get(image_id)
    if meta is None:
        raise NeedsMeta(image_id)
    lay = layout()
    b = lay["base"]
    dets = []
    for r in nms(raw_predict(image_id, file_path), min_conf):
        lat, lon = pixel_to_latlon(r.cx, r.cy, meta)
        brg = bearing_deg(b["lat"], b["lon"], lat, lon)
        dets.append({"det_id": f"D{len(dets):02d}", "label": r.label, "conf": round(r.conf, 3),
                     "cx": r.cx, "cy": r.cy, "w": r.w, "h": r.h, "lat": round(lat, 6), "lon": round(lon, 6),
                     "zone": assign_zone(lat, lon, lay),
                     "base_dist_m": round(haversine_m(lat, lon, b["lat"], b["lon"])),
                     "direction": compass(brg)})
    clat, clon = frame_center(meta)
    return {"image_id": image_id, "capture_time": meta["capture_time"],
            "size": [meta["width_px"], meta["height_px"]], "footprint": footprint(meta),
            "center": [clon, clat], "zone": assign_zone(clat, clon, lay),
            "base_dist_m": round(haversine_m(clat, clon, b["lat"], b["lon"])), "detections": dets}
