from langchain_core.messages import AIMessage

from s2agent.data import get_data
from s2agent.graph import build_graph, run_image
from s2agent.registry import REGISTRY, run_tool, tools_for_stage
from s2agent.stages import STAGES
from tests.fake_llm import ScriptedLLM, call

IMG = get_data().image_ids()[0]


def test_every_stage_has_tools():
    for st in STAGES:
        assert tools_for_stage(st.name), st.name


def test_tool_outside_stage_is_rejected():
    r = run_tool("match_tracks", {}, stage="LOCATE", image_id=IMG, evidence={})
    assert "error" in r.result and r.evidence_update == {}


def test_lazy_llm_still_finishes_via_fallback():
    out = run_image(IMG, graph=build_graph(ScriptedLLM(), max_turns=2))
    assert out["done"] and "assessment" in out["evidence"]
    assert any(t["kind"] == "gate" and t["status"] == "nudge" for t in out["trace"])
    assert any(t["kind"] == "tool" and t["source"] == "auto" for t in out["trace"])


def test_tool_calling_llm_passes_gates_without_fallback():
    done = AIMessage("aşama tamam")
    script = [
        call("get_image_info"), call("get_detections", {"min_conf": 0.4}), done,  # LOCATE
        call("match_tracks"), done,                                               # TRACKS
        done,                                                                     # MOTION: no matches → gate passes
        call("find_reports"), done,                                               # REPORTS: stub finds none
        call("submit_assessment", {"alerts": [], "summary": "yok"}), done,        # ASSESS
    ]
    llm = ScriptedLLM(script)
    out = run_image(IMG, graph=build_graph(llm, max_turns=4))
    assert out["done"]
    assert not any(t["kind"] == "tool" and t["source"] == "auto" for t in out["trace"])
    assert all(t["status"] == "pass" for t in out["trace"] if t["kind"] == "gate")
    assert out["evidence"]["detections"]["count"] > 0


def test_keyed_evidence_merges():
    ev = {}
    for tid in ("T1", "T2"):
        ev.update(run_tool("get_track_kinematics", {"track_id": tid}, stage="MOTION", image_id=IMG, evidence=ev).evidence_update)
    assert set(ev["kinematics"]) == {"T1", "T2"}
