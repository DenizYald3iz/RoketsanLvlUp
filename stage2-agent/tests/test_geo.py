"""Koordinat donusumu testleri.

Kritik test: gorev tanimindaki img_000123 ornegi. Orada zemin gercegi var —
verilen kose koordinatlari ve piksel icin beklenen enlem/boylam yaziyor.
Donusum formulu yanlissa her sey yanlis olur, bu yuzden once bu tutmali.
"""
import math

import pytest

import data_loader as dl
import tools.geo as geo


# --- Gorev tanimindaki ornek (PDF s.2, "Uctan uca ornek") ----------------
PDF_META = {
    "width_px": 1360,
    "height_px": 765,
    "capture_time": "13:25",
    "corner_coordinates": {
        "top_left": [39.94510, 32.86200],
        "top_right": [39.94510, 32.86519],
        "bottom_left": [39.94373, 32.86200],
        "bottom_right": [39.94373, 32.86519],
    },
}


@pytest.fixture
def pdf_image(monkeypatch):
    """PDF ornegindeki img_000123'u veri setine gecici olarak ekler."""
    ds = dl.load()
    meta = dl.ImageMeta(
        image_id="img_000123",
        width_px=PDF_META["width_px"],
        height_px=PDF_META["height_px"],
        capture_time=PDF_META["capture_time"],
        top_left=tuple(PDF_META["corner_coordinates"]["top_left"]),
        top_right=tuple(PDF_META["corner_coordinates"]["top_right"]),
        bottom_left=tuple(PDF_META["corner_coordinates"]["bottom_left"]),
        bottom_right=tuple(PDF_META["corner_coordinates"]["bottom_right"]),
    )
    ds.images["img_000123"] = meta
    yield meta
    del ds.images["img_000123"]


def test_pdf_example_pixel_to_geo(pdf_image):
    """Kutu (610, 380, 60, 28) -> merkez (640, 394) -> 39.94439 N, 32.86350 E."""
    cx, cy = geo.bbox_center([610, 380, 60, 28])
    assert (cx, cy) == (640, 394)

    out = geo.pixel_to_geo("img_000123", cx, cy)
    assert out["lon"] == pytest.approx(32.86350, abs=1e-5)
    assert out["lat"] == pytest.approx(39.94439, abs=1e-5)
    assert out["in_bounds"] is True


def test_pdf_example_detection_to_geo(pdf_image):
    """detection_to_geo tek adimda ayni sonucu vermeli."""
    out = geo.detection_to_geo("img_000123", [610, 380, 60, 28])
    assert out["lat"] == pytest.approx(39.94439, abs=1e-5)
    assert out["lon"] == pytest.approx(32.86350, abs=1e-5)
    assert out["center_px"] == [640.0, 394.0]


def test_pdf_example_track_distance(pdf_image):
    """PDF: tespit (39.94439, 32.86350) ile T0187 (39.94441, 32.86353)
    arasi yaklasik 2.5 m. Haversine bunu dogrulamali."""
    d = geo.haversine_distance(39.94439, 32.86350, 39.94441, 32.86353)
    assert 1.5 <= d <= 4.0, f"beklenen ~2.5 m, gelen {d:.2f} m"


# --- Kose davranisi -------------------------------------------------------
def test_corners_map_to_corner_coordinates(pdf_image):
    """(0,0) sol ust koseye, (W,H) sag alt koseye dusmeli."""
    tl = geo.pixel_to_geo("img_000123", 0, 0)
    assert tl["lat"] == pytest.approx(39.94510, abs=1e-6)
    assert tl["lon"] == pytest.approx(32.86200, abs=1e-6)

    br = geo.pixel_to_geo("img_000123", PDF_META["width_px"], PDF_META["height_px"])
    assert br["lat"] == pytest.approx(39.94373, abs=1e-6)
    assert br["lon"] == pytest.approx(32.86519, abs=1e-6)


def test_y_increases_southward(pdf_image):
    """Ust kenar kuzey: y buyudukce enlem KUCULMELI."""
    top = geo.pixel_to_geo("img_000123", 100, 10)
    bottom = geo.pixel_to_geo("img_000123", 100, 700)
    assert bottom["lat"] < top["lat"]


def test_x_increases_eastward(pdf_image):
    """Sol kenar bati: x buyudukce boylam BUYUMELI."""
    left = geo.pixel_to_geo("img_000123", 10, 100)
    right = geo.pixel_to_geo("img_000123", 1300, 100)
    assert right["lon"] > left["lon"]


def test_out_of_bounds_flagged(pdf_image):
    assert geo.pixel_to_geo("img_000123", -5, 100)["in_bounds"] is False
    assert geo.pixel_to_geo("img_000123", 100, 9999)["in_bounds"] is False


# --- Gercek veri uzerinde gidis-donus ------------------------------------
def test_geo_to_pixel_roundtrip():
    """pixel -> geo -> pixel ayni yere donmeli (yuvarlama payiyla)."""
    for image_id in list(dl.load().images)[:5]:
        for x, y in [(0, 0), (37, 91), (400, 250)]:
            g = geo.pixel_to_geo(image_id, x, y)
            back = geo.geo_to_pixel(image_id, g["lat"], g["lon"])
            assert abs(back["x"] - x) < 1.0
            assert abs(back["y"] - y) < 1.0


def test_every_track_inside_footprint_roundtrips():
    """Her goruntude, kare icindeki gercek arac kayitlari piksele cevrilince
    goruntu sinirlari icinde kalmali. Donusumun isaret hatasi olsa bu patlar."""
    ds = dl.load()
    checked = 0
    for image_id, meta in ds.images.items():
        for p in ds.points_at(meta.capture_time):
            if not meta.contains(p.lat, p.lon):
                continue
            px = geo.geo_to_pixel(image_id, p.lat, p.lon)
            assert px["in_bounds"], f"{image_id}/{p.track_id} kare disina dustu"
            assert 0 <= px["x"] <= meta.width_px
            assert 0 <= px["y"] <= meta.height_px
            checked += 1
    assert checked > 100, f"yeterince nokta denenmedi ({checked})"


# --- Mesafe ---------------------------------------------------------------
def test_haversine_zero_and_symmetry():
    assert geo.haversine_distance(39.9, 32.8, 39.9, 32.8) == pytest.approx(0, abs=1e-6)
    a = geo.haversine_distance(39.90, 32.80, 39.95, 32.88)
    b = geo.haversine_distance(39.95, 32.88, 39.90, 32.80)
    assert a == pytest.approx(b, abs=1e-6)


def test_haversine_known_scale():
    """39.9 enleminde 0.01 derece enlem ~1.11 km olmali."""
    d = geo.haversine_distance(39.90, 32.85, 39.91, 32.85)
    assert 1100 <= d <= 1120, f"{d:.1f} m"


def test_bearing_cardinal_directions():
    assert geo.bearing_deg(39.9, 32.8, 40.0, 32.8) == pytest.approx(0, abs=0.5)
    assert geo.bearing_deg(39.9, 32.8, 39.9, 32.9) == pytest.approx(90, abs=0.5)
    assert geo.bearing_deg(39.9, 32.8, 39.8, 32.8) == pytest.approx(180, abs=0.5)
    assert geo.compass_label(0) == "kuzey"
    assert geo.compass_label(90) == "dogu"
    assert geo.compass_label(225) == "guneybati"


# --- Bolgeler -------------------------------------------------------------
def test_nearest_zone_returns_own_center():
    """Bir bolgenin tam merkezinde, en yakin bolge kendisi olmali."""
    for z in dl.load().zones:
        out = geo.nearest_zone(z.lat, z.lon)
        assert out["zone"] == z.name
        assert out["zone_distance_m"] == pytest.approx(0, abs=1.0)


def test_zone_names_all_resolve():
    """zones.json'daki her ad resolve_zone_name ile geri bulunmali."""
    for z in dl.load().zones:
        out = geo.resolve_zone_name(z.name)
        assert out is not None and out["zone"] == z.name


def test_resolve_zone_name_handles_turkish_accents():
    """Raporlar aksansiz geliyor ama aksanli gelirse de tutmali."""
    assert geo.resolve_zone_name("Güneydoğu Yerleşimi")["zone"] == "Guneydogu Yerlesimi"
    assert geo.resolve_zone_name("KUZEY YOLU")["zone"] == "Kuzey Yolu"
    assert geo.resolve_zone_name("kuzeybati yolu")["zone"] == "Kuzeybati Yolu"


def test_resolve_zone_name_unknown_returns_none():
    assert geo.resolve_zone_name("Ankara Kalesi") is None
    assert geo.resolve_zone_name("") is None


def test_find_zone_in_text():
    out = geo.find_zone_in_text("Sabah devriyesi Dogu Yolu bolgesinde bir sey bildirmedi.")
    assert out["zone"] == "Dogu Yolu"
    assert geo.find_zone_in_text("Hava acik, gorus mesafesi iyi.") is None


def test_base_direction_matches_zone_name():
    """Bolge adlari yonlerini soyluyor; ussen gorulen yon bunu dogrulamali."""
    expected = {
        "Kuzey Yolu": "kuzey",
        "Dogu Yolu": "dogu",
        "Guneydogu Yerlesimi": "guneydogu",
        "Bati Yerlesimi": "bati",
        "Kuzeybati Yolu": "kuzeybati",
    }
    for z in dl.load().zones:
        if z.name in expected:
            out = geo.nearest_zone(z.lat, z.lon)
            assert out["direction_from_base"] == expected[z.name], z.name


# --- Footprint ------------------------------------------------------------
def test_image_footprint_consistent():
    ds = dl.load()
    for image_id in list(ds.images)[:5]:
        fp = geo.image_footprint(image_id)
        assert fp["lat_min"] < fp["lat_max"]
        assert fp["lon_min"] < fp["lon_max"]
        assert fp["ground_width_m"] > 0 and fp["ground_height_m"] > 0
        assert fp["meters_per_pixel"] > 0
        assert fp["capture_time"] == ds.image(image_id).capture_time


def test_unknown_image_raises_helpful_error():
    with pytest.raises(KeyError, match="Bilinmeyen image_id"):
        geo.pixel_to_geo("img_yok", 10, 10)
