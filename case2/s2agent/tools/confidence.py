"""Stage 5 — ASSESS: how much should we trust an alert? (math ported from the deniz branch, tools/confidence.py)

An alert rests on a chain: detection → detection↔track match → motion classification. Each link gets a
0..1 confidence from the evidence itself (not from the LLM), then the chain is combined:
  * links in the same `group` depend on each other → min (the same doubt is not counted twice),
  * independent groups → product (strict chain); `weakest_link` = min over groups is returned too.
Thresholds give a "cliff" (0.51 m/s is as "moving" as 9 m/s), so each link is scored by its margin from the
threshold: exactly on the threshold = 0.5, `scale` beyond it = 1.0 (margin_confidence).
"""
from pydantic import BaseModel, Field

from ..registry import ToolContext, stage_tool

MATCH_OK_M = 5.0  # deniz branch, measured: true det↔track matches sit ≤ 2 m; 5–88 m are usually a parked car
STOP_MPS = 0.5  # get_track_kinematics: below this a step is "stopped"
TREND_M = 100.0  # get_track_kinematics: 60-min base-distance change that makes a trend


def margin_confidence(value: float, threshold: float, scale: float | None = None, direction: str = "below") -> float:
    """0..1 by distance from a threshold: on it 0.5, `scale` past it on the good side 1.0, on the bad side 0.0.
    direction 'below' = small is good (a distance), 'above' = large is good (a speed / a change)."""
    scale = scale or abs(threshold) / 2 or 1.0
    margin = (threshold - value) if direction == "below" else (value - threshold)
    return round(0.5 + 0.5 * max(-1.0, min(1.0, margin / scale)), 3)


def combine(factors: list[dict], strict: bool = True) -> dict:
    """factors: [{name, confidence 0..1, group?, why?}] → {confidence, product, weakest_link, bottleneck, groups}."""
    clean = [{**f, "group": f.get("group") or f["name"]} for f in factors if f.get("confidence") is not None]
    for f in clean:
        if not 0.0 <= float(f["confidence"]) <= 1.0:
            raise ValueError(f"confidence 0..1 olmalı: {f['name']}={f['confidence']}")
    if not clean:
        return {"confidence": None, "product": None, "weakest_link": None, "bottleneck": None, "groups": {},
                "factors": [], "note": "Güven faktörü yok; skor hesaplanamadı."}
    groups: dict[str, float] = {}
    for f in clean:  # same group = dependent → min
        groups[f["group"]] = min(groups.get(f["group"], 1.0), float(f["confidence"]))
    product = 1.0
    for c in groups.values():
        product *= c
    weakest = min(groups.values())
    neck = min(clean, key=lambda f: (float(f["confidence"]), f["name"]))
    return {"confidence": round(product if strict else weakest, 3), "product": round(product, 3),
            "weakest_link": round(weakest, 3), "mode": "product" if strict else "weakest_link",
            "bottleneck": {"name": neck["name"], "confidence": round(float(neck["confidence"]), 3)},
            "groups": {k: round(v, 3) for k, v in sorted(groups.items())},
            "factors": [{k: f[k] for k in ("name", "confidence", "group", "why") if k in f} for f in clean],
            "note": f"{len(clean)} faktör, {len(groups)} bağımsız grup; darboğaz: {neck['name']} "
                    f"({float(neck['confidence']):.2f})."}


def band(conf: float | None) -> str | None:
    return None if conf is None else "yuksek" if conf >= 0.8 else "orta" if conf >= 0.5 else "dusuk"


def evidence_factors(subject_ids: set[str], ev: dict) -> list[dict]:
    """Chain factors for the vehicles named in an alert (det ids and/or track ids), read from the evidence."""
    dets = {d["det_id"]: d for d in ev.get("detections", {}).get("detections", [])}
    matches = ev.get("matches", {}).get("matches", [])
    by_det = {m["det_id"]: m for m in matches}
    by_trk = {m["track_id"]: m for m in matches}
    det_ids = {i for i in subject_ids if i in dets} | {by_trk[t]["det_id"] for t in subject_ids if t in by_trk}
    trk_ids = {t for t in subject_ids if t.startswith("T")} | {by_det[d]["track_id"] for d in det_ids if d in by_det}

    out = []
    for d in sorted(det_ids):  # 1) is it really a vehicle of that class? (detector score, independent)
        c = float(dets[d]["conf"])
        out.append({"name": f"tespit {d}", "confidence": round(c, 3), "group": f"det:{d}",
                    "why": f"{d} {dets[d]['label']} tespit skoru {c:.2f}"})
    for t in sorted(trk_ids):
        m = by_trk.get(t)
        kin = ev.get("kinematics", {}).get(t)
        # 2) is this movement record really this vehicle? (match distance; 5 m = coin flip)
        if m:
            c = margin_confidence(float(m["dist_m"]), MATCH_OK_M, scale=MATCH_OK_M)
            out.append({"name": f"eşleşme {m['det_id']}↔{t}", "confidence": c, "group": f"trk:{t}",
                        "why": f"tespit ile iz arası {m['dist_m']} m (≤{MATCH_OK_M:.0f} m güvenli)"})
        # 3) how clear is the motion call? same group as the match: a wrong match makes the motion wrong too
        if kin and "trend" in kin:
            delta = abs(float(kin["base_dist_60min_ago_m"]) - float(kin["base_dist_now_m"]))
            v = float(kin.get("last30_speed_mps") or 0.0)
            if kin.get("stopped_now") and v < STOP_MPS:  # parked: how far below the "moving" speed
                c = margin_confidence(v, STOP_MPS, scale=STOP_MPS, direction="below")
                why = f"son 30 dk {v} m/s (duruyor eşiği {STOP_MPS} m/s)"
            elif kin["trend"] == "stationary":  # moving but no net trend: how far inside the ±100 m band
                c = margin_confidence(delta, TREND_M, scale=TREND_M, direction="below")
                why = f"hareketli ama 60 dk'da üsse mesafe yalnızca {delta:.0f} m değişti (eşik {TREND_M:.0f} m)"
            else:  # approaching / receding: how far past the trend threshold
                c = margin_confidence(delta, TREND_M, scale=TREND_M * 2, direction="above")
                why = f"60 dk'da üsse mesafe {delta:.0f} m değişti ({kin['trend']}; eşik {TREND_M:.0f} m)"
            out.append({"name": f"hareket {t}", "confidence": c, "group": f"trk:{t}", "why": why})
    return out


class Factor(BaseModel):
    name: str = Field(description="Faktör adı, ör. 'görsel kontrol D04'")
    confidence: float = Field(ge=0, le=1, description="0..1")
    group: str | None = Field(default=None, description="Aynı belirsizliğe bağlı faktörlere aynı grup adı")
    why: str = Field(default="", description="Kısa gerekçe")


def alert_confidence(subject: str, ev: dict, extra: list[dict] | None = None, strict: bool = True) -> dict:
    from .assess import ID_RE  # late import: assess imports this module

    ids = set(ID_RE.findall(subject))
    res = combine(evidence_factors(ids, ev) + list(extra or []), strict=strict)
    return {"subject": subject, "ids": sorted(ids), "band": band(res["confidence"]), **res}


@stage_tool("ASSESS", writes="confidence", key_by="subject")
def combine_confidence(subject: str, extra_factors: list[Factor] = [], strict: bool = True, *,
                       ctx: ToolContext) -> dict:
    """Bir alert'in güven skorunu (0..1) hesaplar. subject: alert'in konusu, ör. 'D04/T0183'.
    Faktörler delillerden OTOMATİK okunur: tespit skoru, tespit↔iz eşleşme mesafesi, hareket sınıfının eşiğe
    uzaklığı. Aynı belirsizliğe bağlı faktörler (eşleşme + hareket) aynı grupta: aralarında min, gruplar çarpılır.
    extra_factors: kendi eklemek istediğin faktörler (ör. view_image ile görsel kontrol). Dönüş: {confidence, band
    (yuksek|orta|dusuk), bottleneck (darboğaz), factors, weakest_link}. submit_assessment bu değeri alert'e yazar."""
    extra = [f.model_dump() if hasattr(f, "model_dump") else dict(f) for f in extra_factors]
    return alert_confidence(subject, ctx.evidence, extra, strict)
