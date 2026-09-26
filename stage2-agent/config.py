"""GLM gateway ayarlari ve proje yollari.

API key'i koda gomme: ortam degiskeni kullan.
    export GLM_API_KEY="sk-..."
"""
import os
from pathlib import Path

# --- Yollar ---------------------------------------------------------------
ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent


def _find_data_dir() -> Path:
    """Veri klasorunu bulur.

    Ayni veri depoda iki yerde durmasin diye iki konuma da bakilir:
      1. stage2-agent/data/          — yerel calisma kopyasi (git'e girmez)
      2. <repo>/data/stage2/         — depoda zaten commit'li olan kopya

    Ikisi de bayt bayt ayni; hangisi varsa o kullanilir. Ortam degiskeniyle
    ezilebilir:  export STAGE2_DATA_DIR=/baska/yol
    """
    env = os.getenv("STAGE2_DATA_DIR")
    if env:
        return Path(env).expanduser().resolve()
    local = ROOT / "data"
    if (local / "image_meta.json").exists():
        return local
    shared = REPO_ROOT / "data" / "stage2"
    if (shared / "image_meta.json").exists():
        return shared
    raise FileNotFoundError(
        "Veri bulunamadi. Beklenen konumlar:\n"
        f"  {local}\n  {shared}\n"
        "Ya da STAGE2_DATA_DIR ortam degiskenini ayarla."
    )


DATA_DIR = _find_data_dir()
IMAGES_DIR = DATA_DIR / "images"

IMAGE_META_PATH = DATA_DIR / "image_meta.json"
ZONES_PATH = DATA_DIR / "zones.json"
TRACKS_PATH = DATA_DIR / "tracks.csv"
REPORTS_PATH = DATA_DIR / "field_reports.json"

# --- GLM gateway ----------------------------------------------------------
GLM_BASE_URL = os.getenv(
    "GLM_BASE_URL",
    "https://berriailitellm-databasev1826rc3-production-d691.up.railway.app/v1",
)
GLM_API_KEY = os.getenv("GLM_API_KEY", "")
GLM_MODEL = os.getenv("GLM_MODEL", "glm-5.3-flash")

# Gateway limitleri (PDF): 60 istek/dk, ayni anda 4, 15 USD toplam butce.
MAX_CONCURRENCY = 4
DEFAULT_REASONING_EFFORT = "low"
DEFAULT_MAX_TOKENS = 2000  # dusunme de bu butceden yiyor, cimri olma

# --- Alan parametreleri ---------------------------------------------------
# Tespit ile hareket kaydi eslestirme ust siniri (metre).
#
# GERCEK TESPIT VERISIYLE OLCULDU (pred_all_boxes_submission.csv, 280 kutu):
# tespit merkezi ile gercek track noktasi arasindaki mesafe cift tepeli:
#   %64'u  <= 0.5 m      -> gercek eslesme, neredeyse tam isabet
#   %75'i  <= 2   m
#   %79'u  <= 5   m
#   kalan %21 ise 16-88 m'ye yayiliyor -> bunlar track'i OLMAYAN (park
#   halindeki) araclar; en yakin track baska bir aractir.
#
# Eski deger 40 m idi ve zararliydi: tespitlerin %98'ini zorla esleyip
# %20'sini YANLIS araca bagliyordu. Kareler medyan 186 m genisliginde,
# yani 40 m tum karenin besde biri kadardi.
#
# 5 m: gercek eslesmelerin %79'unu yakalar, geri kalanini dogru sekilde
# "eslesme yok" (= park halinde, kaydi yok) olarak birakir. Bu esikte
# coklu-aday sayisi 236'dan 38'e, belirsiz eslesme 26'dan 0'a duser.
TRACK_MATCH_RADIUS_M = 5.0

# Ikinci aday, birincinin bu katindan yakinsa eslesme belirsiz sayilir.
# Oran testi olcekten bagimsiz oldugu icin tek basina yetmiyor: gercek
# eslesme 0.2 m'de oldugunda ikinci aday 0.3 m'de bile "uzak" gorunur.
# Bu yuzden MUTLAK bir esik de aranir (asagi bak).
TRACK_AMBIGUITY_RATIO = 1.5

# Ikinci aday bu mesafenin icindeyse, oran ne olursa olsun belirsiz sayilir.
# Ayni karedeki iki arac 1.7 m kadar yakin olabiliyor.
TRACK_AMBIGUITY_ABS_M = 3.0

# "Duruyor" esigi: adim basina bu mesafenin altindaki hareket durus sayilir.
STOPPED_STEP_M = 15.0

# Saha raporunun bir tespiti "anlatiyor olabilir" sayilmasi icin mesafe (metre).
REPORT_MATCH_RADIUS_M = 400.0

# Rapor zaman penceresi (dakika): cekim saatinin +/- bu kadari taranir.
# Olculdu: 40 goruntunun 20'sinde bu pencerede 400 m'den yakin koordinatli
# rapor var; en yakinlari 7-36 m. Raporlar tipik olarak goruntuden ONCE geliyor.
REPORT_TIME_WINDOW_MIN = 45

# Iki raporun AYNI noktayi anlattigini soyleyebilmek icin mesafe siniri.
# Bu bir tahmin degil, koordinat hassasiyeti: raporlar 4-5 ondalik basamak
# kullaniyor; 4 basamak ~11 m, 5 basamak ~1.1 m eder. 30 m, yuvarlama
# farkini tolere eder ama komsu araci ayni arac saymaz.
SAME_POINT_RADIUS_M = 30.0

# Arac turu/sayisi celiskisi icin ust zaman farki (dakika).
# Bundan uzun surede yerdeki arac gercekten degismis olabilir.
CLAIM_CONFLICT_MAX_GAP_MIN = 30
# Hareket celiskisi icin: duran arac kisa surede yola cikabilir.
MOVEMENT_CONFLICT_MAX_GAP_MIN = 15

# --- Rapor guvenilirligi ---------------------------------------------------
# DIKKAT: Asagidaki oncelikler VARSAYIMDIR, olcum degil. Veride hangi raporun
# dogru oldugu isaretli olmadigi icin turetilemiyorlar; operasyonel kabulu
# yansitiyorlar (resmi kanal teyit surecinden gecer). Olcebildigimiz kadariyla
# veri bu varsayimi DOGRULAMIYOR da yalanlamiyor da: hareket iddialarinda
# official 14 tuttu/12 tutmadi, third_party 6 tuttu/1 tutmadi (kucuk ornek).
# Bu yuzden oncelik sadece BASLANGIC noktasidir; oturum boyunca biriken
# olcum (update_source_reliability) yeterli veriye ulasinca onun yerine gecer.
SOURCE_PRIOR = {
    "official": 0.65,
    "third_party": 0.45,
}

# Oturumda biriken olcum, kac kontrolden sonra onceligin yerine gecsin.
RELIABILITY_OVERRIDES_PRIOR_AFTER = 3

# Raporun kendi ifadesiyle "dogrulanmamis" olmasinin cezasi.
UNVERIFIED_PENALTY = 0.15

# Destekleme bonuslari. Bagimsizlik derecesine gore:
#   capraz kaynak  — farkli kaynak, ayni noktayi dogruluyor: en guclusu
#   ayni kaynak    — ayni kaynak farkli metinle tekrar ediyor: zayif, cunku
#                    ayni gozlemcinin iki kez konusmasi olabilir
#   ayni metin     — bonus YOK; tekrar bagimsiz dogrulama degildir
CORROBORATION_BONUS_CROSS_SOURCE = 0.20
CORROBORATION_BONUS_SAME_SOURCE = 0.07
CORROBORATION_BONUS_CAP = 0.30

# Destekleme aranirken kullanilan zaman penceresi (dakika).
CORROBORATION_WINDOW_MIN = 45

# Hareket kaydi adim araligi (dakika).
TRACK_STEP_MIN = 5


def require_api_key() -> str:
    """GLM cagrisi yapan yerler bunu cagirsin; erken ve anlasilir patlasin."""
    if not GLM_API_KEY:
        raise RuntimeError(
            "GLM_API_KEY tanimli degil. Once: export GLM_API_KEY='sk-...'"
        )
    return GLM_API_KEY
