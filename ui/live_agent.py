"""Live GLM agent run for the UI: streams trace events as NDJSON, saves outputs/<id>.json at the end."""
import json
import threading
import time
from pathlib import Path

from s2agent.config import CFG, ROOT

OUT = ROOT / "outputs"
_graph = None
_lock = threading.Semaphore(2)  # gateway allows 4 concurrent requests team-wide; stay polite


def graph():
    global _graph
    if _graph is None:
        from s2agent.graph import build_graph
        from s2agent.llm import make_llm
        _graph = build_graph(make_llm())
    return _graph


def trim(ev: dict) -> dict:
    return {"assessment": ev.get("assessment"), "matches": ev.get("matches"),
            "kinematics": ev.get("kinematics", {}), "report_checks": ev.get("report_checks", {})}


def load(image_id: str) -> dict | None:
    f = OUT / f"{image_id}.json"
    return {"image_id": image_id, **trim(json.loads(f.read_text()).get("evidence", {}))} if f.exists() else None


def _compact(t: dict) -> dict:
    c = {"kind": t["kind"], "stage": t["stage"]}
    if t["kind"] == "llm":
        c["text"] = (t.get("text") or "")[:220]
        c["calls"] = [{"name": x["name"], "args": json.dumps(x["args"], ensure_ascii=False)[:120]} for x in t.get("tool_calls", [])]
    elif t["kind"] == "tool":
        c.update(source=t.get("source"), name=t.get("name"), result=str(t.get("result", ""))[:220])
    else:
        c.update(status=t.get("status"), missing=t.get("missing"))
    return c


def _line(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str) + "\n"


def stream(image_id: str):
    """Sync generator of NDJSON lines: {type: start|trace|done|error, ...}."""
    from s2agent.budget import assert_budget

    t0 = time.time()
    try:
        spend = assert_budget()
    except BaseException as e:  # assert_budget raises SystemExit when over MAX_SPEND
        yield _line({"type": "error", "msg": f"bütçe kontrolü: {e}"})
        return
    yield _line({"type": "start", "image_id": image_id, "spend": round(spend, 4)})

    with _lock:
        state, n = {}, 0
        init = {"image_id": image_id, "stage_idx": 0, "turns": 0, "evidence": {}, "trace": []}
        try:
            for mode, chunk in graph().stream(init, {"recursion_limit": CFG.recursion_limit},
                                              stream_mode=["updates", "values"]):
                if mode == "values":
                    state = chunk
                    continue
                for upd in chunk.values():
                    for t in (upd or {}).get("trace", []):
                        n += t["kind"] == "llm"
                        yield _line({"type": "trace", "t": round(time.time() - t0, 1), **_compact(t)})
        except Exception as e:
            yield _line({"type": "error", "msg": f"{type(e).__name__}: {e}"})
            return

    OUT.mkdir(exist_ok=True)
    (OUT / f"{image_id}.json").write_text(json.dumps({k: state.get(k) for k in ("image_id", "evidence", "trace")},
                                                     ensure_ascii=False, indent=1, default=str))
    yield _line({"type": "done", "secs": round(time.time() - t0), "llm_calls": n,
                 "agent": {"image_id": image_id, **trim(state.get("evidence", {}))}})
