"""Read-only access to the stage-2 files. Everything is loaded once and cached."""
import json
from functools import cached_property
from pathlib import Path

import pandas as pd

from .config import CFG


class Data:
    def __init__(self, data_dir: Path = CFG.data_dir):
        self.dir = Path(data_dir)

    @cached_property
    def meta(self) -> dict:
        """image_id -> {width_px, height_px, capture_time, corner_coordinates{top_left:[lat,lon],...}}"""
        return json.loads((self.dir / "image_meta.json").read_text())

    @cached_property
    def zones(self) -> dict:
        """{"base": {name, lat, lon}, "zones": [{name, center:[lat,lon]}, ...]}"""
        return json.loads((self.dir / "zones.json").read_text())

    @cached_property
    def tracks(self) -> pd.DataFrame:
        """track_id, time (HH:MM), lat, lon — 25 points / track, 5 min apart, ends at an image capture time."""
        return pd.read_csv(self.dir / "tracks.csv", dtype={"time": str})

    @cached_property
    def reports(self) -> list[dict]:
        """[{report_id, time, source, text}] — report_id (R000..) is added here, stable by file order."""
        raw = json.loads((self.dir / "field_reports.json").read_text())
        return [{"report_id": f"R{i:03d}", **r} for i, r in enumerate(raw)]

    def image_ids(self) -> list[str]:
        return sorted(self.meta)

    def image_path(self, image_id: str) -> Path:
        hits = list((self.dir / "images").glob(f"{image_id}.*"))
        if not hits:
            raise FileNotFoundError(image_id)
        return hits[0]


_DATA: Data | None = None


def get_data() -> Data:
    global _DATA
    if _DATA is None:
        _DATA = Data()
    return _DATA
