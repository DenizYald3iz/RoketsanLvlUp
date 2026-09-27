import pytest

from s2agent.graph import build_graph  # noqa: F401 — registers tools
from s2agent.registry import run_tool
from s2agent.tools.confidence import band, combine, margin_confidence

IMG = "img_003839"  # D04/T0183 approaching (weak detection 0.63), D00/T0182 parked, D06 weak car without track


def evidence(image_id: str = IMG) -> dict:
    ev: dict = {}
    ev.update(run_tool("get_image_info", {}, stage="LOCATE", image_id=image_id, evidence=ev).evidence_update)
    ev.update(run_tool("match_tracks", {}, stage="TRACKS", image_id=image_id, evidence=ev).evidence_update)
    for m in ev["matches"]["matches"]:
        ev.update(run_tool("get_track_kinematics", {"track_id": m["track_id"]}, stage="MOTION",
                           image_id=image_id, evidence=ev).evidence_update)
    return ev


def cc(ev: dict, **args) -> dict:
    r = run_tool("combine_confidence", args, stage="ASSESS", image_id=IMG, evidence=ev)
    ev.update(r.evidence_update)
    return r.result


def test_margin_confidence():
    assert margin_confidence(5, 5, scale=5) == 0.5  # on the threshold
    assert margin_confidence(0, 5, scale=5) == 1.0 and margin_confidence(10, 5, scale=5) == 0.0
    assert margin_confidence(300, 100, scale=200, direction="above") == 1.0


def test_combine_groups_min_then_product():
    r = combine([{"name": "a", "confidence": 0.9, "group": "g"}, {"name": "b", "confidence": 0.6, "group": "g"},
                 {"name": "c", "confidence": 0.5}])
    assert r["groups"] == {"g": 0.6, "c": 0.5} and r["confidence"] == 0.3 and r["weakest_link"] == 0.5
    assert r["bottleneck"]["name"] == "c"
    assert combine([{"name": "c", "confidence": 0.5}], strict=False)["confidence"] == 0.5
    assert combine([])["confidence"] is None
    with pytest.raises(ValueError):
        combine([{"name": "x", "confidence": 1.5}])
    assert [band(x) for x in (0.9, 0.6, 0.2, None)] == ["yuksek", "orta", "dusuk", None]


def test_factors_come_from_evidence():
    ev = evidence()
    r = cc(ev, subject="D04/T0183")
    names = [f["name"] for f in r["factors"]]
    assert names == ["tespit D04", "eşleşme D04↔T0183", "hareket T0183"]
    assert r["bottleneck"]["name"] == "tespit D04" and r["band"] == "orta"
    assert ev["confidence"]["D04/T0183"] == r  # stored for submit_assessment
    assert cc(ev, subject="T0183")["factors"] == r["factors"]  # track id alone finds its detection
    assert [f["name"] for f in cc(ev, subject="D06")["factors"]] == ["tespit D06"]  # no track → detection only


def test_extra_factor_lowers_confidence():
    ev = evidence()
    base = cc(ev, subject="D00/T0182")["confidence"]
    r = cc(ev, subject="D00/T0182", extra_factors=[{"name": "görsel kontrol", "confidence": 0.5, "why": "bulanık"}])
    assert r["confidence"] == round(base * 0.5, 3)


def test_submit_assessment_writes_confidence_and_overrides_guesses():
    ev = evidence()
    ev.update(run_tool("find_reports", {}, stage="REPORTS", image_id=IMG, evidence=ev).evidence_update)
    auto = cc(ev, subject="D04/T0183")["confidence"]
    alert = {"level": "yuksek", "title": "Yaklaşan araç", "subject": "D04/T0183", "reason": "ETA kısa",
             "evidence": ["T0183 yaklaşıyor"], "confidence": 0.99}  # LLM's own guess
    parked = {"level": "dusuk", "title": "Park", "subject": "D00/T0182", "reason": "duruyor",
              "evidence": ["T0182 duruyor"]}  # no confidence given
    res = run_tool("submit_assessment", {"alerts": [alert, parked], "summary": "s"}, stage="ASSESS",
                   image_id=IMG, evidence=ev).result
    a, p = res["alerts"]
    assert a["confidence"] == auto and a["confidence_band"] == "orta"
    assert a["confidence_detail"]["bottleneck"]["name"] == "tespit D04"
    assert p["confidence"] is not None and p["confidence_band"] == "yuksek"
    assert any("0.99" in w and "D04/T0183" in w for w in res["warnings"])
