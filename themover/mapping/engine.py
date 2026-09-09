"""The mapping engine: evaluates a profile against controller state every tick."""
from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from themover.core.state import WorldState
from themover.mapping import vocabulary as V
from themover.mapping.profile import Binding, FeedbackRule, Profile
from themover.outputs.sink import OutputSink

GestureReader = Callable[[int, str], float]
MOUSE_PIXELS_PER_SECOND = 600.0


class SignalReader:
    """Resolves source names ('c0.trigger', 'wheel.angle', ...) to float values."""

    def __init__(self, world: WorldState, gesture_value: Optional[GestureReader] = None,
                 gesture_strength: Optional[Callable[[int], float]] = None,
                 angular_speed: Optional[Callable[[int], float]] = None,
                 wheel_angle: float = 0.0, hit_value: Optional[GestureReader] = None,
                 plugin_value: Optional[Callable[[str], float]] = None) -> None:
        self.world = world
        self.plugin_value = plugin_value or (lambda s: 0.0)
        self.gesture_value = gesture_value or (lambda i, n: 0.0)
        self.hit_value = hit_value or (lambda i, n: 0.0)
        self.gesture_strength = gesture_strength or (lambda i: 0.0)
        self.angular_speed = angular_speed or (lambda i: 0.0)
        self.wheel_angle = wheel_angle
        self._cache: dict[str, float] = {}

    def read(self, source: str) -> float:
        if source in self._cache:
            return self._cache[source]
        value = self._read(source)
        self._cache[source] = value
        return value

    def _read(self, source: str) -> float:
        parts = source.split(".")
        if parts[0] == "wheel":
            return self.wheel_angle
        if parts[0] == "plugin":
            return self.plugin_value(source)
        if parts[0] == "both":
            return self._read_both(parts[1] if len(parts) > 1 else "")
        try:
            idx = int(parts[0][1:])
            st = self.world.controllers[idx]
        except (ValueError, IndexError):
            return 0.0
        group = parts[1] if len(parts) > 1 else ""
        member = parts[2] if len(parts) > 2 else ""
        if group == "button":
            return 1.0 if st.buttons.get(member) else 0.0
        if group == "trigger":
            return st.trigger
        if group == "gesture":
            return self.gesture_value(idx, member)
        if group == "hit":
            return self.hit_value(idx, member)
        if group == "orient":
            return {"roll": st.roll, "pitch": st.pitch, "yaw": st.yaw}.get(member, 0.0)
        if group == "accel":
            return getattr(st.accel, member, 0.0)
        if group == "gyro":
            return math.degrees(getattr(st.gyro, member, 0.0))
        if group == "track":
            tr = st.tracker
            if member == "tracked":
                return 1.0 if tr.tracked else 0.0
            if member == "in_zone":
                return 1.0 if (tr.tracked and tr.in_zone) else 0.0
            return getattr(tr, member, 0.0) if tr.tracked or member in ("x", "y", "depth") else 0.0
        if group == "motion":
            if member == "strength":
                return self.gesture_strength(idx)
            if member == "angular_speed":
                return self.angular_speed(idx)
        return 0.0

    def _read_both(self, member: str) -> float:
        cs = self.world.controllers
        if len(cs) < 2:
            return 0.0
        a, b = cs[0].tracker, cs[1].tracker
        if member == "distance":
            return math.hypot(a.x - b.x, a.y - b.y) if a.tracked and b.tracked else 0.0
        if member == "center_x":
            return (a.x + b.x) / 2.0
        if member == "center_y":
            return (a.y + b.y) / 2.0
        if member == "depth_avg":
            return (a.depth + b.depth) / 2.0
        if member == "height_diff":
            return a.y - b.y
        return 0.0


def shape_axis(value: float, b: Binding, apply_scale: bool = True) -> float:
    """Apply input_range, invert, deadzone, curve and scale.  Output clamped to -1..1."""
    lo, hi = b.input_range
    if hi == lo:
        return 0.0
    # Map input range to -1..1 (or 0..1 when the range starts at 0 - e.g. triggers).
    unipolar = lo == 0.0 and hi > 0.0
    if unipolar:
        v = (value - lo) / (hi - lo)
        v = max(0.0, min(1.0, v))
    else:
        v = (value - lo) / (hi - lo) * 2.0 - 1.0
        v = max(-1.0, min(1.0, v))
    if b.invert:
        v = -v if not unipolar else 1.0 - v
    if b.deadzone > 0:
        if abs(v) < b.deadzone:
            v = 0.0
        else:
            sign = 1.0 if v > 0 else -1.0
            v = sign * (abs(v) - b.deadzone) / max(1e-6, 1.0 - b.deadzone)
    if b.curve == "squared":
        v = math.copysign(v * v, v)
    elif b.curve == "cubic":
        v = v * v * v
    elif b.curve == "step":
        v = 0.0 if abs(v) < 0.5 else math.copysign(1.0, v)
    if apply_scale:
        v *= b.scale
    return max(-1.0, min(1.0, v)) if not unipolar else max(0.0, min(1.0, v))


def compare(value: float, b: Binding) -> bool:
    if b.compare == "<":
        return value < b.threshold
    if b.compare == "abs>":
        return abs(value) > b.threshold
    return value > b.threshold


@dataclass
class _BindingState:
    active: bool = False
    toggled: bool = False
    release_at: float = 0.0
    next_repeat: float = 0.0
    smoothed: float = 0.0
    remainder: float = 0.0


@dataclass
class _FeedbackState:
    active: bool = False
    rumble_until: float = 0.0
    led_until: float = 0.0


@dataclass
class EngineStats:
    ticks: int = 0
    last_tick_ms: float = 0.0
    active_targets: set[str] = field(default_factory=set)


class MappingEngine:
    """Stateless with respect to devices; owns per-binding edge state."""

    def __init__(self, sink: OutputSink) -> None:
        self.sink = sink
        self.profile = Profile()
        self._states: list[_BindingState] = []
        self._fb_states: list[_FeedbackState] = []
        self.stats = EngineStats()
        self.on_feedback: Optional[Callable[[int, float, Optional[tuple[int, int, int]]], None]] = None
        self.last_values: dict[str, float] = {}
        self.axis_accumulator: dict[str, float] = {}
        self._pressed_buttons: dict[str, int] = {}  # target -> number of bindings holding it
        self._screen = [0.5, 0.5]
        # Fast path: sources handled outside the tick (drum hits). Bindings on these
        # sources are skipped by tick(); fast_tap() presses their targets directly.
        self.fast_sources: set[str] = set()
        self._fast_held: dict[str, int] = {}
        self._lock = threading.RLock()
        self.action_taps: list = []  # callables(target, down, t): every button press/release the mapping emits

    # ----------------------------------------------------------------- setup
    def set_profile(self, profile: Profile) -> None:
        self.release_all()
        self.profile = profile.sanitized()
        self._states = [_BindingState() for _ in self.profile.bindings]
        self._fb_states = [_FeedbackState() for _ in self.profile.feedback]

    def release_all(self) -> None:
        with self._lock:
            self._pressed_buttons.clear()
            self._fast_held.clear()
            self.sink.release_all()
            self.sink.flush()

    # ------------------------------------------------------------ fast path
    def fast_bindings_for(self, source: str) -> list[Binding]:
        return [b for b in self.profile.bindings if b.enabled and b.source == source and V.target_kind(b.target) == "button"]

    def fast_tap(self, target: str, tap_ms: int) -> None:
        """Press a button target immediately (from any thread) and release it after tap_ms."""
        with self._lock:
            self._fast_held[target] = self._fast_held.get(target, 0) + 1
            if self._pressed_buttons.get(target, 0) == 0:
                self._press(target, True)
                self._pressed_buttons[target] = 1
            self.sink.flush()
        timer = threading.Timer(max(0.005, tap_ms / 1000.0), self._fast_release, args=(target,))
        timer.daemon = True
        timer.start()

    def hold(self, target: str) -> None:
        """Press a button target and keep it down until unhold() (plugins)."""
        with self._lock:
            self._fast_held[target] = self._fast_held.get(target, 0) + 1
            if self._pressed_buttons.get(target, 0) == 0:
                self._press(target, True)
                self._pressed_buttons[target] = 1
            self.sink.flush()

    def unhold(self, target: str) -> None:
        self._fast_release(target)

    def _fast_release(self, target: str) -> None:
        with self._lock:
            n = self._fast_held.get(target, 0) - 1
            if n > 0:
                self._fast_held[target] = n
                return
            self._fast_held.pop(target, None)
            if self._pressed_buttons.get(target, 0) and target not in self._wanted_last:
                self._press(target, False)
                self._pressed_buttons.pop(target, None)
                self.sink.flush()

    _wanted_last: set[str] = set()

    # -------------------------------------------------------------- one tick
    def tick(self, reader: SignalReader, dt: float, now: Optional[float] = None) -> None:
        now = time.monotonic() if now is None else now
        t0 = time.perf_counter()
        self._lock.acquire()
        try:
            self._tick_locked(reader, dt, now)
        finally:
            self._lock.release()
        self.stats.ticks += 1
        self.stats.last_tick_ms = (time.perf_counter() - t0) * 1000.0

    def _tick_locked(self, reader: SignalReader, dt: float, now: float) -> None:
        self.axis_accumulator = {}
        self.stats.active_targets = set()
        wanted_buttons: dict[str, bool] = {}
        for b, st in zip(self.profile.bindings, self._states):
            if not b.enabled or b.source in self.fast_sources:
                continue
            value = reader.read(b.source)
            self.last_values[b.source] = value
            mode = b.effective_mode()
            if V.target_kind(b.target) == "button" or mode in ("hold", "tap", "toggle", "repeat"):
                self._tick_button(b, st, value, now, wanted_buttons)
            elif mode == "axis":
                self._tick_axis(b, st, value)
            elif mode == "mouse":
                self._tick_mouse(b, st, value, dt)
            elif mode == "absolute":
                self._tick_absolute(b, st, value)

        # Apply button targets (a target is down if any binding - or a fast tap - wants it down).
        self._wanted_last = {t for t, w in wanted_buttons.items() if w}
        for target in set(list(wanted_buttons.keys()) + list(self._pressed_buttons.keys())):
            want = wanted_buttons.get(target, False) or self._fast_held.get(target, 0) > 0
            is_down = self._pressed_buttons.get(target, 0) > 0
            if want and not is_down:
                self._press(target, True)
                self._pressed_buttons[target] = 1
            elif not want and is_down:
                self._press(target, False)
                self._pressed_buttons.pop(target, None)
            if want:
                self.stats.active_targets.add(target)

        # Apply accumulated axes.
        for target, value in self.axis_accumulator.items():
            family, _, name = target.partition(".")
            if family == "gamepad":
                v = max(-1.0, min(1.0, value))
                self.sink.gamepad_axis(name, v)
                if abs(v) > 0.02:
                    self.stats.active_targets.add(target)
            elif target == "mouse.wheel":
                ticks = int(value)
                if ticks:
                    self.sink.mouse_scroll(ticks)
                    self.stats.active_targets.add(target)
        self._flush_mouse_motion()
        self._tick_feedback(reader, now)
        self.sink.flush()

    # ------------------------------------------------------------- helpers
    def _press(self, target: str, down: bool) -> None:
        family, _, name = target.partition(".")
        for tap in self.action_taps:
            try:
                tap(target, down, time.monotonic())
            except Exception:
                pass
        if family == "key":
            (self.sink.key_down if down else self.sink.key_up)(name)
        elif family == "mouse":
            (self.sink.mouse_down if down else self.sink.mouse_up)(name)
        elif family == "gamepad":
            self.sink.gamepad_button(name, down)

    def _tick_button(self, b: Binding, st: _BindingState, value: float, now: float, wanted: dict[str, bool]) -> None:
        active = compare(value, b)
        rising = active and not st.active
        falling = (not active) and st.active
        st.active = active
        mode = b.effective_mode()
        down = False
        if mode == "hold":
            down = active
        elif mode == "tap":
            if rising:
                st.release_at = now + b.tap_ms / 1000.0
            down = now < st.release_at
        elif mode == "toggle":
            if rising:
                st.toggled = not st.toggled
            down = st.toggled
        elif mode == "repeat":
            if rising:
                st.release_at = now + b.tap_ms / 1000.0
                st.next_repeat = now + b.repeat_ms / 1000.0
            elif active and now >= st.next_repeat:
                st.release_at = now + b.tap_ms / 1000.0
                st.next_repeat = now + b.repeat_ms / 1000.0
            if falling:
                st.release_at = 0.0
            down = now < st.release_at
        if V.target_kind(b.target) == "button":
            wanted[b.target] = wanted.get(b.target, False) or down
        elif V.target_kind(b.target) == "axis" and down:
            # A button-ish binding driving an axis: full deflection while down.
            self.axis_accumulator[b.target] = self.axis_accumulator.get(b.target, 0.0) + (1.0 if not b.invert else -1.0) * b.scale

    def _smooth(self, st: _BindingState, b: Binding, v: float) -> float:
        if b.smoothing > 0:
            a = min(0.95, b.smoothing)
            st.smoothed = a * st.smoothed + (1 - a) * v
            return st.smoothed
        st.smoothed = v
        return v

    def _tick_axis(self, b: Binding, st: _BindingState, value: float) -> None:
        v = self._smooth(st, b, shape_axis(value, b))
        if V.target_kind(b.target) == "axis":
            self.axis_accumulator[b.target] = self.axis_accumulator.get(b.target, 0.0) + v

    _mouse_dx = 0.0
    _mouse_dy = 0.0

    def _tick_mouse(self, b: Binding, st: _BindingState, value: float, dt: float) -> None:
        # In mouse mode `scale` is a sensitivity multiplier: 1.0 = 600 px/s at full deflection.
        v = self._smooth(st, b, shape_axis(value, b, apply_scale=False))
        delta = v * b.scale * MOUSE_PIXELS_PER_SECOND * dt + st.remainder
        whole = int(delta)
        st.remainder = delta - whole
        if b.target == "mouse.move_x":
            self._mouse_dx += whole
        elif b.target == "mouse.move_y":
            self._mouse_dy -= whole  # screen y grows downwards
        elif b.target == "mouse.wheel":
            self.axis_accumulator[b.target] = self.axis_accumulator.get(b.target, 0.0) + delta / 60.0

    def _flush_mouse_motion(self) -> None:
        dx, dy = int(self._mouse_dx), int(self._mouse_dy)
        if dx or dy:
            self.sink.mouse_move(dx, dy)
            self.stats.active_targets.add("mouse.move")
        self._mouse_dx = 0.0
        self._mouse_dy = 0.0

    def _tick_absolute(self, b: Binding, st: _BindingState, value: float) -> None:
        v = self._smooth(st, b, shape_axis(value, b))  # -1..1
        pos = (v + 1.0) / 2.0
        if b.target == "mouse.abs_x":
            self._screen[0] = pos
        elif b.target == "mouse.abs_y":
            self._screen[1] = 1.0 - pos
        else:
            return
        self.sink.mouse_move_abs(self._screen[0], self._screen[1])
        self.stats.active_targets.add(b.target)

    def _tick_feedback(self, reader: SignalReader, now: float) -> None:
        if self.on_feedback is None:
            return
        rumble_by_ctrl: dict[int, float] = {}
        led_by_ctrl: dict[int, Optional[tuple[int, int, int]]] = {}
        for f, st in zip(self.profile.feedback, self._fb_states):
            idx = f.controller
            if f.rumble_from:
                v = reader.read(f.rumble_from)
                strength = max(0.0, min(1.0, abs(v))) * (f.rumble or 1.0)
                rumble_by_ctrl[idx] = max(rumble_by_ctrl.get(idx, 0.0), strength)
                continue
            if not f.when:
                continue
            v = reader.read(f.when)
            active = abs(v) > f.threshold
            if active and not st.active:
                st.rumble_until = now + f.duration_ms / 1000.0
                st.led_until = now + f.led_duration_ms / 1000.0
            st.active = active
            if f.rumble and now < st.rumble_until:
                rumble_by_ctrl[idx] = max(rumble_by_ctrl.get(idx, 0.0), f.rumble)
            if f.led and now < st.led_until:
                led_by_ctrl[idx] = (int(f.led[0]), int(f.led[1]), int(f.led[2]))
        for idx in (0, 1):
            self.on_feedback(idx, rumble_by_ctrl.get(idx, 0.0), led_by_ctrl.get(idx))
