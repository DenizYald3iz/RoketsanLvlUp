import httpx

from s2agent import detector
from s2agent.data import get_data
from s2agent.graph import build_graph  # noqa: F401 — registers tools
from s2agent.registry import run_tool

IMG = "img_003839"


def _dets(**args):
    return run_tool("get_detections", args, stage="LOCATE", image_id=IMG, evidence={}).result


def test_csv_detections_have_geo_and_filter():
    r = _dets()
    assert r["count"] > 0 and all(d["conf"] >= 0.3 for d in r["detections"])
    m = get_data().meta[IMG]["corner_coordinates"]
    for d in r["detections"]:
        assert m["bottom_left"][0] <= d["lat"] <= m["top_left"][0]
        assert m["top_left"][1] <= d["lon"] <= m["top_right"][1]
    assert _dets(min_conf=0.9)["count"] <= r["count"]


def test_http_backend_same_output(monkeypatch):
    rows = get_data().boxes.query("image_id == @IMG").drop(columns=["image_id", "cx", "cy"])
    payload = {"boxes": rows.to_dict("records")}  # server omits cx/cy → backend derives them

    def fake_post(url, files, data, timeout):
        assert data["image_id"] == IMG
        return httpx.Response(200, json=payload, request=httpx.Request("POST", url))

    monkeypatch.setenv("DETECTOR", "http")
    monkeypatch.setenv("DETECTOR_URL", "http://gpu.local/predict")
    monkeypatch.setattr(detector.httpx, "post", fake_post)
    detector.get_detector.cache_clear()
    try:
        via_http = _dets()
    finally:
        monkeypatch.delenv("DETECTOR")
        detector.get_detector.cache_clear()
    via_csv = _dets()
    assert via_http["by_label"] == via_csv["by_label"]
    for a, b in zip(via_http["detections"], via_csv["detections"]):
        assert (a["label"], a["conf"], a["probs"]) == (b["label"], b["conf"], b["probs"])
        assert abs(a["cx"] - b["cx"]) <= 0.2 and abs(a["lat"] - b["lat"]) < 1e-5 and abs(a["lon"] - b["lon"]) < 1e-5
