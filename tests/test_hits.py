"""Drum hit detector: realistic air-drum strokes at the controller's frame rate."""
import math

from themover.core.hits import DrumHitDetector, HitConfig
from themover.core.state import Vec3

FRAME = 1.0 / 175.0  # two IMU frames per ~11.4 ms Bluetooth report


def settle(det, t=0.0, gravity=(0.0, 0.0, 1.0), n=60):
    for _ in range(n):
        det.update(Vec3(*gravity), t)
        t += FRAME
    return t


def stroke(det, t, direction, onset_g=1.6, stop_g=3.5, onset_ms=60, gap_ms=25, stop_ms=14, rebound=True,
           gravity=(0.0, 0.0, 1.0), trigger=0.0, move=False):
    """Feed one air-drum stroke; returns (end time, list of (hit, hit time), time of first stop sample)."""
    dx, dy, dz = direction
    hits = []
    stop_t = None

    def feed(ax, ay, az, t):
        h = det.update(Vec3(gravity[0] + ax, gravity[1] + ay, gravity[2] + az), t, trigger, move)
        if h:
            hits.append((h, t))

    n = int(onset_ms / 1000 / FRAME)
    for i in range(n):  # accelerate along the stroke (half sine)
        a = onset_g * math.sin(math.pi * (i + 1) / (n + 1))
        feed(dx * a, dy * a, dz * a, t); t += FRAME
    for _ in range(int(gap_ms / 1000 / FRAME)):  # cruising
        feed(0.05 * dx, 0.05 * dy, 0.05 * dz, t); t += FRAME
    n = int(stop_ms / 1000 / FRAME) or 1
    for i in range(n):  # the stop: sharp spike opposite to the motion
        a = stop_g * (1.0 if i == 0 else 0.5 ** i)
        if stop_t is None:
            stop_t = t
        feed(-dx * a, -dy * a, -dz * a, t); t += FRAME
    if rebound:  # hand comes back up: gentle acceleration back, then a stop
        for i in range(8):
            a = 0.9 * math.sin(math.pi * (i + 1) / 9)
            feed(-dx * a, -dy * a, -dz * a, t); t += FRAME
        for i in range(3):
            a = 1.6 * 0.5 ** i
            feed(dx * a, dy * a, dz * a, t); t += FRAME
    for _ in range(4):
        feed(0, 0, 0, t); t += FRAME
    return t, hits, stop_t


DOWN = (0.0, 0.0, -1.0)  # gravity reads +z at rest, so down is -z
SIDE = (1.0, 0.0, 0.0)
DIAG = (math.sin(math.radians(60)), 0.0, -math.cos(math.radians(60)))


def test_single_down_stroke_is_one_don_at_the_stop():
    det = DrumHitDetector()
    t = settle(det)
    _, hits, stop_t = stroke(det, t, DOWN)
    assert len(hits) == 1
    hit, at = hits[0]
    assert hit.kind == "don" and hit.strength >= 3.0
    assert abs(at - stop_t) < FRAME * 1.5  # fires within a sample of the impact peak
    assert 60 <= hit.stroke_ms <= 130
    assert det.hit_count == 1 and det.value("don", at + 0.05) == 1.0 and det.value("don", at + 0.2) == 0.0


def test_rebound_and_slow_waving_do_not_hit():
    det = DrumHitDetector()
    t = settle(det)
    t, hits, _ = stroke(det, t, DOWN, rebound=True)
    assert len(hits) == 1
    # The rebound of the hand coming back up is gentler than a hit and is ignored.
    t, hits, _ = stroke(det, t + 0.1, (0.0, 0.0, 1.0), onset_g=1.6, stop_g=2.2, rebound=False)
    assert hits == []
    # Slow arm waving of 1 g never produces a sharp stop.
    for i in range(400):
        a = 1.0 * math.sin(2 * math.pi * 0.7 * i * FRAME)
        det.update(Vec3(a, 0.0, 1.0), t); t += FRAME
    assert det.hit_count == 1


def test_direction_and_modifiers_pick_don_or_kat():
    det = DrumHitDetector()
    t = settle(det)
    t, h, _ = stroke(det, t, SIDE)
    assert h[0][0].kind == "kat"
    t, h, _ = stroke(det, t + 0.1, DIAG)
    assert h[0][0].kind == "kat"  # 60 degrees off vertical is a rim hit
    t, h, _ = stroke(det, t + 0.1, DOWN, trigger=1.0)
    assert h[0][0].kind == "kat" and h[0][0].modifier == "trigger"
    t, h, _ = stroke(det, t + 0.1, SIDE, move=True)
    assert h[0][0].kind == "don" and h[0][0].modifier == "move"


def test_works_in_any_holding_orientation():
    # Controller pointing forward: gravity along -y instead of +z.
    det = DrumHitDetector()
    g = (0.0, -1.0, 0.0)
    t = settle(det, gravity=g)
    t, h, _ = stroke(det, t, (0.0, 1.0, 0.0), gravity=g)  # "down" is now +y
    assert h and h[0][0].kind == "don"
    t, h, _ = stroke(det, t + 0.1, (0.0, 0.0, 1.0), gravity=g)  # sideways
    assert h and h[0][0].kind == "kat"


def test_fast_streams_same_hand_and_alternating():
    det = DrumHitDetector()
    t = settle(det)
    times = []
    for _ in range(12):  # 176 ms per hand = 170 BPM 1/4 stream alternating hands
        t0 = t
        t, h, stop_t = stroke(det, t, DOWN, rebound=True)
        assert len(h) == 1
        times.append(h[0][1] - t0)
        t = t0 + 0.176
    assert det.hit_count == 12
    # Timing is consistent from stroke to stroke (jitter well inside osu's +-35 ms GREAT window).
    assert max(times) - min(times) < 0.004
    # Even 8 hits/s on one hand (125 ms) still registers every stroke.
    det2 = DrumHitDetector()
    t = settle(det2)
    for _ in range(8):
        t0 = t
        t, h, _ = stroke(det2, t, DOWN, onset_ms=45, gap_ms=15, rebound=True)
        assert len(h) == 1
        t = t0 + 0.125
    assert det2.hit_count == 8


def test_soft_and_hard_strokes_fire_at_the_same_phase():
    det = DrumHitDetector()
    t = settle(det)
    offsets = []
    for onset, stop in ((1.2, 3.2), (1.6, 3.5), (2.5, 6.0)):
        t, h, stop_t = stroke(det, t + 0.2, DOWN, onset_g=onset, stop_g=stop)
        assert len(h) == 1
        offsets.append(h[0][1] - stop_t)
    assert max(offsets) - min(offsets) < FRAME * 1.5


def test_reset_and_config():
    det = DrumHitDetector(HitConfig(hit_g=10.0))
    t = settle(det)
    _, h, _ = stroke(det, t, DOWN)
    assert h == []  # threshold too high for this stroke
    det.reset()
    assert det.value("any") == 0.0


def test_170bpm_stream_with_colour_switches_like_an_oni_chart():
    """The hardest passage of a real Oni chart: a 37-note 1/4 stream at ~170 BPM (88 ms),
    hands alternating, don/kat colour changing mid-stream, followed by a big note."""
    dets = [DrumHitDetector(), DrumHitDetector()]
    t = 0.0
    for d in dets:
        settle(d, t)
    t = 0.5
    pattern = ("d d k d k k d d k d d d k d k k d k d d k k d d k d k d d k k d k d d k d").split()
    expected = {0: [], 1: []}
    timelines = {0: [], 1: []}  # per hand: list of (stop time, kat)
    for i, note in enumerate(pattern):
        hand = i % 2
        timelines[hand].append((t + i * 0.088, note == "k"))
    for hand in (0, 1):  # big note: both hands, kat
        timelines[hand].append((t + len(pattern) * 0.088 + 0.176, True))
    got = {0: [], 1: []}
    for hand in (0, 1):
        det = dets[hand]
        cur = t - 0.3
        for stop_at, kat in timelines[hand]:
            # place the stroke so that its stop lands at stop_at
            start = stop_at - (0.060 + 0.025)
            while cur < start:
                det.update(Vec3(0, 0, 1), cur); cur += FRAME
            cur, hits, real_stop = stroke(det, start, DIAG if kat else DOWN)
            assert len(hits) == 1, f"hand {hand} note at {stop_at}: {len(hits)} hits"
            got[hand].append((hits[0][1] - real_stop, hits[0][0].kind, kat))
    for hand in (0, 1):
        assert len(got[hand]) == len(timelines[hand])
        for offset, kind, kat in got[hand]:
            assert kind == ("kat" if kat else "don")
            assert abs(offset) < FRAME * 1.5


def test_slow_windup_plateau_is_not_a_hit_but_the_impact_after_it_is():
    """A fast swing shows a slow centripetal hump above hit_g before the sharp impact spike."""
    det = DrumHitDetector(HitConfig(hit_g=3.0))
    t = settle(det)
    hits = []
    for i in range(30):  # slow hump climbing to 4.3 g over ~170 ms, then easing to 3.3 g
        a = 4.3 * math.sin(math.pi * (i + 1) / 40)
        h = det.update(Vec3(0.0, -a, 1.0), t); t += FRAME
        if h:
            hits.append(h)
    assert hits == []
    for a in (4.8, 6.6, 8.1, 9.9, 7.5, 4.0, 1.5):  # the impact: +5 g within 3 samples
        h = det.update(Vec3(0.0, -a * 0.6, 1.0 + a * 0.8), t); t += FRAME
        if h:
            hits.append(h)
    assert len(hits) == 1 and hits[0].strength > 9.0


def test_learned_signatures_decide_the_kind_and_reject_unlike_strokes():
    from themover.core.hits import hit_config_from_dict

    cfg = hit_config_from_dict({"hit_g": 3.0, "prototypes": {"0": {"don": [0.0, 0.0, 1.0], "kat": {"dir": [1.0, 0.0, 0.0], "rise": [-1.0, 0.0, 0.0]}}}, "min_proto_cos": 0.5, "proto_w_rise": 0.0})
    assert cfg.prototypes["0"]["don"] == {"dir": [0.0, 0.0, 1.0]} and cfg.prototypes["0"]["kat"]["rise"] == [-1.0, 0.0, 0.0]
    det = DrumHitDetector(cfg, hand=0)
    t = settle(det)
    t, h, _ = stroke(det, t, DOWN)  # impact spike points +z -> don signature
    assert h and h[0][0].kind == "don" and h[0][0].proto_cos > 0.9
    t, h, _ = stroke(det, t + 0.1, (-1.0, 0.0, 0.0))  # impact spike points +x -> kat signature
    assert h and h[0][0].kind == "kat"
    t, h, _ = stroke(det, t + 0.1, (0.0, 1.0, 0.0))  # impact along -y: unlike both -> ignored
    assert h == []
    other = DrumHitDetector(cfg, hand=1)  # no signatures for this hand: falls back to the angle rule
    t = settle(other)
    t, h, _ = stroke(other, t, DOWN)
    assert h and h[0][0].kind == "don" and h[0][0].proto_cos == 0.0
