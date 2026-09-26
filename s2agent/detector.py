"""Detection backend. Tools only call `get_detector().predict(image_id)`:

    DETECTOR=csv   → PRED_FILE (submission format, default data/stage2/pred_all_boxes_submission.csv)
    DETECTOR=http  → POST image to DETECTOR_URL (GPU server), reply {"PredictionString": "..."}

PredictionString = "label conf x y w h label conf x y w h ..." (x, y = top-left px) or "none".
predict() returns a DataFrame: label, conf, x, y, w, h, cx, cy.
"""
import os
from functools import lru_cache

import httpx
import pandas as pd

from .config import ROOT
from .data import get_data

COLUMNS = ["label", "conf", "x", "y", "w", "h", "cx", "cy"]


def parse_prediction_string(s: str) -> pd.DataFrame:
    tok = [] if not isinstance(s, str) or s.strip().lower() in ("", "none") else s.split()
    rows = [(tok[i], *map(float, tok[i + 1:i + 6])) for i in range(0, len(tok) - 5, 6)]
    df = pd.DataFrame(rows, columns=COLUMNS[:6])
    df["cx"], df["cy"] = df.x + df.w / 2, df.y + df.h / 2
    return df


class CsvDetector:
    def __init__(self, path):
        sub = pd.read_csv(path, dtype=str, keep_default_na=False)
        self.preds = dict(zip(sub.image_id, sub.PredictionString))

    def predict(self, image_id: str) -> pd.DataFrame:
        return parse_prediction_string(self.preds.get(image_id, "none"))


class HttpDetector:
    """POST {DETECTOR_URL} multipart: file=<image>, image_id=<str> → 200 {"PredictionString": "car 0.93 976 533 98 95 ..."}"""

    def __init__(self, url: str, timeout: float = 60):
        self.url, self.timeout = url, timeout

    def predict(self, image_id: str) -> pd.DataFrame:
        path = get_data().image_path(image_id)
        with open(path, "rb") as f:
            r = httpx.post(self.url, files={"file": (path.name, f)}, data={"image_id": image_id}, timeout=self.timeout)
        r.raise_for_status()
        return parse_prediction_string(r.json().get("PredictionString", "none"))


@lru_cache(maxsize=1)
def get_detector():
    if os.getenv("DETECTOR", "csv").lower() == "http":
        url = os.getenv("DETECTOR_URL")
        if not url:
            raise RuntimeError("DETECTOR=http ama DETECTOR_URL boş")
        return HttpDetector(url)
    return CsvDetector(ROOT / os.getenv("PRED_FILE", "data/stage2/pred_all_boxes_submission.csv"))
