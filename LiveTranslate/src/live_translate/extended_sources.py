"""YouTube Live/Facebook resolution and preflight in a cancelable child process."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from uuid import uuid4

from .source_extension.registry import normalize_url
from .source_extension.contracts import Kind, Platform, Quality, StateEvidence
from .source_extension.adapters import YouTubeLiveAdapter, FacebookAdapter, validate_identity
from .source_extension.format_policy import select_candidate
from .seekable_hls import read_window
from .range_media import bounded_get, RangeSource, RangeReader, inspect_av

ROOT = Path(__file__).resolve().parents[2]
ERRORS = {
    'source_cancelled', 'source_timeout', 'source_scheduled', 'youtube_live_ended',
    'youtube_vod_out_of_scope', 'live_state_unknown', 'live_state_conflict',
    'source_login_required', 'source_access_restricted', 'source_unavailable',
    'javascript_runtime_missing', 'ejs_unavailable', 'facebook_state_unverified',
    'single_content_required', 'content_identity_changed', 'drm_unsupported',
    'no_supported_combined_format', 'dash_transport_unimplemented',
    'separate_tracks_unimplemented', 'quality_unavailable', 'finite_duration_unverified',
    'range_not_supported', 'invalid_media_range', 'incomplete_media_range',
    'media_resource_changed', 'media_probe_limit', 'media_decode_failed', 'media_pts_missing',
    'resource_byte_limit', 'encrypted_hls_not_supported', 'invalid_segment_duration',
    'unsupported_hls_feature_master', 'unsupported_hls_feature_map',
    'unsupported_hls_feature_byterange', 'unsupported_hls_feature_gap',
    'unsupported_hls_feature_discontinuity', 'unsupported_hls_feature_map_change',
    'live_window_expired', 'live_timeline_changed', 'live_sequence_reset',
    'source_worker_failed', 'worker_isolation_failed', 'authenticated_media_out_of_scope',
    'paired_clock_unverified', 'paired_codec_unsupported', 'paired_cleanup_failed',
}


def safe_error(error):
    code = str(error)
    return code if code in ERRORS else 'source_unavailable'


def runtime_options():
    node = ROOT/'runtime/javascript/node.exe'
    if not node.is_file():
        raise ValueError('javascript_runtime_missing')
    from importlib.metadata import version, PackageNotFoundError
    try:
        if version('yt-dlp-ejs') != '0.8.0':
            raise ValueError('ejs_unavailable')
    except PackageNotFoundError:
        raise ValueError('ejs_unavailable') from None
    return dict(js_runtimes={'node': {'path': str(node)}}, remote_components=[])


def _extract(url):
    from yt_dlp import YoutubeDL
    from .stream_formats import QuietLog
    options = dict(quiet=True, no_warnings=True, logger=QuietLog(), cachedir=False,
                   skip_download=True, noplaylist=True, socket_timeout=6,
                   retries=0, extractor_retries=0, live_from_start=False,
                   wait_for_video=None, ignore_no_formats_error=True, remote_components=[])
    source = normalize_url(url)
    if source.platform is Platform.YOUTUBE:
        options.update(runtime_options())
    try:
        with YoutubeDL(options) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as error:
        # Inspect only for classification. Never return arbitrary remote messages.
        message = str(error).lower()
        if any(word in message for word in ('login', 'log in', 'sign in', 'cookies')):
            raise ValueError('source_login_required') from None
        raise ValueError('source_unavailable') from None


def inspect_hls(candidate, *, require_live=False, fetch=bounded_get, wait=time.sleep):
    headers = dict(candidate.headers)
    payload, base = fetch(candidate.media_url, headers, 2*1024*1024)
    current = read_window(payload.decode('utf-8-sig'), base)
    kind = Kind.LIVE if current.live else Kind.VIDEO
    if require_live and kind is Kind.VIDEO:
        raise ValueError('youtube_live_ended')
    if current.live:
        # A single open playlist does not prove live state. Verify new media.
        deadline = time.monotonic()+min(30, max(6, current.target*2+2))
        initial_end = current.end
        while time.monotonic() < deadline:
            wait(min(2, current.target))
            payload, base = fetch(candidate.media_url, headers, 2*1024*1024)
            current = read_window(payload.decode('utf-8-sig'), base, current)
            if not current.live:
                if require_live:
                    raise ValueError('youtube_live_ended')
                kind = Kind.VIDEO
                break
            if current.end > initial_end:
                break
        else:
            raise ValueError('facebook_state_unverified' if not require_live else 'live_state_unknown')
    entry = current.select()
    if entry.error:
        raise ValueError(entry.error)
    data = b''
    if entry.init_url:
        data, _ = fetch(entry.init_url, headers, 1024*1024)
    media, _ = fetch(entry.url, headers, 8*1024*1024)
    details = inspect_av(io.BytesIO(data+media))
    if kind is Kind.VIDEO:
        details['duration'] = current.end-current.start
    return kind, details


def resolve_metadata(url, info, height=0, fps=0):
    """Called only in child. Synthetic tests inject metadata/network inspection."""
    source = normalize_url(url)
    validate_identity(source, info)
    quality = Quality(height, fps)
    if source.platform is Platform.YOUTUBE:
        YouTubeLiveAdapter.validate_state(info)
        try:
            plan = YouTubeLiveAdapter().plan(source, info, quality)
        except ValueError as error:
            if str(error) != 'separate_tracks_unimplemented':
                raise
            from .paired_hls import select_tracks, PairedPlaylistSource
            selected = select_tracks(info, height, fps)
            paired = PairedPlaylistSource(lambda: selected, bounded_get)
            try:
                paired.start()
                snapshot = paired.window(threading.Event())
                if not snapshot.live:
                    raise ValueError('youtube_live_ended')
                entry = next(paired.entries(None, 0, threading.Event(), lambda value: None, with_init=True))
                observed = inspect_av(io.BytesIO(paired.load_pair(entry)))
                deadline = time.monotonic()+min(30, snapshot.target*2+2)
                while time.monotonic() < deadline:
                    current = paired.window(threading.Event())
                    if not current.live:
                        raise ValueError('youtube_live_ended')
                    if current.end > snapshot.end:
                        break
                    time.sleep(.2)
                else:
                    raise ValueError('live_state_unknown')
                return dict(selected, content_id=source.content_id, **{
                    key: observed[key] for key in ('width', 'height', 'fps')})
            finally:
                paired.close()
        candidate = plan.candidate
        kind, observed = inspect_hls(candidate, require_live=True)
    else:
        # Prefer HLS when offered. MP4 is a finite video route, never a live fallback.
        hls = [f for f in info.get('formats', ()) if f.get('protocol') in ('m3u8', 'm3u8_native')
               and f.get('vcodec') != 'none' and f.get('acodec') != 'none']
        if hls:
            candidate, _ = select_candidate(source, Kind.LIVE, dict(info, formats=hls), quality)
            kind, observed = inspect_hls(candidate)
        else:
            if '/watch/live/' in url or info.get('is_live') is True:
                raise ValueError('no_supported_combined_format')
            kind = Kind.VIDEO
            candidate, _ = select_candidate(source, kind, info, quality)
            with RangeReader(RangeSource(candidate.media_url, dict(candidate.headers))) as reader:
                observed = inspect_av(reader, finite=True)
    if quality.height and observed['height'] != quality.height or quality.fps and observed['fps'] != quality.fps:
        raise ValueError('quality_unavailable')
    # Expose only the probed profile. Additional metadata profiles remain selectable
    # after their own preflight at the next generation.
    enriched = dict(info, duration=observed.get('duration') or info.get('duration'))
    enriched['formats'] = [dict(f, **{k: v for k, v in observed.items() if k != 'duration'})
                          if f.get('url') == candidate.media_url else f
                          for f in info.get('formats', ())]
    if source.platform is Platform.YOUTUBE:
        plan = YouTubeLiveAdapter().plan(source, enriched, quality)
    else:
        request_id = uuid4().hex
        evidence = StateEvidence(source.content_id, kind,
                                 'hls_growing' if kind is Kind.LIVE else 'finite_vod', request_id)
        # Keep the transport just inspected, rather than switching HLS to MP4.
        enriched['formats'] = [f for f in enriched['formats'] if f.get('url') == candidate.media_url]
        plan = FacebookAdapter().plan(source, enriched, quality, evidence=evidence, request_id=request_id)
    selected = plan.candidate
    if selected.media_url != candidate.media_url:
        raise ValueError('quality_unavailable')
    return dict(url=selected.media_url, http_headers=dict(selected.headers),
                transport=selected.transport.value, media_kind=kind.value,
                width=observed['width'], height=observed['height'], fps=observed['fps'],
                duration=plan.duration_s, content_id=source.content_id,
                qualities=[dict(height=q.height, fps=q.fps) for q in plan.available_qualities],
                resolutions=sorted({q.height for q in plan.available_qualities}, reverse=True))


def resolve(url, height=0, fps=0, cancel=None):
    """Captured private pipe only; never print this result or persist it."""
    cancel = cancel or threading.Event()
    if cancel.is_set():
        raise ValueError('source_cancelled')
    python = Path(sys.executable)
    if python.name.lower() == 'pythonw.exe':
        python = python.with_name('python.exe')
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    process = subprocess.Popen([str(python), '-B', '-m', __name__],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        creationflags=flags)
    request = json.dumps(dict(url=url, height=height, fps=fps)).encode()
    deadline, first = time.monotonic()+50, True
    try:
        while not cancel.is_set() and time.monotonic() < deadline:
            try:
                output, _ = process.communicate(request if first else None, timeout=.1)
                if len(output) > 2*1024*1024:
                    raise ValueError('source_worker_failed')
                result = json.loads(output)
                if 'error' in result:
                    raise ValueError(safe_error(result['error']))
                return result
            except subprocess.TimeoutExpired:
                first = False
        raise ValueError('source_cancelled' if cancel.is_set() else 'source_timeout')
    except (json.JSONDecodeError, UnicodeError):
        raise ValueError('source_worker_failed') from None
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate()


_JOB = None


def isolate_worker():
    """Kill child JS runtimes when this worker is killed, including by QProcess."""
    global _JOB
    if os.name != 'nt':
        return
    import ctypes
    from ctypes import wintypes as w
    class Basic(ctypes.Structure):
        _fields_ = [('per_process', ctypes.c_int64), ('per_job', ctypes.c_int64),
                    ('flags', w.DWORD), ('min_ws', ctypes.c_size_t), ('max_ws', ctypes.c_size_t),
                    ('active', w.DWORD), ('affinity', ctypes.c_size_t),
                    ('priority', w.DWORD), ('scheduling', w.DWORD)]
    class IO(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in ('ro', 'wo', 'oo', 'rt', 'wt', 'ot')]
    class Extended(ctypes.Structure):
        _fields_ = [('basic', Basic), ('io', IO), ('process_memory', ctypes.c_size_t),
                    ('job_memory', ctypes.c_size_t), ('peak_process', ctypes.c_size_t),
                    ('peak_job', ctypes.c_size_t)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateJobObjectW.restype = w.HANDLE
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
    kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
    kernel.GetCurrentProcess.restype = w.HANDLE
    handle = kernel.CreateJobObjectW(None, None)
    limits = Extended()
    limits.basic.flags = 0x2000
    if not handle or not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        raise ValueError('worker_isolation_failed')
    if not kernel.AssignProcessToJobObject(handle, kernel.GetCurrentProcess()):
        raise ValueError('worker_isolation_failed')
    _JOB = handle  # Held until process exit. Do not close while its own process lives.


def main():
    try:
        isolate_worker()
        request = json.loads(sys.stdin.buffer.read(16384))
        from .media_source import content_url
        url = content_url(request['url'])
        result = resolve_metadata(url, _extract(url), request.get('height', 0), request.get('fps', 0))
    except Exception as error:
        result = dict(error=safe_error(error))
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
