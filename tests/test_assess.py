from s2agent.graph import build_graph, run_image
from s2agent.registry import run_tool
from s2agent.tools.assess import auto_assessment
from tests.fake_llm import ScriptedLLM

IMG = "img_001147"  # truck T0078/D01 moving towards base; R054 + R021 contradict it, R085 unverifiable


def evidence(image_id: str = IMG) -> dict:
    """Evidence as LOCATE + REPORTS leave it (no LLM)."""
    ev: dict = {}
    for name, args, stage in [("get_image_info", {}, "LOCATE"), ("find_reports", {}, "REPORTS")]:
        ev.update(run_tool(name, args, stage=stage, image_id=image_id, evidence=ev).evidence_update)
    for r in ev["reports"]["reports"]:
        ev.update(run_tool("compare_report", {"report_id": r["report_id"]}, stage="REPORTS", image_id=image_id,
                           evidence=ev).evidence_update)
    return ev


def submit(ev: dict, **args):
    r = run_tool("submit_assessment", args, stage="ASSESS", image_id=IMG, evidence=ev)
    ev.update(r.evidence_update)
    return r.result


TRUCK = {"level": "yuksek", "title": "Üsse yaklaşan kamyon", "subject": "D01/T0078",
         "reason": "Hareketli, üsse yaklaşıyor; R054/R021 çelişiyor",
         "evidence": ["T0078: 3.9 m/s, yaklaşma +1.5 m/s", "R054 contradicts"]}
# what GLM actually sent in the baseline run: an alert about an ignored report + incomplete ignored list
REPORT_ALERT = {"level": "orta", "title": "Çelişkili raporlar", "subject": "R054",
                "reason": "Raporlar güvenilmez", "evidence": ["R054 ve R021 çelişiyor"]}


def test_first_invalid_call_is_rejected_with_problems():
    ev = evidence()
    res = submit(ev, alerts=[TRUCK, REPORT_ALERT], summary="s", ignored_reports=["R021", "R085"])
    assert "error" in res and "assessment" not in ev
    assert any(p.startswith("alert[1]") and "yok sayılan" in p for p in res["problems"])


def test_second_invalid_call_is_accepted_with_warnings():
    ev = evidence()
    submit(ev, alerts=[TRUCK, REPORT_ALERT], summary="s", ignored_reports=["R021", "R085"])
    res = submit(ev, alerts=[REPORT_ALERT, TRUCK], summary="s", ignored_reports=["R021", "R085"])
    assert "error" not in res and ev["assessment"] == res
    assert res["ignored_reports"] == ["R021", "R054", "R085"]  # R054 contradicts → added
    assert [a["level"] for a in res["alerts"]] == ["yuksek", "orta"] and res["alerts"][0]["ids"] == ["D01", "T0078"]
    assert any("R054" in w for w in res["warnings"]) and any("Tekrar" in w for w in res["warnings"])
    assert res["report_verdicts"] == {"R054": "contradicts", "R021": "contradicts", "R085": "unverifiable"}


def test_valid_call_passes_first_time_and_unknown_ids_are_rejected():
    ev = evidence()
    assert "error" in submit(ev, alerts=[{**TRUCK, "subject": "T9999"}], summary="s")
    ev = evidence()
    res = submit(ev, alerts=[TRUCK], summary="s", ignored_reports=["R021", "R054"])
    assert "error" not in res and res["warnings"] == []
    assert "error" in submit(evidence(), alerts=[{**TRUCK, "evidence": []}], summary="s")  # evidence required


def test_auto_assessment_uses_kinematics_and_report_checks():
    ev = evidence()
    ev["kinematics"] = {"T0078": {"closing_speed_mps": 1.5, "eta_min": 8.0, "base_dist_now_m": 3522},
                        "T0029": {"closing_speed_mps": -0.2, "eta_min": None}}
    args = auto_assessment(ev)
    assert [(a["subject"], a["level"]) for a in args["alerts"]] == [("T0078", "yuksek")]
    assert args["ignored_reports"] == ["R021", "R054"]
    assert "error" not in submit(ev, **args)


def test_lazy_llm_gets_rule_based_assessment():
    out = run_image(IMG, graph=build_graph(ScriptedLLM(), max_turns=1))
    a = out["evidence"]["assessment"]
    assert "kural tabanlı" in a["summary"] and set(a["ignored_reports"]) >= {"R021", "R054"}
