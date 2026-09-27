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
    def done():
        return AIMessage("aşama tamam")
    ev = run_tool("get_image_info", {}, stage="LOCATE", image_id=IMG, evidence={}).evidence_update
    tids = [m["track_id"] for m in run_tool("match_tracks", {}, stage="TRACKS", image_id=IMG, evidence=ev).result["matches"]]
    assert tids
    kin = AIMessage("", tool_calls=[{"name": "get_track_kinematics", "args": {"track_id": t}, "id": f"k{t}"} for t in tids])
    rids = [r["report_id"] for r in run_tool("find_reports", {}, stage="REPORTS", image_id=IMG, evidence={})
            .result["reports"]]
    compare_all = AIMessage("", tool_calls=[{"name": "compare_report", "args": {"report_id": r}, "id": f"cc{r}"}
                                            for r in rids])
    script = [
        call("get_image_info"), done(),                                           # LOCATE
        call("match_tracks"), done(),                                             # TRACKS
        kin, done(),                                                              # MOTION: every matched track
        call("find_reports"), compare_all, done(),                                # REPORTS: compare every report
        call("submit_assessment", {"alerts": [], "summary": "yok"}), done(),      # ASSESS
    ]
    llm = ScriptedLLM(script)
    out = run_image(IMG, graph=build_graph(llm, max_turns=4))
    assert out["done"]
    assert not any(t["kind"] == "tool" and t["source"] == "auto" for t in out["trace"])
    assert all(t["status"] == "pass" for t in out["trace"] if t["kind"] == "gate")
    assert out["evidence"]["detections"]["count"] > 0


def test_keyed_evidence_merges():
    ev = {}
    for tid in ("T0001", "T0002"):
        ev.update(run_tool("get_track_kinematics", {"track_id": tid}, stage="MOTION", image_id=IMG, evidence=ev).evidence_update)
    assert set(ev["kinematics"]) == {"T0001", "T0002"}


def test_get_image_info_returns_and_stores_detections():
    r = run_tool("get_image_info", {}, stage="LOCATE", image_id=IMG, evidence={})
    assert r.result["vehicles"]["count"] > 0 and "capture_time" in r.result
    assert set(r.evidence_update) == {"image_info", "detections"}
    assert r.evidence_update["detections"] == r.result["vehicles"]
    assert "_evidence" not in r.llm_text


def test_locate_passes_after_single_get_image_info_call():
    out = run_image(IMG, graph=build_graph(ScriptedLLM([call("get_image_info"), AIMessage("tamam")]), max_turns=4))
    locate = [t for t in out["trace"] if t["stage"] == "LOCATE"]
    assert [t["name"] for t in locate if t["kind"] == "tool"] == ["get_image_info"]
    assert locate[-1]["status"] == "pass"


def test_later_stages_see_full_history():
    llm = ScriptedLLM([call("get_image_info"), AIMessage("LOCATE notu: D03 şüpheli")])
    run_image(IMG, graph=build_graph(llm, max_turns=2))
    tracks_call = llm.calls[2]  # first TRACKS turn
    text = " ".join(str(m.content) for m in tracks_call)
    assert "LOCATE notu: D03 şüpheli" in text and '"det_id": "D00"' in text and "Aşama 2/5: TRACKS" in text
