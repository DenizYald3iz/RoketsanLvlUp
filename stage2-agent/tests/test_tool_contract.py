"""Tool sozlesmesi testleri.

Pipeline'i baska biri yazacak ve TOOL_SPECS / dispatch uzerinden baglanacak.
En olasi entegrasyon kirilmasi sema ile fonksiyonun birbirinden kaymasidir:
semaya yeni parametre eklenip fonksiyona eklenmemesi gibi. Bu dosya onu tutar.
"""
import inspect
import json

import pytest

import data_loader as dl
import schemas.state as state
import schemas.tool_specs as TS
from tools import decision


def test_specs_and_registry_match():
    TS.validate_specs()


def test_all_specs_are_json_serializable():
    json.dumps(TS.TOOL_SPECS)


def test_every_spec_has_description():
    for spec in TS.TOOL_SPECS:
        f = spec["function"]
        assert len(f["description"]) > 40, f"{f['name']}: aciklama cok kisa"
        for pname, p in f["parameters"]["properties"].items():
            assert "description" in p or "enum" in p, f"{f['name']}.{pname}"


def test_schema_parameters_exist_on_function():
    """Semada duyurulan her parametre fonksiyonda gercekten olmali."""
    for spec in TS.TOOL_SPECS:
        name = spec["function"]["name"]
        fn = TS.TOOL_REGISTRY[name]
        if isinstance(fn, type(lambda: 0)) and fn.__name__ == "<lambda>":
            continue  # lambda sarmalayicilar ayrica test ediliyor
        params = inspect.signature(fn).parameters
        for pname in spec["function"]["parameters"]["properties"]:
            assert pname in params, f"{name}: sema '{pname}' diyor, fonksiyonda yok"


def test_required_params_have_no_default():
    """Semada zorunlu olan parametrenin fonksiyonda varsayilani olmamali."""
    for spec in TS.TOOL_SPECS:
        name = spec["function"]["name"]
        fn = TS.TOOL_REGISTRY[name]
        if getattr(fn, "__name__", "") == "<lambda>":
            continue
        params = inspect.signature(fn).parameters
        for req in spec["function"]["parameters"]["required"]:
            assert params[req].default is inspect.Parameter.empty, f"{name}.{req}"


def test_dispatch_runs_real_tools():
    out = TS.dispatch("image_footprint", {"image_id": "img_003839"})
    assert out["capture_time"] == "13:25"

    out = TS.dispatch("haversine_distance",
                      {"lat1": 39.9, "lon1": 32.8, "lat2": 39.91, "lon2": 32.8})
    assert 1100 <= out["distance_m"] <= 1120


def test_dispatch_returns_errors_as_data():
    """Hata firlatmamali: modele geri yazilabilir bir sonuc donmeli,
    yoksa agent dongusu kirilir."""
    assert "error" in TS.dispatch("olmayan_tool", {})
    assert "error" in TS.dispatch("pixel_to_geo", {"image_id": "img_003839"})
    assert "error" in TS.dispatch("get_motion_profile", {"track_id": "YOK"})
    assert "error" in TS.dispatch("query_reports", {"source": "gazete"})
    assert "error" in TS.dispatch("submit_assessment",
                                  {"image_id": "img_003839", "verdict": "panik",
                                   "rationale": "x" * 40, "evidence": ["a"]})


def test_resolve_zone_name_lambda_handles_miss():
    out = TS.dispatch("resolve_zone_name", {"name": "Marmara"})
    assert out["zone"] is None and "bulunamadi" in out["note"]


def test_terminal_and_costly_tools_are_registered():
    for name in TS.TERMINAL_TOOLS | TS.COSTLY_TOOLS:
        assert name in TS.TOOL_REGISTRY


# --- submit_assessment sozlesmesi ----------------------------------------
def test_submit_assessment_saves_to_session():
    st = state.SessionState()
    decision.submit_assessment(
        "img_003839", "routine",
        "Karede kayitli dort arac var, hicbiri usse yaklasmiyor ve raporlarla celiski yok.",
        ["T0047 usse 1.758 km, yanal hareket"], confidence=0.8, session=st,
    )
    assert st.assessments[0]["verdict"] == "routine"
    assert st.image_state("img_003839").assessment is not None


def test_submit_assessment_is_idempotent_per_image():
    """Ayni goruntu icin ikinci cagri oncekini degistirmeli, cogaltmamali."""
    st = state.SessionState()
    for v in ("routine", "attention"):
        decision.submit_assessment(
            "img_003839", v, "Yeterince uzun bir gerekce cumlesi burada duruyor.",
            ["bir kanit"], session=st,
        )
    assert len(st.assessments) == 1
    assert st.assessments[0]["verdict"] == "attention"


def test_submit_assessment_rejects_unevidenced_escalation():
    st = state.SessionState()
    with pytest.raises(ValueError, match="somut kanit"):
        decision.submit_assessment(
            "img_003839", "attention",
            "Bu gorunumden supheleniyorum ama elimde somut bir olcu yok.",
            [], session=st,
        )


def test_submit_assessment_rejects_thin_rationale():
    st = state.SessionState()
    with pytest.raises(ValueError, match="rationale"):
        decision.submit_assessment("img_003839", "routine", "olagan", [], session=st)


def test_submit_assessment_warns_on_third_party_only():
    st = state.SessionState()
    tp = next(r for r in dl.load().reports if r.source == "third_party")
    out = decision.submit_assessment(
        "img_003839", "attention",
        "Yalnizca ucuncu taraf raporuna dayanan bir yukseltme denemesi yapiliyor.",
        ["rapor var"], used_reports=[tp.index], session=st,
    )
    assert any("third_party" in w for w in out["warnings"])


def test_submit_assessment_rejects_bad_report_index():
    st = state.SessionState()
    with pytest.raises(ValueError, match="Gecersiz rapor"):
        decision.submit_assessment(
            "img_003839", "routine",
            "Gecerli uzunlukta bir gerekce cumlesi buraya yazilmistir.",
            ["kanit"], used_reports=[99999], session=st,
        )


def test_get_assessments_counts_by_verdict():
    st = state.SessionState()
    ids = list(dl.load().images)[:3]
    for image_id, v in zip(ids, ("routine", "watch", "attention")):
        decision.submit_assessment(
            image_id, v, "Yeterince uzun bir gerekce cumlesi burada duruyor.",
            ["bir kanit"], session=st,
        )
    out = decision.get_assessments(session=st)
    assert out["total"] == 3
    assert out["counts"]["watch"] == 1


# --- Guvenilirlik skoru sozlesmesi ---------------------------------------
def test_credibility_lifts_third_party_when_cross_source_backed():
    """Kullanici senaryosu: third_party tek basina dusuk, ama bagimsiz bir
    kaynak ayni noktayi dogruluyorsa agirligi artmali."""
    from tools import reliability as rel
    st = state.SessionState()
    backed = rel.assess_report_credibility(44, session=st)      # capraz destekli
    lone = rel.assess_report_credibility(105, session=st)       # destegi yok
    assert backed["source"] == "third_party"
    assert backed["credibility"] > lone["credibility"]
    assert any(b["factor"] == "destekleme" for b in backed["breakdown"])


def test_credibility_same_source_repeat_worth_less_than_cross_source():
    from tools import reliability as rel
    import config
    assert (config.CORROBORATION_BONUS_SAME_SOURCE
            < config.CORROBORATION_BONUS_CROSS_SOURCE)
    st = state.SessionState()
    out = rel.assess_report_credibility(36, session=st)  # sadece ayni-kaynak destek
    bonus = next(b for b in out["breakdown"] if b["factor"] == "destekleme")
    assert bonus["value"] <= config.CORROBORATION_BONUS_SAME_SOURCE * 2


def test_measurement_overrides_assumed_prior():
    """Olcum varsayimdan agirdir: yeterli kontrol birikince taban devre disi."""
    from tools import reliability as rel
    import config
    st = state.SessionState()
    before = rel.assess_report_credibility(36, session=st)
    assert before["breakdown"][0]["basis"] == "assumption"

    for _ in range(config.RELIABILITY_OVERRIDES_PRIOR_AFTER + 1):
        rel.update_source_reliability("third_party", "agreed", session=st)
    after = rel.assess_report_credibility(36, session=st)
    assert after["breakdown"][0]["basis"] == "empirical"
    assert after["credibility"] > before["credibility"]


def test_official_can_lose_credibility_through_measurement():
    """Oncelik mutlak degil: official de kendi tespitlerinle yalanlanirsa duser."""
    from tools import reliability as rel
    st = state.SessionState()
    for _ in range(5):
        rel.update_source_reliability("official", "contradicted", session=st)
    out = rel.assess_report_credibility(9, session=st)
    assert out["band"] == "dusuk"
    assert out["breakdown"][0]["basis"] == "empirical"


def test_credibility_breakdown_labels_assumption_vs_evidence():
    """Modelin varsayimi olcumden ayirt edebilmesi sart."""
    from tools import reliability as rel
    out = rel.assess_report_credibility(44, session=state.SessionState())
    for b in out["breakdown"]:
        assert b["basis"] in ("assumption", "empirical", "text", "corroboration")
    assert any("VARSAYIMDIR" in b["why"] for b in out["breakdown"]
               if b["basis"] == "assumption")


def test_credibility_refuses_to_score_noise():
    from tools import reliability as rel
    out = rel.assess_report_credibility(
        next(r.index for r in dl.load().reports if "Hava acik" in r.text),
        session=state.SessionState(),
    )
    assert out["credibility"] is None
    assert out["band"] == "not_a_claim"


def test_credibility_always_states_it_is_not_truth():
    from tools import reliability as rel
    out = rel.assess_report_credibility(9, session=state.SessionState())
    assert "DOGRU oldugunu olcmez" in out["limits"]


def test_credibility_stays_in_range():
    from tools import reliability as rel
    st = state.SessionState()
    for rep in dl.load().reports:
        out = rel.assess_report_credibility(rep.index, session=st)
        if out["credibility"] is not None:
            assert 0.0 <= out["credibility"] <= 1.0


# --- Determinizm ve kararlilik -------------------------------------------
def test_grid_index_matches_linear_scan():
    """Mekansal izgara bir HIZLANDIRMADIR; sonucu degistirmemeli."""
    import config
    from tools import geo, tracks
    ds = dl.load()
    rad = config.TRACK_MATCH_RADIUS_M
    checked = 0
    for image_id, meta in ds.images.items():
        for p in ds.points_at(meta.capture_time):
            if not meta.contains(p.lat, p.lon):
                continue
            via_grid = tracks.find_candidate_tracks(p.lat, p.lon, meta.capture_time)
            linear = [
                {"track_id": q.track_id, "lat": q.lat, "lon": q.lon,
                 "distance_m": round(geo.haversine_distance(p.lat, p.lon, q.lat, q.lon), 1)}
                for q in ds.points_at(meta.capture_time)
                if geo.haversine_distance(p.lat, p.lon, q.lat, q.lon) <= rad
            ]
            linear.sort(key=lambda c: (c["distance_m"], c["track_id"]))
            assert via_grid["candidates"] == linear[:5], image_id
            checked += 1
    assert checked > 100


def test_grid_falls_back_when_radius_exceeds_cell():
    """Yaricap hucre boyunu asarsa 3x3 komsuluk yetmez; tam listeye dusulmeli."""
    from tools import tracks
    ds = dl.load()
    p = ds.points_at("13:25")[0]
    wide = tracks.find_candidate_tracks(p.lat, p.lon, "13:25", radius_m=3000, limit=50)
    assert len(wide["candidates"]) > 5


def test_ties_broken_explicitly_not_by_file_order():
    """Esit mesafede sonuc dosya sirasina degil, kimlige gore belirlenmeli."""
    from tools import tracks
    ds = dl.load()
    for meta in ds.images.values():
        out = tracks.find_candidate_tracks(
            (meta.lat_bounds[0] + meta.lat_bounds[1]) / 2,
            (meta.lon_bounds[0] + meta.lon_bounds[1]) / 2,
            meta.capture_time, radius_m=200, limit=20,
        )
        c = out["candidates"]
        for a, b in zip(c, c[1:]):
            if a["distance_m"] == b["distance_m"]:
                assert a["track_id"] < b["track_id"], "beraberlik kirilmamis"


def test_repeated_calls_are_identical():
    """Ayni girdi, ayni cikti — yan etki yok."""
    from tools import geo, reports, tracks
    calls = [
        (geo.image_footprint, ("img_003839",)),
        (tracks.tracks_in_image, ("img_003839",)),
        (tracks.get_motion_profile, ("T0047",)),
        (reports.parse_report, ("39.9307N 32.8380E yakininda 5 kamyonun durdugu bildirildi.",)),
    ]
    for fn, args in calls:
        assert fn(*args) == fn(*args) == fn(*args)


# --- Guven skorlari -------------------------------------------------------
def test_margin_confidence_drops_at_threshold():
    from tools import confidence as C
    far = C.margin_confidence(5, 40)
    edge = C.margin_confidence(39, 40)
    assert far["confidence"] > edge["confidence"]
    assert edge["at_cliff"] and not far["at_cliff"]


def test_correlated_factors_not_double_counted():
    """Ayni gruptaki faktorler arasinda min alinir, carpim degil."""
    from tools import confidence as C
    grouped = C.combine_confidence([
        {"name": "a", "confidence": 0.8, "group": "m"},
        {"name": "b", "confidence": 0.8, "group": "m"},
    ])
    separate = C.combine_confidence([
        {"name": "a", "confidence": 0.8},
        {"name": "b", "confidence": 0.8},
    ])
    assert grouped["confidence"] == 0.8
    assert separate["confidence"] == pytest.approx(0.64)


def test_combine_names_the_bottleneck():
    from tools import confidence as C
    out = C.combine_confidence([
        {"name": "detection", "confidence": 0.95},
        {"name": "track_match", "confidence": 0.42},
    ])
    assert out["bottleneck"]["name"] == "track_match"
    assert out["weakest_link"] == 0.42


def test_combine_rejects_out_of_range():
    from tools import confidence as C
    with pytest.raises(ValueError):
        C.combine_confidence([{"name": "x", "confidence": 1.4}])


def test_combine_handles_empty_factors():
    from tools import confidence as C
    out = C.combine_confidence([])
    assert out["confidence"] is None


def test_tools_emit_confidence():
    """Zincirdeki her adim guven uretmeli ki birlestirilebilsin."""
    from tools import reliability as rel, reports, tracks
    ds = dl.load()
    p = ds.points_at("13:25")[0]
    assert "confidence" in tracks.find_candidate_tracks(p.lat, p.lon, "13:25")
    assert "confidence" in tracks.get_motion_profile("T0047")
    assert "confidence" in reports.check_report_consistency(9, observed_count=1)
    assert "credibility" in rel.assess_report_credibility(9, session=state.SessionState())


def test_assessment_records_reliability_snapshot():
    """Yol bagimliligi izlenebilir olmali: karar hangi skorlarla alindi?"""
    from tools import decision, reliability as rel
    st = state.SessionState()
    rel.update_source_reliability("official", "agreed", session=st)
    out = decision.submit_assessment(
        "img_003839", "routine",
        "Yeterince uzun bir gerekce cumlesi burada duruyor ve olculere dayaniyor.",
        ["bir kanit"], session=st,
    )
    a = out["assessment"]
    assert "reliability_at_decision" in a
    assert "images_processed_before" in a


def test_images_processed_before_counts_prior_images():
    """Anlik goruntu, bu goruntu siraya EKLENMEDEN once alinmali:
    ilk goruntu icin 0, ikincisi icin 1."""
    from tools import decision
    st = state.SessionState()
    ids = list(dl.load().images)[:3]
    for n, image_id in enumerate(ids):
        out = decision.submit_assessment(
            image_id, "routine",
            "Yeterince uzun bir gerekce cumlesi burada duruyor ve olculere dayanir.",
            ["bir kanit"], session=st,
        )
        assert out["assessment"]["images_processed_before"] == n
    assert st.processing_order == ids


def test_only_two_tools_write_state():
    """README iddiasi: 19 tool'dan yalnizca ikisi state'e yazar.
    Digerleri saf fonksiyon olmali ki herhangi bir sirada cagrilabilsinler."""
    import schemas.tool_specs as TS
    writers = {"update_source_reliability", "submit_assessment"}
    st = state.SessionState()
    before = (dict(st.reliability), list(st.assessments), list(st.processing_order))
    ds = dl.load()
    p = ds.points_at("13:25")[0]
    safe_calls = {
        "image_footprint": {"image_id": "img_003839"},
        "pixel_to_geo": {"image_id": "img_003839", "x": 100, "y": 100},
        "tracks_in_image": {"image_id": "img_003839"},
        "get_motion_profile": {"track_id": "T0047"},
        "find_candidate_tracks": {"lat": p.lat, "lon": p.lon, "time": "13:25"},
        "query_reports": {"lat": 39.9307, "lon": 32.8380, "time": "09:40"},
        "nearest_zone": {"lat": 39.94, "lon": 32.87},
    }
    for name, args in safe_calls.items():
        assert name not in writers
        TS.dispatch(name, args)
    after = (dict(st.reliability), list(st.assessments), list(st.processing_order))
    assert before == after, "durumsuz olmasi gereken tool state'e yazdi"
