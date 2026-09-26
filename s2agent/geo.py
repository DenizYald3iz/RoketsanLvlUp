"""Small geo helpers shared by tools. Keep math here, not in prompts."""
import base64
import io
import math
from pathlib import Path

EARTH_R = 6_371_000.0


def pixel_to_latlon(x: float, y: float, meta: dict) -> tuple[float, float]:
    """Linear mapping from the task PDF (top edge = north, left edge = west)."""
    c = meta["corner_coordinates"]
    tl, tr, bl = c["top_left"], c["top_right"], c["bottom_left"]
    lon = tl[1] + (x / meta["width_px"]) * (tr[1] - tl[1])
    lat = tl[0] + (y / meta["height_px"]) * (bl[0] - tl[0])
    return lat, lon


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing 0=N, 90=E."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def hhmm_to_min(t: str) -> int:
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def image_data_url(path: Path, max_side: int = 1280, crop: tuple[int, int, int, int] | None = None) -> str:
    """JPEG data URL for the vision model; optional crop=(x, y, w, h) in original pixels."""
    from PIL import Image

    img = Image.open(path).convert("RGB")
    if crop:
        x, y, w, h = crop
        img = img.crop((x, y, x + w, y + h))
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


COMPASS = ["K", "KD", "D", "GD", "G", "GB", "B", "KB"]  # Kuzey, Kuzeydoğu, ...


def compass(bearing: float) -> str:
    return COMPASS[int((bearing + 22.5) // 45) % 8]


def describe_point(zones: dict, lat: float, lon: float) -> dict:
    """Nearest named zone + distance/direction from the base for a coordinate."""
    base = zones["base"]
    zd = [(z["name"], haversine_m(lat, lon, *z["center"])) for z in zones["zones"]]
    name, dist = min(zd, key=lambda t: t[1])
    brg = bearing_deg(base["lat"], base["lon"], lat, lon)
    return {"zone": name, "zone_dist_m": round(dist), "base_dist_m": round(haversine_m(lat, lon, base["lat"], base["lon"])),
            "bearing_from_base_deg": round(brg), "direction_from_base": compass(brg)}
