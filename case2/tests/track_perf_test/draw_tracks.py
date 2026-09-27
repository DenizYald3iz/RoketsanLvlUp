"""Draw fixed-size track position markers on north-up, georeferenced images.

Requires Pillow. Run from your project directory; see --help for paths.
These are debug markers, not detected vehicle bounding boxes.
"""

import argparse
import csv
import json
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw


def normalize_time(value):
    value = str(value).strip()
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).strftime("%H:%M:%S")
        except ValueError:
            pass
    raise ValueError(f"Invalid time: {value!r}")


def frame_bounds(meta):
    corners = meta["corner_coordinates"]
    tl, tr, bl, br = (corners[key] for key in
                      ("top_left", "top_right", "bottom_left", "bottom_right"))
    aligned = ((tl[0], tr[0]), (bl[0], br[0]),
               (tl[1], bl[1]), (tr[1], br[1]))
    if not all(math.isclose(a, b, rel_tol=0, abs_tol=1e-8)
               for a, b in aligned):
        raise ValueError("Only north-up, axis-aligned geographic rectangles are supported")
    north, south, west, east = tl[0], bl[0], tl[1], tr[1]
    if not (north > south and east > west):
        raise ValueError("Invalid geographic bounds")
    return north, south, west, east


def pixel_position(lat, lon, bounds, width, height):
    north, south, west, east = bounds
    x = (lon - west) / (east - west) * (width - 1)
    y = (north - lat) / (north - south) * (height - 1)
    return round(x), round(y)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--images", type=Path, default=Path("images"))
    parser.add_argument("--meta", type=Path, default=None,
                        help="Default: images/image_meta.json, then ./image_meta.json")
    parser.add_argument("--tracks", type=Path, default=Path("tracks.csv"))
    parser.add_argument("--output", type=Path, default=Path("debug"))
    parser.add_argument("--box-size", type=int, default=40,
                        help="Marker side length in pixels (default: 40)")
    args = parser.parse_args()
    if args.box_size < 2:
        parser.error("--box-size must be at least 2")
    meta_path = args.meta or args.images / "image_meta.json"
    if args.meta is None and not meta_path.exists():
        meta_path = Path("image_meta.json")
    with meta_path.open(encoding="utf-8-sig") as file:
        metadata = json.load(file)

    tracks_by_time = defaultdict(list)
    with args.tracks.open(newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        if not {"track_id", "time", "lat", "lon"}.issubset(reader.fieldnames or []):
            raise ValueError("tracks.csv must have track_id,time,lat,lon columns")
        for row in reader:
            lat, lon = float(row["lat"]), float(row["lon"])
            if not (math.isfinite(lat) and math.isfinite(lon)):
                raise ValueError(f"Non-finite coordinates: {row}")
            tracks_by_time[normalize_time(row["time"])].append(
                (row["track_id"], lat, lon))

    image_paths = {}
    for path in args.images.rglob("*"):
        if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}:
            if path.stem in image_paths:
                raise ValueError(f"Ambiguous image ID: {path.stem}")
            image_paths[path.stem] = path

    args.output.mkdir(parents=True, exist_ok=True)
    saved = missing = total_matches = 0
    report = []
    for image_id, meta in metadata.items():
        capture_time = normalize_time(meta["capture_time"])
        bounds = frame_bounds(meta)
        north, south, west, east = bounds
        matches = [(track_id, lat, lon)
                   for track_id, lat, lon in tracks_by_time.get(capture_time, [])
                   if south <= lat <= north and west <= lon <= east]
        if not matches:
            continue
        image_path = image_paths.get(image_id)
        if image_path is None:
            missing += 1
            print(f"[MISSING] {image_id}: {len(matches)} matching tracks")
            continue
        with Image.open(image_path) as source:
            image = source.convert("RGB")
        width, height = image.size
        if (width, height) != (meta["width_px"], meta["height_px"]):
            print(f"[SIZE] {image_id}: using actual size {width}x{height}; "
                  "assumes the same geographic extent, without cropping")
        draw = ImageDraw.Draw(image)
        for track_id, lat, lon in matches:
            x, y = pixel_position(lat, lon, bounds, width, height)
            left = max(0, x - args.box_size // 2)
            top = max(0, y - args.box_size // 2)
            right = min(width - 1, x - args.box_size // 2 + args.box_size - 1)
            bottom = min(height - 1, y - args.box_size // 2 + args.box_size - 1)
            draw.rectangle((left, top, right, bottom), outline="lime", width=2)
            draw.line((max(0, x - 4), y, min(width - 1, x + 4), y), fill="red", width=1)
            draw.line((x, max(0, y - 4), x, min(height - 1, y + 4)), fill="red", width=1)
            label = f"{track_id} {meta['capture_time']}"
            text_bounds = draw.textbbox((0, 0), label)
            text_w = text_bounds[2] - text_bounds[0]
            text_h = text_bounds[3] - text_bounds[1]
            tx = max(0, min(left, width - text_w - 4))
            ty = max(0, min(top - text_h - 6, height - text_h - 4))
            draw.rectangle((tx, ty, tx + text_w + 4, ty + text_h + 4), fill="black")
            draw.text((tx + 2 - text_bounds[0], ty + 2 - text_bounds[1]), label, fill="lime")
            report.append([image_id, capture_time, track_id, lat, lon, x, y,
                           left, top, right, bottom])
        output_path = args.output / f"{image_id}_tracks.jpg"
        image.save(output_path, quality=95)
        saved += 1
        total_matches += len(matches)
        print(f"[SAVED] {output_path}: {len(matches)} tracks")

    with (args.output / "track_matches.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["image_id", "time", "track_id", "lat", "lon", "pixel_x", "pixel_y",
                         "box_left", "box_top", "box_right", "box_bottom"])
        writer.writerows(report)
    print(f"Done: {saved} images saved, {total_matches} track markers, "
          f"{missing} matching images missing from disk.")
    print("Only exact capture-time matches are used; no time interpolation.")


if __name__ == "__main__":
    main()
