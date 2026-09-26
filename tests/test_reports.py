from s2agent.data import get_data
from s2agent.geo import hhmm_to_min
from s2agent.registry import run_tool
from s2agent.tools.reports import parse_location

D = get_data()


def find(image_id: str, **args) -> dict:
    return run_tool("find_reports", args, stage="REPORTS", image_id=image_id, evidence={}).result


def test_parse_location_variants():
    assert parse_location("39.9374N 32.8483E civarinda 1 kamyon", D.zones) == \
        {"loc_type": "coord", "lat": 39.9374, "lon": 32.8483}
    assert parse_location("39.9374 N, 32.8483 E yakınında", D.zones)["loc_type"] == "coord"
    assert parse_location("Kuzeybatı Yolu bölgesinde trafik normal", D.zones)["zone"] == "Kuzeybati Yolu"
    assert parse_location("Hava acik, gorus mesafesi iyi.", D.zones) is None


def test_example_image_reports():
    res = find("img_003839")  # 13:25, Kuzey Yolu
    ids = [r["report_id"] for r in res["reports"]]
    assert res["image_zone"] == "Kuzey Yolu"
    assert ids[:2] == ["R000", "R088"]  # in-frame coordinate reports first, newest first
    assert all(r["in_frame"] for r in res["reports"][:2])
    assert "R100" not in ids  # coordinate ~1.1 km outside the frame → belongs to another image
    assert all(r["loc_type"] == "zone" for r in res["reports"][2:])
    assert any("R002" in g["report_ids"] for g in res["general"])  # location-less notice kept separately


def test_only_reports_before_capture_within_window():
    for image_id in D.image_ids():
        cap = hhmm_to_min(D.meta[image_id]["capture_time"])
        for r in find(image_id, window_min=60)["reports"]:
            assert 0 <= cap - hhmm_to_min(r["time"]) == r["dt_min"] <= 60


def test_every_coordinate_report_lands_on_exactly_one_image():
    seen: dict[str, int] = {}
    for image_id in D.image_ids():
        for r in find(image_id)["reports"]:
            if r["loc_type"] == "coord":
                seen[r["report_id"]] = seen.get(r["report_id"], 0) + 1
    coord_ids = [r["report_id"] for r in D.reports if parse_location(r["text"], D.zones)
                 and parse_location(r["text"], D.zones)["loc_type"] == "coord"]
    assert set(seen) == set(coord_ids) and set(seen.values()) == {1}
