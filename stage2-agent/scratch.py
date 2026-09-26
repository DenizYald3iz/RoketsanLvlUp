#!/usr/bin/env python3
"""Tool'lari pipeline'a baglamadan tek tek deneme scripti.

Kullanim:
    python scratch.py                        # veri ozeti
    python scratch.py walk img_003839        # bir goruntuyu ucdan uca gez
    python scratch.py tool nearest_zone lat=39.94 lon=32.87
    python scratch.py reports 39.9307 32.8380 09:40
    python scratch.py contradictions         # gun boyunca celisen raporlar
    python scratch.py credibility            # tum raporlarin guvenilirlik skoru
    python scratch.py credibility 44         # tek raporun kirilimi
    python scratch.py crop img_003839 39.937208 32.848238   # kirpmayi diske yaz
    python scratch.py vision img_003839 39.937208 32.848238 # GLM'e sor (PARA HARCAR)
    python scratch.py specs                  # tool semalarini dok

'walk' hicbir LLM cagrisi yapmaz: sadece deterministik tool'lari gercek veri
uzerinde sirayla calistirip ciktiyi gosterir. Pipeline'in modelden yapmasini
bekledigi akisin aynisidir.
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import data_loader as dl
import schemas.state as state
import schemas.tool_specs as TS
from tools import decision, geo, reliability, reports, tracks


def _p(label: str, obj) -> None:
    print(f"\n--- {label} ---")
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def summary() -> None:
    ds = dl.load()
    print(f"goruntu    : {len(ds.images)}")
    print(f"bolge      : {len(ds.zones)} (+ us: {ds.base.name})")
    print(f"hareket    : {len(ds.tracks)} kayit, "
          f"{sum(len(v) for v in ds.tracks.values())} nokta")
    print(f"rapor      : {len(ds.reports)} "
          f"(official {sum(1 for r in ds.reports if r.source == 'official')}, "
          f"third_party {sum(1 for r in ds.reports if r.source == 'third_party')})")

    kinds: dict = {}
    for r in ds.reports:
        k = str(reports.parse_report(r.text)["kind"])
        kinds[k] = kinds.get(k, 0) + 1
    print(f"rapor turu : {kinds}")
    times = sorted({m.capture_time for m in ds.images.values()})
    print(f"cekim saati: {times[0]} - {times[-1]}")
    print("\nOrnek goruntuler:", ", ".join(list(ds.images)[:5]))
    print("Devam: python scratch.py walk", next(iter(ds.images)))


def walk(image_id: str) -> None:
    """Bir goruntuyu tool'larla ucdan uca gezer. LLM cagrisi YOK."""
    ds = dl.load()
    st = state.reset_session()

    fp = geo.image_footprint(image_id)
    _p("1. image_footprint", fp)

    inside = tracks.tracks_in_image(image_id)
    _p("2. tracks_in_image", inside)
    if not inside["tracks_inside"]:
        print("Karede kayitli arac yok; burada durulur.")
        return

    # Gercek tespit modeli yerine: kare icindeki kaydi piksele cevirip
    # onu "tespit" gibi kullan. Pipeline burada 1. gun modelinin kutusunu verir.
    target = inside["tracks_inside"][0]
    px = geo.geo_to_pixel(image_id, target["lat"], target["lon"])
    print(f"\n(1. gun modeli yerine) {target['track_id']} pikseli: "
          f"x={px['x']} y={px['y']}")

    pt = geo.pixel_to_geo(image_id, px["x"], px["y"])
    _p("3. pixel_to_geo", pt)

    cand = tracks.find_candidate_tracks(pt["lat"], pt["lon"], fp["capture_time"])
    _p("4. find_candidate_tracks", cand)
    if not cand["best"]:
        print("Eslesme yok; arac park halinde olabilir.")
        return

    motion = tracks.get_motion_profile(cand["best"]["track_id"],
                                       at_time=str(fp["capture_time"]))
    _p("5. get_motion_profile", motion)

    q = reports.query_reports(lat=pt["lat"], lon=pt["lon"],
                              time=str(fp["capture_time"]))
    _p("6. query_reports", q)

    for rep in q["reports"][:2]:
        chk = reports.check_report_consistency(
            rep["index"],
            observed_movement=str(motion["movement"]),
            observed_lat=pt["lat"], observed_lon=pt["lon"],
            observed_time=str(fp["capture_time"]),
        )
        _p(f"7. check_report_consistency (#{rep['index']})", chk)
        outcome = {"consistent": "agreed", "contradicts": "contradicted"}.get(
            str(chk["verdict"]), "unverified"
        )
        reliability.update_source_reliability(
            str(rep["source"]), outcome, report_index=int(rep["index"]),
            detail=str(chk["verdict"]), session=st,
        )

    cross = reports.cross_report_contradiction_check(
        lat=pt["lat"], lon=pt["lon"], time=str(fp["capture_time"])
    )
    _p("8. cross_report_contradiction_check", cross)
    _p("9. get_source_reliability", reliability.get_source_reliability(session=st))

    # Karari burada MODEL verir. Asagisi sadece sozlesmeyi gostermek icin.
    saved = decision.submit_assessment(
        image_id=image_id,
        verdict="watch" if motion["movement"] == "approaching_base" else "routine",
        rationale=(
            f"{motion['track_id']} {fp['capture_time']} itibariyla usse "
            f"{motion['distance_to_base_km']} km mesafede, {motion['speed_mps']} m/s "
            f"hizla {motion['movement']}. Konumla eslesen {q['counts']['total']} rapor var."
        ),
        evidence=[str(motion["summary"])] + [
            f"#{r['index']} {r['time']} {r['source']}: {r['text'][:60]}"
            for r in q["reports"][:2]
        ],
        confidence=0.4 if cand["ambiguous"] else 0.7,
        vehicles=[{
            "track_id": motion["track_id"],
            "lat": motion["position"]["lat"], "lon": motion["position"]["lon"],
            "movement": motion["movement"],
            "distance_to_base_km": motion["distance_to_base_km"],
        }],
        used_reports=[int(r["index"]) for r in q["reports"][:2]],
        session=st,
    )
    _p("10. submit_assessment", saved)
    print("\nNOT: 10. adimdaki karar ORNEKTIR; gercekte modelin verdigi karar girer.")


def run_tool(name: str, kv: list[str]) -> None:
    """python scratch.py tool <ad> k=v k=v ..."""
    args: dict = {}
    for item in kv:
        if "=" not in item:
            raise SystemExit(f"Arguman 'anahtar=deger' olmali: {item!r}")
        k, v = item.split("=", 1)
        try:
            args[k] = json.loads(v)
        except json.JSONDecodeError:
            args[k] = v
    _p(f"{name}({args})", TS.dispatch(name, args))


def show_reports(lat: float, lon: float, time: str) -> None:
    _p("query_reports", reports.query_reports(lat=lat, lon=lon, time=time))
    _p("cross_report_contradiction_check",
       reports.cross_report_contradiction_check(lat=lat, lon=lon, time=time))


def all_contradictions() -> None:
    """Gun boyunca AYNI NOKTAYA dair celisen rapor ciftlerini tarar."""
    ds = dl.load()
    seen: set = set()
    rows = []
    for rep in ds.reports:
        c = reports.parse_report(rep.text)
        if c["lat"] is None or c["zone"] is not None:
            continue
        out = reports.cross_report_contradiction_check(
            lat=float(c["lat"]), lon=float(c["lon"]), time=rep.time, window_min=90
        )
        for con in out["conflicts"]:
            key = tuple(sorted((con["a_index"], con["b_index"]))) + (con["field"],)
            if key in seen:
                continue
            seen.add(key)
            rows.append(con)

    print(f"{len(rows)} celiski bulundu "
          f"(ayni nokta = {__import__('config').SAME_POINT_RADIUS_M:.0f} m icinde)\n")
    for con in sorted(rows, key=lambda c: c["minutes_apart"]):
        print(f"[{con['field']}] {con['separation_m']} m, {con['minutes_apart']} dk arayla")
        print(f"    #{con['a_index']} {con['a_time']} {con['a_source']}: {con['a_text']}")
        print(f"    #{con['b_index']} {con['b_time']} {con['b_source']}: {con['b_text']}")
        print(f"    -> {con['why_it_conflicts']}")
        print()
    print("Hangisinin dogru oldugunu bu veri soylemiyor; hakem kendi tespitin.")


def credibility(idx=None) -> None:
    """Rapor guvenilirlik skorlarini kirilimiyla gosterir."""
    from tools import reliability as rel
    ds = dl.load()
    if idx is not None:
        _p(f"assess_report_credibility({idx})", rel.assess_report_credibility(int(idx)))
        return
    rows = []
    for rep in ds.reports:
        o = rel.assess_report_credibility(rep.index)
        if o["credibility"] is None:
            continue
        rows.append(o)
    rows.sort(key=lambda o: -float(o["credibility"]))
    print(f"{len(rows)} skorlanabilir rapor (gurultu haric)\n")
    for o in rows[:10] + [{"_sep": True}] + rows[-8:]:
        if o.get("_sep"):
            print("  ...")
            continue
        factors = " ".join(
            f"{b['factor']}{b['value']:+.2f}" for b in o["breakdown"]
        )
        print(f"  {o['credibility']:.2f} [{o['band']:6s}] {o['source']:11s} "
              f"{factors:46s} #{o['report_index']}")
        print(f"       {o['text'][:92]}")
    print()
    import collections
    band = collections.Counter(str(o["band"]) for o in rows)
    print("dagilim:", dict(band))
    lifted = [o for o in rows if o["source"] == "third_party"
              and any(b["factor"] == "destekleme" for b in o["breakdown"])]
    print(f"destekleme ile agirligi artan third_party raporu: {len(lifted)}")


def save_crop(image_id: str, lat: float, lon: float) -> None:
    from tools import vision
    out = vision.crop_image(image_id, lat=lat, lon=lon)
    path = Path(f"crop_{image_id}.jpg")
    path.write_bytes(base64.b64decode(str(out["image_b64"])))
    print(f"kaydedildi: {path} ({out['width']}x{out['height']}, {out['bytes']} bayt)")
    _p("crop", {k: v for k, v in out.items() if k != "image_b64"})


def ask_vision(image_id: str, lat: float, lon: float) -> None:
    from tools import vision
    print("GLM'e soruluyor (bu cagri butceden harcar)...")
    _p("reinspect_crop", vision.reinspect_crop(image_id, lat=lat, lon=lon))


def main(argv: list[str]) -> None:
    if not argv:
        summary()
        return
    cmd, rest = argv[0], argv[1:]
    if cmd == "walk":
        walk(rest[0] if rest else next(iter(dl.load().images)))
    elif cmd == "tool":
        run_tool(rest[0], rest[1:])
    elif cmd == "reports":
        show_reports(float(rest[0]), float(rest[1]), rest[2])
    elif cmd == "contradictions":
        all_contradictions()
    elif cmd == "credibility":
        credibility(rest[0] if rest else None)
    elif cmd == "crop":
        save_crop(rest[0], float(rest[1]), float(rest[2]))
    elif cmd == "vision":
        ask_vision(rest[0], float(rest[1]), float(rest[2]))
    elif cmd == "specs":
        print(json.dumps(TS.TOOL_SPECS, indent=2, ensure_ascii=False))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
