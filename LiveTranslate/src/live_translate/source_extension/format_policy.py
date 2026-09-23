"""Selección conservadora, independiente de Qt y de clientes internos de sitios."""
import math
from collections.abc import Mapping
from urllib.parse import urlsplit
from .contracts import Candidate, Kind, Platform, Quality, SourceError, Transport

SAFE_HEADERS = {"user-agent", "referer", "origin", "accept", "accept-language"}
RANGE_CHUNK = 4 * 1024 * 1024  # propuesta, pendiente de validación de transporte


def number(value, default=0.0):
    return (float(value) if type(value) in (int, float)
            and math.isfinite(value) and value >= 0 else default)


def _https_url(value):
    if not isinstance(value, str) or any(ord(c) < 32 or c.isspace() for c in value):
        return False
    try:
        parsed = urlsplit(value)
        return (parsed.scheme == "https" and bool(parsed.hostname)
                and not parsed.username and not parsed.password
                and parsed.port in (None, 443))
    except ValueError:
        return False


def _headers(info, item):
    merged = {}
    for values in (info.get("http_headers"), item.get("http_headers")):
        if not isinstance(values, Mapping):
            continue
        for key, value in values.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise SourceError("invalid_media_headers")
            name = key.lower()
            if any(ord(c) < 32 for c in key + value):
                raise SourceError("invalid_media_headers")
            if name in {"cookie", "authorization", "proxy-authorization"}:
                raise SourceError("authenticated_media_out_of_scope")
            if name in SAFE_HEADERS:
                merged[name] = value
    return tuple(sorted(merged.items()))


def select_candidate(source, kind, info, quality=Quality()):
    """Codecs/altura ausentes exigen sonda; nunca se convierten en confirmados."""
    candidates = []
    dash_seen = split_seen = drm_seen = False
    formats = info.get("formats") or ()
    if not isinstance(formats, (list, tuple)):
        raise SourceError("invalid_format_metadata")
    for item in formats:
        if not isinstance(item, Mapping):
            continue
        if item.get("has_drm") is True:
            drm_seen = True
            continue
        protocol = item.get("protocol")
        if protocol in {"http_dash_segments", "dash", "dash_frag_urls"}:
            dash_seen = True
            continue
        if item.get("acodec") == "none" or item.get("vcodec") == "none":
            split_seen = True
            continue
        if not _https_url(item.get("url")):
            continue
        if protocol in {"m3u8", "m3u8_native"}:
            route = Transport.HLS
        elif (source.platform is Platform.FACEBOOK and kind is Kind.VIDEO
              and protocol in {"http", "https"} and item.get("ext") == "mp4"):
            route = Transport.MP4
        else:
            continue
        height = int(number(item.get("height")))
        fps = round(number(item.get("fps")))
        if height > 16384 or fps > 1000:
            continue
        # Unknown height cannot participate in exact quality selection.
        fmt_quality = Quality(height, fps if height else 0)
        codecs_known = all(
            isinstance(item.get(key), str)
            and item[key].lower() not in {"", "unknown", "none"}
            for key in ("acodec", "vcodec"))
        options = item.get("downloader_options") or {}
        upstream_chunk = number(options.get("http_chunk_size")) if isinstance(options, Mapping) else 0
        range_chunk = min(RANGE_CHUNK, int(upstream_chunk)) if upstream_chunk >= 1 else RANGE_CHUNK
        candidates.append(Candidate(route, item["url"], _headers(info, item),
                                    fmt_quality, codecs_known,
                                    number(item.get("tbr")), range_chunk))
    if not candidates:
        if dash_seen:
            raise SourceError("dash_transport_unimplemented")
        if split_seen:
            raise SourceError("separate_tracks_unimplemented")
        if drm_seen:
            raise SourceError("drm_unsupported")
        raise SourceError("no_supported_combined_format")
    catalog = tuple(Quality(h, f) for h, f in sorted(
        {(c.quality.height, c.quality.fps) for c in candidates if c.quality.height},
        reverse=True))
    selected = [c for c in candidates
                if (not quality.height or c.quality.height == quality.height)
                and (not quality.fps or c.quality.fps == quality.fps)]
    if not selected:
        raise SourceError("quality_unavailable")
    # Para videos, MP4 es el primer puente propuesto. No prometer resolución máxima
    # entre transportes antes de implementar y probar ambos.
    chosen = max(selected, key=lambda c: (
        c.transport is Transport.MP4, c.quality.height, c.quality.fps, c.bitrate))
    return chosen, catalog

