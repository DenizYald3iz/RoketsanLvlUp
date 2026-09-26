"""Kaynak guvenilirligi: raporlar dogrulandikca/yalanlandikca skor birikir.

Tool'lar:
    update_source_reliability — bir dogrulamanin sonucunu oturuma isler
    get_source_reliability    — birikmis skorlari okur
    assess_report_credibility — bir rapora ne kadar agirlik verilmeli

Neden onemli: 40 goruntu tek tek islenirken ayni kaynaklar tekrar tekrar
karsimiza cikar. 5. goruntude "third_party sayiyi sisiriyor" diye ogrenilen
sey 30. goruntude kullanilabilir olmali. Skor SessionState icinde birikir.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import config
import data_loader as dl
from schemas.state import SessionState, get_session
from tools.reports import find_supporting_reports, parse_report

_SOURCES = ("official", "third_party")


def _blank(source: str) -> Dict[str, object]:
    return {
        "source": source,
        "checks": 0,
        "agreements": 0,
        "contradictions": 0,
        "score": 0.5,
        "label": "unknown",
    }


def _label(score: float, checks: int) -> str:
    if checks < 3:
        return "unknown"
    if score >= 0.7:
        return "reliable"
    if score >= 0.45:
        return "mixed"
    return "unreliable"


def _recompute(entry: Dict[str, object]) -> None:
    """Laplace duzeltmeli dogrulanma orani.

    Tek bir dogrulama skoru 0 ya da 1'e savurmasin diye (+1, +2) eklenir.
    """
    a = int(entry["agreements"])
    c = int(entry["contradictions"])
    entry["score"] = round((a + 1) / (a + c + 2), 3)
    entry["label"] = _label(float(entry["score"]), int(entry["checks"]))


# --- Tool: guvenilirlik guncelleme ---------------------------------------
def update_source_reliability(
    source: str,
    outcome: str,
    report_index: Optional[int] = None,
    detail: str = "",
    session: Optional[SessionState] = None,
) -> Dict[str, object]:
    """Bir raporun tespitle uyusup uyusmadigini oturum skoruna isler.

    Args:
        source: "official" | "third_party".
        outcome: "agreed" (tespit raporu dogruladi) |
                 "contradicted" (tespit raporu yalanladi) |
                 "unverified" (karsilastirilamadi; skoru degistirmez, sayilir).
        report_index: hangi rapor uzerinden; iz surmek icin.
        detail: kisa gerekce; log'a yazilir.
        session: test icin ayri oturum. Verilmezse global oturum.

    Donen: guncel skor + tum kaynaklarin ozeti.
    """
    if source not in _SOURCES:
        raise ValueError(f"source {_SOURCES} icinden olmali, gelen: {source!r}")
    if outcome not in ("agreed", "contradicted", "unverified"):
        raise ValueError(
            "outcome 'agreed' | 'contradicted' | 'unverified' olmali, "
            f"gelen: {outcome!r}"
        )

    st = session or get_session()
    entry = st.reliability.get(source) or _blank(source)  # type: ignore[assignment]

    entry["checks"] = int(entry["checks"]) + 1  # type: ignore[index]
    if outcome == "agreed":
        entry["agreements"] = int(entry["agreements"]) + 1  # type: ignore[index]
    elif outcome == "contradicted":
        entry["contradictions"] = int(entry["contradictions"]) + 1  # type: ignore[index]

    _recompute(entry)  # type: ignore[arg-type]
    entry.setdefault("log", [])  # type: ignore[attr-defined]
    entry["log"].append(  # type: ignore[index]
        {"report_index": report_index, "outcome": outcome, "detail": detail}
    )
    st.reliability[source] = entry  # type: ignore[assignment]

    return {
        "updated": dict(entry, log=len(entry["log"])),  # type: ignore[index]
        "all_sources": get_source_reliability(session=st)["sources"],
        "note": _advice(entry),  # type: ignore[arg-type]
    }


def _advice(entry: Dict[str, object]) -> str:
    label, src = entry["label"], entry["source"]
    if label == "unknown":
        return (
            f"{src} icin henuz yeterli dogrulama yok "
            f"({entry['checks']} kontrol). Skoru karar verirken tek basina kullanma."
        )
    if label == "reliable":
        return f"{src} raporlari bugun tutarli cikti; yine de tespit esastir."
    if label == "mixed":
        return f"{src} raporlari karisik; her iddiayi ayri dogrula."
    return (
        f"{src} raporlari bugun sik sik yalanlandi. Bu kaynaktan gelen "
        f"iddialari tek basina gerekce yapma."
    )


def get_source_reliability(
    source: Optional[str] = None,
    session: Optional[SessionState] = None,
) -> Dict[str, object]:
    """Oturumda birikmis guvenilirlik skorlarini okur (yazmaz)."""
    st = session or get_session()
    out = {}
    for s in _SOURCES:
        e = st.reliability.get(s) or _blank(s)
        out[s] = {k: v for k, v in e.items() if k != "log"}
    if source is not None:
        if source not in _SOURCES:
            raise ValueError(f"source {_SOURCES} icinden olmali")
        return {"sources": {source: out[source]}}
    return {"sources": out}


# --- Tool: rapor guvenilirlik skoru --------------------------------------
def assess_report_credibility(
    report_index: int,
    session: Optional[SessionState] = None,
) -> Dict[str, object]:
    """Bir rapora ne kadar agirlik verilmeli? Kirilimiyla birlikte doner.

    Skor tek bir sihirli sayi degil; uc kaynaktan toplanir ve her biri
    ciktida AYRI AYRI gorunur, cunku bunlarin epistemik statusu farkli:

      1. taban (prior) — kaynak turu. VARSAYIMDIR, olcum degil: resmi kanal
         teyit surecinden gecer kabulu. Veride dogrulanmis degil.
      2. olcum — oturum boyunca senin kendi tespitlerinle biriken skor.
         Yeterli kontrole (varsayilan 3) ulasinca TABANIN YERINE GECER,
         cunku olcum varsayimdan agirdir.
      3. destekleme — ayni noktayi anlatan baska raporlar. Capraz kaynak
         guclu, ayni kaynagin tekrari zayif, birebir ayni metin sifir.

    Ayrica raporun kendi ifadesiyle "dogrulanmamis" olmasi ceza yazar.

    Donen: {"credibility": 0..1, "breakdown": [...], "band": str,
            "supports": [...], "advice": str}
    """
    ds = dl.load()
    if not (0 <= report_index < len(ds.reports)):
        raise ValueError(
            f"report_index {report_index} araligin disinda (0..{len(ds.reports)-1})"
        )
    rep = next(r for r in ds.reports if r.index == report_index)
    claim = parse_report(rep.text)
    st = session or get_session()

    # Cevresel gurultu bir iddia tasimiyor; skorlanacak bir sey yok.
    if claim["kind"] == "environment":
        return {
            "report_index": report_index, "time": rep.time,
            "source": rep.source, "text": rep.text,
            "credibility": None, "band": "not_a_claim",
            "breakdown": [], "supports": [], "duplicates": [],
            "advice": "Bu rapor konum/arac iddiasi tasimiyor (hava, lojistik "
                      "gibi). Guvenilirlik skoru anlamsiz; gerekcede kullanma.",
            "limits": "Skorlanmadi.",
        }

    breakdown: List[Dict[str, object]] = []

    # --- 1/2. taban: varsayim mi, olcum mu? ---
    measured = st.reliability.get(rep.source)
    checks = int(measured["checks"]) if measured else 0
    if checks >= config.RELIABILITY_OVERRIDES_PRIOR_AFTER:
        score = float(measured["score"])  # type: ignore[index]
        breakdown.append({
            "factor": "olcum",
            "value": round(score, 3),
            "basis": "empirical",
            "why": (
                f"{rep.source} icin bu oturumda {checks} kontrol yapildi "
                f"({measured['agreements']} uyum, {measured['contradictions']} celiski). "  # type: ignore[index]
                f"Olcum, varsayilan onceligin yerine gecti."
            ),
        })
    else:
        score = config.SOURCE_PRIOR.get(rep.source, 0.5)
        breakdown.append({
            "factor": "taban",
            "value": round(score, 3),
            "basis": "assumption",
            "why": (
                f"{rep.source} icin varsayilan oncelik. Bu bir VARSAYIMDIR, "
                f"veriden olculmedi. Oturumda {checks}/"
                f"{config.RELIABILITY_OVERRIDES_PRIOR_AFTER} kontrol var; "
                f"yeterli olcum birikince bunun yerine gececek."
            ),
        })

    # --- raporun kendi supheliligi ---
    if claim["unverified"]:
        score -= config.UNVERIFIED_PENALTY
        breakdown.append({
            "factor": "dogrulanmamis",
            "value": -config.UNVERIFIED_PENALTY,
            "basis": "text",
            "why": "Rapor kendi ifadesiyle dogrulanmamis ('ihbar', 'bildirildi').",
        })

    # --- 3. destekleme ---
    sup = find_supporting_reports(report_index)
    cross = int(sup.get("cross_source_count", 0))
    same = int(sup.get("same_source_count", 0))
    dups = len(sup.get("duplicates", []))  # type: ignore[arg-type]

    bonus = min(
        cross * config.CORROBORATION_BONUS_CROSS_SOURCE
        + same * config.CORROBORATION_BONUS_SAME_SOURCE,
        config.CORROBORATION_BONUS_CAP,
    )
    if bonus > 0:
        score += bonus
        breakdown.append({
            "factor": "destekleme",
            "value": round(bonus, 3),
            "basis": "corroboration",
            "why": (
                f"{cross} capraz-kaynak, {same} ayni-kaynak destek "
                f"(ust sinir {config.CORROBORATION_BONUS_CAP}). Capraz kaynak "
                f"bagimsiz dogrulamadir; ayni kaynagin tekrari degildir."
            ),
        })
    if dups:
        breakdown.append({
            "factor": "kopya",
            "value": 0.0,
            "basis": "corroboration",
            "why": (
                f"{dups} rapor birebir AYNI metni tekrarliyor. Bu bagimsiz "
                f"dogrulama degildir; skora katkisi yok."
            ),
        })

    score = max(0.0, min(1.0, score))
    band = "yuksek" if score >= 0.7 else "orta" if score >= 0.45 else "dusuk"

    advice = {
        "yuksek": "Bu rapor gerekcede dayanak olarak kullanilabilir, ama yine de "
                  "kendi tespitinle birlikte an.",
        "orta": "Tek basina gerekce yapma; kendi tespitinle destekle.",
        "dusuk": "Zayif. Yalnizca bu rapora dayanarak karar yukseltme.",
    }[band]
    if claim["friendly"]:
        advice += (" Rapor araci dost/teyitli unsur diyor; konum ve zaman "
                   "tutuyorsa dikkat gereksinimini dusurur.")

    return {
        "report_index": report_index,
        "time": rep.time,
        "source": rep.source,
        "text": rep.text,
        "credibility": round(score, 3),
        "band": band,
        "breakdown": breakdown,
        "supports": sup.get("supports", []),
        "duplicates": sup.get("duplicates", []),
        "advice": advice,
        "limits": (
            "Bu skor raporun DOGRU oldugunu olcmez. Veride hangi raporun dogru "
            "oldugu isaretli degil; skor yalnizca 'ne kadar agirlik verilmeli' "
            "sorusuna yapilandirilmis bir cevaptir. Celiski halinde hakem yine "
            "senin tespitindir."
        ),
    }
