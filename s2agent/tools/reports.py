"""Stage 4 — REPORTS: which field reports concern this image, and do they agree with our evidence?
Rule (task PDF): if a report contradicts our detections/tracks, trust our evidence and ignore the report."""
from ..registry import ToolContext, stage_tool
from ._stub import todo


@stage_tool("REPORTS", writes="reports")
def find_reports(radius_m: float = 1500.0, window_min: int = 120, *, ctx: ToolContext) -> dict:
    """Konumu (metindeki koordinat veya bölge adı) görüntüye radius_m içinde olan ve saati çekimden
    en fazla window_min önce/sonra olan raporlar.
    Dönüş: {reports:[{report_id, time, source, text, loc_type: coord|zone, dist_m, dt_min}]}."""
    # TODO: regex '(\d+\.\d+)N (\d+\.\d+)E' + zone-name match via ctx.data.zones; ctx.data.reports
    return todo(reports=[])


@stage_tool("REPORTS", writes="report_checks", key_by="report_id")
def compare_report(report_id: str, *, ctx: ToolContext) -> dict:
    """Raporun iddiasını (araç tipi, sayı, hareket/durma, 'dost unsur' beyanı) kendi tespit ve
    track bulgularımızla karşılaştırır.
    Dönüş: {report_id, claim:{type,count,motion,friendly}, verdict: consistent|contradicts|unverifiable|irrelevant,
            related:[det_id/track_id], reason}."""
    # TODO: parse claim (regex or small LLM call) + compare with ctx.evidence
    return todo(report_id=report_id, claim={}, verdict="unverifiable", related=[], reason="not implemented")
