"""Oturum boyunca tasinan durum.

Iki katman var:
  SessionState — tum gun boyunca yasar. Kaynak guvenilirligi burada birikir;
                 40 goruntu islenirken ogrenilenler sonrakilere tasinir.
  ImageState   — tek bir goruntunun islenmesi boyunca yasar.

LangGraph tarafi bu tipleri import edip graph state olarak kullanabilir.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional, TypedDict

Source = Literal["official", "third_party"]
Verdict = Literal["routine", "watch", "attention"]


# --- Tool ciktilarinin yapisi (TypedDict: JSON'a birebir serilestirilir) ---
class GeoPoint(TypedDict):
    lat: float
    lon: float


class TrackCandidate(TypedDict):
    track_id: str
    lat: float
    lon: float
    distance_m: float


class MotionProfile(TypedDict):
    track_id: str
    at_time: str
    speed_mps: float               # son yarim saatin ortalama hizi
    recent_speed_mps: float        # son adimin hizi
    heading_deg: Optional[float]   # kuzeyden saat yonu; duruyorsa None
    distance_to_base_km: float
    distance_change_km: float      # (+) uzaklasiyor, (-) yaklasiyor
    movement: Literal["approaching_base", "departing_base", "lateral", "stationary"]
    stopped_minutes: int           # kaydin sonunda kac dakikadir duruyor
    path_length_km: float


class ReportClaim(TypedDict):
    """Serbest metinden cikarilan yapisal iddia."""
    vehicle_type: Optional[str]     # "kamyon" | "otomobil" | "panelvan" | "agir_arac" | None
    count: Optional[int]
    movement: Optional[str]         # "approaching_base" | "departing" | "stationary" | "transit" | None
    color: Optional[str]
    friendly: bool                  # "dost unsur / teyitli / planli ikmal"
    unverified: bool                # "dogrulanmamis ihbar / dogrulanamadi"
    lat: Optional[float]
    lon: Optional[float]
    zone: Optional[str]


class MatchedReport(TypedDict):
    index: int
    time: str
    source: Source
    text: str
    distance_m: Optional[float]
    claim: ReportClaim


class ConsistencyCheck(TypedDict):
    report_index: int
    agrees: List[str]
    conflicts: List[str]
    verdict: Literal["consistent", "partial", "contradicts", "unrelated"]


class Contradiction(TypedDict):
    a_index: int
    b_index: int
    field: str                      # "count" | "vehicle_type" | "movement" | "presence"
    a_value: str
    b_value: str
    note: str


class SourceReliability(TypedDict):
    source: Source
    checks: int
    agreements: int
    contradictions: int
    score: float                    # 0..1, Laplace duzeltmeli dogrulanma orani
    label: Literal["reliable", "mixed", "unreliable", "unknown"]


class Assessment(TypedDict):
    image_id: str
    verdict: Verdict
    confidence: float
    rationale: str
    evidence: List[str]
    vehicles: List[Dict]
    used_reports: List[int]


# --- Oturum durumu --------------------------------------------------------
@dataclass
class ImageState:
    """Tek goruntunun islenme durumu."""

    image_id: str
    capture_time: str = ""
    detections: List[Dict] = field(default_factory=list)      # 1. gun modelinin kutulari
    geo_points: List[GeoPoint] = field(default_factory=list)
    matched_tracks: List[TrackCandidate] = field(default_factory=list)
    motion: List[MotionProfile] = field(default_factory=list)
    reports: List[MatchedReport] = field(default_factory=list)
    consistency: List[ConsistencyCheck] = field(default_factory=list)
    contradictions: List[Contradiction] = field(default_factory=list)
    reinspections: List[Dict] = field(default_factory=list)
    assessment: Optional[Assessment] = None
    tool_calls: int = 0                                        # sonsuz dongu freni


@dataclass
class SessionState:
    """Tum gunun durumu. Guvenilirlik burada birikir.

    YOL BAGIMLILIGI uyarisi: reliability oturum boyunca birikir, yani bir
    goruntunun degerlendirmesi ONDAN ONCE hangi goruntulerin islendigine
    baglidir. Ayni 40 goruntu farkli sirada islenirse ara kararlar farkli
    cikabilir (nihai skor ayni olur, cunku Laplace orani sira bagimsizdir).
    Bu bir hata degil, ogrenen bir sistemin dogasi — ama tekrar uretilebilir
    kosu icin `processing_order` kaydediliyor ve sabit bir sira kullanilmali
    (ornegin cekim saatine gore).
    """

    reliability: Dict[str, SourceReliability] = field(default_factory=dict)
    images: Dict[str, ImageState] = field(default_factory=dict)
    assessments: List[Assessment] = field(default_factory=list)
    processing_order: List[str] = field(default_factory=list)

    def image_state(self, image_id: str) -> ImageState:
        st = self.images.get(image_id)
        if st is None:
            st = ImageState(image_id=image_id)
            self.images[image_id] = st
            self.processing_order.append(image_id)
        return st

    def reliability_snapshot(self) -> Dict[str, float]:
        """Su anki skorlar. Bir karari sonradan yeniden uretmek icin,
        o karar alinirken bu anlik goruntuyu kaydet."""
        return {k: float(v["score"]) for k, v in sorted(self.reliability.items())}


# Tool'lar tek bir ortak oturum uzerinde calisir. Pipeline isterse
# set_session() ile kendi state'ini enjekte eder (test izolasyonu icin de boyle).
_SESSION = SessionState()


def get_session() -> SessionState:
    return _SESSION


def set_session(state: SessionState) -> SessionState:
    global _SESSION
    _SESSION = state
    return _SESSION


def reset_session() -> SessionState:
    return set_session(SessionState())
