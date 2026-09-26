# Aşama 2 — Saha Raporu Destekli LLM Agent (tool katmanı)

Bu depo agent'ın **tool katmanını** içerir. LangGraph pipeline'ı `pipeline/`
altında, başka bir yerde yazılacak. Buradaki sözleşme:

```python
from schemas.tool_specs import TOOL_SPECS, TOOL_REGISTRY, dispatch, TERMINAL_TOOLS

resp = client.chat.completions.create(model=..., messages=..., tools=TOOL_SPECS)
for call in resp.choices[0].message.tool_calls:
    result = dispatch(call.function.name, json.loads(call.function.arguments))
    # dispatch asla exception fırlatmaz; hata da {"error": ...} olarak döner,
    # böylece modele geri yazılıp düzeltilmesi istenebilir.
    if call.function.name in TERMINAL_TOOLS:
        done = True
```

## Kurulum

```bash
pip install openai pillow pytest
export GLM_API_KEY="sk-..."        # yalnızca reinspect_crop için gerekli
python -m pytest tests/ -q         # 130 test
python scratch.py                  # veri özeti
python scratch.py walk img_003839  # bir görüntüyü uçtan uca gez (LLM çağrısı yok)
```

## Tool'lar

19 tool, altı modülde. Üç istisna dışında hepsi **saf fonksiyon**: aynı girdi
her zaman aynı çıktıyı verir, yan etkisi yoktur.

- `update_source_reliability` ve `submit_assessment` oturum durumuna **yazar**
- `reinspect_crop` model çağırır, yani **deterministik değil** ve para harcar

`assess_report_credibility` ve `get_source_reliability` state'i okur ama
değiştirmez. Tablodaki **State** sütunu hepsini gösterir.

| Tool | Neden var | Takas | State |
|---|---|---|---|
| `image_footprint` | Karenin nereye baktığını ve yer ölçeğini verir; sonraki her sorgunun çerçevesi bu | `meters_per_pixel` ölçek verir ama **araç türü vermez** — kutu boyutu sınıfı ayırmıyor | — |
| `pixel_to_geo` | Tespit pikselini WGS84'e çevirir; tüm zincirin temeli | Kuşbakışı + perspektif düzeltilmiş varsayar; eğik çekimde geçersiz | — |
| `detection_to_geo` | Kutu merkezini kendisi hesaplar, köşe gönderme hatasını engeller | `pixel_to_geo` ile aynı işi yapar; ayrı olması bir hatayı kapatmak için | — |
| `haversine_distance` | Tek bir doğru mesafe tanımı; ayrı ayrı uydurulmasını engeller | Eşdikdörtgen izdüşüm kullanır: 2× hızlı, max 1.3 cm hata. İsim tarihsel | — |
| `nearest_zone` | Koordinatı insan diline çevirir (bölge + üsse mesafe/yön) | Bölge merkezleri nokta; sınır yok, "içinde mi" sorusuna cevap veremez | — |
| `resolve_zone_name` | Rapordaki bölge adını koordinata bağlar | Substring eşleşmesi kullanır; benzer adlar çakışabilir | — |
| `find_candidate_tracks` | Tespiti hareket kaydına bağlar — görüntüyü zamana açan tek köprü | Yarıçap dar (5 m): gerçek eşleşmeleri alır, park halindeki araçları "eşleşme yok" bırakır. Geniş tutmak yanlış araca bağlar | — |
| `get_motion_profile` | Hız/yön/üsse davranış — "dikkat gerekir mi" sorusunun asıl dayanağı | Eşikler (0.5 m/s, ±150 m) kalibre **edilmedi**, makul seçimler. Ham değerler de döner ki model kendi yorumlayabilsin | — |
| `tracks_in_image` | "Bu karede kaç araç olmalı" — tespit sayısıyla kıyas | Park halindeki araç kaydı olmadığı için eksik sayar | — |
| `query_reports` | Rapor havuzunu konum+zamana göre süzer | 400 m yarıçap gevşek görünür ama gerekli: kareler 150×84 m, rapor koordinatı yuvarlanınca dışarı düşer. 40 görüntünün 20'sinde boş döner | — |
| `check_report_consistency` | Raporu **kendi bulgunla** karşılaştırır: konum, sayı, hareket | Hakemlik yapmaz, yapamaz. Araç türü ekseni kaldırıldı (her kare her türü içeriyor) | — |
| `cross_report_contradiction_check` | Aynı noktaya dair çelişen iddiaları yan yana koyar | 40 görüntüde 3 kez ateşliyor — düşük frekans, yüksek değer. Hangisinin doğru olduğunu söyleyemez | — |
| `find_supporting_reports` | Bağımsız doğrulamayı tekrardan ayırır (çapraz kaynak / aynı kaynak / aynı metin) | 29 raporun 2'sinde ateşliyor. Bu veri seti için pahalı bir mekanizma | — |
| `assess_report_credibility` | Rapora ne kadar ağırlık verileceğini **kırılımıyla** verir | Taban (official 0.65 / third_party 0.45) bir **varsayım**, ölçüm değil | **okur** `reliability` |
| `update_source_reliability` | Kaynak skorunu kendi tespitlerinle günceller; öğrenme buradan gelir | **Yol bağımlı**: görüntü sırası ara kararları değiştirir | **yazar** `reliability` |
| `get_source_reliability` | Birikmiş skorları okur | 3 kontrolden az veri varsa `unknown` döner | **okur** `reliability` |
| `reinspect_crop` | Metinle çözülmeyen soruları görüntüye sorar | **Tek para harcayan tool.** Yavaş (~10-15 sn) ve deterministik değil | — |
| `combine_confidence` | Zincir adımlarının güvenini tek sayıya indirger | Çarpım mı en-zayıf-halka mı — tek doğru yok, ikisini de döner | — |
| `submit_assessment` | Kararı kaydeder ve döngüyü kapatır | Kanıtsız `attention`/`watch` reddedilir; model düzeltip tekrar çağırmak zorunda | **yazar** `assessments`, `images`, `processing_order` |

Önerilen sıra: `image_footprint` → tespitleri `detection_to_geo` →
`find_candidate_tracks` → `get_motion_profile` → `query_reports` →
`check_report_consistency` → (gerekirse) `cross_report_contradiction_check` /
`reinspect_crop` → `combine_confidence` → `submit_assessment`.

Tespitler `detections.py` üzerinden gelir:

```python
import detections as D
for d in D.get_detections("img_003839", min_score=0.30):
    geo = detection_to_geo("img_003839", d.bbox)
```

### Maliyet ve döngü kontrolü

```python
from schemas.tool_specs import COSTLY_TOOLS, TERMINAL_TOOLS
COSTLY_TOOLS   # {"reinspect_crop"}      — bütçe sayacında ayrı tut
TERMINAL_TOOLS # {"submit_assessment"}   — görülünce döngü durur
```

## State

### Aktif state — gerçekten yazılan ve okunan

Tek bir `SessionState` tüm günü taşır. Üç alanı canlıdır:

| Alan | Kim yazar | Kim okur | Ne işe yarar |
|---|---|---|---|
| `reliability` | `update_source_reliability` | `get_source_reliability`, `assess_report_credibility` | Kaynak güvenilirliği 40 görüntü boyunca **birikir**. 5. görüntüde öğrenilen 30.'da kullanılır. 3 kontrolden sonra varsayılan önceliğin yerine geçer. |
| `assessments` + `images[id].assessment` | `submit_assessment` | `get_assessments` | Nihai kararlar. Görüntü başına tek kayıt; ikinci çağrı öncekini değiştirir. |
| `processing_order` | `image_state()` (otomatik) | izleme | Hangi görüntünün kaçıncı işlendiği. Yol bağımlılığını yeniden üretilebilir kılar. |

`submit_assessment` her karara iki iz bırakır:

```python
assessment["reliability_at_decision"]    # o anki skorlar
assessment["images_processed_before"]    # kaçıncı görüntüydü
```

Bu ikisi olmadan "neden böyle dedi" sorusu sonradan cevaplanamaz, çünkü
aynı görüntü farklı sırada farklı skorlarla değerlendirilir.

### Yol bağımlılığı

`reliability` biriktiği için **görüntü sırası ara kararları etkiler.**
Nihai skor sıra bağımsızdır (Laplace oranı komütatif), ama 20. görüntüde
alınan karar o ana kadarki skora bakar.

Bu bir hata değil, öğrenen sistemin doğası. Ama tekrar üretilebilirlik için
**sabit bir sıra kullanın** — çekim saatine göre:

```python
for image_id, meta in sorted(ds.images.items(), key=lambda kv: kv[1].capture_time):
    ...
```

### Oturum izolasyonu

State'e yazan tüm fonksiyonlar `session=` parametresi alır. Verilmezse
global oturum kullanılır. Testler ve paralel koşular kendi `SessionState()`
nesnesini geçirmeli:

```python
from schemas.state import SessionState, reset_session
st = SessionState()                      # izole
update_source_reliability("official", "agreed", session=st)
reset_session()                          # globali sıfırla
```

### Atıl state — bildirilmiş ama tool'lar doldurmuyor

Dürüst olmak gerekirse `ImageState`'in 12 alanından **10'una hiçbir tool
dokunmuyor**:

| Alan | Durum |
|---|---|
| `assessment`, `capture_time` | `submit_assessment` yazar — **canlı** |
| `detections`, `geo_points`, `matched_tracks`, `motion`, `reports`, `consistency`, `contradictions`, `reinspections`, `tool_calls` | **hiç yazılmıyor** |

Bunlar pipeline'ın ara sonuçları biriktirmesi için ayrılmış yerler. LangGraph
tarafı kullanacaksa doldurmalı; kullanmayacaksa silinebilir — tool katmanı
onlara ihtiyaç duymuyor.

Aynı şekilde `schemas/state.py`'deki TypedDict'ler (`GeoPoint`,
`TrackCandidate`, `ReportClaim`, `MatchedReport`, `ConsistencyCheck`,
`Contradiction`, `SourceReliability`, `Assessment`) **tip belgesi olarak**
duruyor; tool'lar düz `dict` döndürüyor ve bu tipleri çalışma zamanında
kullanmıyor. Pipeline isterse bunlarla tip denetimi yapabilir.

### Durumsuz olan ne

Kalan 16 tool **hiçbir state'e dokunmuyor**. Veri (`data_loader`,
`detections`) salt okunur ve süreç ömrü boyunca önbellekte. Bu sayede:

- tool'lar herhangi bir sırada, tekrar tekrar çağrılabilir
- paralel çağrı güvenlidir
- aynı girdi her zaman aynı çıktıyı verir (3 farklı hash seed'de bit-birebir
  doğrulandı)

Tek deterministik olmayan tool `reinspect_crop` — model çağrısı içerir.

## Gerçek veriyle doğrulama

`pred_all_boxes_submission.csv` (1. gün modelinin çıktısı) ile uçtan uca
ölçüldü: **206 zemin-gerçeği track'inin 200'ü geri bulundu (recall 0.97)**.
Tespit merkezleri track noktalarına medyan **0.26 m** uzaklıkta düşüyor.

Bu ölçüm iki parametreyi düzeltti:

**Eşleşme yarıçapı 40 m → 5 m.** Eşleşme mesafesi çift tepeli: %75'i ≤2 m
(gerçek eşleşmeler), kalan %21'i 16-88 m'ye yayılıyor (track'i olmayan park
halindeki araçlar). 40 m'de tespitlerin %20'si *yanlış* araca bağlanıyordu.
Kareler medyan 186 m genişliğinde — 40 m tüm karenin beşte biriydi.

**Belirsizlik testine mutlak eşik eklendi.** Oran testi (`2. ≤ 1.5 × 1.`)
ölçekten bağımsız: gerçek eşleşme 0.2 m'deyken 0.3 m'deki ikinci aday "iki
kat uzak" sayılıp bayrak yanmıyordu. Artık 3 m içindeki ikinci aday, oran ne
olursa olsun belirsizlik sayılıyor.

## Tasarım kararları

**Raporlar kanıt değil iddiadır.** Hiçbir rapor peşinen doğru sayılmaz.
`check_report_consistency` uyuşan ve çelişen noktaları *ayrı ayrı* döner;
çelişki varsa rapor değil tespit esas alınır. Görev tanımı bunu açıkça
istiyor.

**Rapor türü ayrımı.** 137 raporun hepsi aynı ağırlıkta değil. Ayrıştırıcı
her raporu sınıflar: `sighting` (32, somut gözlem) · `rumor` (32,
doğrulanmamış ihbar) · `all_clear` (25) · `environment` (22, hava/lojistik —
filtrelenir) · `friendly_id` (18, bize bağlı araç) · `comms_loss` (6, o
bölgede gözlem boşluğu) · `density_anomaly` (2).

**Sayı tuzakları.** "bir saatten uzun süredir" ifadesindeki *bir* araç sayısı
değildir; "olağan trafik 4 araç civarıdır" taban çizgisidir, gözlem değil.
İkisi de ayrı alanlarda tutulur (`baseline_count`), aksi halde agent
raporların yarısında yanlış sayı okur.

**Raporlar arası çelişki: dar kapsam, hakemlik yok.** İlk versiyon "250 m
içindeki iki rapor aynı olayı anlatır" varsayımıyla 38 çelişki üretiyordu.
Bu varsayım veride desteklenmiyor — birkaç km'lik bölgede 250 m birden fazla
aracı kapsıyor. Yarıçapı **koordinat hassasiyetine** indirdim (30 m; raporlar
4-5 ondalık yazıyor, 4 ondalık ≈ 11 m): 8 çelişki kaldı, hepsi aynı noktaya
dair. Ayrıca zaman sınırı var — araç türü/sayı çelişkisi ≤30 dk, hareket
çelişkisi ≤15 dk. Daha uzun aralıkta araç gerçekten değişmiş olabilir.

**Neden hakem yok.** Tool hangi raporun doğru olduğunu söyleyemiyor, çünkü
(a) veride bu işaretli değil, (b) hareket kayıtları da hakem olamıyor: her
track bir görüntünün çekim saatinde biten 2 saatlik penceredir, rastgele bir
rapor saatinde eldeki kayıt havuzu keyfi bir alt kümedir, (c) park halindeki
aracın kaydı hiç bulunmayabilir. Ölçtüm: rapor sayım iddialarının track'lerle
tutarlılığı yarıçaptan bağımsız olarak %30-42 arasında geziniyor — sinyal yok.
Tool bunu gizlemek yerine `why_it_conflicts` ve `note` alanlarında açıkça
söylüyor ve ham metinleri yan yana koyuyor; karar modelin.

**Ağırlıklandırma var, ama gizli katsayı olarak değil.**
`check_report_consistency` içindeki `weight` (third_party ×0.6, doğrulanmamış
×0.5) kaldırıldı — gizli bir çarpan, modele dayanaksız bir kesinlik hissi
veriyordu. Yerine `assess_report_credibility` geldi: skoru **kırılımıyla**
döndürüyor, her bileşen epistemik statüsüyle etiketli.

| bileşen | `basis` | ne |
|---|---|---|
| taban | `assumption` | Kaynak türü önceliği. official 0.65, third_party 0.45. **Ölçülmedi**, operasyonel kabul. |
| ölçüm | `empirical` | Oturumda kendi tespitlerinle biriken skor. 3 kontrolden sonra **tabanın yerine geçer**. |
| destekleme | `corroboration` | Aynı noktayı anlatan başka raporlar. Çapraz kaynak +0.20, aynı kaynak +0.07, aynı metin **0**. |
| doğrulanmamış | `text` | Rapor kendini "ihbar/bildirildi" diye niteliyorsa −0.15. |

Bu, "official daha güvenilir ama desteklenen third_party ağırlık kazanmalı"
davranışını veriyor: `#44` (third_party) tek başına 0.45, ama `#9` (official)
aynı noktayı doğruladığı için **0.65**. Buna karşılık `#36` yalnızca kendi
kaynağının tekrarını aldığı için 0.37'de kalıyor. Gün genelinde 7 third_party
raporu destekleme ile ağırlık kazanıyor.

**Varsayım kanıta teslim olur.** Taban mutlak değil: third_party 4 kez
doğrulanırsa 0.75'e çıkıyor, official 5 kez yalanlanırsa 0.19'a düşüyor.
`basis` alanı modelin hangisine baktığını görmesini sağlıyor.

**Kaldırılanlar (ölçümle).** `get_report_frequency` silindi: aynı
yarıçap/pencerede `query_reports` ile 40/40 aynı sayıyı döndürüyordu, tek
özgün alanı `duplicate_texts` 0/40 kez doldu, ve yoğunluk anomalisi eşiği
bölge geneli sayıyla yerel sayıyı karşılaştırdığı için 40/40 hiç yanmadı.
`check_report_consistency`'nin araç-türü ekseni silindi: gerçek veride
neredeyse her kare her türü içerdiği için hiçbir şey ayırt etmiyordu.
`class_alternatives` silindi (çağıran yoktu).

**Kutu boyutu araç türünü göstermez.** Ölçüldü: car medyan 6.8 m / p90
25.5 m, truck medyan 9.6 m. car'ın p90'ı truck'ın medyanının üstünde.
`meters_per_pixel` yer ölçeğidir, sınıf göstergesi değil — docstring ve
şema bunu açıkça söylüyor.

**Dürüstlük notu:** Veri, official'ın daha doğru olduğunu **doğrulamıyor**.
Hareket iddialarında official 14 tuttu / 12 tutmadı, third_party 6 tuttu /
1 tutmadı (küçük örnek, üstelik official'ın "üsse yaklaşan planlı ikmal"
raporları sınıflandırıcıyı zorluyor). Bu yüzden öncelik `config.SOURCE_PRIOR`
içinde açıkça "varsayım" olarak işaretli ve tek satırda değiştirilebilir.

**Destekleme eksen bazında.** İki rapor araç türünde anlaşıp sayıda
ayrılabilir. `#44`/`#9` tam böyle: "orada kamyon var" çapraz doğrulanmış,
"kaç tane" tartışmalı. Tool ikisini ayrı ayrı (`agreed_on` / `disputed_on`)
döndürüyor — "destekliyor" demek yeterli değil.

**Tekrar, doğrulama değildir.** Birebir aynı metnin tekrarı bonus almıyor,
ayrı `duplicates` listesine gidiyor. Bu veride 0 tane var ama mekanizma
yerinde, çünkü bir kaynağın kendini kopyalaması en kolay sahte güven kaynağı.

**Konum eşiği raporun kendi hassasiyetinden geliyor.** Sabit bir sayı değil:
`39.9374N` (4 ondalık) ≈ 11 m, `39.92087N` (5 ondalık) ≈ 1.1 m. Alt sınır 50 m.

**Eşleştirme belirsizliği saklanmaz.** Aynı karede iki araç 1.7 m kadar
yakın olabiliyor. `find_candidate_tracks` sessizce birini seçmez;
`ambiguous=true` der ve hareket profillerine bakmayı söyler.

**Duran araca yön atfedilmez.** Park halindeki aracın kaydı da GPS gürültüsü
yüzünden birkaç metre oynar. Eşik sabit mesafe değil, pencereyle ölçekli.

**Kanıtsız yükseltme reddedilir.** `submit_assessment`, `attention` veya
`watch` kararını boş `evidence` ile kabul etmez ve kısa `rationale`'ı
`ValueError` ile geri çevirir. Model düzeltip tekrar çağırmak zorundadır.

**Skor görüntüler arasında birikir.** `SessionState.reliability` 40 görüntü
boyunca yaşar; 5. görüntüde öğrenilen "bu kaynak sayıyı şişiriyor" 30.
görüntüde kullanılabilir. 3 kontrolden az veri varsa etiket `unknown` kalır.

## Doğrulama

`tests/test_geo.py` görev tanımındaki `img_000123` örneğini birebir
doğrular: kutu (610, 380, 60, 28) → merkez (640, 394) → 39.94439 N,
32.86350 E ve T0187'ye 2.5 m mesafe. Ayrıca 40 görüntüdeki *gerçek* araç
kayıtlarının tamamı piksele çevrilip kare içinde kaldığı kontrol edilir —
dönüşümde işaret hatası olsa bu test patlar.

Dönüşüm ayrıca görsel olarak da doğrulandı: `T0047`'nin 13:25 koordinatı
`img_003839` üzerinde kırpıldığında tam bir aracın üstüne düşüyor.

## Bilinmeyen / sınırlar

- Tespit modeli (1. gün) bu depoda **yok**. Pipeline kutuları dışarıdan verir;
  `scratch.py walk` onun yerine hareket kaydını "tespit" gibi kullanır.
- `reinspect_crop` canlı GLM anahtarı olmadan denenmedi; anahtarsız çağrıda
  anlaşılır `RuntimeError` verir. Kırpma/base64 yolu gerçek görüntüyle test edildi.
- Raporlarda hangisinin doğru olduğu veride işaretli değil; bu yüzden
  ayrıştırma testleri metni zemin gerçeği alır, doğruluk kararını agent verir.
- **Track'ler görüntüye bağlı.** 226 track / 40 görüntü; her track penceresi
  tam olarak bir görüntünün çekim saatinde bitiyor. Yani track'ler bir
  karedeki tespitlerle eşleştirilmek üzere var. Rastgele bir saatte "bölgedeki
  tüm araçlar" diye bir havuz yok — bu, raporları track'lerle doğrulamayı
  yapısal olarak güvenilmez kılıyor.
- Raporlar karelerin içine düşmüyor (0/72), ama 40 görüntünün 20'sinde ±45 dk
  içinde 400 m'den yakın koordinatlı rapor var; en yakınları 7-36 m. Kareler
  150×84 m olduğu için rapor koordinatı yuvarlanınca hemen dışarı düşüyor ama
  aynı sahneyi anlatıyor. `query_reports`'un 400 m eşiği buna dayanıyor.
