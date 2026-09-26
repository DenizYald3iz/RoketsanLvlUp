import httpx  # comes with openai; bundles certifi, so SSL works on fresh macOS/Windows Pythons

from .config import CFG


def key_info() -> dict:
    """Gateway spend for our key: {'spend': float, 'max_budget': 15.0, ...}."""
    url = CFG.base_url.rstrip("/").removesuffix("/v1") + "/key/info"
    r = httpx.get(url, headers={"Authorization": f"Bearer {CFG.api_key}"}, timeout=20)
    r.raise_for_status()
    data = r.json()
    return data.get("info", data)


def assert_budget() -> float:
    spend = float(key_info().get("spend") or 0)
    if spend > CFG.max_spend:
        raise SystemExit(f"Spend {spend:.2f} USD > MAX_SPEND {CFG.max_spend}; stopping.")
    return spend
