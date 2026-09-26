"""LangGraph wiring:  agent ⇄ tools  →  gate  → (next stage | nudge | fallback)  → … → END

- agent: LLM with ONLY the current stage's tools bound; sees the full history of all stages.
- tools: executes tool calls through the registry; results land in state.evidence.
- gate:  code (not the LLM) checks the stage's requirements. Pass → next stage (same
         conversation); missing → nudge the LLM; out of turns → run the stage's fallback calls.
"""
import operator
import time
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from . import tools as _tools  # noqa: F401 — registers all @stage_tool functions
from .config import CFG
from .data import get_data
from .geo import image_data_url
from .prompts import AUTO_RESULTS, NUDGE, STAGE_START, SYSTEM, UPLOAD
from .registry import run_tool, tools_for_stage
from .stages import STAGES, Stage


def merge_dict(old: dict | None, new: dict | None) -> dict:
    return {**(old or {}), **(new or {})}


class AgentState(TypedDict, total=False):
    image_id: str
    stage_idx: int
    turns: int  # LLM calls in the current stage
    messages: Annotated[list, add_messages]  # one conversation across all stages
    intro_idx: int  # last stage whose start message was sent
    evidence: Annotated[dict, merge_dict]  # everything tools produced, keyed by ToolSpec.writes
    trace: Annotated[list, operator.add]  # full history for the demo / debugging
    done: bool


def _t(kind: str, stage: str, **kw) -> dict:
    return {"ts": round(time.time(), 2), "kind": kind, "stage": stage, **kw}


def build_graph(llm, stages: list[Stage] = STAGES, max_turns: int = CFG.max_turns_per_stage):
    def stage_of(s: AgentState) -> Stage:
        return stages[s.get("stage_idx", 0)]

    # ---------------- nodes ----------------
    def agent(s: AgentState) -> dict:
        st, idx = stage_of(s), s.get("stage_idx", 0)
        new_msgs: list = []
        if s.get("intro_idx", -1) != idx:  # stage start → announce it; history of earlier stages is kept
            if idx == 0:
                new_msgs.append(SystemMessage(SYSTEM.format(n=len(stages), stages=" → ".join(x.name for x in stages))))
            text = STAGE_START.format(idx=idx + 1, n=len(stages), stage=st.name, goal=st.goal)
            if st.attach_image:
                url = image_data_url(get_data().image_path(s["image_id"]), CFG.image_max_side)
                text += "\n\n" + UPLOAD.format(image_id=s["image_id"])
                new_msgs.append(HumanMessage([{"type": "text", "text": text}, {"type": "image_url", "image_url": {"url": url}}]))
            else:
                new_msgs.append(HumanMessage(text))
        specs = tools_for_stage(st.name)
        bound = llm.bind_tools([t.lc_tool for t in specs]) if specs else llm
        ai: AIMessage = bound.invoke(list(s.get("messages", [])) + new_msgs)
        calls = [{"name": c["name"], "args": c["args"]} for c in ai.tool_calls]
        return {"messages": new_msgs + [ai], "turns": s.get("turns", 0) + 1, "intro_idx": idx,
                "trace": [_t("llm", st.name, text=(ai.content or "")[:2000], tool_calls=calls)]}

    def _execute(s: AgentState, calls: list[dict], source: str) -> dict:
        st, ev = stage_of(s), dict(s.get("evidence", {}))
        msgs, trace, upd = [], [], {}
        images = []
        for c in calls:
            r = run_tool(c["name"], c.get("args") or {}, stage=st.name, image_id=s["image_id"], evidence=ev)
            ev.update(r.evidence_update)
            upd.update(r.evidence_update)
            if c.get("id"):
                msgs.append(ToolMessage(r.llm_text, tool_call_id=c["id"], name=r.name))
            if r.image:
                images.append({"type": "image_url", "image_url": {"url": r.image}})
            trace.append(_t("tool", st.name, source=source, name=r.name, args=r.args,
                            result=r.llm_text[:3000]))
        if images:  # OpenAI tool messages can't carry images → follow-up user message
            msgs.append(HumanMessage([{"type": "text", "text": "İstenen görüntü:"}, *images]))
        return {"messages": msgs, "evidence": upd, "trace": trace}

    def tools(s: AgentState) -> dict:
        last = s["messages"][-1]
        return _execute(s, [{"name": c["name"], "args": c["args"], "id": c["id"]} for c in last.tool_calls], "llm")

    def gate(s: AgentState) -> dict:
        st, idx = stage_of(s), s.get("stage_idx", 0)
        ev = s.get("evidence", {})
        missing = st.missing(ev)
        if missing and s.get("turns", 0) < max_turns:
            return {"messages": [HumanMessage(NUDGE.format(stage=st.name, missing=", ".join(missing)))],
                    "trace": [_t("gate", st.name, status="nudge", missing=missing)]}
        upd, trace = {}, []
        for _ in range(3):  # out of turns → run fallback calls (may take rounds, e.g. find → compare)
            calls = st.fallback(ev) if missing else []
            if not calls:
                break
            fb = _execute({**s, "evidence": ev}, [{"name": n, "args": a} for n, a in calls], "auto")
            ev, upd, trace = merge_dict(ev, fb["evidence"]), merge_dict(upd, fb["evidence"]), trace + fb["trace"]
            missing = st.missing(ev)
        auto = [t for t in trace if t["kind"] == "tool"]
        msgs = [HumanMessage(AUTO_RESULTS.format(stage=st.name, results="\n".join(
            f"- {t['name']}({t['args']}) → {t['result']}" for t in auto)))] if auto else []
        status = "pass" if not trace else ("fallback" if not missing else "forced")
        return {"evidence": upd, "stage_idx": idx + 1, "turns": 0, "done": idx + 1 >= len(stages),
                "messages": msgs,
                "trace": trace + [_t("gate", st.name, status=status, missing=missing)]}

    # ---------------- routing ----------------
    def after_agent(s: AgentState) -> str:
        return "tools" if s["messages"][-1].tool_calls else "gate"

    def after_tools(s: AgentState) -> str:
        return "gate" if s.get("turns", 0) >= max_turns else "agent"

    def after_gate(s: AgentState) -> str:
        return END if s.get("done") else "agent"

    g = StateGraph(AgentState)
    g.add_node("agent", agent)
    g.add_node("tools", tools)
    g.add_node("gate", gate)
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", after_agent, ["tools", "gate"])
    g.add_conditional_edges("tools", after_tools, ["agent", "gate"])
    g.add_conditional_edges("gate", after_gate, ["agent", END])
    return g.compile()


def run_image(image_id: str, llm=None, graph=None) -> dict:
    from .llm import make_llm

    graph = graph or build_graph(llm or make_llm())
    return graph.invoke({"image_id": image_id, "stage_idx": 0, "turns": 0, "evidence": {}, "trace": []},
                        {"recursion_limit": CFG.recursion_limit})
