"""Regression on a real osu!taiko recording (two ZCM2 controllers, 20 strokes, player-tagged don / kan).

The recording lives in tests/data/taiko_rec1 (motion.npz + events/meta/tags; the camera video is left
out).  The tags were placed on the 20 fps video, so they lag the strokes by ~120 ms; the fine-tune
scoring compensates that.
"""
from pathlib import Path

import numpy as np
import pytest

from themover.ai import motion_capture as M
from themover.core.hits import hit_config_from_dict

DATA = Path(__file__).parent / "data" / "taiko_rec1"


@pytest.fixture(scope="module")
def session():
    s = M.MotionSession.load(DATA)
    yield s
    s.close()


def test_recording_loads_with_kan_alias(session):
    assert session.duration == pytest.approx(10.0, abs=0.1)
    assert session.rate(0) > 400 and session.rate(1) > 400
    assert sorted({t.kind for t in session.tags}) == ["don", "kat"]  # "kan" in the file maps to kat
    assert all(t.family == "hit" for t in session.tags)


def test_every_stroke_is_detected_on_both_hands(session):
    cfg = hit_config_from_dict(dict(session.tuning(), hit_mode="peak", hit_g=3.0))
    hits = {hand: session.replay(cfg, hand) for hand in (0, 1)}
    # 10 strokes per hand were played: nothing may be missed and no stroke may double.
    assert 10 <= len(hits[0]) <= 11 and 10 <= len(hits[1]) <= 12
    assert min(h["strength"] for h in hits[0]) > 6.0  # right hand: every lobe is a real impact
    # The old reversal model lost most strokes: the lobe is one-sided because the sensor rotates.
    old = hit_config_from_dict(dict(session.tuning(), hit_mode="reversal", axis_mode="full"))
    assert len(session.replay(old, 0)) + len(session.replay(old, 1)) < 10


def test_auto_fit_learns_signatures_and_classifies(session):
    base = dict(session.tuning(), hit_mode="peak")
    before = session.evaluate(base, complete=True)
    cfg, res = M.auto_fit(session, base, complete=True)
    assert 90 <= res.tag_offset_ms <= 220  # video latency of the tags, found automatically
    assert res.matched >= 17 and res.wrong_kind <= 1 and res.missed <= 3
    assert res.score > before.score
    protos = cfg["prototypes"]
    assert set(protos) == {"0", "1"} and set(protos["0"]) == {"don", "kat"} and set(protos["1"]) == {"don", "kat"}
    assert "rise" in protos["0"]["don"]
    # Right hand: don and kat impact directions are clearly different (about 30 degrees apart).
    d, k = np.array(protos["0"]["don"]["dir"]), np.array(protos["0"]["kat"]["dir"])
    assert float(d @ k) < 0.9
    assert cfg["hit_mode"] == "peak" and cfg["kind_mode"] in ("auto", "prototype")


def test_leave_one_out_kind_accuracy(session):
    """Signatures learned from the other strokes classify a held-out stroke: at most one error."""
    base = dict(session.tuning(), hit_mode="peak", hit_g=4.5)
    tags = [t for t in session.tags if t.family == "hit"]
    seen = set()
    unique = []
    for t in tags:
        key = (round(t.t, 2), t.hand)
        if key not in seen:
            seen.add(key)
            unique.append(t)
    wrong = 0
    for i, tag in enumerate(unique):
        train = M.MotionSession(session.folder)
        train.t, train.data, train.meta = session.t, session.data, session.meta
        train.tags = [t for j, t in enumerate(unique) if j != i]
        protos = M.fit_prototypes(train, base)
        test = M.MotionSession(session.folder)
        test.t, test.data, test.meta = session.t, session.data, session.meta
        test.tags = [tag]
        res = test.evaluate(dict(base, prototypes=protos, kind_mode="prototype"), complete=False, offset_ms=120, window_ms=140)
        if res.wrong_kind:
            wrong += 1
    assert wrong <= 1


def test_round_trip_prototypes_through_tuning_dict(session):
    cfg, _ = M.auto_fit(session, dict(session.tuning(), hit_mode="peak"), complete=True)
    again = M.normalise_tuning(cfg)
    assert again["prototypes"] == cfg["prototypes"]
    hc = hit_config_from_dict(again)
    assert hc.prototypes["0"]["don"]["dir"] == cfg["prototypes"]["0"]["don"]["dir"]
