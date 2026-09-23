"""Contratos del diseño; un CandidatePlan NO autoriza reproducción."""
from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Protocol
from threading import Event


class Platform(str, Enum):
    YOUTUBE = "youtube"
    FACEBOOK = "facebook"


class Kind(str, Enum):
    LIVE = "live"
    VIDEO = "video"


class Transport(str, Enum):
    HLS = "hls_combined"
    MP4 = "mp4_progressive"


class SourceError(ValueError):
    """Solo códigos constantes; nunca incluir la excepción remota original."""


@dataclass(frozen=True)
class SourceRef:
    platform: Platform
    content_id: str = field(repr=False)
    canonical_url: str = field(repr=False)


@dataclass(frozen=True)
class Quality:
    height: int = 0
    fps: int = 0

    def __post_init__(self):
        if (type(self.height) is not int or type(self.fps) is not int
                or not 0 <= self.height <= 16384 or not 0 <= self.fps <= 1000
                or (self.fps and not self.height)):
            raise SourceError("invalid_quality")


@dataclass(frozen=True)
class StateEvidence:
    """Producido por un inspector futuro, NUNCA inferido de la forma del enlace.

    Debe corresponder a la misma consulta, contenido y generación. No persistir,
    reutilizar entre sesiones ni construir a partir de is_live ausente.
    """
    content_id: str = field(repr=False)
    kind: Kind
    basis: str  # hls_growing, dash_dynamic, authoritative_state, finite_vod
    request_id: str = field(repr=False)

    def __post_init__(self):
        if (not isinstance(self.kind, Kind) or not isinstance(self.basis, str)
                or not isinstance(self.content_id, str) or not self.content_id
                or not isinstance(self.request_id, str) or not self.request_id):
            raise SourceError("invalid_state_evidence")


@dataclass(frozen=True)
class Candidate:
    transport: Transport
    media_url: str = field(repr=False)
    headers: tuple[tuple[str, str], ...] = field(repr=False)
    quality: Quality
    codecs_known: bool
    bitrate: float
    range_chunk_bytes: int


@dataclass(frozen=True)
class CandidatePlan:
    source: SourceRef = field(repr=False)
    kind: Kind
    candidate: Candidate
    available_qualities: tuple[Quality, ...]
    duration_s: float | None
    requirements: tuple[str, ...]

    def public_summary(self) -> dict:
        """Único objeto diseñado para cruzar hacia UI/métricas."""
        return {
            "platform": self.source.platform.value,
            "kind": self.kind.value,
            "transport": self.candidate.transport.value,
            "height": self.candidate.quality.height,
            "fps": self.candidate.quality.fps,
            "qualities": [(q.height, q.fps) for q in self.available_qualities],
            "requirements": list(self.requirements),
            "status": "candidate_requires_validation",
        }


class MetadataBackend(Protocol):
    def extract(self, source: SourceRef, cancel: Event) -> Mapping:
        """Proceso aislado cancelable; metadata efímera; no descarga de medios."""
        ...


class StateInspector(Protocol):
    def inspect(self, source: SourceRef, metadata: Mapping,
                cancel: Event, *, request_id: str) -> StateEvidence:
        """Estado verificado. Facebook no aporta is_live fiable por defecto."""
        ...


@dataclass(frozen=True)
class TransportDetails:
    """Interfaz común para evitar leer playlist.details o bridge.buffer desde UI."""
    height: int
    fps: int
    duration_s: float | None
    history_available: bool
    seekable: bool


class RunningTransport(Protocol):
    @property
    def details(self) -> TransportDetails: ...
    @property
    def epoch_s(self) -> float | None: ...
    @property
    def source_origin_s(self) -> float: ...
    @property
    def remaining_bytes(self) -> int: ...
    def start(self) -> None: ...
    def pause(self) -> None: ...
    def resume(self) -> None: ...
    def close(self) -> None: ...


class TransportFactory(Protocol):
    def preflight_and_create(self, plan: CandidatePlan, *, delay_s: float,
                             generation: int, cancel: Event) -> RunningTransport:
        """Implementar todas las requirements antes de devolver un transporte.

        Dos consumidores a 1x; ASR sin salida audible; una escucha retrasada.
        Estado vivo y URL efímera se revalidan en la generación correspondiente.
        """
        ...
