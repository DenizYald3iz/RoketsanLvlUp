"""Kurulum kontrolü: python -m scripts.doctor"""
import sys

ok = True


def check(name, fn):
    global ok
    try:
        msg = fn()
        print(f"  ✅ {name}{': ' + msg if msg else ''}")
    except Exception as e:  # noqa: BLE001
        ok = False
        print(f"  ❌ {name}: {type(e).__name__}: {e}")


def py():
    assert sys.version_info >= (3, 10), f"Python 3.10+ gerekli, bu {sys.version.split()[0]}"
    return sys.version.split()[0]


def deps():
    import langchain_openai, langgraph, pandas, PIL, dotenv  # noqa: F401,E401
    return "langgraph, langchain-openai, pandas, pillow, python-dotenv"


def env():
    from s2agent.config import CFG
    assert CFG.api_key.startswith("sk-"), ".env içinde LLM_API_KEY yok"
    assert CFG.base_url, ".env içinde LLM_BASE_URL yok"
    return f"model={CFG.model}, effort={CFG.reasoning_effort}"


def data():
    from s2agent.data import get_data
    d = get_data()
    n = len(d.image_ids())
    d.image_path(d.image_ids()[0])
    return f"{d.dir} — {n} görüntü, {len(d.tracks)} track satırı, {len(d.reports)} rapor"


def detector():
    from s2agent.data import get_data
    from s2agent.detector import get_detector
    det, img = get_detector(), get_data().image_ids()[0]
    return f"{type(det).__name__}, {img}: {len(det.predict(img))} ham kutu"


def tools():
    from s2agent.graph import build_graph  # noqa: F401 — imports every tool module
    from s2agent.registry import REGISTRY
    return f"{len(REGISTRY)} tool: {', '.join(REGISTRY)}"


def gateway():
    from s2agent.budget import key_info
    i = key_info()
    return f"spend={float(i.get('spend') or 0):.4f} / {i.get('max_budget')} USD"


print("Stage-2 agent kurulum kontrolü")
for n, f in [("Python", py), ("Paketler", deps), (".env", env), ("Veri", data), ("Detector", detector), ("Tool registry", tools),
             ("LLM gateway", gateway)]:
    check(n, f)
print("\nHer şey hazır 🎉  →  python -m scripts.run" if ok else "\nYukarıdaki ❌ satırlarını düzelt.")
sys.exit(0 if ok else 1)
