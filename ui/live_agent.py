"""Live GLM agent run for the UI: streams trace events as NDJSON, saves outputs/<id>.json at the end."""
import json
import os
import threading
import time
from pathlib import Path

from s2agent.config import CFG, ROOT

OUT = ROOT / "outputs"
_graph = None
_lock = threading.Semaphore(2)  # gateway allows 4 concurrent requests team-wide; stay polite
# stages that skip the LLM's summary turn once their gate is satisfied (LOCATE kept: it may call view_image)
FAST_STAGES = frozenset(x for x in os.getenv("FAST_STAGES", "TRACKS,MOTION,REPORTS,ASSESS").split(",") if x)


def graph():
    global _graph
    if _graph is None:
        from s2agent.graph import build_graph
        from s2agent.llm import make_llm
        _graph = build_graph(make_llm(), fast_stages=FAST_STAGES)
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


def _parse(text: str) -> dict:
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return {}


def ui_payload(image_id: str, t: dict) -> dict | None:
    """Visual payload for a tool event, so the UI can show what the tool is doing right now."""
    from s2agent.data import get_data

    from . import pipeline

    name, args = t.get("name"), t.get("args") or {}
    d = get_data()
    if name in ("get_image_info", "get_detections"):
        return {"locate": pipeline.analyze(d.image_path(image_id), image_id,
                                           float(args.get("min_conf", pipeline.MIN_CONF)))}
    if name == "view_image":
        keys = ("crop_x", "crop_y", "crop_w", "crop_h")
        return {"view": {"crop": [args[k] for k in keys] if all(args.get(k) is not None for k in keys) else None}}
    if name == "get_track_kinematics":
        tid = args.get("track_id")
        pts = d.tracks[d.tracks.track_id == tid].sort_values("time")
        return {"track": {"track_id": tid, "points": pts[["lon", "lat"]].values.round(6).tolist(),
                          "times": pts.time.tolist(), "kin": _parse(t.get("result"))}}
    if name == "match_tracks":
        return {"matches": _parse(t.get("result")).get("matches", [])}
    if name == "compare_report":
        rid = args.get("report_id")
        rep = next((r for r in d.reports if r["report_id"] == rid), {})
        chk = _parse(t.get("result"))
        return {"report": {"report_id": rid, "text": rep.get("text"), "source": rep.get("source"),
                           "time": rep.get("time"), "verdict": chk.get("verdict"), "related": chk.get("related", [])}}
    return None


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
                        ev = {"type": "trace", "t": round(time.time() - t0, 1), **_compact(t)}
                        if t["kind"] == "tool":
                            try:
                                ev["ui"] = ui_payload(image_id, t)
                            except Exception as e:  # visuals must never break the run
                                ev["ui_error"] = f"{type(e).__name__}: {e}"
                        yield _line(ev)
        except Exception as e:
            yield _line({"type": "error", "msg": f"{type(e).__name__}: {e}"})
            return

    OUT.mkdir(exist_ok=True)
    (OUT / f"{image_id}.json").write_text(json.dumps({k: state.get(k) for k in ("image_id", "evidence", "trace")},
                                                     ensure_ascii=False, indent=1, default=str))
    yield _line({"type": "done", "secs": round(time.time() - t0), "llm_calls": n,
                 "agent": {"image_id": image_id, **trim(state.get("evidence", {}))}})
