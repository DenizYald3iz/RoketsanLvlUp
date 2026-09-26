"""Guven skorlarinin uretimi ve birlestirilmesi.

Iki problem cozuluyor:

1. ESIK UCURUMU. Tool'lar esik kullaniyor (40 m eslesme, 0.5 m/s durus,
   30 m ayni-nokta). Esigin 1 cm otesi ile 1 cm berisi taban tabana zit
   sonuc veriyor ama ayni kesinlikle sunuluyor. Cozum: esige UZAKLIGI
   (margin) olcup guvene cevirmek. Sinirda duran karar dusuk guven alir.

2. ZINCIR GUVENI. Bir cikarim birkac adimdan geciyor: tespit -> koordinat
   -> track eslesmesi -> hareket sinifi -> rapor yorumu. Her adimin kendi
   guveni var. Bunlari birlestirmenin TEK dogru yolu yok; bu modul iki
   modeli de hesaplayip hangisinin ne zaman gecerli oldugunu soyluyor.

Tum fonksiyonlar saf (pure): ayni girdi her zaman ayni cikti.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence


def margin_confidence(
    value: float,
    threshold: float,
    scale: Optional[float] = None,
    direction: str = "below",
) -> Dict[str, float]:
    """Bir degerin esige gore NE KADAR net oldugunu 0..1 arasinda verir.

    Esigin tam ustunde 0.5, uzaklastikca 0 ya da 1'e gider. Gecis keskin
    degil dogrusal: 'scale' kadar uzakta tam kesinlige ulasir.

    Args:
        value:     olculen deger (ornek: eslesme mesafesi 12 m).
        threshold: karar esigi (ornek: 40 m).
        scale:     tam kesinlige ulasma mesafesi. Verilmezse esigin yarisi.
        direction: "below" -> kucuk deger iyi (mesafe gibi).
                   "above" -> buyuk deger iyi (hiz gibi).

    Donen: {"confidence": 0..1, "margin": esige uzaklik, "at_cliff": bool}

    Ornek: 40 m esikte 12 m eslesme -> margin 28, confidence ~0.90 (net).
           40 m esikte 39 m eslesme -> margin 1,  confidence ~0.52 (sinirda).
    """
    if scale is None:
        scale = abs(threshold) / 2.0 or 1.0
    margin = (threshold - value) if direction == "below" else (value - threshold)
    # Dogrusal rampa: -scale -> 0.0, 0 -> 0.5, +scale -> 1.0
    conf = 0.5 + 0.5 * max(-1.0, min(1.0, margin / scale))
    return {
        "confidence": round(conf, 3),
        "margin": round(margin, 3),
        "at_cliff": abs(margin) < scale * 0.15,
    }


def combine_confidence(
    factors: Sequence[Dict[str, object]],
    strict: bool = True,
) -> Dict[str, object]:
    """Zincirdeki adim guvenlerini tek bir sayiya indirger.

    Neden tek bir formul yok: carpim ile en-zayif-halka farkli varsayimlar
    yapar ve ikisi de bazen dogrudur.

      * CARPIM — "her adim ayri ayri dogru olmali ve adimlar BAGIMSIZ".
        Kati kosullu zincir icin dogrusudur. Ama bagimli faktorleri
        carparsan ayni belirsizligi iki kez cezalandirirsin.
      * EN ZAYIF HALKA (min) — "sonucu tek bir darbogaz belirliyor".
        Faktorler birbiriyle iliskiliyse (ornek: eslesme belirsiz oldugu
        icin hareket profili de belirsiz) dogrusu budur.

    Bu yuzden faktorler "group" ile etiketlenebilir: AYNI gruptakiler
    birbirine bagli sayilir ve aralarinda min alinir; gruplar arasinda
    carpim uygulanir. Boylece ayni belirsizlik iki kez sayilmaz.

    Args:
        factors: [{"name": str, "confidence": 0..1, "group": str|None}, ...]
        strict:  True ise gruplar arasi carpim (kati zincir), False ise
                 tum faktorlerin min'i (en iyimser).

    Donen: {"confidence", "product", "weakest_link", "bottleneck",
            "groups", "note"}
    """
    clean: List[Dict[str, object]] = []
    for f in factors:
        if "confidence" not in f or f["confidence"] is None:
            continue
        c = float(f["confidence"])  # type: ignore[arg-type]
        if not 0.0 <= c <= 1.0:
            raise ValueError(
                f"confidence 0..1 olmali, gelen: {f.get('name')}={c}"
            )
        clean.append({
            "name": str(f.get("name", "?")),
            "confidence": c,
            "group": f.get("group") or str(f.get("name", "?")),
        })

    if not clean:
        return {
            "confidence": None, "product": None, "weakest_link": None,
            "bottleneck": None, "groups": {},
            "note": "Hicbir guven faktoru verilmedi; skor hesaplanamaz.",
        }

    # Ayni gruptaki faktorler bagimli -> aralarinda min
    groups: Dict[str, float] = {}
    for f in clean:
        g = str(f["group"])
        c = float(f["confidence"])  # type: ignore[arg-type]
        groups[g] = min(groups.get(g, 1.0), c)

    product = 1.0
    for c in groups.values():
        product *= c
    weakest = min(groups.values())
    bottleneck = min(clean, key=lambda f: (float(f["confidence"]), str(f["name"])))

    value = product if strict else weakest
    note = (
        f"{len(clean)} faktor, {len(groups)} bagimsiz grup. "
        f"Darbogaz: {bottleneck['name']} ({bottleneck['confidence']:.2f}). "
    )
    if len(groups) >= 4 and strict:
        note += (
            "Zincir uzun; carpim hizla dusuyor. Sonucun gercekten TUM "
            "adimlarin dogrulugunu gerektirip gerektirmedigini dusun — "
            "gerektirmiyorsa weakest_link daha dogru olabilir."
        )
    if weakest < 0.4:
        note += f" Zayif halka var ({bottleneck['name']}); once onu guclendir."

    return {
        "confidence": round(value, 3),
        "product": round(product, 3),
        "weakest_link": round(weakest, 3),
        "bottleneck": {"name": bottleneck["name"],
                       "confidence": round(float(bottleneck["confidence"]), 3)},  # type: ignore[arg-type]
        "groups": {k: round(v, 3) for k, v in sorted(groups.items())},
        "mode": "product" if strict else "weakest_link",
        "note": note.strip(),
    }
