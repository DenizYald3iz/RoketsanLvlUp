"""Goruntunun bir bolgesini kirpip GLM'e tekrar baktirma.

Tek tool: reinspect_crop.

Ne zaman kullanilir: 1. gun modeli bir seyden emin degilse (dusuk skor),
kutu cok kucukse, ya da saha raporu tespitle celisiyorsa (rapor "kamyon"
diyor model "otomobil" bulduysa). Kirpma, tam kareye gore cok daha az
token yakar ve kucuk nesnede cozunurluk kazandirir.

Butce notu: her cagri para harcar. Once ucuz tool'lari (geo, tracks,
reports) tuket; goruntuye ancak metinle cozulmeyen sorularda don.
"""
from __future__ import annotations

import base64
import io
import json
from typing import Dict, List, Optional

import config
import data_loader as dl
from tools.geo import geo_to_pixel, pixel_to_geo

_DEFAULT_PROMPT = (
    "Bu, bir insansiz hava aracindan alinmis kusbakisi goruntunun kirpilmis "
    "bir parcasidir. Yalnizca GORDUGUNU bildir, tahmin yurutme.\n"
    "Su sorulara cevap ver:\n"
    "1. Kac adet kara araci goruyorsun?\n"
    "2. Her biri icin tur: otomobil / panelvan / kamyon / otobus / belirsiz.\n"
    "3. Rengi ve ayirt edici ozelligi (romork, ortulu yuk, aciksa yuk) var mi?\n"
    "4. Arac park halinde mi, yolda mi duruyor?\n"
    "Emin olmadigin yerde 'belirsiz' yaz. JSON dondur: "
    '{"vehicles":[{"type":...,"color":...,"note":...}],"count":N,"confidence":0-1}'
)


def _client():
    """OpenAI uyumlu istemciyi tembel kurar (import maliyeti test'i yavaslatmasin)."""
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "openai paketi kurulu degil. Kur: pip install openai"
        ) from exc
    return OpenAI(
        base_url=config.GLM_BASE_URL,
        api_key=config.require_api_key(),
        max_retries=3,  # 429 ve 5xx'te SDK kendisi bekleyip tekrar dener
    )


def crop_image(
    image_id: str,
    bbox: Optional[List[float]] = None,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    pad_px: int = 48,
    min_size_px: int = 160,
    upscale_to_px: int = 512,
) -> Dict[str, object]:
    """Kirpmayi yapar ama GLM'e GONDERMEZ. Test ve on-izleme icin.

    bbox [x, y, w, h] ya da (lat, lon) ver. Ikisi de yoksa ValueError.
    Kucuk kutular min_size_px'e genisletilir, sonra upscale_to_px'e buyutulur:
    3 pikselik bir lekeye modelin soyleyecegi bir sey olmaz.

    Donen: {"image_b64", "crop_box", "width", "height", "geo_bounds"}
    """
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Pillow kurulu degil. Kur: pip install pillow") from exc

    ds = dl.load()
    meta = ds.image(image_id)
    path = ds.image_path(image_id)
    if not path.exists():
        raise FileNotFoundError(f"Goruntu bulunamadi: {path}")

    if bbox is not None:
        if len(bbox) != 4:
            raise ValueError(f"bbox [x, y, w, h] olmali, gelen: {bbox!r}")
        x, y, w, h = bbox
        cx, cy = x + w / 2.0, y + h / 2.0
        half_w, half_h = w / 2.0 + pad_px, h / 2.0 + pad_px
    elif lat is not None and lon is not None:
        px = geo_to_pixel(image_id, lat, lon)
        cx, cy = float(px["x"]), float(px["y"])
        half_w = half_h = float(min_size_px) / 2.0
    else:
        raise ValueError("bbox ya da (lat, lon) vermelisin.")

    half_w = max(half_w, min_size_px / 2.0)
    half_h = max(half_h, min_size_px / 2.0)

    left = int(max(0, cx - half_w))
    top = int(max(0, cy - half_h))
    right = int(min(meta.width_px, cx + half_w))
    bottom = int(min(meta.height_px, cy + half_h))
    if right <= left or bottom <= top:
        raise ValueError(
            f"Kirpma alani goruntu disinda kaldi: ({left},{top})-({right},{bottom}); "
            f"goruntu {meta.width_px}x{meta.height_px}"
        )

    with Image.open(path) as im:
        crop = im.convert("RGB").crop((left, top, right, bottom))
        # Kucuk kirpmayi buyut: model 512 px'lik bir karede daha iyi gorur.
        if max(crop.size) < upscale_to_px:
            scale = upscale_to_px / max(crop.size)
            crop = crop.resize(
                (int(crop.width * scale), int(crop.height * scale)), Image.LANCZOS
            )
        buf = io.BytesIO()
        crop.save(buf, format="JPEG", quality=88)
        raw = buf.getvalue()

    tl = pixel_to_geo(image_id, left, top)
    br = pixel_to_geo(image_id, right, bottom)
    return {
        "image_b64": base64.b64encode(raw).decode(),
        "crop_box": [left, top, right, bottom],
        "width": crop.width,
        "height": crop.height,
        "bytes": len(raw),
        "geo_bounds": {
            "top_left": {"lat": tl["lat"], "lon": tl["lon"]},
            "bottom_right": {"lat": br["lat"], "lon": br["lon"]},
        },
    }


def reinspect_crop(
    image_id: str,
    bbox: Optional[List[float]] = None,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    question: Optional[str] = None,
    pad_px: int = 48,
    max_tokens: int = config.DEFAULT_MAX_TOKENS,
) -> Dict[str, object]:
    """Goruntunun ilgili parcasini kirpip GLM'e tekrar baktirir.

    Args:
        image_id: goruntu kimligi.
        bbox:     [x, y, w, h] tespit kutusu. Yoksa lat/lon ver.
        lat, lon: bakilacak koordinat (bbox verilmediyse).
        question: ozel soru. Verilmezse standart arac sayim/tur sorusu sorulur.
        pad_px:   kutunun cevresine eklenen baglam payi.

    Donen: {"answer": str, "parsed": dict|None, "crop_box": [...], ...}

    Not: Bu, ilk gun tespit modelinin yerine gecmez; onun supheli buldugu
    yerlerde ikinci bir goz gorevi gorur. Celiski durumunda iki kaynagi da
    gerekcende ac.
    """
    crop = crop_image(image_id, bbox=bbox, lat=lat, lon=lon, pad_px=pad_px)
    prompt = question or _DEFAULT_PROMPT

    resp = _client().chat.completions.create(
        model=config.GLM_MODEL,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{crop['image_b64']}"
                    },
                },
            ],
        }],
        reasoning_effort=config.DEFAULT_REASONING_EFFORT,
        max_tokens=max_tokens,
    )

    msg = resp.choices[0].message
    answer = (msg.content or "").strip()
    if not answer:
        # PDF: max_tokens dusunmeyi de kapsar; bitmisse content bos doner.
        finish = resp.choices[0].finish_reason
        return {
            "image_id": image_id,
            "crop_box": crop["crop_box"],
            "answer": "",
            "parsed": None,
            "error": (
                f"Model bos cevap dondu (finish_reason={finish}). "
                f"max_tokens'i artir (su an {max_tokens})."
            ),
        }

    return {
        "image_id": image_id,
        "crop_box": crop["crop_box"],
        "crop_size": [crop["width"], crop["height"]],
        "geo_bounds": crop["geo_bounds"],
        "answer": answer,
        "parsed": _try_json(answer),
        "usage": getattr(resp, "usage", None) and {
            "prompt_tokens": resp.usage.prompt_tokens,
            "completion_tokens": resp.usage.completion_tokens,
        },
    }


def _try_json(text: str) -> Optional[dict]:
    """Model JSON'u ```json bloguna sarabilir; ilk gecerli nesneyi cikar."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```")[1]
        t = t[4:] if t.startswith("json") else t
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(t[start:end + 1])
    except json.JSONDecodeError:
        return None
