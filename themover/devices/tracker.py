"""Colour-blob tracking of the glowing PS Move spheres.

Each controller is assigned an RGB colour; the sphere is found as the largest
blob of that hue.  The blob centre gives x/y, its radius gives depth (closer =>
bigger).  Results are normalised to -1..1 (x, y) and 0..1 (depth).
"""
from __future__ import annotations

import colorsys
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

try:
    import cv2  # type: ignore
except Exception:  # pragma: no cover
    cv2 = None  # type: ignore

from themover.core.state import TrackerState


@dataclass
class ColorTarget:
    rgb: tuple[int, int, int]
    hue_tolerance: int = 12  # OpenCV hue units (0..179)
    min_saturation: int = 90
    min_value: int = 110

    @property
    def hue(self) -> int:
        h, _s, _v = colorsys.rgb_to_hsv(self.rgb[0] / 255, self.rgb[1] / 255, self.rgb[2] / 255)
        return int(round(h * 179))

    def mask(self, hsv: np.ndarray) -> np.ndarray:
        h = self.hue
        lo_h, hi_h = h - self.hue_tolerance, h + self.hue_tolerance
        lower = np.array([max(lo_h, 0), self.min_saturation, self.min_value], dtype=np.uint8)
        upper = np.array([min(hi_h, 179), 255, 255], dtype=np.uint8)
        mask = cv2.inRange(hsv, lower, upper)
        # Hue wraps around at red.
        if lo_h < 0:
            mask |= cv2.inRange(hsv, np.array([180 + lo_h, self.min_saturation, self.min_value], dtype=np.uint8), np.array([179, 255, 255], dtype=np.uint8))
        if hi_h > 179:
            mask |= cv2.inRange(hsv, np.array([0, self.min_saturation, self.min_value], dtype=np.uint8), np.array([hi_h - 180, 255, 255], dtype=np.uint8))
        return mask


@dataclass
class DepthCalibration:
    radius_far: float = 8.0  # pixels when the player stands far away
    radius_near: float = 60.0  # pixels when the player is close

    def depth(self, radius: float) -> float:
        span = max(self.radius_near - self.radius_far, 1e-3)
        return float(min(1.0, max(0.0, (radius - self.radius_far) / span)))


@dataclass
class Detection:
    x: float  # pixels
    y: float
    radius: float
    area: float


@dataclass
class SphereTracker:
    targets: list[ColorTarget]
    depth: DepthCalibration = field(default_factory=DepthCalibration)
    min_radius: float = 3.0
    smoothing: float = 0.5  # 0 = raw, 1 = frozen
    mirror: bool = True
    lost_timeout_s: float = 0.35
    states: list[TrackerState] = field(default_factory=list)
    last_detections: list[Optional[Detection]] = field(default_factory=list)
    frame_size: tuple[int, int] = (640, 480)

    def __post_init__(self) -> None:
        self.states = [TrackerState() for _ in self.targets]
        self.last_detections = [None for _ in self.targets]

    def set_color(self, index: int, rgb: tuple[int, int, int]) -> None:
        self.targets[index].rgb = tuple(int(c) for c in rgb)  # type: ignore[assignment]

    def detect(self, frame: np.ndarray, index: int) -> Optional[Detection]:
        if cv2 is None:
            return None
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        return self._detect_in_hsv(hsv, index)

    def _detect_in_hsv(self, hsv: np.ndarray, index: int) -> Optional[Detection]:
        mask = self.targets[index].mask(hsv)
        kernel = np.ones((5, 5), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_DILATE, kernel)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None
        best = max(contours, key=cv2.contourArea)
        area = float(cv2.contourArea(best))
        (cx, cy), radius = cv2.minEnclosingCircle(best)
        if radius < self.min_radius:
            return None
        return Detection(float(cx), float(cy), float(radius), area)

    def process(self, frame: np.ndarray, now: float | None = None) -> list[TrackerState]:
        """Update every controller's tracker state from a new frame."""
        now = time.monotonic() if now is None else now
        if cv2 is None:
            return self.states
        h, w = frame.shape[:2]
        self.frame_size = (w, h)
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        for i, _target in enumerate(self.targets):
            det = self._detect_in_hsv(hsv, i)
            self.last_detections[i] = det
            st = self.states[i]
            if det is None:
                if st.tracked and now - st.last_seen > self.lost_timeout_s:
                    st.tracked = False
                    st.vx = st.vy = 0.0
                continue
            nx = (det.x / w) * 2.0 - 1.0
            ny = -((det.y / h) * 2.0 - 1.0)
            if self.mirror:
                nx = -nx
            depth = self.depth.depth(det.radius)
            if st.tracked:
                dt = max(now - st.last_seen, 1e-3)
                a = self.smoothing
                new_x = a * st.x + (1 - a) * nx
                new_y = a * st.y + (1 - a) * ny
                st.vx = 0.7 * st.vx + 0.3 * ((new_x - st.x) / dt)
                st.vy = 0.7 * st.vy + 0.3 * ((new_y - st.y) / dt)
                st.x, st.y = new_x, new_y
                st.radius = a * st.radius + (1 - a) * det.radius
                st.depth = a * st.depth + (1 - a) * depth
            else:
                st.x, st.y, st.radius, st.depth = nx, ny, det.radius, depth
                st.vx = st.vy = 0.0
            st.tracked = True
            st.last_seen = now
        return self.states

    def sample_color_at(self, frame: np.ndarray, x: int, y: int, size: int = 9) -> tuple[int, int, int]:
        """Average colour around a pixel (used by the 'click the sphere' calibration)."""
        h, w = frame.shape[:2]
        x0, x1 = max(0, x - size // 2), min(w, x + size // 2 + 1)
        y0, y1 = max(0, y - size // 2), min(h, y + size // 2 + 1)
        patch = frame[y0:y1, x0:x1].reshape(-1, 3).astype(np.float32)
        b, g, r = patch.mean(axis=0)
        # Push saturation up so the tracker keys on hue rather than brightness.
        hh, ss, vv = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        rr, gg, bb = colorsys.hsv_to_rgb(hh, max(ss, 0.9), 1.0)
        return int(rr * 255), int(gg * 255), int(bb * 255)

    def draw_overlay(self, frame: np.ndarray) -> np.ndarray:
        if cv2 is None:
            return frame
        out = frame.copy()
        for i, det in enumerate(self.last_detections):
            if det is None:
                continue
            rgb = self.targets[i].rgb
            bgr = (int(rgb[2]), int(rgb[1]), int(rgb[0]))
            cv2.circle(out, (int(det.x), int(det.y)), int(det.radius), (255, 255, 255), 2)
            cv2.circle(out, (int(det.x), int(det.y)), max(int(det.radius) + 4, 6), bgr, 2)
            st = self.states[i]
            cv2.putText(
                out,
                f"P{i + 1} d={st.depth:.2f}",
                (int(det.x) - 30, int(det.y) - int(det.radius) - 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (255, 255, 255),
                1,
                cv2.LINE_AA,
            )
        return out
