"""Create one JSON summary per track relative to a zone point.

Inputs: zones.json, tracks.csv, and matched_tracks.json from match_tracks_json.py.
Times are assumed to belong to ONE calendar day; records are sorted by time.
Distances describe recorded positions, not unobserved movement between samples.
Approach speed is the distance-to-zone reduction divided by elapsed seconds.
Image matches represent geographic/time containment, not visual detection.
Uses only the Python standard library.
"""

import argparse
import csv
import json
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import quote


def seconds(value):
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            t = datetime.strptime(value.strip(), fmt)
            return t.hour * 3600 + t.minute * 60 + t.second
        except ValueError:
            pass
    raise ValueError(f"Invalid time: {value!r}")


def coordinates(item):
    lat, lon = float(item["lat"]), float(item["lon"])
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError(f"Invalid coordinates: {item}")
    return lat, lon


def haversine_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(max(0.0, min(1.0, a))))


def summarize(track_id, rows, zone_key, zone, images):
    base_lat, base_lon = coordinates(zone)
    by_time = {}
    for row in rows:
        time_s = seconds(row["time"])
        lat, lon = coordinates(row)
        if time_s in by_time:
            old = by_time[time_s]
            if (old["lat"], old["lon"]) != (lat, lon):
                raise ValueError(f"{track_id}: conflicting positions at {row['time']}")
            continue  # Ignore identical duplicate samples.
        by_time[time_s] = {
            "time": row["time"].strip(), "lat": lat, "lon": lon,
            "distance_m": haversine_m(lat, lon, base_lat, base_lon),
        }
    points = sorted(by_time.items())
    closest_s, closest = min(points, key=lambda pair: pair[1]["distance_m"])
    _, farthest = max(points, key=lambda pair: pair[1]["distance_m"])
    fastest = None
    best_speed = 0.0
    approaching_intervals = 0
    for (start_s, start), (end_s, end) in zip(points, points[1:]):
        reduction = start["distance_m"] - end["distance_m"]
        if reduction <= 0:
            continue
        approaching_intervals += 1
        elapsed_s = end_s - start_s
        speed = reduction / elapsed_s
        if speed > best_speed:
            best_speed = speed
            fastest = {
                "start_time": start["time"], "end_time": end["time"],
                "duration_seconds": elapsed_s,
                "start_distance_m": round(start["distance_m"], 3),
                "end_distance_m": round(end["distance_m"], 3),
                "distance_reduction_m": round(reduction, 3),
                "speed_m_s": round(speed, 6),
                "speed_km_h": round(speed * 3.6, 6),
            }

    def rounded(point):
        return {**point, "distance_m": round(point["distance_m"], 3)}

    return {
        "track_id": track_id,
        "zone": {"key": zone_key, "name": zone["name"], "lat": base_lat, "lon": base_lon},
        "sample_count": len(points),
        "first_seen_time": points[0][1]["time"],
        "last_seen_time": points[-1][1]["time"],
        "closest": rounded(closest),
        "farthest": rounded(farthest),
        "seconds_from_first_seen_to_closest": closest_s - points[0][0],
        "approaching_interval_count": approaching_intervals,
        "fastest_approach": fastest,
        "image_count": len(images),
        "images": sorted(images, key=lambda image: (seconds(image["capture_time"]), image["image_id"])),
        "method": {
            "distance": "Haversine distance in meters to the zone point at recorded samples",
            "approach_speed": "Decrease in distance to zone / elapsed seconds between consecutive samples; interval average, not instantaneous travel speed",
            "time_assumption": "All records are from one calendar day",
            "image_matching": "Exact capture time and geographic containment, from matched_tracks.json; not visual confirmation",
            "ties": "Earliest sample or interval wins",
            "null_fastest_approach": "No observed interval with decreasing distance, or fewer than two samples",
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zones", type=Path, default=Path("zones.json"))
    parser.add_argument("--zone-key", default="base")
    parser.add_argument("--tracks", type=Path, default=Path("tracks.csv"))
    parser.add_argument("--matches", type=Path, default=Path("matched_tracks.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("track_summaries"))
    args = parser.parse_args()
    zones = json.loads(args.zones.read_text(encoding="utf-8-sig"))
    zone = zones[args.zone_key]
    matched = json.loads(args.matches.read_text(encoding="utf-8-sig"))
    tracks = defaultdict(list)
    with args.tracks.open(newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        if not {"track_id", "time", "lat", "lon"}.issubset(reader.fieldnames or []):
            raise ValueError("tracks.csv must have track_id,time,lat,lon columns")
        for row in reader:
            if not row["track_id"].strip():
                raise ValueError("Empty track_id")
            tracks[row["track_id"]].append(row)
    images_by_track = defaultdict(list)
    for image_id, image in matched.items():
        for track_id in {point["track_id"] for point in image["tracks"]}:
            if track_id not in tracks:
                raise ValueError(f"{track_id} in matches but absent from tracks.csv; regenerate matched_tracks.json")
            images_by_track[track_id].append({
                "image_id": image_id, "capture_time": image["capture_time"],
            })
    # Validate and compute every track before writing outputs.
    results = [summarize(track_id, rows, args.zone_key, zone, images_by_track[track_id])
               for track_id, rows in sorted(tracks.items())]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for result in results:
        filename = quote(result["track_id"], safe="") + ".json"
        path = args.output_dir / filename
        path.write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                        encoding="utf-8")
    print(f"Saved {len(results)} track summaries to {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()
