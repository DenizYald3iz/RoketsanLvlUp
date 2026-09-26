"""Detection backend. get_detections() only talks to `get_detector()`, so the source can be swapped
without touching tools:

    DETECTOR=csv   → data/stage2/pred_all_boxes.csv (default, offline)
    DETECTOR=http  → POST image to DETECTOR_URL (GPU inference server)

Every backend returns a DataFrame with the pred_all_boxes.csv columns:
    label, conf, x, y, w, h, cx, cy, p_car, p_van, p_truck, p_bus, p_bg
(x, y = top-left, cx, cy = centre, all in original image pixels; p_* optional → filled with NaN).
"""
import os
from functools import lru_cache

import httpx
import pandas as pd

from .data import get_data

COLUMNS = ["label", "conf", "x", "y", "w", "h", "cx", "cy", "p_car", "p_van", "p_truck", "p_bus", "p_bg"]


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "cx" not in df:
        df["cx"] = df["x"] + df["w"] / 2
    if "cy" not in df:
        df["cy"] = df["y"] + df["h"] / 2
    for c in COLUMNS:
        if c not in df:
            df[c] = float("nan")
    return df[COLUMNS].reset_index(drop=True)


class CsvDetector:
    """Precomputed day-1 predictions."""

    def predict(self, image_id: str) -> pd.DataFrame:
        b = get_data().boxes
        return _normalize(b[b.image_id == image_id])


class HttpDetector:
    """GPU server contract:
    POST {DETECTOR_URL}  multipart: file=<image bytes>, image_id=<str>
    → 200 {"boxes": [{"label": "car", "conf": 0.91, "x": .., "y": .., "w": .., "h": .., "p_car": .., ...}]}
    """

    def __init__(self, url: str, timeout: float = 60):
        self.url, self.timeout = url, timeout

    def predict(self, image_id: str) -> pd.DataFrame:
        path = get_data().image_path(image_id)
        with open(path, "rb") as f:
            r = httpx.post(self.url, files={"file": (path.name, f)}, data={"image_id": image_id}, timeout=self.timeout)
        r.raise_for_status()
        boxes = r.json().get("boxes", [])
        return _normalize(pd.DataFrame(boxes, columns=None) if boxes else pd.DataFrame(columns=COLUMNS))


@lru_cache(maxsize=1)
def get_detector():
    kind = os.getenv("DETECTOR", "csv").lower()
    if kind == "http":
        url = os.getenv("DETECTOR_URL")
        if not url:
            raise RuntimeError("DETECTOR=http ama DETECTOR_URL boş")
        return HttpDetector(url)
    return CsvDetector()
