"""Motion gesture detection for one controller.

Produces named *pulse* signals (1.0 for a short time after the gesture fires)
plus a few continuous helper signals.  Everything is based on linear
acceleration (accelerometer with gravity removed) so it works even when the
gyroscope scale is only approximately calibrated.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from themover.core.state import Vec3

# Opposite directions of the same axis share a cooldown: one swing has an
# acceleration phase and a stop phase, and must count as ONE gesture.
GESTURE_AXIS = {
    "swing_left": "x", "swing_right": "x",
    "swing_up": "z", "swing_down": "z",
    "thrust": "y", "pull": "y",
}

GESTURE_NAMES = (
    "swing_any",
    "swing_left",
    "swing_right",
    "swing_up",
    "swing_down",
    "thrust",
    "pull",
    "shake",
    "flick",
)


@dataclass
class GestureConfig:
    swing_threshold_g: float = 1.1  # linear acceleration needed to count as a swing
    thrust_threshold_g: float = 1.4
    flick_threshold_dps: float = 400.0  # angular speed for a wrist flick
    cooldown_s: float = 0.22
    pulse_s: float = 0.12
    shake_window_s: float = 0.7
    shake_reversals: int = 3


@dataclass
class GestureDetector:
    config: GestureConfig = field(default_factory=GestureConfig)
    gravity: Vec3 = field(default_factory=lambda: Vec3(0.0, 1.0, 0.0))
    linear: Vec3 = field(default_factory=Vec3)
    strength: float = 0.0  # current linear acceleration magnitude (g)
    angular_speed: float = 0.0  # deg/s
    _pulses: dict[str, float] = field(default_factory=dict)
    _last_fire: dict[str, float] = field(default_factory=dict)
    _reversal_times: list[float] = field(default_factory=list)
    _last_sign: int = 0
    _initialised: bool = False

    def update(self, accel: Vec3, gyro: Vec3, dt: float, now: float | None = None) -> list[str]:
        """Feed a sample; returns the list of gestures that fired this update."""
        now = time.monotonic() if now is None else now
        g = self.gravity
        k = 0.05 if self._initialised else 1.0
        self._initialised = True
        g.x += (accel.x - g.x) * k
        g.y += (accel.y - g.y) * k
        g.z += (accel.z - g.z) * k
        lin = Vec3(accel.x - g.x, accel.y - g.y, accel.z - g.z)
        self.linear = lin
        self.strength = lin.magnitude()
        self.angular_speed = math.degrees(gyro.magnitude())

        fired: list[str] = []
        cfg = self.config

        if self.strength >= cfg.swing_threshold_g:
            ax, ay, az = abs(lin.x), abs(lin.y), abs(lin.z)
            dominant = max(ax, ay, az)
            if dominant == ay and self.strength >= cfg.thrust_threshold_g:
                fired.append("thrust" if lin.y > 0 else "pull")
            elif dominant == ax:
                fired.append("swing_right" if lin.x > 0 else "swing_left")
            elif dominant == az:
                fired.append("swing_up" if lin.z < 0 else "swing_down")

        if self.angular_speed >= cfg.flick_threshold_dps:
            fired.append("flick")

        # Shake: rapid direction reversals of the strongest linear axis.
        sign = 0
        if self.strength >= cfg.swing_threshold_g * 0.6:
            comp = max((lin.x, lin.y, lin.z), key=abs)
            sign = 1 if comp > 0 else -1
        if sign and self._last_sign and sign != self._last_sign:
            self._reversal_times.append(now)
        if sign:
            self._last_sign = sign
        self._reversal_times = [t for t in self._reversal_times if now - t <= cfg.shake_window_s]
        if len(self._reversal_times) >= cfg.shake_reversals:
            fired.append("shake")
            self._reversal_times.clear()

        result: list[str] = []
        for name in fired:
            key = GESTURE_AXIS.get(name, name)
            last = self._last_fire.get(key, -1e9)
            if now - last >= cfg.cooldown_s:
                self._last_fire[key] = now
                self._pulses[name] = now + cfg.pulse_s
                result.append(name)
                if name in GESTURE_AXIS:
                    self._pulses["swing_any"] = now + cfg.pulse_s
                    result.append("swing_any")
        return result

    def value(self, name: str, now: float | None = None) -> float:
        now = time.monotonic() if now is None else now
        until = self._pulses.get(name)
        if until is None:
            return 0.0
        if now > until:
            return 0.0
        return 1.0
