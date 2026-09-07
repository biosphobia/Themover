"""Orientation estimation from the PS Move IMU.

A small complementary filter: the gyroscope gives fast, smooth changes; the
accelerometer (gravity) slowly pulls roll/pitch back so they never drift.
Yaw has no absolute reference (the magnetometer is unreliable indoors) so it is
integrated from the gyro and gently decays towards zero - it therefore behaves as
"how far have I turned recently", which is exactly what pointing needs.

Axis convention (controller held upright, sphere on top, buttons facing you):
``x`` right, ``y`` up along the handle towards the sphere, ``z`` towards you.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from themover.core.state import Vec3


def wrap_degrees(angle: float) -> float:
    while angle > 180.0:
        angle -= 360.0
    while angle < -180.0:
        angle += 360.0
    return angle


@dataclass
class OrientationFilter:
    alpha: float = 0.03  # weight of the accelerometer correction per update
    yaw_decay_per_second: float = 0.15
    roll: float = 0.0
    pitch: float = 0.0
    yaw: float = 0.0
    gravity: Vec3 = field(default_factory=lambda: Vec3(0.0, 1.0, 0.0))
    _initialised: bool = False

    @staticmethod
    def angles_from_gravity(accel: Vec3) -> tuple[float, float]:
        """Roll (rotation about the handle) and pitch (handle above horizon)."""
        roll = math.degrees(math.atan2(accel.x, accel.z)) if (accel.x or accel.z) else 0.0
        pitch = math.degrees(math.atan2(accel.y, math.sqrt(accel.x**2 + accel.z**2)))
        return wrap_degrees(roll), pitch

    def update(self, accel: Vec3, gyro: Vec3, dt: float) -> tuple[float, float, float]:
        # Low-pass gravity so swings don't yank the orientation around.
        g = self.gravity
        k = 0.1 if self._initialised else 1.0
        g.x += (accel.x - g.x) * k
        g.y += (accel.y - g.y) * k
        g.z += (accel.z - g.z) * k

        roll_acc, pitch_acc = self.angles_from_gravity(g)
        if not self._initialised:
            self.roll, self.pitch, self.yaw = roll_acc, pitch_acc, 0.0
            self._initialised = True
            return self.roll, self.pitch, self.yaw

        # Gyro integration (degrees).
        self.roll = wrap_degrees(self.roll + math.degrees(gyro.y) * dt)
        self.pitch = self.pitch + math.degrees(gyro.x) * dt
        self.yaw = wrap_degrees(self.yaw - math.degrees(gyro.z) * dt)

        # Accelerometer correction with the shortest angular path.
        self.roll = wrap_degrees(self.roll + wrap_degrees(roll_acc - self.roll) * self.alpha)
        self.pitch = self.pitch + (pitch_acc - self.pitch) * self.alpha
        # Gentle yaw re-centering.
        self.yaw *= max(0.0, 1.0 - self.yaw_decay_per_second * dt)
        return self.roll, self.pitch, self.yaw

    def reset_yaw(self) -> None:
        self.yaw = 0.0
