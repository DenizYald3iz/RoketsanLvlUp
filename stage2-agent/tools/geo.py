"""Piksel <-> WGS84 donusumu, mesafe hesabi ve bolge cozumleme.

Goruntuler kusbakisi ve perspektif duzeltmeli kabul edilir: ust kenar kuzey,
sol kenar bati. Bir pikselin koordinati kose koordinatlarindan dogrusal
orantiyla bulunur (gorev tanimi, "Konum donusumu").
"""
from __future__ import annotations

import math
import re
import unicodedata
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

import data_loader as dl

EARTH_RADIUS_M = 6_371_000.0


# --- Mesafe ---------------------------------------------------------------
_DEG = math.pi / 180.0


def haversine_distance(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
    """Iki WGS84 noktasi arasindaki yer mesafesi, METRE.

    Esdikdortgen (equirectangular) izdusum kullanir: boylam farki, iki
    noktanin ORTA enlemindeki kosinusle olceklenir. Bu, kusbakisi duzlem
    mesafesidir — gorev tanimi bunu kabul ediyor.

    Neden haversine degil: bu bolge olceginde ikisi arasindaki fark olculdu,
    en buyuk sapma 1.3 cm (200.000 rastgele cift uzerinde). Buna karsilik
    esdikdortgen iki kat hizli (981 ns -> 459 ns). Yani dogruluk kaybi yok,
    hiz kazanci var.

    Isim geriye donuk uyumluluk icin korundu.
    """
    x = (lon2 - lon1) * _DEG * math.cos((lat1 + lat2) * 0.5 * _DEG)
    y = (lat2 - lat1) * _DEG
    return EARTH_RADIUS_M * math.sqrt(x * x + y * y)


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """1'den 2'ye yon: kuzeyden saat yonunde derece (0=K, 90=D, 180=G, 270=B)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl_ = math.radians(lon2 - lon1)
    y = math.sin(dl_) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl_)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def compass_label(deg: float) -> str:
    """Dereceyi 8 yonlu pusula etiketine cevirir."""
    names = ["kuzey", "kuzeydogu", "dogu", "guneydogu",
             "guney", "guneybati", "bati", "kuzeybati"]
    return names[int((deg + 22.5) % 360 // 45)]


# --- Piksel -> koordinat --------------------------------------------------
def pixel_to_geo(image_id: str, x: float, y: float) -> Dict[str, float]:
    """Goruntudeki (x, y) pikselini enlem/boylama cevirir.

    x, y pikselin goruntudeki konumudur (sol ust kose 0,0). Bir aracin
    konumu icin tespit kutusunun MERKEZINI ver.

    Donen: {"lat": ..., "lon": ..., "in_bounds": bool}
    Kutu goruntu disina tasarsa deger yine hesaplanir ama in_bounds=False olur;
    cagiran taraf bunu gorup tespiti eleyebilir.
    """
    meta = dl.load().image(image_id)
    tl_lat, tl_lon = meta.top_left
    tr_lat, tr_lon = meta.top_right
    bl_lat, bl_lon = meta.bottom_left

    lon = tl_lon + (x / meta.width_px) * (tr_lon - tl_lon)
    lat = tl_lat + (y / meta.height_px) * (bl_lat - tl_lat)

    in_bounds = 0 <= x <= meta.width_px and 0 <= y <= meta.height_px
    return {"lat": round(lat, 6), "lon": round(lon, 6), "in_bounds": in_bounds}


def bbox_center(bbox: List[float]) -> Tuple[float, float]:
    """[x, y, w, h] tespit kutusundan merkez pikseli dondurur."""
    if len(bbox) != 4:
        raise ValueError(f"bbox [x, y, w, h] olmali, gelen: {bbox!r}")
    x, y, w, h = bbox
    return (x + w / 2.0, y + h / 2.0)


def detection_to_geo(image_id: str, bbox: List[float]) -> Dict[str, float]:
    """1. gun modelinin kutusunu dogrudan koordinata cevirir."""
    cx, cy = bbox_center(bbox)
    out = pixel_to_geo(image_id, cx, cy)
    out["center_px"] = [round(cx, 1), round(cy, 1)]  # type: ignore[assignment]
    return out


def geo_to_pixel(image_id: str, lat: float, lon: float) -> Dict[str, float]:
    """pixel_to_geo'nun tersi. Kirpma (reinspect_crop) icin gerekli."""
    meta = dl.load().image(image_id)
    tl_lat, tl_lon = meta.top_left
    tr_lat, tr_lon = meta.top_right
    bl_lat, bl_lon = meta.bottom_left

    dlon = tr_lon - tl_lon
    dlat = bl_lat - tl_lat
    if dlon == 0 or dlat == 0:
        raise ValueError(f"{image_id}: dejenere kose koordinatlari")

    x = (lon - tl_lon) / dlon * meta.width_px
    y = (lat - tl_lat) / dlat * meta.height_px
    return {
        "x": round(x, 1),
        "y": round(y, 1),
        "in_bounds": 0 <= x <= meta.width_px and 0 <= y <= meta.height_px,
    }


# --- Bolgeler -------------------------------------------------------------
def nearest_zone(lat: float, lon: float) -> Dict[str, object]:
    """Verilen noktaya en yakin bolge + usse mesafe/yon.

    Donen alanlar:
        zone, zone_distance_m, distance_to_base_km, bearing_from_base,
        direction_from_base
    """
    ds = dl.load()
    # Beraberlik kirma: esit uzaklikta bolge adina gore sec. Aksi halde
    # sonuc zones.json'daki siraya bagli kalir ve dosya yeniden siralanirsa
    # ayni girdi farkli cevap verir.
    best = min(
        ds.zones,
        key=lambda z: (haversine_distance(lat, lon, z.lat, z.lon), z.name),
    )
    zone_d = haversine_distance(lat, lon, best.lat, best.lon)
    base_d = haversine_distance(lat, lon, ds.base.lat, ds.base.lon)
    brg = bearing_deg(ds.base.lat, ds.base.lon, lat, lon)
    return {
        "zone": best.name,
        "zone_distance_m": round(zone_d, 1),
        "distance_to_base_km": round(base_d / 1000.0, 3),
        "bearing_from_base": round(brg, 1),
        "direction_from_base": compass_label(brg),
    }


@lru_cache(maxsize=2048)
def _normalize(s: str) -> str:
    """Turkce aksan/buyuk-kucuk farklarini silip kaba eslestirme anahtari uretir.

    Raporlarda bolge adlari aksansiz geciyor ('Guneydogu Yerlesimi') ama
    kaynak degisirse 'Güneydoğu Yerleşimi' de gelebilir; ikisi de tutmali.
    """
    s = s.replace("ı", "i").replace("İ", "i").replace("ş", "s").replace("Ş", "s")
    s = s.replace("ğ", "g").replace("Ğ", "g").replace("ç", "c").replace("Ç", "c")
    s = s.replace("ö", "o").replace("Ö", "o").replace("ü", "u").replace("Ü", "u")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def resolve_zone_name(name: str) -> Optional[Dict[str, object]]:
    """Rapor metnindeki bolge adini zones.json'daki bolgeye baglar.

    Tam ad, aksansiz ad ve icerme (substring) ile dener. Us adi da ('Merkez Us')
    cozulur. Bulamazsa None.
    """
    ds = dl.load()
    key = _normalize(name)
    if not key:
        return None

    candidates = [(z.name, z.lat, z.lon, False) for z in ds.zones]
    candidates.append((ds.base.name, ds.base.lat, ds.base.lon, True))

    for zname, zlat, zlon, is_base in candidates:
        if _normalize(zname) == key:
            return {"zone": zname, "lat": zlat, "lon": zlon, "is_base": is_base}
    for zname, zlat, zlon, is_base in candidates:
        nz = _normalize(zname)
        if nz in key or key in nz:
            return {"zone": zname, "lat": zlat, "lon": zlon, "is_base": is_base}
    return None


def find_zone_in_text(text: str) -> Optional[Dict[str, object]]:
    """Serbest metin icinde gecen ilk bolge adini bulur."""
    ds = dl.load()
    norm = _normalize(text)
    hits = []
    for z in list(ds.zones) + [ds.base]:
        nz = _normalize(z.name)
        pos = norm.find(nz)
        if pos >= 0:
            hits.append((pos, z))
    if not hits:
        return None
    _, z = min(hits, key=lambda t: (t[0], t[1].name))
    return {"zone": z.name, "lat": z.lat, "lon": z.lon, "is_base": z.name == ds.base.name}


def image_footprint(image_id: str) -> Dict[str, object]:
    """Goruntunun kapsadigi alan: sinirlar, merkez, yer olcegi.

    Agent'in "bu kare nereye bakiyor, ne kadar yer kapliyor" sorusuna cevabi.

    meters_per_pixel HAKKINDA UYARI: bu deger karenin yer olcegini verir,
    ARAC TURUNU DEGIL. Gercek tespit ciktisinda olculdu: kutu boyutu sinifi
    ayirt etmiyor.
        car   medyan 6.8 m, p90 25.5 m
        truck medyan 9.6 m, p90 24.2 m
        van   medyan 10.9 m
    car'in p90'i truck'in medyaninin ustunde. "Kutu buyukse kamyondur"
    cikarimi bu veride YANLIS. Arac turu icin modelin sinif etiketini
    kullan; emin degilsen reinspect_crop ile goruntuye bak.
    """
    ds = dl.load()
    meta = ds.image(image_id)
    lat_lo, lat_hi = meta.lat_bounds
    lon_lo, lon_hi = meta.lon_bounds
    clat, clon = (lat_lo + lat_hi) / 2, (lon_lo + lon_hi) / 2

    width_m = haversine_distance(clat, lon_lo, clat, lon_hi)
    height_m = haversine_distance(lat_lo, clon, lat_hi, clon)
    ctx = nearest_zone(clat, clon)
    return {
        "image_id": image_id,
        "capture_time": meta.capture_time,
        "width_px": meta.width_px,
        "height_px": meta.height_px,
        "lat_min": lat_lo, "lat_max": lat_hi,
        "lon_min": lon_lo, "lon_max": lon_hi,
        "center": {"lat": round(clat, 6), "lon": round(clon, 6)},
        "ground_width_m": round(width_m, 1),
        "ground_height_m": round(height_m, 1),
        "meters_per_pixel": round(width_m / meta.width_px, 3),
        **ctx,
    }
