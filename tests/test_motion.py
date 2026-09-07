import math

from themover.core.fusion import OrientationFilter, wrap_degrees
from themover.core.gestures import GestureDetector
from themover.core.state import Vec3


def test_wrap():
    assert wrap_degrees(190) == -170
    assert wrap_degrees(-190) == 170


def test_orientation_from_gravity():
    f = OrientationFilter()
    roll, pitch, yaw = f.update(Vec3(0, 1, 0), Vec3(), 0.01)  # upright
    assert abs(pitch - 90) < 1e-6 and yaw == 0
    f2 = OrientationFilter()
    roll, pitch, _ = f2.update(Vec3(1, 0, 0), Vec3(), 0.01)  # rolled 90 degrees right
    assert abs(roll - 90) < 1e-6 and abs(pitch) < 1e-6


def test_gyro_integration_and_correction():
    f = OrientationFilter(alpha=0.0)
    f.update(Vec3(0, 0, 1), Vec3(), 0.01)
    for _ in range(100):
        f.update(Vec3(0, 0, 1), Vec3(0, math.radians(90), 0), 0.01)  # 90 deg/s about the handle for 1 s
    assert abs(f.roll - 90) < 2
    # With accel correction the estimate is pulled back towards gravity.
    f.alpha = 0.5
    for _ in range(50):
        f.update(Vec3(0, 0, 1), Vec3(), 0.01)
    assert abs(f.roll) < 5


def test_yaw_decays_to_zero():
    f = OrientationFilter(yaw_decay_per_second=1.0)
    f.update(Vec3(0, 1, 0), Vec3(), 0.01)
    f.yaw = 50
    for _ in range(300):
        f.update(Vec3(0, 1, 0), Vec3(), 0.01)
    assert abs(f.yaw) < 5


def test_swing_detection_direction_and_pulse():
    g = GestureDetector()
    t = 0.0
    for _ in range(50):  # settle gravity
        g.update(Vec3(0, 1, 0), Vec3(), 0.01, now=t)
        t += 0.01
    fired = g.update(Vec3(2.5, 1, 0), Vec3(), 0.01, now=t)
    assert "swing_right" in fired and "swing_any" in fired
    assert g.value("swing_right", now=t + 0.05) == 1.0
    assert g.value("swing_right", now=t + 0.5) == 0.0
    # cooldown: immediate re-fire is suppressed
    assert g.update(Vec3(2.5, 1, 0), Vec3(), 0.01, now=t + 0.01) == []


def test_thrust_and_flick():
    g = GestureDetector()
    t = 0.0
    for _ in range(50):
        g.update(Vec3(0, 1, 0), Vec3(), 0.01, now=t)
        t += 0.01
    fired = g.update(Vec3(0, 3.0, 0), Vec3(), 0.01, now=t)
    assert "thrust" in fired
    fired = g.update(Vec3(0, 1, 0), Vec3(math.radians(500), 0, 0), 0.01, now=t + 1.0)
    assert "flick" in fired


def test_shake_needs_reversals():
    g = GestureDetector()
    t = 0.0
    for _ in range(50):
        g.update(Vec3(0, 1, 0), Vec3(), 0.01, now=t)
        t += 0.01
    fired_all = []
    for i in range(8):
        sign = 1 if i % 2 == 0 else -1
        fired_all += g.update(Vec3(sign * 2.0, 1, 0), Vec3(), 0.01, now=t)
        t += 0.05
    assert "shake" in fired_all
