import math

from themover.core.state import MoveState, TrackerState, Vec3, WorldState
from themover.mapping.engine import MappingEngine, SignalReader, shape_axis
from themover.mapping.profile import Binding, FeedbackRule, Profile
from themover.outputs.sink import RecordingSink


def world(**kw):
    a, b = MoveState(0, connected=True), MoveState(1, connected=True)
    for k, v in kw.items():
        setattr(a, k, v)
    return WorldState([a, b], t=0.0, dt=0.01)


def reader(w, gestures=None, wheel=0.0):
    gestures = gestures or {}
    return SignalReader(w, gesture_value=lambda i, n: gestures.get((i, n), 0.0), wheel_angle=wheel)


def test_shape_axis_bipolar_and_unipolar():
    b = Binding("c0.orient.roll", "gamepad.left_stick_x", input_range=[-45, 45], deadzone=0.1)
    assert shape_axis(45, b) == 1.0 and shape_axis(-45, b) == -1.0 and shape_axis(0, b) == 0.0
    assert shape_axis(3, b) == 0.0  # inside deadzone
    b.curve = "squared"
    assert 0 < shape_axis(22.5, b) < 0.5
    b.invert = True
    assert shape_axis(45, b) == -1.0
    t = Binding("c0.trigger", "gamepad.right_trigger", input_range=[0, 1])
    assert shape_axis(0.5, t) == 0.5 and shape_axis(-1, t) == 0.0
    t.invert = True
    assert shape_axis(0.25, t) == 0.75


def test_hold_tap_toggle_repeat():
    sink = RecordingSink()
    eng = MappingEngine(sink)
    eng.set_profile(Profile(bindings=[
        Binding("c0.button.move", "key.space"),
        Binding("c0.button.cross", "key.x", mode="tap", tap_ms=50),
        Binding("c0.button.circle", "key.c", mode="toggle"),
        Binding("c0.button.square", "key.s", mode="repeat", tap_ms=20, repeat_ms=100),
    ]))
    w = world()
    w.controllers[0].buttons.update(move=True, cross=True, circle=True, square=True)
    eng.tick(reader(w), 0.01, now=0.0)
    assert sink.keys_down == {"space", "x", "c", "s"}
    eng.tick(reader(w), 0.01, now=0.03)
    assert "s" not in sink.keys_down and "x" in sink.keys_down  # repeat tap ended, tap still going
    eng.tick(reader(w), 0.01, now=0.07)
    assert "x" not in sink.keys_down
    eng.tick(reader(w), 0.01, now=0.11)
    assert "s" in sink.keys_down  # repeated
    w.controllers[0].buttons.update(move=False, cross=False, circle=False, square=False)
    eng.tick(reader(w), 0.01, now=0.2)
    assert sink.keys_down == {"c"}  # toggle stays on
    w.controllers[0].buttons["circle"] = True
    eng.tick(reader(w), 0.01, now=0.3)
    assert sink.keys_down == set()


def test_axis_threshold_holds_and_gamepad_axis():
    sink = RecordingSink()
    eng = MappingEngine(sink)
    eng.set_profile(Profile(bindings=[
        Binding("c1.orient.roll", "key.a", threshold=-25, compare="<"),
        Binding("c1.orient.roll", "key.d", threshold=25),
        Binding("wheel.angle", "gamepad.left_stick_x", mode="axis", input_range=[-70, 70]),
        Binding("c0.trigger", "gamepad.right_trigger", mode="axis", input_range=[0, 1]),
    ]))
    w = world(trigger=0.5)
    w.controllers[1].roll = -40
    eng.tick(reader(w, wheel=35), 0.01, now=0.0)
    assert sink.keys_down == {"a"}
    assert sink.gamepad_axes["left_stick_x"] == 0.5
    assert sink.gamepad_axes["right_trigger"] == 0.5
    w.controllers[1].roll = 40
    eng.tick(reader(w, wheel=-140), 0.01, now=0.01)
    assert sink.keys_down == {"d"}
    assert sink.gamepad_axes["left_stick_x"] == -1.0


def test_gesture_tap_and_feedback():
    sink = RecordingSink()
    eng = MappingEngine(sink)
    fb = []
    eng.on_feedback = lambda idx, rumble, led: fb.append((idx, rumble, led))
    eng.set_profile(Profile(
        bindings=[Binding("c0.gesture.swing_left", "mouse.left", mode="tap", tap_ms=80)],
        feedback=[FeedbackRule(when="c0.gesture.swing_left", controller=0, rumble=0.9, duration_ms=100, led=[1, 2, 3]),
                  FeedbackRule(rumble_from="c1.trigger", controller=1, rumble=0.5)],
    ))
    w = world()
    w.controllers[1].trigger = 0.8
    eng.tick(reader(w, {(0, "swing_left"): 1.0}), 0.01, now=0.0)
    assert "left" in sink.mouse_down_buttons
    assert (0, 0.9, (1, 2, 3)) in fb and (1, 0.4, None) in fb
    fb.clear()
    eng.tick(reader(w, {(0, "swing_left"): 0.0}), 0.01, now=0.2)
    assert "left" not in sink.mouse_down_buttons
    assert (0, 0.0, None) in fb


def test_mouse_relative_and_absolute():
    sink = RecordingSink()
    eng = MappingEngine(sink)
    eng.set_profile(Profile(bindings=[
        Binding("c0.orient.yaw", "mouse.move_x", mode="mouse", input_range=[-60, 60], scale=1.0),
        Binding("c0.track.x", "mouse.abs_x", mode="absolute", input_range=[-1, 1]),
        Binding("c0.track.y", "mouse.abs_y", mode="absolute", input_range=[-1, 1]),
    ]))
    w = world(yaw=60.0)
    w.controllers[0].tracker = TrackerState(tracked=True, x=0.5, y=0.5)
    for _ in range(10):
        eng.tick(reader(w), 0.1, now=0.0)
    assert sink.mouse_delta[0] == 600  # 600 px/s at full deflection for 1 s
    assert sink.mouse_abs == [0.75, 0.25]


def test_signal_reader_globals():
    w = world()
    w.controllers[0].tracker = TrackerState(tracked=True, x=-0.5, y=0.2, depth=0.4)
    w.controllers[1].tracker = TrackerState(tracked=True, x=0.5, y=-0.2, depth=0.6)
    w.controllers[0].gyro = Vec3(0, 0, math.radians(90))
    r = reader(w, wheel=12.0)
    assert r.read("both.distance") > 1.0
    assert r.read("both.center_x") == 0.0 and abs(r.read("both.depth_avg") - 0.5) < 1e-9
    assert abs(r.read("both.height_diff") - 0.4) < 1e-9
    assert r.read("wheel.angle") == 12.0
    assert abs(r.read("c0.gyro.z") - 90) < 1e-6
    assert r.read("c0.track.tracked") == 1.0 and r.read("c5.trigger") == 0.0 and r.read("c0.nothing") == 0.0


def test_release_all_on_profile_change():
    sink = RecordingSink()
    eng = MappingEngine(sink)
    eng.set_profile(Profile(bindings=[Binding("c0.button.move", "key.space")]))
    w = world()
    w.controllers[0].buttons["move"] = True
    eng.tick(reader(w), 0.01, now=0.0)
    assert sink.keys_down == {"space"}
    eng.set_profile(Profile())
    assert sink.keys_down == set()


def test_fast_tap_presses_immediately_and_releases_after_tap():
    import time as _time

    sink = RecordingSink()
    eng = MappingEngine(sink)
    eng.set_profile(Profile(bindings=[
        Binding("c0.hit.don", "key.j", mode="tap", tap_ms=30),
        Binding("c0.button.move", "key.j"),  # a normal hold on the same key
    ]))
    eng.fast_sources = {"c0.hit.don"}
    w = world()
    assert [b.target for b in eng.fast_bindings_for("c0.hit.don")] == ["j"] or eng.fast_bindings_for("c0.hit.don")[0].target == "key.j"
    eng.fast_tap("key.j", 30)
    assert "j" in sink.keys_down
    eng.tick(reader(w), 0.01, now=0.0)  # the tick must not release a fast-held key
    assert "j" in sink.keys_down
    _time.sleep(0.06)
    assert "j" not in sink.keys_down and ("key_up", "j") in sink.events
    # While the hold binding wants the key, a fast release must not lift it.
    w.controllers[0].buttons["move"] = True
    eng.tick(reader(w), 0.01, now=0.1)
    eng.fast_tap("key.j", 10)
    _time.sleep(0.04)
    assert "j" in sink.keys_down
    w.controllers[0].buttons["move"] = False
    eng.tick(reader(w), 0.01, now=0.2)
    assert "j" not in sink.keys_down
    # Bindings on fast sources are skipped by the tick even if the pulse is high.
    r = SignalReader(w, hit_value=lambda i, n: 1.0)
    eng.tick(r, 0.01, now=0.3)
    assert "j" not in sink.keys_down
