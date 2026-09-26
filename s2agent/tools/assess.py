"""Stage 5 — ASSESS: the agent's final, structured judgement.

submit_assessment validates the LLM's alerts against the evidence (ids must exist, an alert must be about a
vehicle/track or an accepted report, not about an ignored one). The first invalid call is rejected with the
problems listed so the LLM can fix it; a second invalid call is accepted with warnings, so a stubborn model
cannot loop until the turn limit. Reports that compare_report found contradicting are always ignored (task rule:
trust our detections over reports), and so are irrelevant ones.
"""
import re
from typing import Literal

from pydantic import BaseModel, Field

from ..registry import ToolContext, stage_tool

LEVELS = ("yuksek", "orta", "dusuk")
ID_RE = re.compile(r"\b(D\d{2,}|T\d{4}|R\d{3})\b")


class Alert(BaseModel):
    level: Literal["yuksek", "orta", "dusuk"]
    title: str = Field(description="Kısa başlık, ör. 'Üsse hızla yaklaşan kamyon'")
    subject: str = Field(description="İlgili det_id / track_id (birden fazlaysa 'D01/T0078'); rapor yalnızca "
                                     "yok sayılmamışsa konu olabilir")
    reason: str = Field(description="Neden dikkat gerektiriyor")
    evidence: list[str] = Field(min_length=1, description="Dayanılan veriler, ör. 'T0187: 9 m/s, üsse 3.0 km, "
                                                          "ETA 5.5 dk', 'R012 uyumlu'")


def _known_ids(ctx: ToolContext) -> tuple[set[str], set[str]]:
    """(vehicle ids = det_ids + track_ids, report ids) that this image's evidence can back."""
    ev = ctx.evidence
    dets = {d["det_id"] for d in ev.get("detections", {}).get("detections", []) if "det_id" in d}
    tr = ctx.data.tracks
    capture = ctx.data.meta[ctx.image_id]["capture_time"]
    tracks = set(tr.loc[tr.groupby("track_id")["time"].transform("max") == capture, "track_id"])
    tracks |= set(ev.get("kinematics", {}))
    reps = ev.get("reports", {})
    reports = {r["report_id"] for r in reps.get("reports", [])}
    reports |= {i for g in reps.get("general", []) for i in g.get("report_ids", [])}
    reports |= set(ev.get("report_checks", {}))
    return dets | tracks, reports


def _problems(alerts: list[dict], ignored: set[str], vehicles: set[str], reports: set[str]) -> list[str]:
    out = []
    for i, a in enumerate(alerts):
        subj = set(ID_RE.findall(a["subject"]))
        cited = subj | {x for e in a["evidence"] for x in ID_RE.findall(e)}
        if unknown := sorted(cited - vehicles - reports):
            out.append(f"alert[{i}]: delillerde olmayan id: {unknown}")
        if not subj:
            out.append(f"alert[{i}]: subject bir det_id/track_id/report_id içermeli (şu an '{a['subject']}')")
        elif subj <= ignored:
            out.append(f"alert[{i}]: konusu yalnızca yok sayılan rapor(lar) {sorted(subj)}; yok sayılan rapor "
                       f"alert konusu olamaz. İlgili aracın alert'ine delil olarak ekle ya da alert'i kaldır")
    return out


@stage_tool("ASSESS", writes="assessment")
def submit_assessment(alerts: list[Alert], summary: str, ignored_reports: list[str] = [], *,
                      ctx: ToolContext) -> dict:
    """Nihai değerlendirmeyi kaydeder ve agent'ı bitirir. Dikkat gerektiren HER durum için bir alert; yoksa boş liste.
    Alert bir araç/track hakkındadır (subject: 'D01/T0078'); raporlar evidence'a yazılır. level: yuksek = üsse
    yaklaşan ve yakın/hızlı (kısa ETA) ya da acil; orta = izlenmeli; dusuk = bilgi amaçlı.
    ignored_reports: çelişkili/ilgisiz olduğu için yok sayılan report_id'ler (compare_report 'contradicts' ve
    'irrelevant' dedikleri otomatik eklenir). Id'ler delillerde olmalı; hatalıysa sorunlar listelenir, düzeltip tekrar çağır."""
    alerts = [a.model_dump() if hasattr(a, "model_dump") else dict(a) for a in alerts]
    checks = ctx.evidence.get("report_checks", {})
    verdicts = {rid: c.get("verdict") for rid, c in checks.items()}
    auto_ignored = {rid for rid, v in verdicts.items() if v in ("contradicts", "irrelevant")}
    ignored = set(ignored_reports) | auto_ignored

    vehicles, reports = _known_ids(ctx)
    problems = _problems(alerts, ignored, vehicles, reports)
    if unknown := sorted(set(ignored_reports) - reports):
        problems.append(f"ignored_reports içinde bu görüntünün raporu olmayan id: {unknown}")
    attempts = ctx.evidence.get("assessment_attempts", 0) + 1
    if problems and attempts == 1:
        return {"error": "Değerlendirme kaydedilmedi, düzeltip submit_assessment'ı tekrar çağır.",
                "problems": problems, "_evidence": {"assessment_attempts": attempts}}

    warnings = [f"Tekrar denemede düzeltilmedi: {p}" for p in problems]
    if added := sorted(auto_ignored - set(ignored_reports)):
        warnings.append(f"compare_report 'contradicts'/'irrelevant' dediği için yok sayılanlara eklendi: {added}")
    if kept := sorted(r for r in ignored if verdicts.get(r) == "consistent"):
        warnings.append(f"Tespitle uyumlu (consistent) raporlar yok sayıldı: {kept}")
    for a in alerts:
        a["ids"] = sorted(set(ID_RE.findall(a["subject"])))
    alerts.sort(key=lambda a: LEVELS.index(a["level"]))
    return {"image_id": ctx.image_id, "capture_time": ctx.data.meta[ctx.image_id]["capture_time"],
            "alerts": alerts, "summary": summary, "ignored_reports": sorted(ignored),
            "report_verdicts": verdicts, "warnings": warnings, "_evidence": {"assessment_attempts": attempts}}


def auto_assessment(ev: dict) -> dict:
    """Rule-based submit_assessment args, used when the LLM runs out of turns. Better than an empty
    assessment: approaching tracks become alerts, contradicting reports are ignored."""
    alerts = []
    for tid, k in ev.get("kinematics", {}).items():
        closing, eta = k.get("closing_speed_mps"), k.get("eta_min")
        if closing is None or closing <= 0.3:
            continue
        level = "yuksek" if eta is not None and eta <= 10 else "orta" if eta is not None and eta <= 30 else "dusuk"
        dist = k.get("base_dist_now_m")
        alerts.append({"level": level, "title": "Üsse yaklaşan araç", "subject": tid,
                       "reason": "Hareket kaydına göre üsse yaklaşıyor (otomatik kural).",
                       "evidence": [f"{tid}: yaklaşma {closing} m/s, üsse {dist} m, ETA {eta} dk"]})
    checks = ev.get("report_checks", {})
    ignored = sorted(r for r, c in checks.items() if c.get("verdict") in ("contradicts", "irrelevant"))
    return {"alerts": alerts, "ignored_reports": ignored,
            "summary": f"LLM tur limitinde değerlendirme üretemedi; kural tabanlı otomatik kayıt: "
                       f"{len(alerts)} yaklaşan araç alert'i, {len(ignored)} rapor yok sayıldı."}
