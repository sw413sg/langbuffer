"""Formatos combinados por calidad para las plataformas admitidas."""
import re
from .media_source import content_kind, content_url


class QuietLog:
    def debug(self, *args, **kwargs):
        pass

    info = warning = error = debug


def extract_formats(url):
    from yt_dlp import YoutubeDL
    url = content_url(url)
    kind = content_kind(url)
    platform = 'twitch' if kind == 'twitch_live' else 'kick' if kind == 'kick_live' else None
    try:
        with YoutubeDL(dict(quiet=True, no_warnings=True, logger=QuietLog(), cachedir=False,
                            skip_download=True, noplaylist=True, socket_timeout=8,
                            retries=0, extractor_retries=0, live_from_start=False)) as ydl:
            info = ydl.extract_info(url, download=False)
            if platform and (not info or info.get('is_live') is not True):
                raise ValueError(platform+'_offline')
            if platform == 'kick':
                # El manifiesto live de Kick conserva solo unos pocos segmentos.
                # La repetición activa publicada por el propio canal expone el
                # historial creciente sin descargarlo ni persistirlo.
                channel = url.rsplit('/', 1)[-1]
                videos = ydl.get_info_extractor('Kick')._call_api(
                    f'v2/channels/{channel}/videos', channel)
                entry = next((item for item in videos if isinstance(item, dict)
                              and item.get('is_live') is True
                              and isinstance(item.get('video'), dict)), None)
                identifier = (entry.get('video') or {}).get('uuid') if entry else None
                if not isinstance(identifier, str) or not re.fullmatch(
                        r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', identifier):
                    raise ValueError('kick_dvr_unavailable')
                info = ydl.extract_info(f'https://kick.com/{channel}/videos/{identifier}', download=False)
                if not info:
                    raise ValueError('kick_dvr_unavailable')
    except Exception as error:
        code = str(error)
        if code in {'twitch_offline', 'kick_offline', 'kick_dvr_unavailable'}:
            raise ValueError(code) from None
        if not platform:
            raise
        from yt_dlp.utils import UserNotLive
        cause = (getattr(error, 'exc_info', None) or (None, None))[1]
        code = (platform+'_offline' if isinstance(error, UserNotLive) or isinstance(cause, UserNotLive)
                else platform+'_unavailable')
        raise ValueError(code) from None
    return info or {}


def candidates(info, progressive=False):
    return [item for item in info.get('formats') or ()
            if item.get('url') and isinstance(item.get('height'), (int, float))
            and 0 < item['height'] <= 16384
            and item.get('vcodec') != 'none' and item.get('acodec') != 'none'
            and (item.get('protocol') in {'http', 'https'} and item.get('ext') == 'mp4'
                 if progressive else item.get('protocol') in {'m3u8', 'm3u8_native'})]


def resolutions(info, progressive=False):
    return sorted({int(item['height']) for item in candidates(info, progressive)}, reverse=True)


def format_fps(item):
    value = item.get('fps')
    return int(round(value)) if isinstance(value, (int, float)) and 0 < value <= 1000 else 0


def qualities(info, progressive=False):
    values = {(int(item['height']), format_fps(item)) for item in candidates(info, progressive)}
    return [dict(height=height, fps=fps) for height, fps in sorted(values, reverse=True)]


def select_format(info, height=0, fps=0, progressive=False):
    formats = candidates(info, progressive)
    if not formats:
        raise ValueError('no_progressive_video' if progressive else 'no_hls_video')
    available = resolutions(info, progressive)
    available_qualities = qualities(info, progressive)
    if height:
        formats = [item for item in formats if item['height'] == height]
        if not formats:
            raise ValueError('resolution_unavailable')
    if fps:
        formats = [item for item in formats if format_fps(item) == fps]
        if not formats:
            raise ValueError('quality_unavailable')
    chosen = max(formats, key=lambda item: (item.get('height') or 0,
                                           format_fps(item), item.get('tbr') or 0))
    return dict(chosen, resolutions=available, qualities=available_qualities)


def resolve_stream(url, height=0, fps=0):
    return select_format(extract_formats(url), height, fps)


def select_hls_format(info, height=0, fps=0):
    return select_format(info, height, fps)


def select_progressive_format(info, height=0, fps=0):
    selected = select_format(info, height, fps, progressive=True)
    return {key: selected.get(key) for key in
            ('url', 'width', 'height', 'fps', 'tbr', 'http_headers', 'resolutions', 'qualities')}


def resolve_progressive_stream(url, height=0, fps=0):
    info = extract_formats(url)
    return dict(select_progressive_format(info, height, fps), duration=info.get('duration'))


def main():
    """Sonda aislada cancelable: no imprime URLs firmadas, títulos ni excepciones."""
    import json
    import sys
    from pathlib import Path
    try:
        url = content_url(sys.argv[1])
        if content_kind(url) in {'youtube_live', 'facebook'}:
            from .extended_sources import resolve, isolate_worker
            isolate_worker()
            selected = resolve(url)
            print(json.dumps({key: selected.get(key) for key in ('resolutions', 'qualities')}))
            return
        info = extract_formats(url)
        progressive = content_kind(url) == 'status'
        print(json.dumps(dict(resolutions=resolutions(info, progressive),
                              qualities=qualities(info, progressive))))
    except Exception as error:
        from .extended_sources import safe_error
        print(json.dumps(dict(resolutions=[], qualities=[], error=safe_error(error))))


if __name__ == '__main__':
    main()
