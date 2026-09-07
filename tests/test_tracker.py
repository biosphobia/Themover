import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from themover.devices.camera import SyntheticCamera
from themover.devices.tracker import ColorTarget, DepthCalibration, SphereTracker


def frame_with_circles(circles, w=640, h=480):
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    for (x, y, r, rgb) in circles:
        cv2.circle(frame, (x, y), r, (rgb[2], rgb[1], rgb[0]), -1)
    return frame


def test_tracks_two_colours_and_depth():
    tracker = SphereTracker([ColorTarget((255, 0, 255)), ColorTarget((0, 255, 255))], depth=DepthCalibration(8, 60), mirror=False)
    frame = frame_with_circles([(160, 120, 30, (255, 0, 255)), (480, 360, 12, (0, 255, 255))])
    states = tracker.process(frame, now=1.0)
    a, b = states
    assert a.tracked and b.tracked
    assert a.x == pytest.approx(-0.5, abs=0.02) and a.y == pytest.approx(0.5, abs=0.02)
    assert b.x == pytest.approx(0.5, abs=0.02) and b.y == pytest.approx(-0.5, abs=0.02)
    assert a.depth > b.depth
    assert 0.35 < a.depth < 0.5


def test_mirror_flips_x():
    tracker = SphereTracker([ColorTarget((255, 0, 255))], mirror=True)
    frame = frame_with_circles([(100, 240, 20, (255, 0, 255))])
    st = tracker.process(frame, now=1.0)[0]
    assert st.x > 0.5


def test_lost_tracking_times_out():
    tracker = SphereTracker([ColorTarget((255, 0, 255))], mirror=False, lost_timeout_s=0.3)
    tracker.process(frame_with_circles([(320, 240, 20, (255, 0, 255))]), now=1.0)
    assert tracker.states[0].tracked
    tracker.process(frame_with_circles([]), now=1.1)
    assert tracker.states[0].tracked  # brief dropout is tolerated
    tracker.process(frame_with_circles([]), now=1.5)
    assert not tracker.states[0].tracked


def test_velocity_and_smoothing():
    tracker = SphereTracker([ColorTarget((255, 0, 255))], mirror=False, smoothing=0.0)
    tracker.process(frame_with_circles([(100, 240, 20, (255, 0, 255))]), now=1.0)
    tracker.process(frame_with_circles([(200, 240, 20, (255, 0, 255))]), now=1.1)
    st = tracker.states[0]
    assert st.vx > 0.5


def test_red_hue_wraparound():
    tracker = SphereTracker([ColorTarget((255, 0, 0))], mirror=False)
    frame = frame_with_circles([(320, 240, 25, (255, 10, 10))])
    assert tracker.process(frame, now=1.0)[0].tracked


def test_sample_color_and_overlay():
    tracker = SphereTracker([ColorTarget((255, 0, 255))], mirror=False)
    frame = frame_with_circles([(320, 240, 25, (0, 200, 255))])
    rgb = tracker.sample_color_at(frame, 320, 240)
    assert rgb[2] == 255 and rgb[0] < 60
    tracker.process(frame, now=1.0)
    out = tracker.draw_overlay(frame)
    assert out.shape == frame.shape


def test_synthetic_camera_is_trackable():
    cam = SyntheticCamera()
    frame = cam.read()
    tracker = SphereTracker([ColorTarget((255, 0, 255)), ColorTarget((0, 255, 255))], mirror=False)
    states = tracker.process(frame, now=1.0)
    assert all(s.tracked for s in states)
