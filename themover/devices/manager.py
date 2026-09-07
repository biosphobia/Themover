"""Owns the controllers, the camera thread and the per-controller filters."""
from __future__ import annotations

import copy
import logging
import threading
import time
from typing import Callable, Optional

import numpy as np

from themover.config import Settings
from themover.core.fusion import OrientationFilter
from themover.core.gestures import GestureDetector
from themover.core.state import MoveState, WorldState
from themover.devices.camera import CameraSource, CameraThread, open_camera
from themover.devices.psmove import (
    HidMoveController,
    MoveController,
    SimulatedMove,
    enumerate_controllers,
)
from themover.devices.tracker import ColorTarget, SphereTracker

log = logging.getLogger(__name__)

NUM_CONTROLLERS = 2


class DeviceManager:
    """Two controllers (real or simulated) + optional camera tracking."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.controllers: list[MoveController] = []
        self.filters: list[OrientationFilter] = [OrientationFilter() for _ in range(NUM_CONTROLLERS)]
        self.gestures: list[GestureDetector] = [GestureDetector() for _ in range(NUM_CONTROLLERS)]
        self.tracker = SphereTracker(
            [ColorTarget(tuple(c)) for c in settings.controller_colors[:NUM_CONTROLLERS]],
            mirror=settings.camera_mirror,
        )
        self.camera: Optional[CameraThread] = None
        self._lock = threading.RLock()
        self._last_tick = time.monotonic()
        self.status_message = ""
        self.on_status: Optional[Callable[[str], None]] = None
        self.wheel_angle = 0.0  # degrees, from the two tracked spheres or roll of P1
        self.wheel_source = "roll"

    # ------------------------------------------------------------------ setup
    def _status(self, text: str) -> None:
        self.status_message = text
        log.info(text)
        if self.on_status:
            try:
                self.on_status(text)
            except Exception:
                pass

    def open_controllers(self) -> None:
        with self._lock:
            self.close_controllers()
            found = enumerate_controllers() if self.settings.controller_backend in ("auto", "hid") else []
            for i in range(NUM_CONTROLLERS):
                ctrl: MoveController
                if i < len(found) and self.settings.controller_backend != "simulated":
                    d = found[i]
                    ctrl = HidMoveController(i, d.path, d.model, d.serial)
                    try:
                        ctrl.open()
                        self._status(f"Controller {i + 1}: PS Move ({d.model.upper()}, {d.interface})")
                    except Exception as exc:
                        log.warning("controller %s failed to open: %s", i, exc)
                        ctrl = SimulatedMove(i)
                        self._status(f"Controller {i + 1}: could not open ({exc}); using simulated")
                else:
                    ctrl = SimulatedMove(i)
                    self._status(f"Controller {i + 1}: simulated (no PS Move found)")
                color = self.settings.controller_colors[i]
                ctrl.set_led(*color)
                self.controllers.append(ctrl)
            for ctrl in self.controllers:
                ctrl.apply_outputs_now()

    def close_controllers(self) -> None:
        for ctrl in self.controllers:
            try:
                ctrl.close()
            except Exception:
                pass
        self.controllers = []

    def open_camera(self) -> None:
        self.close_camera()
        colors = [tuple(c) for c in self.settings.controller_colors]
        try:
            source: CameraSource = open_camera(self.settings.camera_backend, self.settings.camera_index, colors)
        except Exception as exc:
            self._status(f"Camera: {exc}")
            return
        self.camera = CameraThread(source)
        self.camera.on_frame = self._on_frame
        self.camera.start()
        self._status(f"Camera: {source.name} ({source.width}x{source.height})")

    def close_camera(self) -> None:
        if self.camera is not None:
            self.camera.stop()
            self.camera = None

    def start(self) -> None:
        self.open_controllers()
        self.open_camera()

    def stop(self) -> None:
        self.close_camera()
        self.close_controllers()

    # ---------------------------------------------------------------- runtime
    def _on_frame(self, frame: np.ndarray) -> None:
        states = self.tracker.process(frame)
        with self._lock:
            for i, ctrl in enumerate(self.controllers):
                if i < len(states):
                    ctrl.state.tracker = states[i]

    def update(self) -> WorldState:
        """Poll all controllers and refresh derived signals.  Called every tick."""
        now = time.monotonic()
        dt = max(1e-4, min(0.1, now - self._last_tick))
        self._last_tick = now
        with self._lock:
            for i, ctrl in enumerate(self.controllers):
                ctrl.poll()
                st = ctrl.state
                st.roll, st.pitch, st.yaw = self.filters[i].update(st.accel, st.gyro, dt)
                self.gestures[i].update(st.accel, st.gyro, dt, now)
                ctrl.flush_outputs()
            self._update_wheel()
            world = WorldState(controllers=[copy.deepcopy(c.state) for c in self.controllers], t=now, dt=dt)
        return world

    def _update_wheel(self) -> None:
        import math

        if len(self.controllers) >= 2:
            a, b = self.controllers[0].state.tracker, self.controllers[1].state.tracker
            if a.tracked and b.tracked:
                # Angle of the line between the two spheres, 0 = level.
                dx, dy = a.x - b.x, a.y - b.y
                if dx < 0:
                    dx, dy = -dx, -dy
                self.wheel_angle = math.degrees(math.atan2(dy, dx))
                self.wheel_source = "camera"
                return
        if self.controllers:
            self.wheel_angle = self.controllers[0].state.roll
            self.wheel_source = "roll"

    def gesture_value(self, index: int, name: str) -> float:
        if index >= len(self.gestures):
            return 0.0
        return self.gestures[index].value(name)

    def gesture_strength(self, index: int) -> float:
        if index >= len(self.gestures):
            return 0.0
        return self.gestures[index].strength

    # ---------------------------------------------------------------- outputs
    def set_led(self, index: int, rgb: tuple[int, int, int]) -> None:
        with self._lock:
            if index < len(self.controllers):
                self.controllers[index].set_led(*rgb)

    def set_rumble(self, index: int, strength: float) -> None:
        with self._lock:
            if index < len(self.controllers):
                self.controllers[index].set_rumble(strength)

    def set_color(self, index: int, rgb: tuple[int, int, int]) -> None:
        """Change the tracked/LED colour of one controller."""
        rgb = tuple(int(c) for c in rgb)  # type: ignore[assignment]
        self.settings.controller_colors[index] = list(rgb)
        self.tracker.set_color(index, rgb)
        self.set_led(index, rgb)
        if self.camera is not None and hasattr(self.camera.source, "colors"):
            self.camera.source.colors[index] = rgb  # type: ignore[attr-defined]

    def latest_frame(self, overlay: bool = True) -> Optional[np.ndarray]:
        if self.camera is None:
            return None
        frame = self.camera.latest()
        if frame is None:
            return None
        return self.tracker.draw_overlay(frame) if overlay else frame

    def controller_state(self, index: int) -> Optional[MoveState]:
        if index < len(self.controllers):
            return self.controllers[index].state
        return None

    def reset_yaw(self) -> None:
        for f in self.filters:
            f.reset_yaw()
