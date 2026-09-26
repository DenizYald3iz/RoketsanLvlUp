import json

SYSTEM = """Sen bir üs güvenlik analistisin. Drone görüntülerini, araç hareket kayıtlarını ve saha raporlarını
birlikte değerlendirip hangi durumların dikkat gerektirdiğini, nedenleriyle ve dayandığın verilerle açıklarsın.

Kurallar:
- Aşama aşama ilerliyorsun. Şu anki aşama: {stage} ({idx}/{n}). Sadece bu aşamanın tool'larını kullan.
- Hesap yapma (mesafe, hız, koordinat); tool'lar hesaplar. Sayıları sadece tool çıktılarından al.
- Raporlar doğru, hatalı veya ilgisiz olabilir. Rapor tespitle/track'le çelişiyorsa raporu değil tespiti esas al.
- Aşama hedefi tamamlanınca tool çağırmadan kısa bir aşama özeti yaz; kod geçişi kontrol eder.

Aşama hedefi: {goal}

Şimdiye kadarki deliller (kısaltılmış):
{evidence}"""

TASK = "Görüntü: {image_id}. Aşama {stage} için gerekli tool'ları çağır."
UPLOAD = "Yeni drone görüntüsü yüklendi: {image_id} (ekte). get_image_info ile başla."

NUDGE = "Aşama {stage} henüz tamamlanmadı. Eksik: {missing}. İlgili tool'u çağır."


def evidence_digest(ev: dict, per_key: int = 4000) -> str:
    if not ev:
        return "(henüz yok)"
    out = []
    for k, v in ev.items():
        s = json.dumps(v, ensure_ascii=False, default=str)
        out.append(f"- {k}: {s[:per_key]}{' …' if len(s) > per_key else ''}")
    return "\n".join(out)
