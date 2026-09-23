"""Políticas puras de fuentes. Importarlas no consulta red ni carga Qt/yt-dlp."""
from .adapters import FacebookAdapter, YouTubeLiveAdapter
from .contracts import SourceError
from .registry import normalize_url

__all__ = ["FacebookAdapter", "YouTubeLiveAdapter", "SourceError", "normalize_url"]
