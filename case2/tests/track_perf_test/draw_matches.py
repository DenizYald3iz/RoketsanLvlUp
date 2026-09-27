import argparse
import re
from pathlib import Path

from PIL import Image, ImageDraw

parser = argparse.ArgumentParser()
parser.add_argument("log_file", type=Path)
args = parser.parse_args()

image_pattern = re.compile(r"^=+\s*(img_\d+)\s*=+$")
match_pattern = re.compile(
    r"^\s*T\d+\s+\{\s*['\"]cx['\"]:\s*([-\d.]+),"
    r"\s*['\"]cy['\"]:\s*([-\d.]+)\s*\}\s*$"
)

# Collect coordinates for each image.
matches = {}
current_image = None

with args.log_file.open(encoding="utf-8", errors="replace") as log:
    for line in log:
        image_match = image_pattern.match(line.strip())
        if image_match:
            current_image = image_match.group(1)
            matches.setdefault(current_image, [])
            continue

        coordinate_match = match_pattern.match(line)
        if current_image and coordinate_match:
            cx, cy = map(float, coordinate_match.groups())
            track_id = line.split()[0]
            matches[current_image].append((track_id, cx, cy))

source_dir = Path("./debug")
output_dir = source_dir / "matchdebug"
output_dir.mkdir(parents=True, exist_ok=True)

box_size = 20

for image_id, coordinates in matches.items():
    source_path = source_dir / f"{image_id}_tracks.jpg"
    output_path = output_dir / source_path.name

    if not source_path.is_file():
        print(f"Missing image: {source_path}")
        continue

    with Image.open(source_path) as original:
        image = original.convert("RGB")

    draw = ImageDraw.Draw(image)

    for track_id, cx, cy in coordinates:
        left = round(cx - box_size / 2)
        top = round(cy - box_size / 2)
        bottom = top + box_size - 1

        draw.rectangle(
            [left, top, left + box_size - 1, bottom],
            outline="red",
            width=2,
        )

        # Center the track ID below the box.
        draw.text(
            (left + (box_size - 1) / 2, bottom + 4),
            track_id,
            fill="red",
            anchor="mt",
            stroke_width=1,
            stroke_fill="black",
        )

    image.save(output_path, quality=95)
    print(f"Saved: {output_path} ({len(coordinates)} boxes)")