"""Runs the device manager + mapping engine on a fixed-rate background thread."""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

from themover.config import Settings
from themover.core.state import WorldState
from themover.devices.manager import DeviceManager
from themover.mapping.engine import MappingEngine, SignalReader
from themover.mapping.profile import Profile
from themover.outputs.gamepad import create_gamepad_sink
from themover.outputs.keyboard_mouse import create_keyboard_mouse_sink
from themover.outputs.sink import CompositeSink, OutputSink, RecordingSink

log = logging.getLogger(__name__)


class Runtime:
    def __init__(self, settings: Settings, sink: Optional[OutputSink] = None, devices: Optional[DeviceManager] = None) -> None:
        self.settings = settings
        self.devices = devices or DeviceManager(settings)
        self._external_sink = sink
        self.sink: OutputSink = sink or RecordingSink()
        self.engine = MappingEngine(self.sink)
        self.engine.on_feedback = self._apply_feedback
        self.profile = Profile()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self.world: Optional[WorldState] = None
        self.running = False
        self.armed = False  # when False the engine still runs but sends nothing (preview)
        self.on_tick: Optional[Callable[[WorldState], None]] = None
        self.devices_started = False
        self.tick_rate = 0.0
        self._overrides: dict[int, tuple[float, float, Optional[tuple[int, int, int]]]] = {}

    # ---------------------------------------------------------------- setup
    def start_devices(self) -> None:
        if not self.devices_started:
            self.devices.start()
            self.devices_started = True

    def stop_devices(self) -> None:
        if self.devices_started:
            self.devices.stop()
            self.devices_started = False

    def set_profile(self, profile: Profile) -> None:
        with self._lock:
            self.profile = profile
            self.engine.set_profile(profile)
            for i, c in enumerate(profile.controllers[:2]):
                self.devices.set_color(i, tuple(c.color))
            for g in self.devices.gestures:
                sens = max(0.2, min(3.0, profile.gesture_sensitivity or 1.0))
                g.config.swing_threshold_g = 1.1 * sens
                g.config.thrust_threshold_g = 1.4 * sens
                g.config.flick_threshold_dps = 400.0 * sens

    def _build_sink(self) -> OutputSink:
        if self._external_sink is not None:
            return self._external_sink
        km = create_keyboard_mouse_sink(self.settings.output_backend)
        pad = create_gamepad_sink(self.settings.gamepad_enabled)
        return CompositeSink(km, pad)

    # -------------------------------------------------------------- control
    def arm(self) -> None:
        """Start sending input to the game."""
        with self._lock:
            if self.armed:
                return
            self.sink = self._build_sink()
            self.engine.sink = self.sink
            self.engine.set_profile(self.profile)
            self.armed = True
            log.info("armed: %s", self.sink.description)

    def disarm(self) -> None:
        with self._lock:
            if not self.armed:
                return
            self.engine.release_all()
            self.sink.close()
            self.sink = RecordingSink()
            self.engine.sink = self.sink
            self.engine.set_profile(self.profile)
            self.armed = False
            for i in range(2):
                self.devices.set_rumble(i, 0.0)

    def start(self) -> None:
        if self.running:
            return
        self.start_devices()
        self._stop.clear()
        self.running = True
        self._thread = threading.Thread(target=self._loop, name="mover-engine", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self.running = False
        self.disarm()
        self.stop_devices()

    # ----------------------------------------------------------------- loop
    def _loop(self) -> None:
        period = 1.0 / max(20, self.settings.tick_hz)
        next_t = time.monotonic()
        last = next_t
        while not self._stop.is_set():
            now = time.monotonic()
            dt = max(1e-4, now - last)
            last = now
            try:
                self.step(dt)
            except Exception as exc:  # keep the loop alive no matter what
                log.exception("engine tick failed: %s", exc)
            self.tick_rate = 0.9 * self.tick_rate + 0.1 * (1.0 / dt)
            next_t += period
            sleep = next_t - time.monotonic()
            if sleep > 0:
                time.sleep(sleep)
            else:
                next_t = time.monotonic()

    def step(self, dt: float) -> WorldState:
        """One engine tick (public so tests and headless mode can drive it)."""
        world = self.devices.update()
        reader = SignalReader(
            world,
            gesture_value=self.devices.gesture_value,
            gesture_strength=self.devices.gesture_strength,
            angular_speed=lambda i: self.devices.gestures[i].angular_speed if i < len(self.devices.gestures) else 0.0,
            wheel_angle=self.devices.wheel_angle,
        )
        with self._lock:
            self.engine.tick(reader, dt)
        self.world = world
        if self.on_tick is not None:
            try:
                self.on_tick(world)
            except Exception:
                pass
        return world

    def _base_color(self, index: int) -> tuple[int, int, int]:
        if index < len(self.profile.controllers):
            return tuple(self.profile.controllers[index].color)  # type: ignore[return-value]
        return (255, 255, 255)

    def _apply_feedback(self, index: int, rumble: float, led: Optional[tuple[int, int, int]]) -> None:
        override = self._overrides.get(index)
        if override is not None:
            until, o_rumble, o_led = override
            if time.monotonic() < until:
                rumble = max(rumble, o_rumble)
                led = o_led if o_led is not None else led
            else:
                self._overrides.pop(index, None)
        self.devices.set_rumble(index, rumble)
        self.devices.set_led(index, led if led is not None else self._base_color(index))

    def buzz(self, index: int, rumble: float = 0.8, led: Optional[tuple[int, int, int]] = None, duration_ms: int = 300) -> None:
        """Pulse rumble / flash the sphere for a while, on top of whatever the profile does."""
        self._overrides[index] = (time.monotonic() + max(0.05, duration_ms / 1000.0), max(0.0, min(1.0, rumble)), led)
        # Apply immediately too, in case the engine loop is not running.
        self._apply_feedback(index, 0.0, None)

    # ------------------------------------------------------------ snapshots
    def signal_snapshot(self) -> dict[str, float]:
        """Live values of the most useful signals (for the UI and for Claude)."""
        world = self.world
        if world is None:
            return {}
        reader = SignalReader(
            world,
            gesture_value=self.devices.gesture_value,
            gesture_strength=self.devices.gesture_strength,
            angular_speed=lambda i: self.devices.gestures[i].angular_speed if i < len(self.devices.gestures) else 0.0,
            wheel_angle=self.devices.wheel_angle,
        )
        out: dict[str, float] = {}
        for i in range(len(world.controllers)):
            for name in ("trigger", "orient.roll", "orient.pitch", "orient.yaw", "track.x", "track.y", "track.depth", "track.tracked", "motion.strength", "motion.angular_speed"):
                out[f"c{i}.{name}"] = round(reader.read(f"c{i}.{name}"), 3)
            for btn, down in world.controllers[i].buttons.items():
                if down:
                    out[f"c{i}.button.{btn}"] = 1.0
        out["wheel.angle"] = round(self.devices.wheel_angle, 1)
        out["both.distance"] = round(reader.read("both.distance"), 3)
        return out
