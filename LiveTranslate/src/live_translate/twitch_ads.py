"""Explicit Twitch ad markers. Discontinuity alone is not an advertisement."""
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
import math
import re
import time

from live_translate.seekable_hls import PlaylistSource, read_window


class TwitchAdBreak(ValueError):
    def __init__(self):
        super().__init__('twitch_ad_break')


class TwitchAdTimeout(ValueError):
    def __init__(self):
        super().__init__('twitch_ad_timeout')


@dataclass(frozen=True)
class _AdRange:
    start: float
    end: float | None


def _date(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            return None
        result = parsed.timestamp()
        return result if math.isfinite(result) else None
    except (ValueError, TypeError, OverflowError, AttributeError):
        return None


def _attributes(value):
    """Read bounded HLS attributes, including commas inside quoted strings."""
    result = {}
    for match in re.finditer(r'(?:^|,)([A-Z0-9-]+)=("[^"\r\n]*"|[^,\r\n]*)', value):
        key, item = match.groups()
        if len(key) <= 64 and len(item) <= 512:
            result[key] = item[1:-1] if item.startswith('"') else item
    return result


class TwitchPlaylistSource(PlaylistSource):
    """One HLS session, with a bounded wait when its consumer reaches a known ad.

    ``ad_status`` is thread-safe and raises the source error, if any. A new
    ``entries`` after ``ready`` starts at its advertised resume sequence and
    consumes the waiting state. Times in status are estimates, not a promise.
    ``poll_interval`` and ``monotonic`` allow offline deterministic verification.
    """
    def __init__(self, resolve, fetch, reference_window=None, follow_endlist=False, *, poll_interval=None,
                 monotonic=time.monotonic, ad_timeout=360):
        if follow_endlist:
            raise ValueError('invalid_twitch_source')
        super().__init__(resolve, fetch, reference_window=reference_window)
        if not 0 < ad_timeout <= 360:
            raise ValueError('invalid_ad_timeout')
        if poll_interval is not None and not 0 < poll_interval <= 60:
            raise ValueError('invalid_poll_interval')
        self._now = monotonic
        self._poll_interval = poll_interval
        self._ad_timeout = ad_timeout
        self._ranges = OrderedDict()
        self._dates = {}
        self._ads = set()
        self._unknown = set()
        self._ad_ends = {}
        self._waiting_since = None
        self._wait_date = None
        self._wait_first_sequence = None
        self._last_ad_sequence = None
        self._resume_sequence = None
        self._estimated_end = None

    def _metadata(self, text, current):
        # Metadata is bounded by one playlist and 64 date ranges. No URL, title,
        # ad identifier, or transcript is exposed through status or metrics.
        present, rows = set(), []
        stamp, title = None, ''
        for line in text.splitlines():
            line = line.strip()
            if line.startswith('#EXT-X-DATERANGE:'):
                attrs = _attributes(line.split(':', 1)[1])
                key = attrs.get('ID', '')
                if attrs.get('CLASS') != 'twitch-stitched-ad' and not key.startswith('stitched-ad-'):
                    continue
                start = _date(attrs.get('START-DATE'))
                old = self._ranges.get(key)
                if start is None and old is not None:
                    start = old.start
                if start is None or not key:
                    continue
                end = _date(attrs.get('END-DATE'))
                if end is None:
                    try:
                        duration = float(attrs.get('DURATION', ''))
                        if math.isfinite(duration) and 0 <= duration <= 86400:
                            end = start + duration
                    except ValueError:
                        pass
                if end is None and old is not None:
                    end = old.end
                if end is not None and end < start:
                    continue
                present.add(key)
                self._ranges[key] = _AdRange(start, end)
                self._ranges.move_to_end(key)
                while len(self._ranges) > 64:
                    self._ranges.popitem(last=False)
            elif line.startswith('#EXT-X-PROGRAM-DATE-TIME:'):
                stamp = _date(line.split(':', 1)[1])
            elif line.startswith('#EXTINF:'):
                title = line.partition(',')[2]
            elif line and not line.startswith('#'):
                rows.append((stamp, 'Amazon' in title))
                stamp, title = None, ''
        # An open range is only evidence while present. Already-classified
        # overlapping sequence numbers remain ads even if its tag disappears.
        self._ranges = OrderedDict((key, value) for key, value in self._ranges.items()
                                   if value.end is not None or key in present)
        dates = {}
        previous = None
        if self.snapshot is not None:
            last = self.snapshot.segments[-1]
            if last.sequence+1 == current.segments[0].sequence:
                previous = last, self._dates.get(last.sequence)
        for entry, (stamp, _) in zip(current.segments, rows):
            if stamp is None:
                stamp = self._dates.get(entry.sequence)
            if stamp is None and previous is not None and not entry.boundary:
                before, before_date = previous
                if before_date is not None and entry.init_url == before.init_url:
                    stamp = before_date + before.duration
            dates[entry.sequence] = stamp
            previous = entry, stamp
        # A later explicit PDT can anchor preceding rows in the same epoch.
        following = None
        for entry in reversed(current.segments):
            if dates.get(entry.sequence) is None and following is not None:
                after, after_date = following
                if (not after.boundary and after_date is not None
                        and after.init_url == entry.init_url):
                    dates[entry.sequence] = after_date-entry.duration
            following = entry, dates.get(entry.sequence)
        ads, ends, unknown = set(), {}, set()
        for entry, (_, titled_ad) in zip(current.segments, rows):
            stamp = dates[entry.sequence]
            if stamp is None and self._ranges and not titled_ad:
                unknown.add(entry.sequence)
            matches = [value for value in self._ranges.values() if stamp is not None
                       and stamp+entry.duration > value.start
                       and (value.end is None or stamp < value.end)]
            if titled_ad or matches or entry.sequence in self._ads:
                ads.add(entry.sequence)
                known = [value.end for value in matches if value.end is not None]
                ends[entry.sequence] = max(known) if known else self._ad_ends.get(entry.sequence)
        valid_dates = [value for value in dates.values() if value is not None]
        if valid_dates:
            first_date = min(valid_dates)
            self._ranges = OrderedDict((key, value) for key, value in self._ranges.items()
                                       if value.end is None or value.end >= first_date)
        self._dates, self._ads, self._ad_ends = dates, ads, ends
        self._unknown = unknown-ads

    def _update(self, text, base):
        """Publish one validated metadata snapshot atomically (also used offline)."""
        with self.condition:
            current = read_window(text, base, self.snapshot or self.reference_window)
            self._metadata(text, current)
            self.reference_window = None
            self.snapshot = current
            self._refresh_wait_locked()
            self._check_timeout_locked()
            self.condition.notify_all()
            return current

    def _refresh_wait_locked(self):
        if self._waiting_since is None or self.snapshot is None:
            return
        for entry in self.snapshot.segments:
            if entry.sequence >= self._wait_first_sequence and entry.error:
                self.error = ValueError(entry.error)
                return
        later_ads = [seq for seq in self._ads if seq >= self._last_ad_sequence]
        if later_ads:
            self._last_ad_sequence = max(later_ads)
        if self._wait_date is not None:
            ends = [end for seq, end in self._ad_ends.items()
                    if seq >= self._last_ad_sequence and end is not None]
            if ends:
                predicted = self._waiting_since+max(0, max(ends)-self._wait_date)
                self._estimated_end = max(self._estimated_end or predicted, predicted)
            elif self._last_ad_sequence in self._ad_ends:
                self._estimated_end = None
        self._resume_sequence = None
        clean = []
        for entry in self.snapshot.segments:
            if entry.sequence <= self._last_ad_sequence:
                continue
            if entry.error:
                self.error = ValueError(entry.error)
                return
            if entry.sequence in self._ads or entry.sequence in self._unknown:
                clean = []
                continue
            # Do not declare a stable return across another format boundary.
            if clean and (entry.boundary or entry.init_url != clean[-1].init_url):
                clean = []
            clean.append(entry)
            if len(clean) >= 2:
                self._resume_sequence = clean[0].sequence
                return

    def _check_timeout_locked(self):
        if (self.error is None and self._waiting_since is not None
                and self._resume_sequence is None
                and self._now()-self._waiting_since >= self._ad_timeout):
            self.error = TwitchAdTimeout()
            self.condition.notify_all()

    def _begin_wait_locked(self, entry):
        if self._waiting_since is None:
            self._waiting_since = self._now()
            self._wait_date = self._dates.get(entry.sequence)
            self._wait_first_sequence = entry.sequence
            self._last_ad_sequence = entry.sequence
            self._estimated_end = None
        self._refresh_wait_locked()

    def ad_status(self):
        with self.condition:
            self._check_timeout_locked()
            if self.error is not None:
                raise self.error
            waiting = self._waiting_since is not None
            return dict(waiting=waiting, ready=waiting and self._resume_sequence is not None,
                        elapsed_s=max(0, self._now()-self._waiting_since) if waiting else 0.0,
                        remaining_s=(max(0, self._estimated_end-self._now())
                                     if waiting and self._estimated_end is not None else None),
                        resume_sequence=self._resume_sequence)

    def run(self):
        try:
            selected = self.resolve()
            self.headers = selected.get('http_headers') or {}
            self.details = {key: selected.get(key) for key in
                            ('width', 'height', 'fps', 'resolutions', 'qualities')}
            last_progress, previous_text = self._now(), None
            while not self.stop.is_set():
                data, base = self.fetch(selected['url'], self.headers, 2*1024*1024)
                text = data.decode('utf-8-sig')
                previous = self.snapshot
                current = self._update(text, base)
                with self.condition:
                    if self.error is not None:
                        raise self.error
                    waiting = self._waiting_since is not None and self._resume_sequence is None
                if previous is None or current.end > previous.end or waiting:
                    last_progress = self._now()
                elif current.live and self._now()-last_progress >= max(30, 3*current.target):
                    raise ValueError('live_playlist_stalled')
                if not current.live:
                    return
                interval = (min(2.0, current.target/2) if waiting else
                            current.target/2 if text == previous_text else current.target)
                previous_text = text
                if self.stop.wait(self._poll_interval or interval):
                    return
                self.refreshes += 1
        except Exception as error:
            with self.condition:
                self.error = error
                self.condition.notify_all()

    def entries(self, position, seconds, cancel, origin, with_init=False, latest_epoch=False):
        # Twitch has no programmatic seek. Resume is internal and source-owned.
        if position is not None:
            raise ValueError('invalid_seek_position')
        current = self.window(cancel)
        if current is None:
            return
        with self.condition:
            current = self.snapshot
            self._check_timeout_locked()
            if self.error is not None:
                raise self.error
            if self._waiting_since is not None:
                if self._resume_sequence is None:
                    raise TwitchAdBreak()
                selected = next((entry for entry in current.segments
                                 if entry.sequence == self._resume_sequence), None)
                if selected is None:
                    raise ValueError('live_window_expired')
                self._waiting_since = self._wait_date = self._last_ad_sequence = None
                self._wait_first_sequence = None
                self._resume_sequence = self._estimated_end = None
            else:
                selected = current.select_latest_epoch() if latest_epoch else current.select()
        origin(selected.start)
        next_sequence, media_time = selected.sequence, 0.0
        initial_url = selected.init_url
        while not cancel.is_set() and not self.stop.is_set():
            current = self.window(cancel)
            if current is None:
                return
            with self.condition:
                current = self.snapshot
                if next_sequence < current.segments[0].sequence:
                    raise ValueError('live_window_expired')
                index = next_sequence-current.segments[0].sequence
                if index < len(current.segments):
                    entry = current.segments[index]
                    if entry.sequence != next_sequence:
                        raise ValueError('live_window_expired')
                    if entry.error:
                        raise ValueError(entry.error)
                    if entry.sequence in self._ads:
                        self._begin_wait_locked(entry)
                        if self.error is not None:
                            raise self.error
                        raise TwitchAdBreak()
                    if entry.boundary and media_time > 0:
                        raise ValueError('unsupported_hls_feature_discontinuity')
                    if entry.init_url != initial_url:
                        raise ValueError('unsupported_hls_feature_map_change')
                else:
                    entry = None
            if entry is not None:
                if with_init:
                    yield media_time, entry.duration, entry.url, entry.init_url
                else:
                    if entry.init_url:
                        raise ValueError('unsupported_hls_feature_map')
                    yield media_time, entry.duration, entry.url
                media_time += entry.duration
                next_sequence += 1
                if seconds and media_time >= seconds:
                    return
            elif not current.live:
                return
            else:
                with self.condition:
                    self.condition.wait(.1)
