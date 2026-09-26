"""Nihai degerlendirmeyi yapilandirilmis sekilde kaydeder.

Tek tool: submit_assessment. Agent dongusu bunu cagirdiginda tamamlanmis
sayilir; pipeline bu cagriyi gorup durur.

Gorev tanimi karar konusunda ozgur birakiyor ("dikkat gerektirip
gerektirmedigine agent'iniz karar verir"), ama kararin DAYANDIGI VERIYI
soylemesini sart kosuyor. Bu yuzden gerekce ve kanit alanlari zorunludur
ve dogrulanir: kanitsiz "attention" kabul edilmez.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import data_loader as dl
from schemas.state import SessionState, get_session

VERDICTS = ("routine", "watch", "attention")

_VERDICT_MEANING = {
    "routine": "Olagan. Mudahale ya da takip gerekmiyor.",
    "watch": "Izlemede tut. Tek basina alarm degil ama takip edilmeli.",
    "attention": "Dikkat gerekiyor. Operatorun bakmasi gereken bir durum var.",
}


def submit_assessment(
    image_id: str,
    verdict: str,
    rationale: str,
    evidence: List[str],
    confidence: float = 0.5,
    vehicles: Optional[List[Dict]] = None,
    used_reports: Optional[List[int]] = None,
    session: Optional[SessionState] = None,
) -> Dict[str, object]:
    """Bir goruntu icin nihai degerlendirmeyi kaydeder ve dongusu kapatir.

    Args:
        image_id:  degerlendirilen goruntu.
        verdict:   "routine" | "watch" | "attention".
        rationale: NEDEN. Hangi bulgunun karari surukledigini acikca yaz.
                   "Supheli gorunuyor" yeterli degil; hangi veri, hangi deger.
        evidence:  kararı dayandirdigin somut bulgular. Her madde olcum ya da
                   kaynak icermeli. Ornek:
                   "T0187 13:25'te usse 3.0 km, son 30 dk'da 9 m/s ile yaklasti"
                   "12:40 third_party raporu (#37) tespitle uyumlu"
        confidence: 0..1. Belirsiz eslesme/az kanit varsa dusuk tut.
        vehicles:  [{"track_id":..., "type":..., "lat":..., "lon":...,
                     "movement":..., "distance_to_base_km":...}, ...]
        used_reports: gerekcede kullanilan rapor indeksleri (iz surme icin).

    Donen: kaydedilen degerlendirme + dogrulama uyarilari.

    Hatalar: gecersiz verdict, bos rationale ya da kanitsiz "attention"
    ValueError ile reddedilir; agent duzeltip tekrar cagirmalidir.
    """
    ds = dl.load()
    ds.image(image_id)  # bilinmeyen id ise burada patlar

    if verdict not in VERDICTS:
        raise ValueError(
            f"verdict {VERDICTS} icinden olmali, gelen: {verdict!r}"
        )
    if not rationale or len(rationale.strip()) < 20:
        raise ValueError(
            "rationale bos ya da cok kisa. Karari hangi bulgunun surukledigini "
            "acikca yaz (en az bir cumle, olculerle)."
        )
    evidence = [e.strip() for e in (evidence or []) if e and e.strip()]
    if verdict in ("attention", "watch") and not evidence:
        raise ValueError(
            f"'{verdict}' karari en az bir somut kanit istiyor. Hangi hareket "
            f"kaydi, hangi olcu, hangi rapor? Kanitsiz yukseltme yapma."
        )
    if not 0.0 <= confidence <= 1.0:
        raise ValueError(f"confidence 0..1 araliginda olmali, gelen: {confidence}")

    # --- kalite uyarilari: reddetmez ama kayda gecer ---
    warnings: List[str] = []
    if verdict == "attention" and confidence < 0.4:
        warnings.append(
            "Dusuk guvenle 'attention' verildi. Kanit zayifsa 'watch' daha dogru olabilir."
        )
    if used_reports:
        bad = [i for i in used_reports if not 0 <= i < len(ds.reports)]
        if bad:
            raise ValueError(f"Gecersiz rapor indeksleri: {bad}")
        only_tp = all(
            next(r for r in ds.reports if r.index == i).source == "third_party"
            for i in used_reports
        )
        if only_tp and verdict == "attention":
            warnings.append(
                "Karar yalnizca third_party raporlarina dayaniyor. Bu kaynak "
                "dogrulanmamis olabilir; kendi tespitinle destekle."
            )
    if not vehicles and verdict != "routine":
        warnings.append(
            "Arac listesi bos ama karar 'routine' degil. Neyin dikkat "
            "gerektirdigini arac duzeyinde belirt."
        )

    assessment: Dict[str, object] = {
        "image_id": image_id,
        "capture_time": ds.image(image_id).capture_time,
        "verdict": verdict,
        "verdict_meaning": _VERDICT_MEANING[verdict],
        "confidence": round(float(confidence), 2),
        "rationale": rationale.strip(),
        "evidence": evidence,
        "vehicles": vehicles or [],
        "used_reports": used_reports or [],
        "warnings": warnings,
    }

    st = session or get_session()

    # Yol bagimliligini izlenebilir kil: bu karar, o anki guvenilirlik
    # skorlariyla alindi. Sonradan "neden boyle dedi" sorusu bununla cevaplanir.
    # SIRA ONEMLI: anlik goruntu, bu goruntu siraya eklenmeden ONCE alinir,
    # boylece images_processed_before "bundan once kac goruntu islendi" olur.
    assessment["reliability_at_decision"] = st.reliability_snapshot()
    assessment["images_processed_before"] = len(st.processing_order)

    img_st = st.image_state(image_id)
    img_st.assessment = assessment  # type: ignore[assignment]
    img_st.capture_time = str(assessment["capture_time"])
    st.assessments = [a for a in st.assessments if a["image_id"] != image_id]
    st.assessments.append(assessment)  # type: ignore[arg-type]

    return {
        "saved": True,
        "assessment": assessment,
        "warnings": warnings,
        "note": "Degerlendirme kaydedildi. Bu goruntu icin dongu tamamlandi.",
    }


def get_assessments(session: Optional[SessionState] = None) -> Dict[str, object]:
    """Oturumda kaydedilmis tum degerlendirmeleri dondurur (gun sonu ozeti)."""
    st = session or get_session()
    items = sorted(st.assessments, key=lambda a: str(a.get("capture_time", "")))
    counts: Dict[str, int] = {v: 0 for v in VERDICTS}
    for a in items:
        counts[str(a["verdict"])] = counts.get(str(a["verdict"]), 0) + 1
    return {"assessments": items, "counts": counts, "total": len(items)}
