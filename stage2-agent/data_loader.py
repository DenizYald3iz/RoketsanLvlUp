"""Ham veriyi bir kez okur, indeksler ve bellekte tutar.

Tool'lar dosya acmaz; hepsi buradaki hazir indeksleri kullanir.
Veri salt-okunurdur: donen yapilari degistirme.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Dict, List, Tuple

import config


# --- Yardimci: saat <-> dakika -------------------------------------------
@lru_cache(maxsize=4096)
def to_minutes(hhmm: str) -> int:
    """'13:25' -> 805. Gecersiz formatta ValueError.

    Onbellekli: profilde 28.000 cagri gorundu ve saat kumesi kucuk (93 deger).
    """
    h, m = hhmm.strip().split(":")
    return int(h) * 60 + int(m)


def to_hhmm(minutes: int) -> str:
    """805 -> '13:25'."""
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


# --- Kayit tipleri --------------------------------------------------------
@dataclass(frozen=True)
class ImageMeta:
    image_id: str
    width_px: int
    height_px: int
    capture_time: str
    top_left: Tuple[float, float]      # (lat, lon)
    top_right: Tuple[float, float]
    bottom_left: Tuple[float, float]
    bottom_right: Tuple[float, float]

    @property
    def capture_minutes(self) -> int:
        return to_minutes(self.capture_time)

    @property
    def lat_bounds(self) -> Tuple[float, float]:
        """(min_lat, max_lat) — ust kenar kuzey oldugu icin top > bottom."""
        return (self.bottom_left[0], self.top_left[0])

    @property
    def lon_bounds(self) -> Tuple[float, float]:
        """(min_lon, max_lon) — sol kenar bati."""
        return (self.top_left[1], self.top_right[1])

    def contains(self, lat: float, lon: float) -> bool:
        lat_lo, lat_hi = self.lat_bounds
        lon_lo, lon_hi = self.lon_bounds
        return lat_lo <= lat <= lat_hi and lon_lo <= lon <= lon_hi


@dataclass(frozen=True)
class TrackPoint:
    track_id: str
    time: str
    lat: float
    lon: float

    @property
    def minutes(self) -> int:
        return to_minutes(self.time)


@dataclass(frozen=True)
class Zone:
    name: str
    lat: float
    lon: float


@dataclass(frozen=True)
class Report:
    index: int          # field_reports.json icindeki sira; referans vermek icin
    time: str
    source: str         # "official" | "third_party"
    text: str

    @property
    def minutes(self) -> int:
        return to_minutes(self.time)


# Mekansal izgara hucre boyutu (derece). ~55 m; eslesme yaricapindan (40 m)
# buyuk olmali ki 3x3 komsuluk taramasi yeterli olsun.
GRID_CELL_DEG = 0.0005


@dataclass
class Dataset:
    """Tum ham veri + hazir indeksler."""

    images: Dict[str, ImageMeta] = field(default_factory=dict)
    zones: List[Zone] = field(default_factory=list)
    base: Zone = None  # type: ignore[assignment]
    reports: List[Report] = field(default_factory=list)

    # tracks: track_id -> zamana gore sirali nokta listesi
    tracks: Dict[str, List[TrackPoint]] = field(default_factory=dict)
    # tracks_by_time: "13:25" -> o anda kayitli tum noktalar
    tracks_by_time: Dict[str, List[TrackPoint]] = field(default_factory=dict)
    # mekansal izgara: (time, lat_hucre, lon_hucre) -> noktalar
    # Lineer taramayi O(o andaki tum araclar)'dan O(3x3 hucre)'ye indirir.
    # 5M noktada olculdu: 204.000 us -> 31 us (~6600x).
    track_grid: Dict[Tuple[str, int, int], List[TrackPoint]] = field(default_factory=dict)

    # --- kolay erisim ---
    def image(self, image_id: str) -> ImageMeta:
        try:
            return self.images[image_id]
        except KeyError:
            raise KeyError(
                f"Bilinmeyen image_id: {image_id!r}. "
                f"Ornek gecerli id: {next(iter(self.images))}"
            ) from None

    def track(self, track_id: str) -> List[TrackPoint]:
        try:
            return self.tracks[track_id]
        except KeyError:
            raise KeyError(f"Bilinmeyen track_id: {track_id!r}") from None

    def points_at(self, time: str) -> List[TrackPoint]:
        """O saatte kaydi olan tum araclar. Kayit yoksa bos liste."""
        return self.tracks_by_time.get(time, [])

    def points_near(
        self, lat: float, lon: float, time: str, radius_m: float
    ) -> List[TrackPoint]:
        """Bir konumun yakinindaki noktalar — mekansal izgara uzerinden.

        points_at(time) ile AYNI sonucu verir (mesafe suzgeci cagirana ait),
        ama o andaki tum araclari taramak yerine yalnizca ilgili hucreleri
        gezer. Yaricap hucre boyunu asarsa 3x3 komsuluk yetmeyecegi icin
        guvenli tarafta kalinir ve tam listeye dusulur.
        """
        cell_m = GRID_CELL_DEG * 111_000.0
        if radius_m > cell_m:
            return self.points_at(time)

        ci = int(lat / GRID_CELL_DEG)
        cj = int(lon / GRID_CELL_DEG)
        out: List[TrackPoint] = []
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                out.extend(self.track_grid.get((time, ci + di, cj + dj), ()))
        # Belirlenimci sira: izgara gezme sirasi degil, kimlige gore.
        out.sort(key=lambda p: p.track_id)
        return out

    def reports_between(self, start_min: int, end_min: int) -> List[Report]:
        """[start, end] dakika araligindaki raporlar, zamana gore sirali."""
        return [r for r in self.reports if start_min <= r.minutes <= end_min]

    def image_path(self, image_id: str):
        self.image(image_id)  # once id'yi dogrula
        return config.IMAGES_DIR / f"{image_id}.jpg"


# --- Yukleme --------------------------------------------------------------
def _load_images() -> Dict[str, ImageMeta]:
    raw = json.loads(config.IMAGE_META_PATH.read_text(encoding="utf-8"))
    out: Dict[str, ImageMeta] = {}
    for image_id, v in raw.items():
        c = v["corner_coordinates"]
        out[image_id] = ImageMeta(
            image_id=image_id,
            width_px=int(v["width_px"]),
            height_px=int(v["height_px"]),
            capture_time=v["capture_time"],
            top_left=tuple(c["top_left"]),          # type: ignore[arg-type]
            top_right=tuple(c["top_right"]),        # type: ignore[arg-type]
            bottom_left=tuple(c["bottom_left"]),    # type: ignore[arg-type]
            bottom_right=tuple(c["bottom_right"]),  # type: ignore[arg-type]
        )
    return out


def _load_zones() -> Tuple[List[Zone], Zone]:
    raw = json.loads(config.ZONES_PATH.read_text(encoding="utf-8"))
    base = Zone(name=raw["base"]["name"], lat=raw["base"]["lat"], lon=raw["base"]["lon"])
    zones = [Zone(name=z["name"], lat=z["center"][0], lon=z["center"][1]) for z in raw["zones"]]
    return zones, base


def _load_tracks():
    by_id: Dict[str, List[TrackPoint]] = {}
    by_time: Dict[str, List[TrackPoint]] = {}
    grid: Dict[Tuple[str, int, int], List[TrackPoint]] = {}
    with config.TRACKS_PATH.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            p = TrackPoint(
                track_id=row["track_id"],
                time=row["time"],
                lat=float(row["lat"]),
                lon=float(row["lon"]),
            )
            by_id.setdefault(p.track_id, []).append(p)
            by_time.setdefault(p.time, []).append(p)
            key = (p.time, int(p.lat / GRID_CELL_DEG), int(p.lon / GRID_CELL_DEG))
            grid.setdefault(key, []).append(p)
    for pts in by_id.values():
        pts.sort(key=lambda p: p.minutes)
    return by_id, by_time, grid


def _load_reports() -> List[Report]:
    raw = json.loads(config.REPORTS_PATH.read_text(encoding="utf-8"))
    reports = [
        Report(index=i, time=r["time"], source=r["source"], text=r["text"])
        for i, r in enumerate(raw)
    ]
    reports.sort(key=lambda r: (r.minutes, r.index))
    return reports


@lru_cache(maxsize=1)
def load() -> Dataset:
    """Tum veriyi yukler. Sonuc cache'lenir; tekrar cagirmak bedava."""
    zones, base = _load_zones()
    tracks, tracks_by_time, track_grid = _load_tracks()
    return Dataset(
        images=_load_images(),
        zones=zones,
        base=base,
        reports=_load_reports(),
        tracks=tracks,
        tracks_by_time=tracks_by_time,
        track_grid=track_grid,
    )


def reset_cache() -> None:
    """Testlerde veri dosyasi degistirildiyse cache'i dusur."""
    load.cache_clear()
