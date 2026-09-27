from s2agent.data import get_data
from s2agent.geo import hhmm_to_min
from s2agent.registry import run_tool
from s2agent.tools.reports import parse_claim, parse_location

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


def compare(image_id: str, report_id: str, evidence: dict | None = None) -> dict:
    return run_tool("compare_report", {"report_id": report_id}, stage="REPORTS", image_id=image_id,
                    evidence=evidence or {}).result


def test_parse_claim_templates():
    c = parse_claim("39.9307N 32.8380E yakininda 5 kamyonun durdugu bildirildi.")
    assert (c["type"], c["count"], c["motion"]) == ("truck", 5, "stopped")
    c = parse_claim("39.9017N 32.8702E civarinda 3 araclik bir kamyon konvoyu ilerliyor.")
    assert (c["type"], c["count"], c["motion"]) == ("truck", 3, "moving")
    c = parse_claim("39.92083N 32.89617E konumundan usse dogru ilerleyen otomobil planli ikmal aracidir, "
                    "kimlik teyidi yapilmistir.")
    assert (c["type"], c["motion"], c["friendly"]) == ("car", "approaching", True)
    c = parse_claim("39.9094N 32.8281E cevresinde trafik olagandan yogun; bu bolgede genellikle 4 arac civari gorulur.")
    assert (c["min_count"], c["count"]) == (5, None)
    assert parse_claim("39.8732N 32.8522E civarinda 1 agir arac (kamyon/otobus) gozlendi.")["type"] == "heavy"
    assert parse_claim("39.92087N 32.89536E konumundaki kamyon bir saatten uzun suredir yerinden ayrilmadi.")[
        "motion"] == "stopped_long"
    assert parse_claim("Dun gece Dogu Yolu cevresinde arac hareketliligi oldugu yonunde dogrulanmamis bir ihbar var.")[
        "scope"] == "stale"


def test_compare_verdicts():
    assert compare("img_000267", "R052")["verdict"] == "consistent"  # parked truck, T0045 speed 0
    assert compare("img_000267", "R082")["verdict"] == "consistent"  # car closing on base (T0226)
    r = compare("img_001147", "R054")  # "truck stopped" but T0078 moves 3.9 m/s
    assert r["verdict"] == "contradicts" and "T0078" in r["related"]
    assert [c["ok"] for c in r["checks"] if c["aspect"] == "motion"] == [False]
    r = compare("img_000733", "R112")  # "our car coming to base": closer than 60 min ago, but not closing in last 30
    assert r["verdict"] == "unverifiable" and "dost kabul etmek için yeterli değil" in r["reason"]
    assert compare("img_008001", "R105")["verdict"] == "contradicts"  # "leaving" but approaching over 60 min
    assert compare("img_002256", "R025")["verdict"] == "contradicts"  # 2 trucks claimed, none detected
    r = compare("img_003464", "R100")  # "7 trucks stopped": 6 trucks seen (count ok) but 3 are driving to base
    assert r["verdict"] == "contradicts" and {"T0001", "T0028", "T0135"} <= set(r["related"])
    assert [(c["aspect"], c["ok"]) for c in r["checks"]] == [("count", True), ("motion", False)]
    assert compare("img_008333", "R091")["verdict"] == "contradicts"  # "no heavy movement" vs moving truck
    assert compare("img_000267", "R012")["verdict"] == "irrelevant"  # last night's unverified tip
    assert compare("img_006388", "R001")["verdict"] == "unverifiable"  # "traffic normal"


def test_compare_edge_cases():
    assert "error" in compare("img_003839", "R999")
    assert compare("img_003839", "R100")["verdict"] == "irrelevant"  # ~1.1 km outside this frame
    # refers to LOCATE's det_ids
    dets = run_tool("get_detections", {}, stage="LOCATE", image_id="img_000267", evidence={}).result["detections"]
    truck = next(d["det_id"] for d in dets if d["label"] == "truck")
    assert truck in compare("img_000267", "R052")["related"]


def test_compare_report_links_tracks_via_match_tracks_only():
    # R000 "1 kamyon" at img_003839: point sits on a van (D02); the truck (D05) has no track.
    # T0182 belongs to car D00 and must not be attached to the truck claim.
    from s2agent.registry import run_tool
    img, ev = "img_003839", {}
    for name, stage in (("get_image_info", "LOCATE"), ("match_tracks", "TRACKS")):
        ev.update(run_tool(name, {}, stage=stage, image_id=img, evidence=ev).evidence_update)
    r = run_tool("compare_report", {"report_id": "R000"}, stage="REPORTS", image_id=img, evidence=ev).result
    assert "T0182" not in r["related"] and r["det_tracks"]["D05"] is None
    assert r["verdict"] == "contradicts"
