"""Saha raporu ayristirma, sorgulama ve celiski tespiti testleri.

Raporlarda zemin gercegi yok (hangisi dogru, hangisi yanlis isaretlenmemis).
Bu yuzden testler iki seye bakar:
  1. Ayristirma: metindeki iddia dogru cikariliyor mu (metin zemin gercegidir).
  2. Karsilastirma: uyum ve celiski dogru sinifiyor mu.
"""
import pytest

import data_loader as dl
import schemas.state as state
import tools.reliability as rel
import tools.reports as R


# --- Ayristirma -----------------------------------------------------------
def test_parses_coordinates():
    c = R.parse_report("39.9374N 32.8483E civarinda 1 kamyon goruldu.")
    assert c["lat"] == pytest.approx(39.9374)
    assert c["lon"] == pytest.approx(32.8483)
    assert c["has_position"] is True


def test_parses_high_precision_coordinates():
    c = R.parse_report("39.92087N 32.89536E konumundaki kamyon yerinden ayrilmadi.")
    assert c["lat"] == pytest.approx(39.92087)
    assert c["lon"] == pytest.approx(32.89536)


def test_parses_vehicle_type_and_count():
    c = R.parse_report("39.9209N 32.8953E yakininda 2 kamyonun durdugu bildirildi.")
    assert c["vehicle_type"] == "kamyon"
    assert c["count"] == 2
    assert c["movement"] == "stationary"


def test_word_number_counts():
    c = R.parse_report("39.90653N 32.84972E civarinda bir panelvan hareketsiz duruyor.")
    assert c["vehicle_type"] == "panelvan"
    assert c["count"] == 1


def test_bir_saat_is_not_a_count():
    """'bir saatten uzun suredir' ifadesindeki 'bir' arac sayisi DEGILDIR."""
    c = R.parse_report(
        "39.92087N 32.89536E konumundaki kamyon bir saatten uzun suredir yerinden ayrilmadi."
    )
    assert c["count"] is None
    assert c["movement"] == "stationary"


def test_baseline_count_is_not_observed_count():
    """'olagan trafik 4 arac civaridir' taban cizgisidir, gozlem degil."""
    c = R.parse_report(
        "39.9255N 32.9039E bolgesinde beklenmedik bir yogunluk var; "
        "olagan trafik 4 arac civaridir."
    )
    assert c["count"] is None, "taban cizgisi gozlenen sayi olarak okunmus"
    assert c["baseline_count"] == 4
    assert c["density_anomaly"] is True
    assert c["kind"] == "density_anomaly"


def test_genellikle_baseline_also_excluded():
    c = R.parse_report(
        "39.9094N 32.8281E cevresinde trafik olagandan yogun; "
        "bu bolgede genellikle 4 arac civari gorulur."
    )
    assert c["count"] is None
    assert c["baseline_count"] == 4


def test_heavy_vehicle_with_adjective():
    """'agir bir arac' — sifat araya girse de agir arac olarak okunmali."""
    c = R.parse_report(
        "Bir kaynak, 39.92043N 32.83416E konumunda agir bir aracin "
        "beklemede oldugunu iletti."
    )
    assert c["vehicle_type"] == "agir_arac"
    assert c["movement"] == "stationary"


def test_negated_heavy_claim_is_not_a_sighting():
    """'agir arac hareketi yok' olumsuz iddiadir; arac gorulmus sayilmaz."""
    c = R.parse_report(
        "Kuzeydogu Kavsagi bolgesinde agir arac hareketi yok, "
        "yalnizca binek araclar goruluyor."
    )
    assert c["negated_heavy"] is True
    assert c["vehicle_type"] is None
    assert c["kind"] == "all_clear"


def test_friendly_reports_flagged():
    c = R.parse_report(
        "39.92083N 32.89617E konumundan usse dogru ilerleyen otomobil "
        "planli ikmal aracidir, kimlik teyidi yapilmistir."
    )
    assert c["friendly"] is True
    assert c["movement"] == "approaching_base"
    assert c["kind"] == "friendly_id"


def test_unverified_reports_flagged():
    c = R.parse_report(
        "Dun gece Guneybati Yolu cevresinde arac hareketliligi oldugu "
        "yonunde dogrulanmamis bir ihbar var."
    )
    assert c["unverified"] is True
    assert c["kind"] == "rumor"


def test_zone_name_resolves_to_coordinates():
    c = R.parse_report("Kuzey Yolu bolgesinde trafik akisi normal seyrediyor.")
    assert c["zone"] == "Kuzey Yolu"
    assert c["lat"] is not None


def test_environmental_noise_flagged():
    for text in ["Hava acik, gorus mesafesi iyi.",
                 "Lojistik konvoyu yakit ikmali icin planlanan saatte yola cikacak."]:
        c = R.parse_report(text)
        assert c["noise"] is True
        assert c["kind"] == "environment"


def test_color_and_load_attributes():
    c = R.parse_report("39.9249N 32.8849E yakininda mavi bir kamyon var; transit geciyor.")
    assert c["color"] == "mavi"
    assert c["movement"] == "transit"

    c2 = R.parse_report(
        "39.94643N 32.82861E konumunda yuklu bir kamyonun uzun suredir "
        "park halinde oldugu bildirildi."
    )
    assert c2["loaded"] is True

    c3 = R.parse_report("39.91309N 32.80343E konumunda uzeri ortulu bir agir arac bekliyor.")
    assert c3["covered"] is True


def test_every_real_report_parses_without_error():
    """137 raporun tamami patlamadan ayristirilmali."""
    for rep in dl.load().reports:
        c = R.parse_report(rep.text)
        assert c["kind"] is not None
        if c["count"] is not None:
            assert 0 < int(c["count"]) < 100, rep.text


def test_positional_reports_have_plausible_coordinates():
    """Cikarilan koordinatlar bolge civarinda olmali (yanlis regex yakalamasi olmasin)."""
    for rep in dl.load().reports:
        c = R.parse_report(rep.text)
        if c["lat"] is not None:
            assert 39.5 < c["lat"] < 40.5, rep.text
            assert 32.0 < c["lon"] < 33.5, rep.text


# --- Sorgulama ------------------------------------------------------------
def test_query_filters_by_radius():
    out = R.query_reports(lat=39.9307, lon=32.8380, time="09:40",
                          radius_m=400, include_zone_level=False)
    assert out["counts"]["total"] >= 2
    for r in out["reports"]:
        assert r["distance_m"] <= 400


def test_query_filters_by_time_window():
    near = R.query_reports(lat=39.9307, lon=32.8380, time="09:40", window_min=10,
                           include_zone_level=False)
    wide = R.query_reports(lat=39.9307, lon=32.8380, time="09:40", window_min=180,
                           include_zone_level=False)
    assert wide["counts"]["total"] >= near["counts"]["total"]


def test_query_filters_by_source():
    out = R.query_reports(time="12:00", source="third_party", window_min=300)
    assert out["reports"]
    assert all(r["source"] == "third_party" for r in out["reports"])


def test_query_rejects_bad_source():
    with pytest.raises(ValueError, match="official"):
        R.query_reports(source="gazete")


def test_query_by_zone_name():
    out = R.query_reports(zone="Kuzey Yolu", limit=50)
    assert out["reports"]


def test_query_unknown_zone_raises():
    with pytest.raises(ValueError, match="Bilinmeyen bolge"):
        R.query_reports(zone="Marmara")


def test_query_excludes_environmental_noise():
    out = R.query_reports(time="14:45", window_min=5)
    assert all("Hava acik" not in r["text"] for r in out["reports"])


def test_query_empty_result_has_explanatory_note():
    out = R.query_reports(lat=39.5, lon=32.0, time="13:25", radius_m=50,
                          include_zone_level=False)
    assert out["reports"] == []
    assert "yoklugu" in out["note"]


# --- Rapor vs tespit ------------------------------------------------------
def _index_of(fragment):
    for rep in dl.load().reports:
        if fragment in rep.text:
            return rep.index
    raise AssertionError(f"rapor bulunamadi: {fragment}")


def test_consistency_agrees_when_everything_matches():
    idx = _index_of("39.9209N 32.8953E yakininda 2 kamyonun durdugu")
    out = R.check_report_consistency(
        idx, observed_count=2, observed_movement="stationary",
        observed_lat=39.9209, observed_lon=32.8953, observed_time="08:45",
    )
    assert out["verdict"] == "consistent"
    assert out["conflicts"] == []
    assert len(out["agrees"]) >= 3


def test_consistency_detects_count_mismatch():
    """Sayi uyusmazligi bildirilir ama 'rapor abartiyor' gibi bir hukum
    verilmez — hangisinin dogru oldugu bu veriyle bilinemiyor."""
    idx = _index_of("5 kamyonun durdugu")
    out = R.check_report_consistency(
        idx, observed_count=1, observed_lat=39.9307, observed_lon=32.8380,
    )
    assert out["verdict"] in ("partial", "contradicts")
    assert any("Sayi uyusmuyor" in c for c in out["conflicts"])
    assert not any("abartiyor" in c for c in out["conflicts"])


def test_consistency_has_no_vehicle_type_axis():
    """Arac turu ekseni kaldirildi: gercek veride her kare her turu iceriyor,
    bu yuzden tur karsilastirmasi hicbir sey ayirt etmiyordu."""
    import inspect
    params = inspect.signature(R.check_report_consistency).parameters
    assert "observed_vehicle_type" not in params
    out = R.check_report_consistency(
        _index_of("39.9209N 32.8953E yakininda 2 kamyonun durdugu"),
        observed_count=2, observed_lat=39.9209, observed_lon=32.8953,
    )
    assert not any("Arac turu" in x for x in out["agrees"] + out["conflicts"])


def test_consistency_detects_movement_conflict_when_simultaneous():
    idx = _index_of("39.92087N 32.89536E konumundaki kamyon bir saatten uzun")
    out = R.check_report_consistency(
        idx, observed_movement="approaching_base", observed_time="08:55"
    )
    assert out["conflicts"]
    assert out["minutes_apart"] == 5


def test_consistency_flags_distant_report_as_different_vehicle():
    idx = _index_of("39.9209N 32.8953E yakininda 2 kamyonun durdugu")
    out = R.check_report_consistency(idx, observed_lat=39.99, observed_lon=32.99)
    assert any("baska bir araci" in c for c in out["conflicts"])


def test_third_party_noted_as_caveat_not_as_magic_number():
    """Kaynak farki bir agirlik KATSAYISI degil, acikca yazilmis bir cekincedir.
    Veride hangi raporun dogru oldugu isaretli olmadigi icin katsayi uydurulamaz."""
    tp = next(r for r in dl.load().reports
              if r.source == "third_party" and R.parse_report(r.text)["has_position"])
    out = R.check_report_consistency(tp.index)
    assert "weight" not in out, "uydurma agirlik katsayisi geri gelmis"
    assert any("third_party" in c for c in out["caveats"])


def test_consistency_always_states_its_limits():
    """Tool raporun dogrulugu hakkinda hukum vermedigini her zaman soylemeli."""
    out = R.check_report_consistency(9, observed_count=1)
    assert out["limits"]
    assert any("tespitini esas al" in l for l in out["limits"])


def test_position_tolerance_follows_coordinate_precision():
    """4 ondalikli rapor ~11 m, 5 ondalikli ~1.1 m hassasiyet demek; esik
    uydurma bir sabit degil, raporun kendi hassasiyetinden gelmeli."""
    assert R._coord_precision_m("39.9374N 32.8483E ...") > \
           R._coord_precision_m("39.93740N 32.84903E ...")


def test_movement_conflict_suppressed_when_far_apart_in_time():
    """Duran arac 70 dakika sonra yola cikmis olabilir; bu celiski degildir."""
    near = R.check_report_consistency(52, observed_movement="approaching_base",
                                      observed_time="08:55")
    far = R.check_report_consistency(52, observed_movement="approaching_base",
                                     observed_time="10:00")
    assert near["conflicts"], "es zamanli hareket celiskisi yakalanmali"
    assert not far["conflicts"], "70 dk arayla celiski sayilmamali"
    assert any("hareket etmis olabilir" in c for c in far["caveats"])


def test_count_conflict_carries_its_limit():
    """Sayi farki tek basina raporu yalanlamaz: park halindeki aracin kaydi yok."""
    out = R.check_report_consistency(9, observed_count=1)
    assert any("park halindeki" in l for l in out["limits"])


def test_friendly_report_produces_context_caveat():
    idx = _index_of("planli ikmal aracidir")
    out = R.check_report_consistency(idx)
    assert any("dost" in c for c in out["caveats"])


def test_unrelated_when_nothing_to_compare():
    idx = _index_of("Hava acik")
    out = R.check_report_consistency(idx)
    assert out["verdict"] == "unrelated"


def test_bad_report_index_raises():
    with pytest.raises(ValueError, match="araligin disinda"):
        R.check_report_consistency(99999)


# --- Ayni noktayi anlatan raporlar ---------------------------------------
def test_cross_check_finds_count_conflict_at_same_point():
    """39.9307/32.8380: 20 m, 15 dk arayla '1 kamyon' ve '5 kamyon'."""
    out = R.cross_report_contradiction_check(lat=39.9307, lon=32.8380, time="09:40")
    counts = [c for c in out["conflicts"] if c["field"] == "count"]
    assert counts, "ayni noktadaki sayi uyusmazligi yakalanmadi"
    assert counts[0]["separation_m"] <= 30
    assert counts[0]["minutes_apart"] <= 15


def test_cross_check_radius_is_coordinate_precision_not_a_guess():
    """Varsayilan yaricap koordinat hassasiyeti olcegindedir (30 m).
    250 m gibi genis bir yaricap farkli araclari ayni arac sayardi."""
    import config
    assert config.SAME_POINT_RADIUS_M <= 50
    out = R.cross_report_contradiction_check(lat=39.9307, lon=32.8380, time="09:40")
    assert out["radius_m"] == config.SAME_POINT_RADIUS_M
    for c in out["conflicts"]:
        assert c["separation_m"] <= config.SAME_POINT_RADIUS_M


def test_cross_check_does_not_claim_to_adjudicate():
    """Tool hangi raporun dogru oldugunu SOYLEMEMELI — veride hakem yok."""
    out = R.cross_report_contradiction_check(lat=39.9307, lon=32.8380, time="09:40")
    assert "belirlenemez" in out["conflicts"][0]["why_it_conflicts"] or \
           "soyleyemez" in out["note"]
    for c in out["conflicts"]:
        assert "sisiriyor" not in str(c), "kaynak suclama dili geri gelmis"


def test_cross_check_shows_both_texts_verbatim():
    """Karar modelin; model ham metni gormeli."""
    out = R.cross_report_contradiction_check(lat=39.9307, lon=32.8380, time="09:40")
    c = out["conflicts"][0]
    for key in ("a_text", "b_text", "a_source", "b_source", "a_time", "b_time"):
        assert c[key], key


def test_cross_check_ignores_pairs_too_far_apart_in_time():
    """Arac turu celiskisi ancak kisa zaman farkinda anlamli."""
    import config
    ds = dl.load()
    for rep in ds.reports:
        c = R.parse_report(rep.text)
        if c["lat"] is None or c["zone"]:
            continue
        out = R.cross_report_contradiction_check(
            lat=c["lat"], lon=c["lon"], time=rep.time, window_min=180
        )
        for x in out["conflicts"]:
            if x["field"] in ("count", "vehicle_type"):
                assert x["minutes_apart"] <= config.CLAIM_CONFLICT_MAX_GAP_MIN
            else:
                assert x["minutes_apart"] <= config.MOVEMENT_CONFLICT_MAX_GAP_MIN


def test_cross_check_quiet_when_nothing_nearby():
    out = R.cross_report_contradiction_check(lat=39.5, lon=32.0, time="13:25")
    assert out["conflicts"] == []
    assert out["pairs_examined"] == 0


def test_cross_check_narrower_than_the_250m_version_it_replaced():
    """Gun genelinde: 250 m varsayimi 38 'celiski' uretiyordu, cogu farkli
    araclardi. 30 m ile kalan celiskilerin hepsi ayni noktaya dair."""
    seen, total = set(), 0
    for rep in dl.load().reports:
        c = R.parse_report(rep.text)
        if c["lat"] is None or c["zone"]:
            continue
        out = R.cross_report_contradiction_check(
            lat=c["lat"], lon=c["lon"], time=rep.time, window_min=90
        )
        for x in out["conflicts"]:
            k = tuple(sorted((x["a_index"], x["b_index"]))) + (x["field"],)
            if k not in seen:
                seen.add(k)
                total += 1
    assert 0 < total < 15, f"beklenen 10 civari, gelen {total}"


# --- Guvenilirlik ---------------------------------------------------------
def test_reliability_starts_unknown():
    st = state.SessionState()
    out = rel.get_source_reliability(session=st)
    assert out["sources"]["official"]["label"] == "unknown"
    assert out["sources"]["third_party"]["score"] == 0.5


def test_reliability_drops_after_contradictions():
    st = state.SessionState()
    for _ in range(4):
        rel.update_source_reliability("third_party", "contradicted", session=st)
    out = rel.get_source_reliability("third_party", session=st)["sources"]["third_party"]
    assert out["score"] < 0.3
    assert out["label"] == "unreliable"


def test_reliability_rises_after_agreements():
    st = state.SessionState()
    for _ in range(5):
        rel.update_source_reliability("official", "agreed", session=st)
    out = rel.get_source_reliability("official", session=st)["sources"]["official"]
    assert out["score"] > 0.7
    assert out["label"] == "reliable"


def test_reliability_stays_unknown_below_three_checks():
    """Tek bir dogrulamayla kaynak hakkinda hukum verilmemeli."""
    st = state.SessionState()
    rel.update_source_reliability("official", "agreed", session=st)
    out = rel.get_source_reliability("official", session=st)["sources"]["official"]
    assert out["label"] == "unknown"


def test_unverified_outcome_counts_but_does_not_move_score():
    st = state.SessionState()
    rel.update_source_reliability("official", "unverified", session=st)
    e = rel.get_source_reliability("official", session=st)["sources"]["official"]
    assert e["checks"] == 1
    assert e["agreements"] == 0 and e["contradictions"] == 0


def test_reliability_rejects_bad_arguments():
    st = state.SessionState()
    with pytest.raises(ValueError):
        rel.update_source_reliability("gazete", "agreed", session=st)
    with pytest.raises(ValueError):
        rel.update_source_reliability("official", "belki", session=st)


def test_sessions_are_isolated():
    a, b = state.SessionState(), state.SessionState()
    rel.update_source_reliability("official", "contradicted", session=a)
    assert rel.get_source_reliability("official", session=b)["sources"]["official"]["checks"] == 0
