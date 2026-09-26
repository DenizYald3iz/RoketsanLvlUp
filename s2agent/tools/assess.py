"""Stage 5 — ASSESS: the agent's final, structured judgement."""
from typing import Literal

from pydantic import BaseModel, Field

from ..registry import ToolContext, stage_tool


class Alert(BaseModel):
    level: Literal["yuksek", "orta", "dusuk"]
    title: str = Field(description="Kısa başlık, ör. 'Üsse hızla yaklaşan kamyon'")
    subject: str = Field(description="İlgili det_id / track_id / report_id")
    reason: str = Field(description="Neden dikkat gerektiriyor")
    evidence: list[str] = Field(description="Dayanılan veriler, ör. 'T0187: 9 m/s, üsse 3.0 km, ETA 5.5 dk', 'R012 uyumlu'")


@stage_tool("ASSESS", writes="assessment")
def submit_assessment(alerts: list[Alert], summary: str, ignored_reports: list[str] = [], *, ctx: ToolContext) -> dict:
    """Nihai değerlendirmeyi kaydeder. Dikkat gerektiren her durum için bir alert; yoksa boş liste.
    ignored_reports: çelişkili/ilgisiz olduğu için yok sayılan report_id'ler. Bu çağrı agent'ı bitirir."""
    return {"image_id": ctx.image_id, "alerts": [a.model_dump() if hasattr(a, "model_dump") else a for a in alerts],
            "summary": summary, "ignored_reports": ignored_reports}
