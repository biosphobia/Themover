import time

from themover.config import Settings
from themover.core.state import Vec3
from themover.mapping.profile import Binding, Profile
from themover.mapping.runtime import Runtime
from themover.mapping.templates import load_template
from themover.outputs.sink import RecordingSink


def make_runtime(profile=None):
    s = Settings(camera_backend="synthetic", controller_backend="simulated", tick_hz=100)
    sink = RecordingSink()
    rt = Runtime(s, sink=sink)
    rt.set_profile(profile or load_template("driving_wheel"))
    return rt, sink


def test_runtime_steps_with_simulated_devices():
    rt, sink = make_runtime()
    rt.start_devices()
    try:
        rt.arm()
        for _ in range(5):
            rt.step(0.01)
        assert rt.world is not None and len(rt.world.controllers) == 2
        sim = rt.devices.controllers[0]
        sim.simulate_button("move", True)
        sim.simulate_trigger(0.7)
        rt.step(0.01)
        assert sink.gamepad_buttons.get("a") is True
        assert abs(sink.gamepad_axes["right_trigger"] - 0.7) < 1e-6
        snap = rt.signal_snapshot()
        assert snap["c0.trigger"] == 0.7 and "wheel.angle" in snap and snap.get("c0.button.move") == 1.0
        rt.disarm()
        assert sink.gamepad_buttons.get("a") is False
    finally:
        rt.stop()


def test_camera_tracking_reaches_engine():
    rt, sink = make_runtime(Profile(bindings=[Binding("c0.track.tracked", "key.t")]))
    rt.start_devices()
    try:
        rt.arm()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            rt.step(0.01)
            if "t" in sink.keys_down:
                break
            time.sleep(0.02)
        assert "t" in sink.keys_down
        assert rt.devices.wheel_source == "camera"
    finally:
        rt.stop()


def test_background_loop_runs():
    rt, sink = make_runtime()
    rt.start()
    try:
        time.sleep(0.4)
        assert rt.engine.stats.ticks > 10
        assert rt.tick_rate > 30
    finally:
        rt.stop()
    assert not rt.running


def test_profile_updates_led_colours():
    rt, _ = make_runtime()
    rt.start_devices()
    try:
        p = load_template("boxing")
        p.controllers[0].color = [10, 20, 30]
        rt.set_profile(p)
        rt.step(0.01)
        assert rt.devices.controllers[0].state.led == (10, 20, 30)
        assert rt.devices.tracker.targets[0].rgb == (10, 20, 30)
    finally:
        rt.stop()


def test_buzz_survives_feedback_ticks():
    rt, _ = make_runtime(load_template("sword_and_shield"))
    rt.start_devices()
    try:
        rt.step(0.01)
        rt.buzz(1, rumble=0.9, led=(1, 2, 3), duration_ms=200)
        rt.step(0.01)
        st = rt.devices.controllers[1].state
        assert st.rumble == 0.9 and st.led == (1, 2, 3)
        rt._overrides[1] = (0.0, 0.9, (1, 2, 3))  # expire it
        rt.step(0.01)
        assert st.rumble == 0.0 and st.led == tuple(rt.profile.controllers[1].color)
    finally:
        rt.stop()


def test_taiko_profile_strike_taps_key_once():
    from themover.core.state import Vec3

    rt, sink = make_runtime(load_template("osu_taiko"))
    rt.start_devices()
    try:
        rt.arm()
        sim = rt.devices.controllers[0]
        sim.simulate_motion(Vec3(0, 0, 1.0), Vec3())
        for _ in range(60):
            rt.step(0.01)
        assert rt.devices.gestures[0].config.cooldown_s == 0.11
        sim.simulate_motion(Vec3(0, 0, -2.0), Vec3())
        rt.step(0.01)
        sim.simulate_motion(Vec3(0, 0, 4.0), Vec3())
        rt.step(0.01)
        sim.simulate_motion(Vec3(0, 0, 1.0), Vec3())
        for _ in range(10):
            time.sleep(0.01)
            rt.step(0.01)
        presses = [e for e in sink.events if e == ("key_down", "j")]
        assert len(presses) == 1
        assert ("key_up", "j") in sink.events
    finally:
        rt.stop()
