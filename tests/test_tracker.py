import time

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from themover.devices.camera import SyntheticCamera
from themover.devices.tracker import ColorTarget, SphereTracker, TrackingConfig, hue_distance, hue_of


def frame_with_circles(circles, w=640, h=480, bg=(24, 20, 18)):
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    frame[:] = bg
    for (x, y, r, rgb) in circles:
        cv2.circle(frame, (x, y), r, (rgb[2], rgb[1], rgb[0]), -1, lineType=cv2.LINE_AA)
    return frame


MAGENTA, CYAN = (255, 0, 255), (0, 255, 255)


def two_tracker(**kw):
    return SphereTracker([ColorTarget(MAGENTA), ColorTarget(CYAN)], mirror=False, radius_far=8, radius_near=60, **kw)


def test_tracks_two_colours_and_depth():
    tracker = two_tracker()
    frame = frame_with_circles([(160, 120, 30, MAGENTA), (480, 360, 12, CYAN)])
    a, b = tracker.process(frame, now=1.0)
    assert a.tracked and b.tracked and a.in_zone and b.in_zone
    assert a.x == pytest.approx(-0.5, abs=0.01) and a.y == pytest.approx(0.5, abs=0.01)
    assert b.x == pytest.approx(0.5, abs=0.01) and b.y == pytest.approx(-0.5, abs=0.01)
    assert a.radius == pytest.approx(30, abs=1.5) and b.radius == pytest.approx(12, abs=1.5)
    assert a.depth > b.depth and 0.38 < a.depth < 0.48


def test_subpixel_centre_and_white_core():
    """A real lit sphere saturates to white in the middle with a coloured rim; the centre must still be exact."""
    tracker = two_tracker()
    frame = frame_with_circles([(300, 200, 26, MAGENTA), (300, 200, 14, (255, 255, 255))])
    st = tracker.process(frame, now=1.0)[0]
    px = (st.x + 1) / 2 * 640
    py = (1 - (st.y + 1) / 2) * 480
    assert st.tracked and abs(px - 300) < 0.5 and abs(py - 200) < 0.5 and abs(st.radius - 26) < 1.5
    det = tracker.last_detections[0]
    assert det.hue_error < 3


def test_mask_to_lights_ignores_dim_and_dull_things():
    tracker = two_tracker()
    # A dim magenta patch (a t-shirt) and a bright but grey patch (a lamp) must not be picked up.
    frame = frame_with_circles([(100, 100, 40, (110, 0, 110)), (500, 100, 40, (230, 230, 230)), (320, 300, 20, MAGENTA)])
    a, b = tracker.process(frame, now=1.0)
    assert a.tracked and not b.tracked
    assert abs(((a.x + 1) / 2 * 640) - 320) < 1
    # With the light mask off (bright room fallback) the dim patch is a candidate again.
    tracker.config.mask_lights = False
    tracker = two_tracker(mask_lights=False)
    a, _ = tracker.process(frame_with_circles([(100, 100, 40, (110, 0, 110))]), now=2.0)
    assert a.tracked


def test_one_blob_per_controller_and_colour_separation():
    tracker = two_tracker()
    # Two magenta blobs: controller 2 (cyan) must not grab the second one.
    a, b = tracker.process(frame_with_circles([(150, 240, 20, MAGENTA), (450, 240, 20, MAGENTA)]), now=1.0)
    assert a.tracked and not b.tracked
    # Once it has been seen, a sphere keeps following the blob nearest to where it was.
    tracker.process(frame_with_circles([(160, 240, 20, MAGENTA), (450, 240, 20, MAGENTA)]), now=1.02)
    assert tracker.states[0].x < 0
    # Slightly off hues (purple-ish and teal-ish real spheres) still map to the right controller.
    a, b = tracker.process(frame_with_circles([(150, 240, 20, (230, 40, 255)), (450, 240, 20, (40, 255, 220))]), now=1.05)
    assert a.tracked and b.tracked and a.x < 0 < b.x


def test_crop_normalises_inside_tracking_area_and_ignores_outside():
    cfg = TrackingConfig(crop=[0.5, 0.5, 1.0, 1.0], mirror=False)
    tracker = SphereTracker([ColorTarget(MAGENTA)], config=cfg)
    st = tracker.process(frame_with_circles([(480, 360, 20, MAGENTA)]), now=1.0)[0]
    assert st.tracked and abs(st.x) < 0.02 and abs(st.y) < 0.02  # centre of the crop = 0, 0
    st = tracker.process(frame_with_circles([(100, 100, 20, MAGENTA)]), now=2.0)[0]
    assert not st.tracked
    mask = tracker.mask_view(frame_with_circles([(480, 360, 20, MAGENTA)]))
    assert mask.shape == (480, 640, 3) and mask[:240, :320].max() == 0


def test_trigger_zone_gates_input():
    cfg = TrackingConfig(zone=[0.0, 0.0, 0.5, 1.0], zone_enabled=True, mirror=False)
    tracker = SphereTracker([ColorTarget(MAGENTA)], config=cfg)
    st = tracker.process(frame_with_circles([(160, 240, 20, MAGENTA)]), now=1.0)[0]
    assert st.tracked and st.in_zone
    st = tracker.process(frame_with_circles([(480, 240, 20, MAGENTA)]), now=1.05)[0]
    assert not st.tracked and not st.in_zone
    assert tracker.last_detections[0] is not None and not tracker.last_detections[0].in_zone  # still drawn in the overlay
    overlay = tracker.draw_overlay(frame_with_circles([(480, 240, 20, MAGENTA)]))
    assert overlay.shape == (480, 640, 3)
    cfg.zone_enabled = False
    st = tracker.process(frame_with_circles([(480, 240, 20, MAGENTA)]), now=1.1)[0]
    assert st.tracked and st.in_zone


def test_mirror_flips_x():
    tracker = SphereTracker([ColorTarget(MAGENTA)], mirror=True)
    st = tracker.process(frame_with_circles([(100, 240, 20, MAGENTA)]), now=1.0)[0]
    assert st.x > 0.5


def test_lost_tracking_times_out():
    tracker = SphereTracker([ColorTarget(MAGENTA)], mirror=False, lost_timeout_s=0.3)
    tracker.process(frame_with_circles([(320, 240, 20, MAGENTA)]), now=1.0)
    assert tracker.states[0].tracked
    tracker.process(frame_with_circles([]), now=1.1)
    assert tracker.states[0].tracked  # brief dropout is tolerated
    tracker.process(frame_with_circles([]), now=1.5)
    assert not tracker.states[0].tracked


def test_velocity_and_smoothing():
    tracker = SphereTracker([ColorTarget(MAGENTA)], mirror=False, smoothing=0.0)
    tracker.process(frame_with_circles([(100, 240, 20, MAGENTA)]), now=1.0)
    tracker.process(frame_with_circles([(200, 240, 20, MAGENTA)]), now=1.1)
    assert tracker.states[0].vx > 0.5


def test_red_hue_wraparound():
    tracker = SphereTracker([ColorTarget((255, 0, 0))], mirror=False)
    assert tracker.process(frame_with_circles([(320, 240, 25, (255, 10, 30))]), now=1.0)[0].tracked
    assert hue_distance(hue_of((255, 0, 20)), hue_of((255, 20, 0))) < 10


def test_sample_color_and_overlay():
    tracker = SphereTracker([ColorTarget(MAGENTA)], mirror=False)
    frame = frame_with_circles([(320, 240, 25, (0, 200, 255))])
    rgb = tracker.sample_color_at(frame, 320, 240)
    assert rgb[2] == 255 and rgb[0] < 60
    tracker.process(frame, now=1.0)
    assert tracker.draw_overlay(frame).shape == frame.shape


def test_calibrate_measures_colours_and_thresholds():
    tracker = SphereTracker([ColorTarget((255, 0, 0)), ColorTarget((0, 255, 0))], mirror=False)
    frames = [frame_with_circles([(160 + i, 120, 30, (255, 40, 255)), (480, 360 + i, 18, (40, 255, 255))], bg=(60, 55, 50)) for i in range(5)]
    res = tracker.calibrate(frames)
    assert res["colors"] and hue_distance(hue_of(res["colors"][0]), hue_of(MAGENTA)) < 6 and hue_distance(hue_of(res["colors"][1]), hue_of(CYAN)) < 6
    cfg = res["config"]
    assert cfg.mask_lights and 100 < cfg.min_brightness < 220 and 10 <= cfg.hue_tolerance <= 40
    assert "2 colour" in res["report"]
    # Controller order follows the current colours: swap the targets and the measured colours swap too.
    tracker2 = SphereTracker([ColorTarget(CYAN), ColorTarget(MAGENTA)], mirror=False)
    res2 = tracker2.calibrate(frames)
    assert hue_distance(hue_of(res2["colors"][0]), hue_of(CYAN)) < 6
    assert tracker.calibrate([frame_with_circles([])])["colors"] is None


def test_config_roundtrip_and_clamping():
    cfg = TrackingConfig.from_dict({"crop": [0.9, 0.2, 0.1, 0.8], "zone": [0, 0, 0.01, 0.01], "hue_tolerance": 500, "downscale": 9, "smoothing": "0.5", "bogus": 1})
    assert cfg.crop == [0.1, 0.2, 0.9, 0.8] and cfg.zone[2] > 0.04 and cfg.hue_tolerance == 90 and cfg.downscale == 4 and cfg.smoothing == 0.5
    assert TrackingConfig.from_dict(cfg.to_dict()).to_dict() == cfg.to_dict()
    with pytest.raises(TypeError):
        SphereTracker([ColorTarget(MAGENTA)], nonsense=1)


def test_fast_enough_for_60_fps():
    tracker = two_tracker()
    frame = frame_with_circles([(160, 120, 30, MAGENTA), (480, 360, 12, CYAN), (300, 300, 8, (200, 200, 200))])
    tracker.process(frame, now=0.0)
    t0 = time.perf_counter()
    for i in range(50):
        tracker.process(frame, now=0.02 * i)
    per_frame_ms = (time.perf_counter() - t0) / 50 * 1000
    assert per_frame_ms < 8.0, per_frame_ms  # ~1-2 ms on a laptop; generous for CI


def test_synthetic_camera_is_trackable():
    cam = SyntheticCamera()
    frame = cam.read()
    tracker = two_tracker()
    assert all(s.tracked for s in tracker.process(frame, now=1.0))
