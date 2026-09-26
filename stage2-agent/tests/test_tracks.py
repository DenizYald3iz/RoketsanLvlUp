"""Hareket kaydi eslestirme ve hareket profili testleri.

Zemin gercegi: her goruntunun cekim aninda karesinin icinde kalan gercek
kayitlar bellidir. Bir kaydin kendi koordinatiyla arandiginda kendisini
bulmasi gerekir — bulamiyorsa eslestirme bozuktur.
"""
import pytest

import config
import data_loader as dl
import tools.geo as geo
import tools.tracks as T


# --- Eslestirme -----------------------------------------------------------
def test_track_finds_itself_at_its_own_coordinate():
    """En temel dogrulama: bir kaydin kendi konumunda kendisi cikmali."""
    ds = dl.load()
    checked = 0
    for image_id, meta in list(ds.images.items())[:10]:
        for p in ds.points_at(meta.capture_time):
            if not meta.contains(p.lat, p.lon):
                continue
            out = T.find_candidate_tracks(p.lat, p.lon, meta.capture_time)
            ids = [c["track_id"] for c in out["candidates"]]
            assert p.track_id in ids, f"{p.track_id} kendi konumunda bulunamadi"
            assert out["candidates"][0]["distance_m"] == pytest.approx(0, abs=0.5)
            checked += 1
    assert checked > 30


def test_every_image_has_matchable_tracks():
    """40 goruntunun her birinde en az bir kayit karenin icinde olmali."""
    ds = dl.load()
    for image_id in ds.images:
        out = T.tracks_in_image(image_id)
        assert out["count"] >= 1, f"{image_id} icin kare icinde kayit yok"


def test_no_match_returns_empty_not_error():
    """Eslesme bulunamamasi HATA DEGIL: arac park halinde olabilir.
    Bos liste + aciklayici not donmeli."""
    out = T.find_candidate_tracks(39.99, 32.99, "13:25", radius_m=10)
    assert out["candidates"] == []
    assert out["best"] is None
    assert "park halinde" in out["note"]


def test_unknown_time_explains_itself():
    """Kayitlar 5 dakikalik adimlarla; ara saatte kayit yok."""
    out = T.find_candidate_tracks(39.92, 32.85, "13:27")
    assert out["candidates"] == []
    assert "5 dakikalik" in out["note"]


def test_radius_limits_matches():
    ds = dl.load()
    p = ds.points_at("13:25")[0]
    wide = T.find_candidate_tracks(p.lat, p.lon, "13:25", radius_m=5000)
    narrow = T.find_candidate_tracks(p.lat, p.lon, "13:25", radius_m=1)
    assert len(wide["candidates"]) >= len(narrow["candidates"])
    assert all(c["distance_m"] <= 5000 for c in wide["candidates"])


def test_candidates_sorted_by_distance():
    ds = dl.load()
    p = ds.points_at("13:25")[0]
    out = T.find_candidate_tracks(p.lat, p.lon, "13:25", radius_m=3000, limit=10)
    dists = [c["distance_m"] for c in out["candidates"]]
    assert dists == sorted(dists)


def test_ambiguity_flag_when_two_tracks_close():
    """Iki arac yakinsa eslesme belirsiz isaretlenmeli; sessizce birini secmemeli."""
    ds = dl.load()
    found = False
    for meta in ds.images.values():
        pts = [p for p in ds.points_at(meta.capture_time) if meta.contains(p.lat, p.lon)]
        for p in pts:
            others = [
                q for q in pts
                if q.track_id != p.track_id
                and geo.haversine_distance(p.lat, p.lon, q.lat, q.lon) < 20
            ]
            if others:
                # Iki aracin tam ortasindan bak: ikisi de benzer uzaklikta olmali.
                q = others[0]
                mid_lat, mid_lon = (p.lat + q.lat) / 2, (p.lon + q.lon) / 2
                out = T.find_candidate_tracks(mid_lat, mid_lon, meta.capture_time)
                if len(out["candidates"]) > 1:
                    assert out["ambiguous"] is True
                    assert "Belirsiz" in out["note"]
                    found = True
                    break
        if found:
            break
    assert found, "belirsiz eslesme senaryosu veride bulunamadi"


# --- Hareket profili ------------------------------------------------------
def test_motion_profile_has_required_fields():
    out = T.get_motion_profile("T0001", at_time="12:15")
    for key in ("speed_mps", "heading_deg", "distance_to_base_km",
                "distance_change_km", "movement", "stopped_minutes", "summary"):
        assert key in out, key
    assert out["movement"] in ("approaching_base", "departing_base", "lateral", "stationary")


def test_motion_profile_ends_at_requested_time():
    """Kayit, ait oldugu goruntunun cekim aninda biter."""
    out = T.get_motion_profile("T0047", at_time="13:25")
    assert out["at_time"] == "13:25"


def test_motion_profile_position_matches_track_record():
    """Profildeki konum, kaydin o saatteki gercek satiri olmali."""
    ds = dl.load()
    for track_id in list(ds.tracks)[:20]:
        pts = ds.track(track_id)
        target = pts[len(pts) // 2]
        out = T.get_motion_profile(track_id, at_time=target.time)
        assert out["position"]["lat"] == pytest.approx(target.lat, abs=1e-9)
        assert out["position"]["lon"] == pytest.approx(target.lon, abs=1e-9)


def test_speed_is_physically_plausible():
    """Kara araci: negatif olamaz, 60 m/s (216 km/s) ustu veride olmamali."""
    ds = dl.load()
    for track_id in list(ds.tracks)[:60]:
        out = T.get_motion_profile(track_id)
        assert out["speed_mps"] >= 0
        assert out["speed_mps"] < 60, f"{track_id}: {out['speed_mps']} m/s"


def test_approaching_base_detected():
    """Usse gore mesafesi belirgin azalan bir kayit 'approaching_base' olmali."""
    ds = dl.load()
    hits = [
        T.get_motion_profile(t)
        for t in list(ds.tracks)
    ]
    approaching = [h for h in hits if h["movement"] == "approaching_base"]
    assert approaching, "veride usse yaklasan hicbir arac bulunamadi"
    for h in approaching:
        assert h["distance_change_km"] < 0, h["track_id"]


def test_departing_base_has_positive_change():
    ds = dl.load()
    departing = [
        h for h in (T.get_motion_profile(t) for t in ds.tracks)
        if h["movement"] == "departing_base"
    ]
    assert departing
    for h in departing:
        assert h["distance_change_km"] > 0, h["track_id"]


def test_stationary_track_reports_stopped_minutes():
    ds = dl.load()
    stationary = [
        h for h in (T.get_motion_profile(t) for t in ds.tracks)
        if h["movement"] == "stationary"
    ]
    if stationary:  # veride park halinde arac olmayabilir
        for h in stationary:
            assert h["speed_mps"] < 0.5
            assert h["heading_deg"] is None


def test_heading_is_none_when_barely_moved():
    """Tek adimlik gurultuden yon uydurmamali."""
    ds = dl.load()
    for track_id in ds.tracks:
        out = T.get_motion_profile(track_id)
        if out["movement"] == "stationary":
            assert out["heading_label"] is None
            return
    pytest.skip("veride duran arac yok")


def test_distance_change_matches_endpoints():
    """distance_change_km, pencere baslangici ve sonunun usse mesafe farki olmali."""
    ds = dl.load()
    base = ds.base
    out = T.get_motion_profile("T0001", at_time="12:15", window_min=30)
    pts = [p for p in ds.track("T0001") if p.minutes <= dl.to_minutes("12:15")]
    window = [p for p in pts if p.minutes >= dl.to_minutes(out["window_start"])]
    d_start = geo.haversine_distance(window[0].lat, window[0].lon, base.lat, base.lon)
    d_end = geo.haversine_distance(window[-1].lat, window[-1].lon, base.lat, base.lon)
    assert out["distance_change_km"] == pytest.approx((d_end - d_start) / 1000, abs=1e-3)


def test_window_shrinks_point_count():
    wide = T.get_motion_profile("T0001", at_time="12:15", window_min=60)
    narrow = T.get_motion_profile("T0001", at_time="12:15", window_min=10)
    assert wide["points_used"] >= narrow["points_used"]


def test_summary_is_human_readable():
    out = T.get_motion_profile("T0047", at_time="13:25")
    s = out["summary"]
    assert "T0047" in s and "13:25" in s
    assert len(s) > 40


# --- Hata yollari ---------------------------------------------------------
def test_unknown_track_raises():
    with pytest.raises(KeyError, match="Bilinmeyen track_id"):
        T.get_motion_profile("T9999")


def test_time_before_track_start_returns_error_not_crash():
    ds = dl.load()
    first = ds.track("T0001")[0]
    earlier = dl.to_hhmm(first.minutes - 60)
    out = T.get_motion_profile("T0001", at_time=earlier)
    assert "error" in out


# --- Gercek tespit verisiyle dogrulama -----------------------------------
def test_detections_recover_ground_truth_tracks():
    """1. gun modelinin GERCEK ciktisi (pred_all_boxes_submission.csv)
    koordinata cevrilip eslestirildiginde, karedeki gercek arac kayitlarinin
    buyuk cogunlugu geri bulunmali. Bu, tum geo zincirinin uctan uca testi."""
    import detections as D
    import tools.geo as geo
    ds = dl.load()
    recovered = total = 0
    for image_id, meta in ds.images.items():
        gt = {p.track_id for p in ds.points_at(meta.capture_time)
              if meta.contains(p.lat, p.lon)}
        found = set()
        for d in D.get_detections(image_id, min_score=0.3):
            o = geo.detection_to_geo(image_id, d.bbox)
            if not o["in_bounds"]:
                continue
            c = T.find_candidate_tracks(o["lat"], o["lon"], meta.capture_time)
            if c["best"]:
                found.add(c["best"]["track_id"])
        recovered += len(gt & found)
        total += len(gt)
    assert total > 150
    assert recovered / total >= 0.85, f"recall {recovered}/{total} cok dusuk"


def test_match_radius_matches_measured_error():
    """Yaricap, olculen tespit hatasina dayanmali. Gercek veride eslesmelerin
    %75'i 2 m icinde; 40 m gibi genis bir esik yanlis araca baglar."""
    import config
    assert config.TRACK_MATCH_RADIUS_M <= 10, (
        "Yaricap olculen tespit hatasindan (2-3 m) cok buyuk; kareler medyan "
        "186 m genisliginde, genis esik farkli araci esler."
    )


def test_ambiguity_has_absolute_threshold_too():
    """Oran testi olcekten bagimsiz: 0.2 m'lik gercek eslesmede 0.3 m'deki
    ikinci aday 'uzak' gorunur. Mutlak esik de olmali."""
    import config
    assert hasattr(config, "TRACK_AMBIGUITY_ABS_M")
    out = T.find_candidate_tracks(39.937208, 32.848238, "13:25", radius_m=25)
    c = out["candidates"]
    if len(c) > 1 and c[1]["distance_m"] <= config.TRACK_AMBIGUITY_ABS_M:
        assert out["ambiguous"]


def test_unmatched_detections_are_plausible_parked_vehicles():
    """Dar yaricapta eslesmeyen tespitler olacak. Bu bir hata degil: park
    halindeki aracin hareket kaydi yok. Ama orani makul kalmali."""
    import detections as D
    import tools.geo as geo
    ds = dl.load()
    matched = unmatched = 0
    for image_id, meta in ds.images.items():
        for d in D.get_detections(image_id, min_score=0.3):
            o = geo.detection_to_geo(image_id, d.bbox)
            if not o["in_bounds"]:
                continue
            c = T.find_candidate_tracks(o["lat"], o["lon"], meta.capture_time)
            if c["candidates"]:
                matched += 1
            else:
                unmatched += 1
    assert matched > unmatched * 2, f"eslesen {matched}, eslesmeyen {unmatched}"
