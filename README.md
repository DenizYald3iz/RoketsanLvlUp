# Stage 2 — Saha Raporu Destekli LLM Agent

Drone görüntüsünü, araç hareket kayıtlarını ve saha raporlarını **aşama aşama, tool çağırarak** değerlendiren
bir LangGraph agent'ı. Çıktı: görüntüde **hangi durumların dikkat gerektirdiği**, **nedeni** ve **dayandığı veri**.

LLM: yarışma gateway'i üzerinden **GLM-5.3-flash** (OpenAI uyumlu). Görev tanımı: [`docs/gorev_tanimi.txt`](docs/gorev_tanimi.txt)
(orijinali `data/stage2/gorev_tanimi.pdf`).

---

## İçindekiler
1. [Hızlı kurulum (5 dk)](#1-hızlı-kurulum-5-dk)
2. [Çalıştırma](#2-çalıştırma)
3. [Mimari: aşamalı agent](#3-mimari-aşamalı-agent)
4. [Tool listesi](#4-tool-listesi)
5. [Yeni tool yazma](#5-yeni-tool-yazma)
6. [Yeni aşama ekleme](#6-yeni-aşama-ekleme)
7. [Test](#7-test)
8. [Veri](#8-veri)
9. [Ayarlar (.env)](#9-ayarlar-env)
10. [GLM & bütçe notları](#10-glm--bütçe-notları)
11. [Sorun giderme](#11-sorun-giderme)
12. [Ekip çalışma düzeni](#12-ekip-çalışma-düzeni)
13. [Proje yapısı](#13-proje-yapısı)

---

## 1. Hızlı kurulum (5 dk)

**Gereken:** Python **3.10+** ve git. Veri (`data/stage2/`) ve API key (`.env`) repoda hazır, ayrıca bir şey indirmen gerekmiyor.

### macOS / Linux
```bash
git clone <REPO_URL> stage2-agent
cd stage2-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m scripts.doctor
```

### Windows (PowerShell)
```powershell
git clone <REPO_URL> stage2-agent
cd stage2-agent
py -3 -m venv .venv
.venv\Scripts\Activate.ps1        # hata verirse: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -r requirements.txt
python -m scripts.doctor
```

### (Opsiyonel) uv ile, çok daha hızlı
```bash
uv venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
uv pip install -r requirements.txt
python -m scripts.doctor
```

`scripts.doctor` her şeyi kontrol eder: Python sürümü, paketler, `.env`, veri, tool registry ve gateway bağlantısı.
Hepsi ✅ olmalı:
```
Stage-2 agent kurulum kontrolü
  ✅ Python: 3.11.x
  ✅ Paketler: ...
  ✅ .env: model=glm-5.3-flash, effort=low
  ✅ Veri: .../data/stage2 — 40 görüntü, 5650 track satırı, 137 rapor, 19878 kutu
  ✅ Tool registry: 11 tool: ...
  ✅ LLM gateway: spend=0.0023 / 15.0 USD
```

> Tüm komutları **repo kök klasöründen** ve **venv aktifken** çalıştır (`python -m ...` şeklinde).

---

## 2. Çalıştırma

```bash
python -m pytest -q                       # LLM'siz testler, bütçe harcamaz (~1 sn)
python -m scripts.check_budget            # harcanan / 15 USD
python -m scripts.run img_003839          # tek görüntü: aşama aşama trace + değerlendirme
python -m scripts.run                     # image_id vermezsen ilk görüntü
python -m scripts.run --all --workers 4   # 40 görüntü → outputs/<image_id>.json
```

**Süre ve maliyet (low effort):** Görüntü başına ~11 LLM çağrısı yapılıyor, ~2 dk sürüyor, maliyeti ~0.002 USD.
40 görüntü 4 paralelle ~20 dk sürer.

**Örnek trace (`scripts.run` çıktısı):**
```
[LOCATE] LLM: '' → get_image_info({}), get_detections({})
[LOCATE]   llm get_image_info ⇒ {...}
[LOCATE]   llm get_detections ⇒ {...}
[LOCATE] LLM: 'Aşama özeti ...'
[LOCATE] GATE pass
[TRACKS] LLM: '' → match_tracks({})
...
[ASSESS] LLM: ... → submit_assessment({"alerts": [...], "summary": "..."})
[ASSESS] GATE pass
```
`llm` satırlarında tool'u LLM çağırmış, `auto` satırlarında gate'in fallback'i çağırmış.
Gate durumları: `pass` (LLM tamamladı), `nudge` (eksik var, LLM uyarıldı), `fallback` (tur limiti doldu, kod tamamladı), `forced` (fallback'e rağmen eksik kaldı).

**Çıktı dosyası** `outputs/<image_id>.json`:
```json
{
  "image_id": "img_003839",
  "evidence": {
    "image_info": {...}, "detections": {...}, "matches": {...},
    "kinematics": {"T0187": {...}}, "reports": {...}, "report_checks": {"R012": {...}},
    "assessment": {
      "alerts": [{"level": "yuksek", "title": "...", "subject": "T0187",
                  "reason": "...", "evidence": ["T0187: 9 m/s, üsse 3.0 km, ETA 5.5 dk", "R012 uyumlu"]}],
      "summary": "...", "ignored_reports": ["R044"]
    }
  },
  "trace": [...]
}
```

---

## 3. Mimari: aşamalı agent

```
 START
   │
   ▼
┌─────────┐   ┌─────────┐   ┌─────────┐   ┌──────────┐   ┌─────────┐
│ LOCATE  │──▶│ TRACKS  │──▶│ MOTION  │──▶│ REPORTS  │──▶│ ASSESS  │──▶ END
└─────────┘   └─────────┘   └─────────┘   └──────────┘   └─────────┘
 her aşamanın içi:

   agent ──tool_calls──▶ tools ──▶ agent ... ──(tool çağırmadı / tur bitti)──▶ gate
                                                                             │
                     ┌───────────────────────────────────────────────────────┤
                     │ eksik yok            → sonraki aşama (aynı sohbet)     │
                     │ eksik + tur var      → "eksik: …" mesajı → agent       │
                     │ eksik + tur bitti    → fallback tool'ları kod çağırır  │
                     └────────────────────────────────────────────────────────┘
```

Temel kurallar:
- **Aşama başına tool seti:** LLM her aşamada sadece o aşamanın tool'larını görür. Başka aşamanın tool'unu çağırırsa hata mesajı alır.
- **Geçişi kod kontrol eder:** Her aşamanın `requires`/`check` koşulu `state.evidence` üzerinde kontrol edilir. LLM'in "bitti" demesi aşamayı geçirmeye yetmez.
- **Evidence:** Tool sonuçları `state.evidence[<writes>]` içine yazılır. Sonraki aşamalar ve tool'lar veriyi buradan okur.
  LLM'e sadece kısaltılmış bir kopya gider; yani LLM sayıları kendisi taşımaz ve uyduramaz.
- **Tek sohbet, tam history.** Aşamalar arası sohbet silinmez; her aşama "Aşama X: hedef" mesajıyla başlar ve LLM önceki tüm tool sonuçlarını ve kendi notlarını görür. Fallback'in çalıştırdığı tool sonuçları da sohbete mesaj olarak eklenir.
- **Fallback:** Tur limiti (`MAX_TURNS_PER_STAGE`, varsayılan 4) dolarsa aşamanın zorunlu tool'larını kod çağırır. Agent hiçbir zaman takılmaz ve sonsuz döngüde bütçe yakılmaz.
- **Trace:** Her LLM cevabı, tool çağrısı ve gate kararı `state.trace`'e kaydedilir (demo ve debug için).
- LOCATE aşamasında drone görüntüsü modele ilk mesajla birlikte gönderilir (`attach_image=True`).

| Aşama | Hedef | Gate koşulu | Fallback |
|---|---|---|---|
| LOCATE | Görüntü bilgisi + araç tespitleri (koordinatlı) | `image_info`, `detections` var | `get_image_info`, `get_detections` |
| TRACKS | Tespit ↔ track eşleştirme | `matches` var | `match_tracks` |
| MOTION | Eşleşen **her** track'in kinematiği | her eşleşen track için `kinematics[track_id]` | eksik track'ler için `get_track_kinematics` |
| REPORTS | İlgili raporlar + **her** raporun karşılaştırması | `reports` var ve her rapor için `report_checks[report_id]` | `find_reports`, sonra `compare_report` |
| ASSESS | Nihai karar | `assessment` var | boş alert listesiyle `submit_assessment` |

---

## 4. Tool listesi

Durum: ✅ çalışıyor · 🟡 iskelet (imza ve docstring hazır, gövde TODO; geçerli ama boş sonuç döner)

| Aşama | Tool | Ne yapar | Yazdığı evidence | Durum |
|---|---|---|---|---|
| hepsi | `zone_info(lat, lon)` | En yakın bölge, üsse mesafe ve yön | – | ✅ |
| LOCATE | `get_image_info()` | **İlk çağrı.** Genel bilgi (saat, kapsam, bölge, üsse mesafe) + araç tespitleri (tip, lat/lon) | `image_info`, `detections` | ✅ |
| LOCATE | `get_detections(min_conf=0.3)` | Detector'dan tespitleri alır, çakışan kutuları birleştirir (NMS), kutu merkezlerini lat/lon'a çevirir, `{det_id,label,conf,cx,cy,w,h,lat,lon}` | `detections` | ✅ |
| LOCATE, MOTION | `view_image(crop_x,crop_y,crop_w,crop_h)` | Görüntüyü (veya bir kırpımını) modele gösterir | – | ✅ |
| TRACKS | `match_tracks(max_dist_m=20)` | `time == capture_time` noktalarıyla en yakın 1-1 eşleşme + eşleşmeyenler | `matches` | ✅ |
| TRACKS | `list_tracks_near(radius_m=1000)` | Çekim anında çerçeve dışında kalan yakın track'ler | `nearby_tracks` | ✅ |
| MOTION | `get_track_kinematics(track_id)` | 2 saatlik kayıttan hız, yön, üsse mesafe, yaklaşma hızı, **ETA**, durma, dolaşma | `kinematics[track_id]` | ✅ |
| MOTION | `get_track_points(track_id, last_n=25)` | Ham noktalar (time, lat, lon, base_dist_m) | – | ✅ |
| REPORTS | `find_reports(radius_m=300, window_min=120)` | Çekimden önceki `window_min` dk içinde, koordinatı çerçeveye `radius_m` yakın ya da bölge adı görüntünün bölgesi olan raporlar; konumsuz duyurular ayrı `general` listesinde | `reports` | ✅ |
| REPORTS | `compare_report(report_id)` | Rapor iddiası (tip, sayı, durma/hareket/üsse yaklaşma, dost beyanı) ↔ tespitler + çekim anındaki track'ler, kural tabanlı: `consistent / contradicts / unverifiable / irrelevant` | `report_checks[report_id]` | ✅ |
| ASSESS | `submit_assessment(alerts, summary, ignored_reports)` | Nihai yapılandırılmış çıktı (Pydantic `Alert` şeması) | `assessment` | ✅ |

**Dönüş şemaları** her tool'un docstring'inde yazıyor (`s2agent/tools/*.py`). Gate'ler şu alanlara güvenir, **isimlerini değiştirme**:
- `matches.matches[].track_id` → MOTION gate
- `reports.reports[].report_id` → REPORTS gate

**Görev tanımından gelen kurallar (tool'ları yazarken):**
- Piksel → koordinat: `s2agent/geo.py::pixel_to_latlon` (PDF formülü). Aracın konumu için kutu merkezini `(cx, cy)` kullan.
- Track'in **son noktası** görüntünün çekim anına denk gelir. Eşleştirmede `time == capture_time` satırlarına bak.
- Birebir eşitlik arama. Makul bir eşik içinde en yakın noktayı seç (tespit hatası birkaç metre olabilir).
- Park halindeki araçların kaydı olmayabilir. Kaydı olan bir araç da çekim anında görüntü dışında kalmış olabilir.
- Hız ve yönü tek bir adımdan değil, kaydın tamamından oku. Araçlar dönüş yapar, durur, üs çevresinde dolaşır.
- Raporların bir kısmı hatalı veya ilgisiz. **Rapor tespitle çelişiyorsa tespit esas alınır.**
- `pred_all_boxes.csv` çoğunlukla düşük conf'lu gürültü (medyan conf 0.02). **conf ≥ 0.3** ile görüntü başına ~7 kutu kalıyor
  ve çerçevedeki track'lerin %99'unun 20 m yakınında bir tespit oluyor (0.5 ile %96). Önerilen: `min_conf=0.3`, `max_dist_m=20`.

---

### Detector backend (CSV ↔ GPU)
`get_detections` tespitleri `s2agent/detector.py::get_detector()` üzerinden alır. Format, yarışmanın submission formatıyla aynıdır:
```
image_id,PredictionString
img_000001,car 0.93 976 533 98 95 van 0.71 1012 276 150 76     # label conf x y w h ... (x,y = sol üst köşe, px)
img_000002,none                                                 # hiç araç yoksa
```
| `.env` | Kaynak |
|---|---|
| `DETECTOR=csv` (varsayılan) | `PRED_FILE` = `data/stage2/pred_all_boxes_submission.csv` |
| `DETECTOR=http` + `DETECTOR_URL=...` | GPU sunucusu: `POST` multipart (`file`, `image_id`) → `{"PredictionString": "car 0.93 976 533 98 95 ..."}` |

Model aynı kutuya farklı etiketler verebilir (örn. `van 0.60`, `truck 0.32` aynı kutu). `get_detections` çakışan
kutuları birleştirir (NMS, IoU ≥ 0.5) ve en yüksek conf'lu sınıfı alır.

## 5. Yeni tool yazma

`s2agent/tools/` içine bir `.py` dosyası bırak, **otomatik yüklenir**. Başka bir yere kayıt eklemen gerekmiyor.

```python
# s2agent/tools/speed_tools.py
from ..geo import haversine_m
from ..registry import ToolContext, stage_tool


@stage_tool("MOTION", writes="kinematics", key_by="track_id")
def get_track_kinematics(track_id: str, *, ctx: ToolContext) -> dict:
    """Bir track'in 2 saatlik kaydından hız/yön/üsse yaklaşma/ETA özeti.
    Dönüş: {avg_speed_mps, closing_speed_mps, eta_min, ...}"""
    df = ctx.data.tracks
    pts = df[df.track_id == track_id].sort_values("time")
    if pts.empty:
        return {"error": f"track bulunamadı: {track_id}"}
    base = ctx.data.zones["base"]
    ...
    return {"track_id": track_id, "avg_speed_mps": 8.7, "eta_min": 5.5}
```

**`@stage_tool(*stages, writes=None, key_by=None)`**
| Parametre | Anlamı |
|---|---|
| `stages` | Tool'un görüneceği aşamalar (`"LOCATE"`, `"MOTION"`, ...). `"*"` = tüm aşamalar |
| `writes` | Sonucun yazılacağı evidence anahtarı. `None` = sadece LLM'e döner, kaydedilmez |
| `key_by` | Aynı tool'u farklı argümanla çok kez çağırmak için: `evidence[writes][args[key_by]] = sonuç` |

**`ctx: ToolContext`** (her zaman keyword-only, otomatik enjekte edilir):
| Alan | İçerik |
|---|---|
| `ctx.image_id` | İşlenen görüntü |
| `ctx.evidence` | Önceki tool'ların sonuçları (sadece oku) |
| `ctx.data.meta` | `image_meta.json` (`dict`) |
| `ctx.data.zones` | `zones.json` → `{"base": {name,lat,lon}, "zones": [{name, center:[lat,lon]}]}` |
| `ctx.data.tracks` | `tracks.csv` (`DataFrame`: track_id, time "HH:MM", lat, lon) |
| `ctx.data.reports` | `field_reports.json` + `report_id` (`R000`...) |
| `ctx.data.boxes` | `pred_all_boxes.csv` (`DataFrame`) |
| `ctx.data.image_path(id)` | Görüntü dosyasının yolu |

**Kurallar:**
1. **Docstring LLM'in tool açıklamasıdır.** Ne yaptığını, ne döndürdüğünü ve ne zaman çağrılacağını Türkçe ve kısa yaz.
2. `ctx` dışındaki parametreler **type-hint'li** olmalı (JSON şeması bunlardan üretilir). Opsiyonel olanlara default ver.
3. Sonuç **kompakt ve JSON'lanabilir bir `dict`** olmalı. Büyük tablo döndürme; LLM'e giden metin `MAX_TOOL_CHARS` karakterde kesilir.
4. Beklenen hatalarda exception fırlatma, `{"error": "..."}` döndür. (Beklenmeyen exception'lar da yakalanıp LLM'e hata olarak gider.)
5. LLM'e görüntü göstermek için sonuca `"_image": data_url` ekle (`geo.image_data_url`).
6. Matematik (mesafe, açı, dönüşüm) `s2agent/geo.py` içinde olsun, prompt'a hesap yaptırma.
7. Yeni tool'u **fake LLM ile test et** (bkz. [Test](#7-test)), sonra tek görüntüde gerçek LLM ile dene.

Bir tool'u LLM olmadan, tek başına denemek için:
```python
from s2agent.graph import build_graph  # tool'ları kaydeder
from s2agent.registry import run_tool
r = run_tool("get_detections", {"min_conf": 0.5}, stage="LOCATE", image_id="img_003839", evidence={})
print(r.result)
```

---

## 6. Yeni aşama ekleme

`s2agent/stages.py` içindeki `STAGES` listesine istediğin sıraya ekle:
```python
Stage(
    "WEATHER",                                   # ad; tool'larda @stage_tool("WEATHER")
    "Görüş koşullarını değerlendir ...",         # LLM'e verilen hedef
    requires=["weather"],                        # evidence'ta olması gereken anahtarlar
    check=lambda ev: [],                         # ek koşul → eksik öğelerin listesi
    fallback=lambda ev: [("get_weather", {})],   # tur biterse kodun çağıracağı tool'lar
    attach_image=False,                          # aşamanın ilk mesajına görüntü eklensin mi
)
```

---

## 7. Test

```bash
python -m pytest -q
```
Testler `tests/fake_llm.py::ScriptedLLM` kullanır; gerçek API'ye gitmez, **bütçe harcamaz**. Bu LLM önceden yazılmış bir
senaryoyu oynatır:
```python
from langchain_core.messages import AIMessage
from tests.fake_llm import ScriptedLLM, call
llm = ScriptedLLM([call("get_image_info"), call("get_detections"), AIMessage("tamam"), ...])
out = run_image("img_003839", graph=build_graph(llm, max_turns=4))
```
Mevcut testler şunları kapsıyor: her aşamanın tool'u var; aşama dışı tool reddediliyor; hiç tool çağırmayan bir LLM'le de
fallback üzerinden sona ulaşılıyor; düzgün bir LLM gate'leri fallback'siz geçiyor; `key_by` ile yazılan evidence birleşiyor.

---

## 8. Veri

`data/stage2/` (yarışmanın orijinal dosyaları, **değiştirme**):
| Dosya | İçerik |
|---|---|
| `images/` | 40 drone görüntüsü (`img_XXXXXX.jpg`) |
| `image_meta.json` | `image_id → {width_px, height_px, capture_time "HH:MM", corner_coordinates{top_left,top_right,bottom_left,bottom_right: [lat, lon]}}` |
| `zones.json` | Merkez üs + 8 bölge (ad, merkez koordinat). Raporlardaki bölge adları buradan eşlenir |
| `tracks.csv` | `track_id,time,lat,lon`. 226 track × 25 nokta (son 2 saat, 5 dk arayla). Her track bir görüntünün çekim saatinde biter |
| `field_reports.json` | 137 serbest metin rapor: `{time, source: official/third_party, text}`. Konum metinde koordinat (`39.9374N 32.8483E`) veya bölge adı olarak geçer |
| `pred_all_boxes_submission.csv` | 1. gün tespit modelinin çıktısı, submission formatında (`image_id,PredictionString`). Agent bunu kullanır |
| `pred_all_boxes.csv` | Aynı tahminlerin eski, geniş formatı (`p_*` olasılıklarıyla). Sadece analiz için |
| `gorev_tanimi.pdf` | Görev tanımı |

Rapor türleri (gözlem): tespit iddiası ("5 kamyon durdu"), dost unsur beyanı ("planlı ikmal aracıdır, kimlik
teyit edildi"; dikkat kararını etkiler ama tespitle çelişirse yok sayılır), gürültü (hava durumu, "dün gece doğrulanmamış ihbar").

---

## 9. Ayarlar (`.env`)

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `LLM_BASE_URL` | gateway `/v1` | OpenAI uyumlu endpoint |
| `LLM_API_KEY` | takım key'i | `sk-...` |
| `LLM_MODEL` | `glm-5.3-flash` | Başka model desteklenmiyor |
| `LLM_REASONING_EFFORT` | `low` | `low` / `high` / `max` (yüksek = yavaş ve pahalı) |
| `DATA_DIR` | `data/stage2` | Repo köküne göre göreli yol |
| `MAX_SPEND` | `12` | Harcama bunu aşarsa `scripts.run` başlamaz (USD) |
| `MAX_TURNS_PER_STAGE` | `4` | Aşama başına en fazla LLM çağrısı; dolarsa fallback devreye girer |
| `RECURSION_LIMIT` | `80` | LangGraph'ın toplam adım sınırı |
| `MAX_TOOL_CHARS` | `6000` | LLM'e giden tool sonucunun karakter sınırı |
| `DETECTOR` | `csv` | `csv` ya da `http` (bkz. Detector backend) |
| `PRED_FILE` | `data/stage2/pred_all_boxes_submission.csv` | `DETECTOR=csv` için tahmin dosyası |
| `DETECTOR_URL` | – | `DETECTOR=http` için GPU sunucusunun adresi |
| `IMAGE_MAX_SIDE` | `1280` | Modele gönderilen görüntünün uzun kenarı (px). Token sayısı görüntü boyutuyla artar |

Kişisel denemeler için `.env`'i commit'lemeden ortam değişkeniyle ez:
`LLM_REASONING_EFFORT=high python -m scripts.run img_003839`

---

## 10. GLM & bütçe notları

- **Bütçe:** Takımın toplamı **15 USD**, sıfırlanmıyor ve key'i herkes ortak kullanıyor. Büyük bir iş başlatmadan önce
  `python -m scripts.check_budget` ile harcamaya bak. `--all` komutunu gereksiz yere tekrar tekrar çalıştırma.
- **Limitler:** 60 istek/dk, 500K token/dk, **aynı anda en fazla 4 istek**. Toplam paralel iş sayısı 4'ü geçerse 429 hatası alınır
  (SDK otomatik tekrar dener, `max_retries=5`). **Ekipçe aynı anda `--all` çalıştırmayın.**
- Model her zaman önce **düşünür** (`reasoning_content`). `max_tokens`'ı küçük verme; verirsen `content` boş gelir.
- `thinking` parametresi **gönderilmez** (hata verir). Onun yerine `reasoning_effort` kullan.
- Görüntü token'ları: 960×540 yaklaşık 700, 1920×1080 yaklaşık 2.700 token. `view_image` pahalı, gerektiğinde kullanılmalı.
- Embeddings, görüntü üretme ve ses **desteklenmiyor**.

---

## 11. Sorun giderme

| Belirti | Çözüm |
|---|---|
| `ModuleNotFoundError: s2agent` | Repo kökünde değilsin ya da `python scripts/run.py` yazdın. `python -m scripts.run` kullan |
| `ModuleNotFoundError: langgraph` vb. | venv aktif değil: `source .venv/bin/activate` (Win: `.venv\Scripts\Activate.ps1`) |
| `FileNotFoundError: .../image_meta.json` | `DATA_DIR` yanlış. Varsayılan `data/stage2`, `python -m scripts.doctor` ile kontrol et |
| `401` | `.env`'deki `LLM_API_KEY` yanlış kopyalanmış |
| `429` | Aynı anda 4'ten fazla istek var (ekipte başka biri de çalıştırıyor olabilir). `--workers 2` kullan ya da bekle |
| `400 Budget has been exceeded` | 15 USD bitti, organizatörlere yaz |
| `400 key not allowed to access model` | `LLM_MODEL` tam olarak `glm-5.3-flash` olmalı |
| `content` boş, `finish_reason: length` | `max_tokens` verme ya da 1000+ ver |
| Trace'te sürekli `nudge` → `fallback` | LLM tool'u çağırmıyor. Tool docstring'ini ve `stages.py`'deki `goal` metnini netleştir |
| `Recursion limit reached` | `RECURSION_LIMIT` değerini artır ya da `MAX_TURNS_PER_STAGE` değerini düşür |
| Windows'ta `Activate.ps1` çalışmıyor | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |

---

## 12. Ekip çalışma düzeni

- `main` her zaman çalışır durumda kalsın. İş için branch aç: `git checkout -b tool/match-tracks`
- **Bir tool = bir dosya ya da bir fonksiyon.** Aynı dosyada iki kişi çalışacaksanız önceden haber verin.
- PR'dan önce: `python -m pytest -q` yeşil olmalı ve `python -m scripts.run img_003839` uçtan uca çalışmalı.
- Gate'lerin kullandığı alan adlarını (`matches[].track_id`, `reports[].report_id`) değiştirecekseniz `stages.py`'yi de güncelleyin.
- `outputs/` git'e girmez. Paylaşmak istediğiniz bir sonucu ayrıca gönderin.

---

## 13. Proje yapısı

```
stage2-agent/
├── .env                  # gateway + key (takım içi)
├── .env.example
├── requirements.txt
├── data/stage2/          # yarışma verisi (salt okunur)
├── docs/gorev_tanimi.txt # görev tanımının metin hali
├── s2agent/
│   ├── config.py         # .env → CFG
│   ├── data.py           # veri yükleme (cache'li): meta, zones, tracks, reports, boxes
│   ├── geo.py            # pixel→latlon, haversine, bearing, görüntü → data URL
│   ├── detector.py       # tespit kaynağı: CSV ya da GPU (HTTP)
│   ├── llm.py            # ChatOpenAI → GLM gateway
│   ├── budget.py         # /key/info, harcama koruması
│   ├── registry.py       # @stage_tool, ToolContext, run_tool
│   ├── stages.py         # STAGES: hedef, gate, fallback
│   ├── prompts.py        # system / nudge prompt'ları
│   ├── graph.py          # LangGraph: agent ⇄ tools → gate
│   └── tools/            # ← tool'lar burada (otomatik yüklenir)
│       ├── common.py     # zone_info
│       ├── locate.py     # get_image_info, get_detections, view_image
│       ├── tracks.py     # match_tracks, list_tracks_near
│       ├── motion.py     # get_track_kinematics, get_track_points
│       ├── reports.py    # find_reports, compare_report
│       └── assess.py     # submit_assessment + Alert şeması
├── scripts/
│   ├── doctor.py         # kurulum kontrolü
│   ├── run.py            # tek görüntü / --all
│   └── check_budget.py
└── tests/
    ├── fake_llm.py       # ScriptedLLM (bütçesiz test)
    ├── test_graph.py
    └── test_detections.py # CSV/HTTP detector aynı çıktıyı veriyor mu
```
