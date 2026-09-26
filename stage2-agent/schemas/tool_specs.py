"""Her tool'un OpenAI function-calling semasi + isim -> fonksiyon esleme.

Pipeline (LangGraph) su sekilde kullanir:

    from schemas.tool_specs import TOOL_SPECS, TOOL_REGISTRY, dispatch
    resp = client.chat.completions.create(..., tools=TOOL_SPECS)
    ...
    result = dispatch(call.function.name, json.loads(call.function.arguments))

Sema aciklamalari modelin OKUDUGU metindir: ne zaman kullanilacagini ve
tuzaklari burada anlatmak, sistem prompt'unu sisirmekten daha etkili.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, List

from tools import confidence, decision, geo, reliability, reports, tracks, vision


def _fn(name: str, description: str, properties: Dict[str, Any],
        required: List[str]) -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


_LAT = {"type": "number", "description": "Enlem (WGS84), ornek 39.94439"}
_LON = {"type": "number", "description": "Boylam (WGS84), ornek 32.86350"}
_TIME = {
    "type": "string",
    "description": "Saat 'HH:MM' formatinda, ornek '13:25'. Genelde goruntunun cekim saati.",
}
_IMAGE_ID = {"type": "string", "description": "Goruntu kimligi, ornek 'img_003839'"}
_BBOX = {
    "type": "array",
    "items": {"type": "number"},
    "description": "Tespit kutusu [x, y, genislik, yukseklik] piksel cinsinden.",
}


TOOL_SPECS: List[Dict[str, Any]] = [
    # --- geo ---
    _fn(
        "image_footprint",
        "Goruntunun hangi alana baktigini dondurur: sinir koordinatlari, merkez, "
        "yer olcegi (metre/piksel), en yakin bolge ve usse mesafe. Bir goruntuyle "
        "ise BASLARKEN ilk bunu cagir; sonraki sorgularin cercevesini bu belirler. "
        "UYARI: meters_per_pixel yer olcegidir, arac turu gostergesi DEGIL — "
        "gercek veride kutu boyutu sinifi ayirt etmiyor (car p90 25 m, truck "
        "medyan 10 m). Arac turu icin modelin sinif etiketini kullan.",
        {"image_id": _IMAGE_ID},
        ["image_id"],
    ),
    _fn(
        "pixel_to_geo",
        "Goruntudeki bir pikseli enlem/boylama cevirir. Bir aracin konumu icin "
        "tespit kutusunun MERKEZ pikselini ver (kutunun kosesini degil). "
        "in_bounds=false donerse piksel goruntu disindadir, o tespiti kullanma.",
        {"image_id": _IMAGE_ID,
         "x": {"type": "number", "description": "Yatay piksel, sol kenar 0."},
         "y": {"type": "number", "description": "Dikey piksel, UST kenar 0."}},
        ["image_id", "x", "y"],
    ),
    _fn(
        "detection_to_geo",
        "Tespit kutusunu dogrudan koordinata cevirir; merkez hesabini kendisi yapar. "
        "1. gun modelinin ciktisi elindeyse pixel_to_geo yerine bunu kullan.",
        {"image_id": _IMAGE_ID, "bbox": _BBOX},
        ["image_id", "bbox"],
    ),
    _fn(
        "haversine_distance",
        "Iki koordinat arasindaki mesafeyi METRE olarak dondurur.",
        {"lat1": _LAT, "lon1": _LON, "lat2": _LAT, "lon2": _LON},
        ["lat1", "lon1", "lat2", "lon2"],
    ),
    _fn(
        "nearest_zone",
        "Bir koordinatin en yakin bolgesini, usse mesafesini (km) ve ussen gorulen "
        "yonunu dondurur. 'Bu arac nerede ve usse gore nasil konumlanmis' sorusunun cevabi.",
        {"lat": _LAT, "lon": _LON},
        ["lat", "lon"],
    ),
    _fn(
        "resolve_zone_name",
        "Raporlarda gecen bolge adini (ornek 'Kuzeydogu Kavsagi') koordinata cevirir. "
        "Bulamazsa null doner.",
        {"name": {"type": "string", "description": "Bolge adi."}},
        ["name"],
    ),

    # --- tracks ---
    _fn(
        "find_candidate_tracks",
        "Verilen koordinat ve saatte yakindaki hareket kayitlarini (track_id) dondurur. "
        "Birebir esitlik ARAMA: en yakin kayit makul mesafedeyse eslesmedir. "
        "Bos liste hata degildir — arac park halinde olup kaydi olmayabilir. "
        "ambiguous=true ise iki aday benzer uzaklikta; hareket profillerine bakip ayir.",
        {"lat": _LAT, "lon": _LON, "time": _TIME,
         "radius_m": {"type": "number",
                      "description": "Eslesme mesafe siniri, metre. Varsayilan 40."},
         "limit": {"type": "integer", "description": "En fazla kac aday. Varsayilan 5."}},
        ["lat", "lon", "time"],
    ),
    _fn(
        "get_motion_profile",
        "Bir aracin hareketini kaydin TAMAMINDAN okur: ortalama ve anlik hiz, yon, "
        "usse mesafe ve mesafenin nasil degistigi, kac dakikadir durdugu. "
        "Hizi ya da yonu tek adimdan cikarma — araclar donus yapar, durur, dolasir. "
        "movement alani: approaching_base | departing_base | lateral | stationary.",
        {"track_id": {"type": "string", "description": "Kayit kimligi, ornek 'T0187'."},
         "at_time": {"type": "string",
                     "description": "Hangi ana kadar bakilacak 'HH:MM'. Goruntunun "
                                    "cekim saatini ver; kayit orada biter."},
         "window_min": {"type": "integer",
                        "description": "Hiz/yon ortalamasinin penceresi (dk). Varsayilan 30."}},
        ["track_id"],
    ),
    _fn(
        "tracks_in_image",
        "Cekim aninda karenin icinde kalan tum hareket kayitlarini listeler. "
        "'Bu karede kayitli kac arac olmali' sorusu icin. Tespitlerle eslestirmeyi "
        "yine find_candidate_tracks yapar.",
        {"image_id": _IMAGE_ID},
        ["image_id"],
    ),

    # --- reports ---
    _fn(
        "query_reports",
        "Bir konum ve saat civarindaki saha raporlarini, serbest metinden cikarilmis "
        "yapisal iddialariyla birlikte dondurur. Raporlar goruntuye bagli degildir; "
        "tum bolge icin tek havuzdur, bu tool onu suzer. "
        "claim.kind alanina bak: sighting (somut gozlem) > friendly_id (bize bagli arac) "
        "> density_anomaly > rumor (dogrulanmamis ihbar) > all_clear / comms_loss. "
        "Hicbir rapor pesinen dogru degildir.",
        {"lat": _LAT, "lon": _LON, "time": _TIME,
         "radius_m": {"type": "number",
                      "description": "Koordinatli raporlar icin mesafe siniri (m). Varsayilan 400."},
         "window_min": {"type": "integer",
                        "description": "time +/- bu kadar dakika taranir. Varsayilan 45."},
         "source": {"type": "string", "enum": ["official", "third_party"],
                    "description": "Sadece bu kaynakla sinirla."},
         "zone": {"type": "string", "description": "Koordinat yerine bolge adiyla ara."},
         "include_zone_level": {
             "type": "boolean",
             "description": "Koordinat yerine sadece bolge adi gecen raporlar da donsun mu. "
                            "Bunlar zayif kanittir, zone_level=true ile isaretlenir."}},
        [],
    ),
    _fn(
        "check_report_consistency",
        "Tek bir raporun iddiasini KENDI bulgularinla karsilastirir: KONUM, SAYI ve "
        "HAREKET eksenlerinde uyusan ve celisen noktalari ayri ayri listeler. "
        "Arac turu bilincli olarak disarida — gercek veride her kare her turu "
        "icerdigi icin o test hicbir sey ayirt etmiyor. Raporun DOGRU olup olmadigini soylemez — veride "
        "bu isaretli degil — yalnizca senin bulgunla ortusup ortusmedigini. Celiski "
        "cikarsa raporu degil tespitini esas al. Ciktidaki 'caveats' ve 'limits' "
        "alanlarini oku: karsilastirmanin neyi belirleyemedigini orada yazar. "
        "Bildigin alanlari doldur, bilmediklerini bos birak; observed_time'i vermek "
        "onemli, yoksa hareket celiskisi zaman farkindan ayirt edilemez.",
        {"report_index": {"type": "integer",
                          "description": "query_reports'un dondugu rapor indeksi."},
         "observed_count": {"type": "integer",
                            "description": "Senin saydigin arac sayisi."},
         "observed_movement": {
             "type": "string",
             "enum": ["approaching_base", "departing_base", "lateral", "stationary"],
             "description": "get_motion_profile'dan gelen hareket sinifi."},
         "observed_lat": _LAT, "observed_lon": _LON,
         "observed_time": {
             "type": "string",
             "description": "Gozleminin saati 'HH:MM' (goruntunun cekim saati). "
                            "Rapor saatiyle arasindaki fark buyukse hareket "
                            "farki celiski sayilmaz — arac gercekten hareket "
                            "etmis olabilir."},
         "position_tolerance_m": {
             "type": "number",
             "description": "Konum esigi (m). Verilmezse raporun kendi koordinat "
                            "hassasiyetinden hesaplanir."}},
        ["report_index"],
    ),
    _fn(
        "cross_report_contradiction_check",
        "AYNI NOKTAYI anlatan raporlarin birbirini tutup tutmadigini gosterir. Kapsam "
        "bilincli olarak dar: iki rapor ancak koordinatlari 30 m icindeyse (raporlarin "
        "kendi yazim hassasiyeti) ayni noktayi anlatiyor sayilir. HAKEMLIK YAPMAZ — "
        "hangi raporun dogru oldugunu soyleyemez, cunku veride bu isaretli degil ve "
        "hareket kayitlari da hakem olamaz (her kayit bir goruntunun cekim saatinde "
        "biten 2 saatlik penceredir; park halindeki aracin kaydi hic olmayabilir). "
        "Yaptigi sey celisen iddialari ham metinleriyle yan yana koymaktir; karar senin.",
        {"lat": _LAT, "lon": _LON, "time": _TIME,
         "radius_m": {"type": "number",
                      "description": "Ayni nokta sayilma siniri (m). Varsayilan 30. "
                                     "Buyutme: birkac km'lik bolgede 250 m farkli "
                                     "araclari ayni arac sanmak demektir."},
         "window_min": {"type": "integer", "description": "Zaman penceresi (dk). Varsayilan 45."}},
        [],
    ),

    _fn(
        "find_supporting_reports",
        "Bir raporun iddiasini AYNI NOKTADA destekleyen baska raporlari bulur. "
        "Destek bagimsizlik derecesine gore siniflanir: cross_source (farkli kaynak "
        "ayni seyi soyluyor — guclu), same_source (ayni kaynak farkli metinle tekrar "
        "ediyor — zayif, ayni gozlemci olabilir), duplicate (birebir ayni metin — "
        "destek SAYILMAZ). Destek eksen bazindadir: iki rapor arac turunde anlasip "
        "sayida ayrilabilir; o zaman tur desteklenmis, sayi tartismali demektir.",
        {"report_index": {"type": "integer", "description": "Rapor indeksi."},
         "radius_m": {"type": "number",
                      "description": "Ayni nokta siniri (m). Varsayilan 30."},
         "window_min": {"type": "integer",
                        "description": "Zaman penceresi (dk). Varsayilan 45."}},
        ["report_index"],
    ),

    # --- reliability ---
    _fn(
        "assess_report_credibility",
        "Bir rapora NE KADAR AGIRLIK verilmeli? Tek sayi degil, kirilimiyla doner. "
        "Uc bilesen: (1) taban — kaynak turune gore varsayilan oncelik, bu bir "
        "VARSAYIMDIR veriden olculmedi; (2) olcum — oturumda senin kendi "
        "tespitlerinle biriken skor, 3 kontrolden sonra tabanin YERINE GECER cunku "
        "olcum varsayimdan agirdir; (3) destekleme — ayni noktayi anlatan baska "
        "raporlar, capraz kaynak guclu / ayni kaynak zayif / ayni metin sifir. "
        "Ayrica raporun kendi 'dogrulanmamis' ifadesi ceza yazar. "
        "band: yuksek | orta | dusuk | not_a_claim. Skor raporun DOGRU oldugunu "
        "olcmez, sadece ne kadar agirlik verilecegini.",
        {"report_index": {"type": "integer", "description": "Rapor indeksi."}},
        ["report_index"],
    ),
    _fn(
        "update_source_reliability",
        "Bir raporun tespitinle uyusup uyusmadigini oturum skoruna isler. "
        "check_report_consistency'den anlamli bir sonuc aldiginda cagir: verdict "
        "'consistent' ise agreed, 'contradicts' ise contradicted. "
        "Skor goruntuler arasinda BIRIKIR; bir kaynagin bugun sistematik olarak "
        "sisirdigini boyle ogrenirsin.",
        {"source": {"type": "string", "enum": ["official", "third_party"]},
         "outcome": {"type": "string", "enum": ["agreed", "contradicted", "unverified"],
                     "description": "agreed: tespit raporu dogruladi. contradicted: "
                                    "yalanladi. unverified: karsilastirilamadi."},
         "report_index": {"type": "integer", "description": "Hangi rapor uzerinden."},
         "detail": {"type": "string", "description": "Kisa gerekce."}},
        ["source", "outcome"],
    ),
    _fn(
        "get_source_reliability",
        "Oturumda o ana kadar birikmis kaynak guvenilirlik skorlarini okur. "
        "label: reliable | mixed | unreliable | unknown. 3 kontrolden az varsa "
        "unknown doner ve tek basina gerekce yapilmamalidir.",
        {"source": {"type": "string", "enum": ["official", "third_party"]}},
        [],
    ),

    # --- vision ---
    _fn(
        "reinspect_crop",
        "Goruntunun ilgili parcasini kirpip modele TEKRAR baktirir. PAHALI ve yavastir: "
        "once geo/tracks/reports tool'larini tuket. Yalnizca su durumlarda cagir: "
        "(a) tespit skoru dusuk, (b) rapor tespitle celisiyor ve hangisinin dogru oldugu "
        "goruntuden anlasilabilir, (c) arac turu kritik ve belirsiz. "
        "bbox ya da lat/lon ver; ikisi de olur.",
        {"image_id": _IMAGE_ID, "bbox": _BBOX, "lat": _LAT, "lon": _LON,
         "question": {"type": "string",
                      "description": "Ozel soru. Verilmezse standart arac sayim/tur sorusu sorulur."}},
        ["image_id"],
    ),

    # --- guven birlestirme ---
    _fn(
        "combine_confidence",
        "Zincirdeki adim guvenlerini tek sayiya indirger. Cikarimin birkac adimdan "
        "gectigi her yerde submit_assessment'tan ONCE cagir: tespit guveni, "
        "find_candidate_tracks.confidence, get_motion_profile.confidence, "
        "check_report_consistency.confidence, assess_report_credibility.credibility. "
        "ONEMLI: ayni belirsizlikten etkilenen faktorlere AYNI 'group' adini ver "
        "(ornek: eslesme belirsizse hareket profili de belirsizdir -> ikisine de "
        "group='match'); ayni gruptakiler arasinda min alinir, gruplar carpilir, "
        "boylece ayni belirsizlik iki kez cezalandirilmaz. Cikti hem 'product' "
        "(kati zincir) hem 'weakest_link' (tek darbogaz) verir ve darbogazi adlandirir.",
        {"factors": {
            "type": "array",
            "items": {"type": "object"},
            "description": "[{\"name\": \"track_match\", \"confidence\": 0.9, "
                           "\"group\": \"match\"}, ...] — group verilmezse "
                           "faktor kendi basina bagimsiz sayilir."},
         "strict": {"type": "boolean",
                    "description": "True (varsayilan): gruplar arasi carpim, kati "
                                   "zincir. False: en zayif halka, iyimser."}},
        ["factors"],
    ),

    # --- decision ---
    _fn(
        "submit_assessment",
        "Goruntu icin NIHAI degerlendirmeyi kaydeder ve dongusu kapatir. Her goruntu icin "
        "tam bir kez cagrilir. rationale karari hangi bulgunun surukledigini olculerle "
        "anlatmalidir; evidence maddeleri olcum ya da rapor referansi icermelidir. "
        "Kanitsiz 'attention' reddedilir.",
        {"image_id": _IMAGE_ID,
         "verdict": {"type": "string", "enum": ["routine", "watch", "attention"],
                     "description": "routine: olagan. watch: izlemede tut. "
                                    "attention: operator bakmali."},
         "rationale": {"type": "string",
                       "description": "NEDEN. Hangi veri, hangi deger. En az bir cumle."},
         "evidence": {"type": "array", "items": {"type": "string"},
                      "description": "Somut bulgular. Ornek: \"T0187 13:25'te usse 3.0 km, "
                                     "son 30 dk 9 m/s ile yaklasti\"."},
         "confidence": {"type": "number",
                        "description": "0..1. Belirsiz eslesme ya da az kanit varsa dusuk tut."},
         "vehicles": {"type": "array", "items": {"type": "object"},
                      "description": "Karari ilgilendiren araclar: track_id, type, lat, lon, "
                                     "movement, distance_to_base_km."},
         "used_reports": {"type": "array", "items": {"type": "integer"},
                          "description": "Gerekcede kullanilan rapor indeksleri."}},
        ["image_id", "verdict", "rationale", "evidence"],
    ),
]


# --- isim -> fonksiyon ----------------------------------------------------
TOOL_REGISTRY: Dict[str, Callable[..., Any]] = {
    "image_footprint": geo.image_footprint,
    "pixel_to_geo": geo.pixel_to_geo,
    "detection_to_geo": geo.detection_to_geo,
    "haversine_distance": lambda lat1, lon1, lat2, lon2: {
        "distance_m": round(geo.haversine_distance(lat1, lon1, lat2, lon2), 1),
        "distance_km": round(geo.haversine_distance(lat1, lon1, lat2, lon2) / 1000, 3),
    },
    "nearest_zone": geo.nearest_zone,
    "resolve_zone_name": lambda name: geo.resolve_zone_name(name) or {
        "zone": None, "note": f"{name!r} zones.json icinde bulunamadi."
    },
    "find_candidate_tracks": tracks.find_candidate_tracks,
    "get_motion_profile": tracks.get_motion_profile,
    "tracks_in_image": tracks.tracks_in_image,
    "query_reports": reports.query_reports,
    "find_supporting_reports": reports.find_supporting_reports,
    "check_report_consistency": reports.check_report_consistency,
    "cross_report_contradiction_check": reports.cross_report_contradiction_check,
    "assess_report_credibility": reliability.assess_report_credibility,
    "update_source_reliability": reliability.update_source_reliability,
    "get_source_reliability": reliability.get_source_reliability,
    "reinspect_crop": vision.reinspect_crop,
    "combine_confidence": lambda factors, strict=True: confidence.combine_confidence(
        factors, strict=strict
    ),
    "submit_assessment": decision.submit_assessment,
}

# Dongusu kapatan tool'lar: pipeline bunlari gorunce durmali.
TERMINAL_TOOLS = {"submit_assessment"}

# Para harcayan tool'lar: butce sayacinda ayri tutulur.
COSTLY_TOOLS = {"reinspect_crop"}


def dispatch(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Modelin istedigi tool'u calistirir; hatayi modele geri verilebilir hale getirir.

    Exception firlatmaz: hata da bir sonuctur ve modele geri yazilirsa model
    cagriyi duzeltip tekrar deneyebilir. Pipeline'in dongusu boylece kirilmaz.
    """
    fn = TOOL_REGISTRY.get(name)
    if fn is None:
        return {
            "error": f"Bilinmeyen tool: {name!r}",
            "available": sorted(TOOL_REGISTRY),
        }
    try:
        return fn(**arguments)
    except TypeError as exc:
        return {"error": f"{name} argumanlari hatali: {exc}"}
    except (ValueError, KeyError, FileNotFoundError) as exc:
        return {"error": f"{name} calisamadi: {exc}"}
    except Exception as exc:  # pragma: no cover - beklenmeyen
        return {"error": f"{name} beklenmeyen hata: {type(exc).__name__}: {exc}"}


def tool_names() -> List[str]:
    return [s["function"]["name"] for s in TOOL_SPECS]


def validate_specs() -> None:
    """Sema ile kayit defteri tutarli mi? Import aninda degil, testte cagrilir."""
    spec_names = set(tool_names())
    reg_names = set(TOOL_REGISTRY)
    if spec_names != reg_names:
        raise AssertionError(
            f"Sema/registry uyusmuyor. Sadece semada: {spec_names - reg_names}; "
            f"sadece registry'de: {reg_names - spec_names}"
        )
    for spec in TOOL_SPECS:
        f = spec["function"]
        params = f["parameters"]
        for req in params["required"]:
            if req not in params["properties"]:
                raise AssertionError(f"{f['name']}: zorunlu '{req}' properties'te yok")
        json.dumps(spec)  # serilestirilemiyorsa burada patlar
