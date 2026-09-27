"""Write track points that fall inside image bounds at the exact capture time.

Uses only the Python standard library. No image files are needed.
Assumes north-up rectangular geographic bounds, as in the provided metadata.
Only images with matches are included. Boundary points are included.
The inputs have no calendar dates; matching uses time of day only.
"""

import argparse
import csv
import json
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path


def normalize_time(value):
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(value.strip(), fmt).strftime("%H:%M:%S")
        except ValueError:
            pass
    raise ValueError(f"Invalid time: {value!r}")


def match_tracks(metadata, rows):
    by_time = defaultdict(list)
    for row in rows:
        lat, lon = float(row["lat"]), float(row["lon"])
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError(f"Invalid coordinates: {row}")
        by_time[normalize_time(row["time"])].append({
            "track_id": row["track_id"],
            "time": row["time"].strip(),
            "lat": lat,
            "lon": lon,
        })

    result = {}
    for image_id, meta in metadata.items():
        corners = meta["corner_coordinates"]
        tl, tr, bl, br = (corners[key] for key in
                          ("top_left", "top_right", "bottom_left", "bottom_right"))
        aligned = ((tl[0], tr[0]), (bl[0], br[0]),
                   (tl[1], bl[1]), (tr[1], br[1]))
        if not all(math.isclose(a, b, rel_tol=0, abs_tol=1e-8)
                   for a, b in aligned):
            raise ValueError(f"{image_id}: expected north-up rectangular bounds")
        north, south, west, east = tl[0], bl[0], tl[1], tr[1]
        if not (-90 <= south < north <= 90 and -180 <= west < east <= 180):
            raise ValueError(f"{image_id}: invalid bounds")
        matches = [point for point in by_time.get(normalize_time(meta["capture_time"]), [])
                   if south <= point["lat"] <= north
                   and west <= point["lon"] <= east]
        if matches:
            result[image_id] = {
                "capture_time": meta["capture_time"],
                "corner_coordinates": corners,
                "tracks": matches,
            }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--meta", type=Path, default=Path("image_meta.json"))
    parser.add_argument("--tracks", type=Path, default=Path("tracks.csv"))
    parser.add_argument("--output", type=Path, default=Path("matched_tracks.json"))
    args = parser.parse_args()
    if args.output.resolve() in {args.meta.resolve(), args.tracks.resolve()}:
        parser.error("Output must differ from the input files")
    with args.meta.open(encoding="utf-8-sig") as file:
        metadata = json.load(file)
    with args.tracks.open(newline="", encoding="utf-8-sig") as file:
        result = match_tracks(metadata, csv.DictReader(file))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False)
                           + "\n", encoding="utf-8")
    count = sum(len(item["tracks"]) for item in result.values())
    print(f"Saved {args.output}: {len(result)} images, {count} track-image matches")


if __name__ == "__main__":
    main()
