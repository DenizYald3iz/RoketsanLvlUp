"""Run the agent.

    python -m scripts.run img_003839            # one image, prints the trace
    python -m scripts.run --all --workers 4      # every image → outputs/<image_id>.json
"""
import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from s2agent.budget import assert_budget
from s2agent.config import CFG
from s2agent.data import get_data
from s2agent.graph import build_graph, run_image
from s2agent.llm import make_llm

OUT = Path(__file__).resolve().parent.parent / "outputs"


def save(image_id: str, state: dict) -> Path:
    OUT.mkdir(exist_ok=True)
    p = OUT / f"{image_id}.json"
    p.write_text(json.dumps({k: state.get(k) for k in ("image_id", "evidence", "trace")},
                            ensure_ascii=False, indent=1, default=str))
    return p


def fmt(t: dict, t0: float) -> str:
    head = f"{t['ts'] - t0:6.1f}s [{t['stage']}]"
    if t["kind"] == "llm":
        calls = ", ".join(f"{c['name']}({json.dumps(c['args'], ensure_ascii=False)[:80]})" for c in t["tool_calls"])
        return f"{head} LLM #{t['n']}: {t['text'][:160]!r}" + (f" → {calls}" if calls else "")
    if t["kind"] == "tool":
        return f"{head}   {t['source']} {t['name']} ⇒ {t['result'][:160]}"
    return f"{head} GATE {t['status']} {t.get('missing') or ''}"


def run_live(image_id: str, graph) -> dict:
    """Stream the graph: print every LLM call / tool call / gate decision as it happens."""
    t0, n, state = time.time(), 0, {}
    init = {"image_id": image_id, "stage_idx": 0, "turns": 0, "evidence": {}, "trace": []}
    for mode, chunk in graph.stream(init, {"recursion_limit": CFG.recursion_limit}, stream_mode=["updates", "values"]):
        if mode == "values":
            state = chunk
            continue
        for upd in chunk.values():
            for t in (upd or {}).get("trace", []):
                if t["kind"] == "llm":
                    n += 1
                    t["n"] = n
                print(fmt(t, t0), flush=True)
    a = state["evidence"].get("assessment")
    print(f"\n=== {image_id}: {n} LLM çağrısı, {time.time() - t0:.0f}s ===")
    print(json.dumps(a, ensure_ascii=False, indent=1))
    return state


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("image_id", nargs="?")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--workers", type=int, default=4)  # gateway allows 4 concurrent requests
    a = ap.parse_args()

    print(f"spend so far: {assert_budget():.4f} USD")
    graph = build_graph(make_llm())
    if a.all:
        def one(i):
            try:
                return i, save(i, run_image(i, graph=graph))
            except Exception as e:  # keep the batch going
                return i, f"ERROR {e}"
        with ThreadPoolExecutor(a.workers) as ex:
            for i, res in ex.map(one, get_data().image_ids()):
                print(i, res)
        print(f"spend after: {assert_budget():.4f} USD")
    else:
        state = run_live(a.image_id or get_data().image_ids()[0], graph)
        print("saved", save(state["image_id"], state))
        print(f"spend: {assert_budget():.4f} USD")


if __name__ == "__main__":
    main()
