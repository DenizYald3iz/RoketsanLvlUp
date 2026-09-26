"""The stage machine. Each stage: a goal for the LLM, a gate the *code* checks, and a fallback
(tool calls run without the LLM) used when the LLM runs out of turns.

To add a stage: append a Stage here and tag its tools with @stage_tool("<NAME>").
"""
from dataclasses import dataclass, field
from typing import Callable

Evidence = dict
ToolCall = tuple[str, dict]


@dataclass
class Stage:
    name: str
    goal: str
    requires: list[str] = field(default_factory=list)  # evidence keys that must exist
    check: Callable[[Evidence], list[str]] | None = None  # extra gate -> list of missing items
    fallback: Callable[[Evidence], list[ToolCall]] = lambda ev: []
    attach_image: bool = False  # send the drone image with the stage's first prompt

    def missing(self, ev: Evidence) -> list[str]:
        miss = [f"evidence.{k}" for k in self.requires if k not in ev]
        if not miss and self.check:
            miss += self.check(ev)
        return miss


def _matched_tracks(ev: Evidence) -> list[str]:
    return [m["track_id"] for m in ev.get("matches", {}).get("matches", [])]


def _report_ids(ev: Evidence) -> list[str]:
    return [r["report_id"] for r in ev.get("reports", {}).get("reports", [])]


STAGES: list[Stage] = [
    Stage(
        "LOCATE",
        "Görüntünün nereyi/ne zamanı kapsadığını ve içindeki araçları (tip + koordinat) çıkar. "
        "get_image_info ve get_detections çağır; tespitler şüpheliyse view_image ile bak.",
        requires=["image_info", "detections"],
        fallback=lambda ev: [("get_image_info", {}), ("get_detections", {})],
        attach_image=True,
    ),
    Stage(
        "TRACKS",
        "Tespit edilen araçları çekim saatindeki hareket kayıtlarıyla eşleştir (match_tracks). "
        "Eşleşmeyenleri not et; çerçeve dışında yakındaki track'lere de bakabilirsin (list_tracks_near).",
        requires=["matches"],
        fallback=lambda ev: [("match_tracks", {})],
    ),
    Stage(
        "MOTION",
        "Eşleşen HER track için get_track_kinematics çağır: hız, yön, üsse mesafe, yaklaşma hızı, ETA. "
        "Hızı/yönü tek adımdan değil kaydın tamamından oku.",
        check=lambda ev: [f"kinematics[{t}]" for t in _matched_tracks(ev) if t not in ev.get("kinematics", {})],
        fallback=lambda ev: [("get_track_kinematics", {"track_id": t})
                             for t in _matched_tracks(ev) if t not in ev.get("kinematics", {})],
    ),
    Stage(
        "REPORTS",
        "Bu görüntüyle (konum + saat) ilgili saha raporlarını bul (find_reports) ve HER birini "
        "compare_report ile kendi bulgularımızla karşılaştır. Çelişen rapor yok sayılır; esas olan tespittir.",
        requires=["reports"],
        check=lambda ev: [f"report_checks[{r}]" for r in _report_ids(ev) if r not in ev.get("report_checks", {})],
        fallback=lambda ev: [("find_reports", {})] if "reports" not in ev else
        [("compare_report", {"report_id": r}) for r in _report_ids(ev) if r not in ev.get("report_checks", {})],
    ),
    Stage(
        "ASSESS",
        "Toplanan delillere göre hangi durumların dikkat gerektirdiğine karar ver ve submit_assessment çağır. "
        "Her alert için gerekçe ve dayandığı veriyi (track_id, hız, mesafe, ETA, report_id) yaz. "
        "Sadece delillerde olan sayıları kullan; uydurma.",
        requires=["assessment"],
        fallback=lambda ev: [("submit_assessment", {
            "alerts": [], "summary": "LLM tur limitinde değerlendirme üretemedi (otomatik kayıt)."})],
    ),
]
