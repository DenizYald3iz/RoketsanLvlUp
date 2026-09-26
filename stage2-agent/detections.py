"""1. gun tespit modelinin ciktisini okur ve kullanilabilir hale getirir.

Ham format (pred_all_boxes_submission.csv):
    image_id,PredictionString
    img_000267,"car 0.914 1184 191 63 40 truck 0.783 834 163 45 50 ..."

Yani her kutu 6 alan: sinif skor x y w h. Goruntu basina 500 kutuya kadar
cikiyor ve AYNI kutuya birden fazla sinif atanmis olabiliyor (NMS oncesi
ham cikti). Bu modul:
  * ayristirir,
  * ayni kutudaki sinif tekrarlarini teke indirir (en yuksek skorlu sinif),
  * skor esigine gore suzer.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, List, Tuple

import config

DETECTIONS_PATH = config.DATA_DIR / "pred_all_boxes_submission.csv"

# Modelin sinifi -> raporlardaki arac turu sozlugu
CLASS_TO_VEHICLE = {
    "car": "otomobil",
    "van": "panelvan",
    "truck": "kamyon",
    "bus": "otobus",
}
HEAVY_CLASSES = {"truck", "bus"}


@dataclass(frozen=True)
class Detection:
    image_id: str
    cls: str
    score: float
    x: float
    y: float
    w: float
    h: float

    @property
    def bbox(self) -> List[float]:
        return [self.x, self.y, self.w, self.h]

    @property
    def center(self) -> Tuple[float, float]:
        return (self.x + self.w / 2.0, self.y + self.h / 2.0)

    @property
    def area_px(self) -> float:
        return self.w * self.h

    @property
    def vehicle_type(self) -> str:
        return CLASS_TO_VEHICLE.get(self.cls, "arac")


def _parse_prediction_string(image_id: str, s: str) -> List[Detection]:
    tok = s.split()
    out: List[Detection] = []
    for i in range(0, len(tok) - 5, 6):
        out.append(Detection(
            image_id=image_id,
            cls=tok[i],
            score=float(tok[i + 1]),
            x=float(tok[i + 2]), y=float(tok[i + 3]),
            w=float(tok[i + 4]), h=float(tok[i + 5]),
        ))
    return out


@lru_cache(maxsize=1)
def load_raw() -> Dict[str, List[Detection]]:
    """Ham kutular, hicbir suzgec uygulanmadan. Skora gore azalan sirali."""
    out: Dict[str, List[Detection]] = {}
    with DETECTIONS_PATH.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            dets = _parse_prediction_string(row["image_id"], row["PredictionString"])
            dets.sort(key=lambda d: (-d.score, d.cls))
            out[row["image_id"]] = dets
    return out


def dedupe_classes(dets: List[Detection]) -> List[Detection]:
    """Ayni kutuya atanmis coklu siniflari teke indirir.

    Model ayni piksel kutusuna hem 'van 0.598' hem 'truck 0.318' hem
    'car 0.262' yazabiliyor. Bunlar ayri arac degil, ayni arac icin rakip
    sinif tahminleri. En yuksek skorlu olan alinir.
    """
    best: Dict[Tuple[float, float, float, float], Detection] = {}
    for d in dets:
        key = (d.x, d.y, d.w, d.h)
        cur = best.get(key)
        if cur is None or (d.score, d.cls) > (cur.score, cur.cls):
            best[key] = d
    # Belirlenimci sira: skor azalan, sonra kutu konumu
    return sorted(best.values(), key=lambda d: (-d.score, d.x, d.y, d.cls))


def get_detections(
    image_id: str,
    min_score: float = 0.30,
    dedupe: bool = True,
) -> List[Detection]:
    """Bir goruntunun kullanilabilir tespitleri.

    Args:
        min_score: skor esigi. Ham cikti 0.005'e kadar iniyor; dusuk esik
                   yuzlerce hayalet kutu demek.
        dedupe:    ayni kutudaki rakip siniflari teke indir.
    """
    dets = load_raw().get(image_id, [])
    if dedupe:
        dets = dedupe_classes(dets)
    return [d for d in dets if d.score >= min_score]


def reset_cache() -> None:
    load_raw.cache_clear()
