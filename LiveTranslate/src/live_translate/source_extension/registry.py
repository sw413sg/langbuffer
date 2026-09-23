"""Normalización pura. Reconoce IDs; no decide si una emisión está activa."""
import re
from urllib.parse import parse_qs, urlsplit
from .contracts import Platform, SourceError, SourceRef

YT_ID = r"[A-Za-z0-9_-]{11}"
FB_ID = r"[0-9]{1,30}"
YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}
FB_HOSTS = {"facebook.com", "www.facebook.com", "m.facebook.com",
            "web.facebook.com"}


def _single(query, name):
    values = query.get(name, ())
    return values[0] if len(values) == 1 else ""


def normalize_url(raw: str) -> SourceRef:
    value = raw.strip()
    if len(value) > 4096 or any(c.isspace() or ord(c) < 32 for c in value):
        raise SourceError("invalid_source_url")
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or parsed.username or parsed.password
                or parsed.port is not None):
            raise SourceError("https_source_without_credentials_required")
        host = parsed.hostname or ""
        query = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=40)
    except ValueError:
        raise SourceError("invalid_source_url") from None
    path = parsed.path.rstrip("/")
    if host in YT_HOSTS or host == "youtu.be":
        if "list" in query:
            raise SourceError("youtube_playlist_out_of_scope")
        identifier = ""
        if host == "youtu.be":
            match = re.fullmatch("/(" + YT_ID + ")", path)
            identifier = match[1] if match else ""
        elif path == "/watch":
            identifier = _single(query, "v")
        else:
            match = re.fullmatch("/live/(" + YT_ID + ")", path)
            identifier = match[1] if match else ""
        if not re.fullmatch(YT_ID, identifier):
            raise SourceError("youtube_direct_live_link_required")
        return SourceRef(Platform.YOUTUBE, identifier,
                         "https://www.youtube.com/watch?v=" + identifier)
    if host in FB_HOSTS:
        identifier = ""
        if path in {"/watch", "/watch/live", "/video.php"}:
            identifier = _single(query, "v")
        else:
            match = re.fullmatch(
                r"/(?:[A-Za-z0-9._-]+/)?videos/(" + FB_ID + ")", path)
            identifier = match[1] if match else ""
        if not re.fullmatch(FB_ID, identifier):
            raise SourceError("facebook_direct_video_link_required")
        return SourceRef(Platform.FACEBOOK, identifier,
                         "https://www.facebook.com/watch/?v=" + identifier)
    if host in {"fb.watch", "www.fb.watch"}:
        raise SourceError("facebook_direct_video_link_required")
    raise SourceError("unsupported_source_host")

