"""Run the agent.

    python -m scripts.run img_003839            # one image, prints the trace
    python -m scripts.run --all --workers 4      # every image → outputs/<image_id>.json
"""
import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from s2agent.budget import assert_budget
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


def print_trace(state: dict) -> None:
    for t in state["trace"]:
        if t["kind"] == "llm":
            calls = ", ".join(f"{c['name']}({json.dumps(c['args'], ensure_ascii=False)})" for c in t["tool_calls"])
            print(f"[{t['stage']}] LLM: {t['text'][:300]!r} {('→ ' + calls) if calls else ''}")
        elif t["kind"] == "tool":
            print(f"[{t['stage']}]   {t['source']} {t['name']} ⇒ {t['result'][:200]}")
        else:
            print(f"[{t['stage']}] GATE {t['status']} {t.get('missing') or ''}")
    print(json.dumps(state["evidence"].get("assessment"), ensure_ascii=False, indent=1))


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
        state = run_image(a.image_id or get_data().image_ids()[0], graph=graph)
        print_trace(state)
        print("saved", save(state["image_id"], state))


if __name__ == "__main__":
    main()
