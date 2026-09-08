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
from themover.core.hits import DrumHitDetector, Hit
from themover.core.state import MoveState, Vec3, WorldState
from themover.devices.camera import CameraSource, CameraThread, open_camera
from themover.devices.psmove import (
    DiscoveredMove,
    HidMoveController,
    MoveController,
    SimulatedMove,
    enumerate_controllers,
)
from themover.devices.tracker import ColorTarget, SphereTracker

log = logging.getLogger(__name__)

NUM_CONTROLLERS = 2
RESCAN_INTERVAL_S = 3.0


class DeviceManager:
    """Two controllers (real or simulated) + optional camera tracking."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.controllers: list[MoveController] = []
        self.filters: list[OrientationFilter] = [OrientationFilter() for _ in range(NUM_CONTROLLERS)]
        self.gestures: list[GestureDetector] = [GestureDetector() for _ in range(NUM_CONTROLLERS)]
        self.hits: list[DrumHitDetector] = [DrumHitDetector() for _ in range(NUM_CONTROLLERS)]
        self.on_hit: Optional[Callable[[int, Hit], None]] = None  # fired from the reader thread
        self.low_latency = False  # True while a rhythm profile is active
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
        self.discovered: list[DiscoveredMove] = []
        self._last_rescan = 0.0

    # ------------------------------------------------------------------ setup
    def _status(self, text: str) -> None:
        self.status_message = text
        log.info(text)
        if self.on_status:
            try:
                self.on_status(text)
            except Exception:
                pass

    # ---------------------------------------------------------- controllers
    def _slot_key(self, index: int) -> str:
        """Identity (serial/path) of the real controller currently in a slot, or ''."""
        if index < len(self.controllers):
            ctrl = self.controllers[index]
            if isinstance(ctrl, HidMoveController) and ctrl.state.connected:
                return ctrl.key
        return ""

    def open_controllers(self) -> None:
        """(Re)build both slots from scratch."""
        with self._lock:
            self.close_controllers()
            self.controllers = [SimulatedMove(i) for i in range(NUM_CONTROLLERS)]
            for i, ctrl in enumerate(self.controllers):
                ctrl.set_led(*self.settings.controller_colors[i])
            self.rescan(force=True)

    def rescan(self, force: bool = False) -> list[DiscoveredMove]:
        """Look for PS Move controllers and fill empty slots.

        Slots are stable: a controller keeps the slot it had last time (remembered
        by Bluetooth address in the settings).  Unknown controllers take the first
        free slot.  Slots with nothing real in them run a simulated controller.
        """
        with self._lock:
            self._last_rescan = time.monotonic()
            if self.settings.controller_backend == "simulated":
                self.discovered = []
                return []
            found = enumerate_controllers()
            self.discovered = found
            if not self.controllers:
                self.controllers = [SimulatedMove(i) for i in range(NUM_CONTROLLERS)]
            remembered = list(self.settings.controller_serials or ["", ""])
            while len(remembered) < NUM_CONTROLLERS:
                remembered.append("")
            in_use = {self._slot_key(i) for i in range(NUM_CONTROLLERS)} - {""}
            available = [d for d in found if d.key not in in_use]
            changed = False

            # Pass 1: controllers that remember their slot.
            for i in range(NUM_CONTROLLERS):
                if self._slot_key(i):
                    continue
                match = next((d for d in available if remembered[i] and d.key == remembered[i]), None)
                if match is not None:
                    available.remove(match)
                    changed |= self._open_slot(i, match)
            # Pass 2: anything else goes into the first free slot.
            for i in range(NUM_CONTROLLERS):
                if self._slot_key(i) or not available:
                    continue
                d = available.pop(0)
                if self._open_slot(i, d):
                    remembered[i] = d.key
                    changed = True
            if remembered != list(self.settings.controller_serials or []):
                self.settings.controller_serials = remembered
            if changed or force:
                self._announce_controllers()
            return found

    def _open_slot(self, index: int, d: DiscoveredMove) -> bool:
        ctrl = HidMoveController(index, d.path, d.model, d.serial, led_method=self.settings.led_method, alt_paths=list(d.paths))
        try:
            ctrl.open()
        except Exception as exc:
            log.warning("controller %s (%s) failed to open: %s", index + 1, d.label, exc)
            self._status(f"Controller {index + 1}: {d.label} could not be opened ({exc})")
            return False
        old = self.controllers[index]
        try:
            old.close()
        except Exception:
            pass
        # One write only: colour first, then send (back-to-back writes get dropped).
        ctrl.set_led(*self.settings.controller_colors[index])
        ctrl.apply_outputs_now()
        self.controllers[index] = ctrl
        self.filters[index] = OrientationFilter()
        self.gestures[index] = GestureDetector(config=self.gestures[index].config)
        self.hits[index] = DrumHitDetector(config=self.hits[index].config)
        self._wire_hits(index)
        if self.low_latency:
            ctrl.start_reader()
        log.info("controller %s <- %s", index + 1, d.label)
        return True

    def _wire_hits(self, index: int) -> None:
        """Feed every IMU frame of a real controller straight into its hit detector."""
        ctrl = self.controllers[index]
        det = self.hits[index]

        def on_frame(accel: Vec3, gyro: Vec3, t: float, trigger: float, move: bool) -> None:
            hit = det.update(accel, t, trigger, move)
            if hit is not None:
                st = ctrl.state
                st.last_hit = f"{hit.kind} {hit.strength:.1f}g"
                st.hit_count = det.hit_count
                if self.on_hit is not None:
                    self.on_hit(index, hit)

        if isinstance(ctrl, HidMoveController):
            ctrl.on_frame = on_frame

    def set_low_latency(self, on: bool) -> None:
        """Rhythm mode: dedicated reader threads so hits are handled the moment a report arrives."""
        with self._lock:
            self.low_latency = on
            for ctrl in self.controllers:
                if isinstance(ctrl, HidMoveController):
                    if on:
                        ctrl.start_reader()
                    else:
                        ctrl.stop_reader()

    def _announce_controllers(self) -> None:
        parts = []
        for i, ctrl in enumerate(self.controllers):
            if isinstance(ctrl, HidMoveController) and ctrl.state.connected:
                parts.append(f"Controller {i + 1}: {ctrl.state.model.upper()} {ctrl.state.serial or ''}".rstrip())
            else:
                parts.append(f"Controller {i + 1}: simulated")
        self._status(" · ".join(parts))

    def swap_controllers(self) -> None:
        """Exchange slots 1 and 2 (and remember the new assignment)."""
        with self._lock:
            if len(self.controllers) < 2:
                return
            a, b = self.controllers[0], self.controllers[1]
            self.controllers[0], self.controllers[1] = b, a
            for i, ctrl in enumerate(self.controllers):
                ctrl.state.index = i
                ctrl.set_led(*self.settings.controller_colors[i])
                ctrl.apply_outputs_now()
            self.filters.reverse()
            self.gestures.reverse()
            self.hits.reverse()
            for i in range(2):
                self._wire_hits(i)
            serials = list(self.settings.controller_serials or ["", ""])
            while len(serials) < 2:
                serials.append("")
            self.settings.controller_serials = [serials[1], serials[0]]
            self._announce_controllers()

    def forget_assignment(self) -> None:
        self.settings.controller_serials = ["", ""]

    def _drop_disconnected(self) -> bool:
        """Replace controllers that vanished with simulated ones; True if any did."""
        dropped = False
        for i, ctrl in enumerate(self.controllers):
            if isinstance(ctrl, HidMoveController) and not ctrl.state.connected:
                log.warning("controller %s disconnected", i + 1)
                try:
                    ctrl.close()
                except Exception:
                    pass
                sim = SimulatedMove(i)
                sim.set_led(*self.settings.controller_colors[i])
                self.controllers[i] = sim
                dropped = True
        return dropped

    def real_controller_count(self) -> int:
        return sum(1 for c in self.controllers if isinstance(c, HidMoveController) and c.state.connected)

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
            if self._drop_disconnected():
                self._announce_controllers()
            if (
                self.settings.controller_backend != "simulated"
                and self.real_controller_count() < NUM_CONTROLLERS
                and now - self._last_rescan >= RESCAN_INTERVAL_S
            ):
                self.rescan()
            for i, ctrl in enumerate(self.controllers):
                ctrl.poll()
                st = ctrl.state
                st.roll, st.pitch, st.yaw = self.filters[i].update(st.accel, st.gyro, dt)
                self.gestures[i].update(st.accel, st.gyro, dt, now)
                if not isinstance(ctrl, HidMoveController):
                    # Simulated controllers have no reader thread: detect hits here.
                    hit = self.hits[i].update(st.accel, now, st.trigger, st.buttons.get("move", False))
                    if hit is not None:
                        st.last_hit = f"{hit.kind} {hit.strength:.1f}g"
                        st.hit_count = self.hits[i].hit_count
                        if self.on_hit is not None:
                            self.on_hit(i, hit)
                else:
                    st.report_rate = ctrl.report_rate
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

    def hit_value(self, index: int, name: str) -> float:
        if index >= len(self.hits):
            return 0.0
        if name == "strength":
            return self.hits[index].strength
        return self.hits[index].value(name)

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
