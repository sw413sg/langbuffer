"""Short deterministic reading/synchronization checks; no media or models."""
import hashlib
import json
from langbuffer.pipeline import FrozenTimeline, ROOT


def cue(identity=0, start=0, end=.3, text='Una frase breve.'):
    return dict(id=identity, start=start, end=end, text=text, original='Synthetic example.')


def main():
    timeline = FrozenTimeline(1)
    timeline.receive(1, [cue()])
    active = timeline.tick(0)
    assert timeline.tick(.3) is active
    assert timeline.tick(1.99) is active
    assert timeline.tick(2) is None

    # More text gets more reading time, with a six-second upper target.
    for characters, duration in ((68, 4), (136, 6)):
        timeline = FrozenTimeline(1)
        timeline.receive(1, [cue(text='a'*characters)])
        active = timeline.tick(0)
        assert timeline.tick(duration-.01) is active
        assert timeline.tick(duration) is None

    # An upcoming phrase replaces the tail on time, whether known in advance
    # or arriving while the old phrase is being held. Neither phrase is lost.
    for early in (True, False):
        timeline = FrozenTimeline(1)
        timeline.receive(1, [cue()])
        following = cue(1, 1, 1.4)
        if early:
            timeline.receive(1, [following])
        active = timeline.tick(0)
        assert timeline.tick(.5) is active
        if not early:
            timeline.receive(1, [following])
        assert timeline.tick(.99) is active
        assert timeline.tick(1).id == 1
        assert timeline.late == 0 and timeline.published == 2

    # Offset changes must not change an already published text or deadline.
    for offset in (-1, 0, 1):
        timeline = FrozenTimeline(1)
        timeline.set_offset(offset)
        timeline.receive(1, [cue(start=2, end=2.3)])
        assert timeline.tick(2+offset-.01) is None
        active = timeline.tick(2+offset)
        deadline = timeline.read_until
        timeline.set_offset(-offset)
        assert timeline.tick(deadline-.01) is active
        assert timeline.tick(deadline) is None
        timeline.clear()
        assert timeline.active is None and timeline.read_until is None
    timeline.receive(0, [cue()])
    assert timeline.obsolete == 1

    report = dict(passed=True, short_page_target_s=2, characters_per_second=17,
                  maximum_reading_target_s=6, next_page_on_time=True,
                  late_arriving_successor_preserved=True, published_deadline_frozen=True,
                  offset_and_generation_checks=True, real_media_tested=False)
    (ROOT/'outputs/local-asr-reading-check.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
