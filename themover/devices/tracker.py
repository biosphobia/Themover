"""Fast tracking of the two glowing PS Move spheres in the PS3 Eye picture.

Pipeline (about 1 ms per 640x480 frame):

1. crop the frame to the *tracking area* the player chose and shrink it,
2. keep only bright, saturated pixels (the lit spheres; the room is dark and
   dull by comparison) - the "mask to the lights" option,
3. label the blobs (connected components) and measure each blob's hue,
4. assign blobs to controllers by hue distance + distance to where each
   sphere was last seen, one blob per controller,
5. refine the centre at full resolution with a brightness-weighted centroid
   (sub-pixel) and take the size from the lit pixel count.

x/y are normalised to -1..1 inside the tracking area, depth 0..1 from the
sphere's radius.  A *trigger zone* can gate the result: outside it the
sphere still shows in the overlay but ``tracked`` (and ``in_zone``) are 0 so
camera bindings stay idle.

``calibrate()`` measures the real hue of each sphere and picks thresholds
from a few frames with both controllers lit.
"""
from __future__ import annotations

import colorsys
import math
import time
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Optional

import numpy as np

try:
    import cv2  # type: ignore
except Exception:  # pragma: no cover
    cv2 = None  # type: ignore

from themover.core.state import TrackerState


def hue_of(rgb: tuple[int, int, int]) -> float:
    h, _s, _v = colorsys.rgb_to_hsv(rgb[0] / 255, rgb[1] / 255, rgb[2] / 255)
    return h * 180.0


def hue_distance(a: float, b: float) -> float:
    d = abs(a - b) % 180.0
    return min(d, 180.0 - d)


def rgb_from_hue(hue: float) -> tuple[int, int, int]:
    r, g, b = colorsys.hsv_to_rgb((hue % 180.0) / 180.0, 1.0, 1.0)
    return int(round(r * 255)), int(round(g * 255)), int(round(b * 255))


@dataclass
class TrackingConfig:
    """Everything the tracker needs; saved in settings.json under ``tracking``."""

    crop: list[float] = field(default_factory=lambda: [0.0, 0.0, 1.0, 1.0])  # tracking area, fractions of the frame
    zone: list[float] = field(default_factory=lambda: [0.0, 0.0, 1.0, 1.0])  # trigger zone, fractions of the frame
    zone_enabled: bool = False
    mask_lights: bool = True  # keep only bright, saturated pixels (the lit spheres)
    min_brightness: int = 150  # V threshold 0..255 (used when mask_lights)
    min_saturation: int = 70  # S threshold 0..255
    hue_tolerance: int = 22  # max hue distance (0..90) between a blob and a controller's colour
    min_radius: float = 3.0  # pixels at full resolution
    max_radius: float = 140.0
    smoothing: float = 0.25  # 0 raw .. 0.9 very smooth
    mirror: bool = True
    downscale: int = 2  # detect at 1/n resolution, refine at full
    radius_far: float = 8.0  # depth calibration
    radius_near: float = 60.0
    lost_timeout_s: float = 0.35
    exposure: int = -7  # camera control (OpenCV: log2 seconds, -13..0; PS3 Eye via pseyepy: 0..255)
    gain: int = 20

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[dict[str, Any]]) -> "TrackingConfig":
        cfg = cls()
        for f in fields(cls):
            if data and f.name in data and data[f.name] is not None:
                try:
                    v = data[f.name]
                    if f.name in ("crop", "zone"):
                        v = _norm_rect([float(x) for x in v])
                    else:
                        v = type(getattr(cfg, f.name))(v)
                    setattr(cfg, f.name, v)
                except (TypeError, ValueError):
                    pass
        cfg.downscale = max(1, min(4, int(cfg.downscale)))
        cfg.smoothing = max(0.0, min(0.95, float(cfg.smoothing)))
        cfg.hue_tolerance = max(3, min(90, int(cfg.hue_tolerance)))
        cfg.min_brightness = max(0, min(255, int(cfg.min_brightness)))
        cfg.min_saturation = max(0, min(255, int(cfg.min_saturation)))
        return cfg

    def depth(self, radius: float) -> float:
        span = max(self.radius_near - self.radius_far, 1e-3)
        return float(min(1.0, max(0.0, (radius - self.radius_far) / span)))


def _norm_rect(r: list[float]) -> list[float]:
    x0, y0, x1, y1 = [min(1.0, max(0.0, float(v))) for v in (r + [0, 0, 1, 1])[:4]]
    x0, x1 = min(x0, x1), max(x0, x1)
    y0, y1 = min(y0, y1), max(y0, y1)
    if x1 - x0 < 0.05:
        x1 = min(1.0, x0 + 0.05)
    if y1 - y0 < 0.05:
        y1 = min(1.0, y0 + 0.05)
    return [x0, y0, x1, y1]


@dataclass
class ColorTarget:
    rgb: tuple[int, int, int]

    @property
    def hue(self) -> float:
        return hue_of(self.rgb)


@dataclass
class Detection:
    x: float  # full-frame pixels
    y: float
    radius: float
    area: float
    hue: float = 0.0
    hue_error: float = 0.0  # distance to the controller's colour
    brightness: float = 0.0
    in_zone: bool = True


@dataclass
class _Blob:
    x: float  # full-frame pixels (approximate, from the shrunken image)
    y: float
    area: float  # full-resolution pixel count
    hue: float
    bbox: tuple[int, int, int, int]  # x, y, w, h in the shrunken crop
    brightness: float


class SphereTracker:
    def __init__(self, targets: list[ColorTarget], config: Optional[TrackingConfig] = None, **overrides: Any) -> None:
        self.targets = targets
        self.config = config or TrackingConfig()
        for k, v in overrides.items():
            if not hasattr(self.config, k):
                raise TypeError(f"unknown tracker option {k}")
            setattr(self.config, k, v)
        self.states: list[TrackerState] = [TrackerState() for _ in targets]
        self.last_detections: list[Optional[Detection]] = [None for _ in targets]
        self.last_blobs: list[_Blob] = []
        self.frame_size: tuple[int, int] = (640, 480)
        self.process_ms = 0.0
        self.last_mask: Optional[np.ndarray] = None

    # ------------------------------------------------------------ helpers
    def set_color(self, index: int, rgb: tuple[int, int, int]) -> None:
        self.targets[index].rgb = tuple(int(c) for c in rgb)  # type: ignore[assignment]

    def crop_px(self, w: int, h: int) -> tuple[int, int, int, int]:
        x0, y0, x1, y1 = self.config.crop
        cx0, cy0 = int(x0 * w), int(y0 * h)
        cx1, cy1 = max(cx0 + 8, int(math.ceil(x1 * w))), max(cy0 + 8, int(math.ceil(y1 * h)))
        return cx0, cy0, min(w, cx1), min(h, cy1)

    def zone_px(self, w: int, h: int) -> tuple[int, int, int, int]:
        x0, y0, x1, y1 = self.config.zone
        return int(x0 * w), int(y0 * h), int(math.ceil(x1 * w)), int(math.ceil(y1 * h))

    def _thresholds(self) -> tuple[int, int]:
        cfg = self.config
        if cfg.mask_lights:
            return cfg.min_saturation, cfg.min_brightness
        return max(30, min(cfg.min_saturation, 80)), 40

    # ------------------------------------------------------------ detection
    def find_blobs(self, frame: np.ndarray) -> list[_Blob]:
        """Bright saturated blobs inside the tracking area, with their mean hue (full-frame pixel coordinates)."""
        if cv2 is None:
            return []
        h, w = frame.shape[:2]
        self.frame_size = (w, h)
        cfg = self.config
        cx0, cy0, cx1, cy1 = self.crop_px(w, h)
        roi = frame[cy0:cy1, cx0:cx1]
        ds = cfg.downscale
        small = cv2.resize(roi, ((cx1 - cx0) // ds, (cy1 - cy0) // ds), interpolation=cv2.INTER_AREA) if ds > 1 else roi
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        min_s, min_v = self._thresholds()
        mask = cv2.inRange(hsv, (0, min_s, min_v), (179, 255, 255))
        self.last_mask = mask
        n, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
        blobs: list[_Blob] = []
        min_area = math.pi * (cfg.min_radius / ds) ** 2 * 0.5
        max_area = math.pi * (cfg.max_radius / ds) ** 2 * 1.5
        order = np.argsort(-stats[1:, cv2.CC_STAT_AREA]) + 1 if n > 1 else []
        for k in order[:8]:
            area = float(stats[k, cv2.CC_STAT_AREA])
            if area < min_area:
                break
            if area > max_area:
                continue
            bx, by, bw, bh = (int(stats[k, cv2.CC_STAT_LEFT]), int(stats[k, cv2.CC_STAT_TOP]), int(stats[k, cv2.CC_STAT_WIDTH]), int(stats[k, cv2.CC_STAT_HEIGHT]))
            sel = labels[by:by + bh, bx:bx + bw] == k
            patch = hsv[by:by + bh, bx:bx + bw]
            hues = patch[..., 0][sel].astype(np.float32)
            sats = patch[..., 1][sel].astype(np.float32) + 1.0
            vals = patch[..., 2][sel].astype(np.float32)
            ang = hues * (2.0 * math.pi / 180.0)
            hue = math.degrees(math.atan2(float((np.sin(ang) * sats).sum()), float((np.cos(ang) * sats).sum()))) / 2.0 % 180.0
            blobs.append(_Blob(cx0 + float(centroids[k][0]) * ds, cy0 + float(centroids[k][1]) * ds, area * ds * ds, hue, (bx, by, bw, bh), float(vals.mean())))
        self.last_blobs = blobs
        return blobs

    def _refine(self, frame: np.ndarray, blob: _Blob) -> tuple[float, float, float]:
        """Sub-pixel centre and radius from the full-resolution lit pixels around a blob."""
        h, w = frame.shape[:2]
        ds = self.config.downscale
        cx0, cy0, _cx1, _cy1 = self.crop_px(w, h)
        bx, by, bw, bh = blob.bbox
        m = max(3, ds * 2)
        x0, y0 = max(0, cx0 + bx * ds - m), max(0, cy0 + by * ds - m)
        x1, y1 = min(w, cx0 + (bx + bw) * ds + m), min(h, cy0 + (by + bh) * ds + m)
        win = frame[y0:y1, x0:x1]
        if win.size == 0:
            return blob.x, blob.y, math.sqrt(blob.area / math.pi)
        v = win.max(axis=2).astype(np.float32)  # HSV value == max(B, G, R)
        _min_s, min_v = self._thresholds()
        lit = v >= min_v
        if not lit.any():
            return blob.x, blob.y, math.sqrt(blob.area / math.pi)
        weights = np.where(lit, v, 0.0)
        total = float(weights.sum())
        ys, xs = np.mgrid[y0:y1, x0:x1]
        x = float((weights * xs).sum() / total)
        y = float((weights * ys).sum() / total)
        radius = math.sqrt(float(lit.sum()) / math.pi)
        return x, y, radius

    def assign(self, blobs: list[_Blob], now: float) -> list[Optional[_Blob]]:
        """One blob per controller: closest hue, then closest to where it was last seen."""
        cfg = self.config
        w, h = self.frame_size
        diag = math.hypot(w, h)
        pairs: list[tuple[float, int, int]] = []
        for ti, target in enumerate(self.targets):
            st = self.states[ti]
            th = target.hue
            for bi, blob in enumerate(blobs):
                err = hue_distance(blob.hue, th)
                if err > cfg.hue_tolerance:
                    continue
                score = err / cfg.hue_tolerance
                if st.tracked and now - st.last_seen < 0.5:
                    px, py = self._to_pixels(st.x, st.y)
                    score += 1.5 * math.hypot(blob.x - px, blob.y - py) / diag
                score -= 0.05 * min(1.0, blob.area / 400.0)
                pairs.append((score, ti, bi))
        pairs.sort()
        out: list[Optional[_Blob]] = [None] * len(self.targets)
        used: set[int] = set()
        for _score, ti, bi in pairs:
            if out[ti] is None and bi not in used:
                out[ti] = blobs[bi]
                used.add(bi)
        return out

    def _to_pixels(self, nx: float, ny: float) -> tuple[float, float]:
        w, h = self.frame_size
        cx0, cy0, cx1, cy1 = self.crop_px(w, h)
        if self.config.mirror:
            nx = -nx
        return cx0 + (nx + 1.0) / 2.0 * (cx1 - cx0), cy0 + (1.0 - (ny + 1.0) / 2.0) * (cy1 - cy0)

    def _normalise(self, x: float, y: float) -> tuple[float, float]:
        w, h = self.frame_size
        cx0, cy0, cx1, cy1 = self.crop_px(w, h)
        nx = (x - cx0) / max(1, cx1 - cx0) * 2.0 - 1.0
        ny = -((y - cy0) / max(1, cy1 - cy0) * 2.0 - 1.0)
        if self.config.mirror:
            nx = -nx
        return max(-1.0, min(1.0, nx)), max(-1.0, min(1.0, ny))

    def in_zone(self, x: float, y: float) -> bool:
        if not self.config.zone_enabled:
            return True
        w, h = self.frame_size
        zx0, zy0, zx1, zy1 = self.zone_px(w, h)
        return zx0 - 0.5 <= x <= zx1 + 0.5 and zy0 - 0.5 <= y <= zy1 + 0.5

    # ------------------------------------------------------------ main entry
    def detect(self, frame: np.ndarray, index: int) -> Optional[Detection]:
        """One controller, no state update (used by tests / tools)."""
        blobs = self.find_blobs(frame)
        chosen = self.assign(blobs, time.monotonic())[index]
        if chosen is None:
            return None
        x, y, r = self._refine(frame, chosen)
        return Detection(x, y, r, chosen.area, chosen.hue, hue_distance(chosen.hue, self.targets[index].hue), chosen.brightness, self.in_zone(x, y))

    def process(self, frame: np.ndarray, now: Optional[float] = None) -> list[TrackerState]:
        """Update every controller's tracker state from a new frame."""
        now = time.monotonic() if now is None else now
        if cv2 is None:
            return self.states
        t0 = time.perf_counter()
        cfg = self.config
        blobs = self.find_blobs(frame)
        chosen = self.assign(blobs, now)
        for i, blob in enumerate(chosen):
            st = self.states[i]
            if blob is None:
                self.last_detections[i] = None
                if st.tracked and now - st.last_seen > cfg.lost_timeout_s:
                    st.tracked = False
                    st.in_zone = False
                    st.vx = st.vy = 0.0
                continue
            x, y, r = self._refine(frame, blob)
            det = Detection(x, y, r, blob.area, blob.hue, hue_distance(blob.hue, self.targets[i].hue), blob.brightness, self.in_zone(x, y))
            self.last_detections[i] = det
            if not det.in_zone:
                st.tracked = False
                st.in_zone = False
                st.vx = st.vy = 0.0
                st.last_seen = now
                continue
            nx, ny = self._normalise(x, y)
            depth = cfg.depth(r)
            if st.tracked and now - st.last_seen < cfg.lost_timeout_s:
                dt = max(now - st.last_seen, 1e-3)
                a = cfg.smoothing
                new_x = a * st.x + (1 - a) * nx
                new_y = a * st.y + (1 - a) * ny
                st.vx = 0.7 * st.vx + 0.3 * ((new_x - st.x) / dt)
                st.vy = 0.7 * st.vy + 0.3 * ((new_y - st.y) / dt)
                st.x, st.y = new_x, new_y
                st.radius = a * st.radius + (1 - a) * r
                st.depth = a * st.depth + (1 - a) * depth
            else:
                st.x, st.y, st.radius, st.depth = nx, ny, r, depth
                st.vx = st.vy = 0.0
            st.tracked = True
            st.in_zone = True
            st.last_seen = now
        self.process_ms = 0.8 * self.process_ms + 0.2 * (time.perf_counter() - t0) * 1000.0
        return self.states

    # ------------------------------------------------------------ calibration
    def sample_color_at(self, frame: np.ndarray, x: int, y: int, size: int = 9) -> tuple[int, int, int]:
        """Colour around a pixel, pushed to full saturation so the tracker keys on hue (click-the-sphere)."""
        h, w = frame.shape[:2]
        x0, x1 = max(0, x - size // 2), min(w, x + size // 2 + 1)
        y0, y1 = max(0, y - size // 2), min(h, y + size // 2 + 1)
        patch = frame[y0:y1, x0:x1].reshape(-1, 3).astype(np.float32)
        b, g, r = patch.mean(axis=0)
        hh, _ss, _vv = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        return rgb_from_hue(hh * 180.0)

    def calibrate(self, frames: list[np.ndarray]) -> dict[str, Any]:
        """Measure the spheres in a few frames (both controllers lit) and pick colours + thresholds.

        Returns ``{"colors": [rgb, rgb] | None, "config": TrackingConfig, "report": str}``;
        nothing is applied here.
        """
        if cv2 is None or not frames:
            return {"colors": None, "config": self.config, "report": "no frames"}
        saved = (self.config.mask_lights, self.config.min_brightness, self.config.min_saturation, self.config.hue_tolerance)
        cfg = self.config
        cfg.mask_lights, cfg.min_brightness, cfg.min_saturation, cfg.hue_tolerance = True, 110, 50, 90
        try:
            all_blobs: list[_Blob] = []
            bg_v: list[float] = []
            for frame in frames:
                blobs = self.find_blobs(frame)
                all_blobs += [b for b in blobs if b.area >= math.pi * cfg.min_radius ** 2]
                h, w = frame.shape[:2]
                cx0, cy0, cx1, cy1 = self.crop_px(w, h)
                v = frame[cy0:cy1:4, cx0:cx1:4].max(axis=2)
                bg_v.append(float(np.percentile(v, 95.0)))  # the spheres are a few % of the picture at most
        finally:
            cfg.mask_lights, cfg.min_brightness, cfg.min_saturation, cfg.hue_tolerance = saved
        if not all_blobs:
            return {"colors": None, "config": cfg, "report": "No bright coloured blob found. Light both controllers, point them at the camera and lower the exposure."}
        # Cluster blobs by hue (simple: seed with the biggest, then the biggest at least 30 hue away).
        all_blobs.sort(key=lambda b: -b.area)
        clusters: list[list[_Blob]] = []
        for b in all_blobs:
            for c in clusters:
                if hue_distance(c[0].hue, b.hue) <= 18:
                    c.append(b)
                    break
            else:
                clusters.append([b])
        clusters.sort(key=lambda c: -len(c))
        hues = []
        for c in clusters[:2]:
            ang = np.array([b.hue for b in c]) * (2 * math.pi / 180.0)
            hues.append(math.degrees(math.atan2(float(np.sin(ang).mean()), float(np.cos(ang).mean()))) / 2.0 % 180.0)
        colors: Optional[list[tuple[int, int, int]]] = None
        if len(hues) == 2:
            # Keep the controller order: the hue closest to each controller's current colour wins.
            if hue_distance(hues[0], self.targets[0].hue) + hue_distance(hues[1], self.targets[1].hue) > hue_distance(hues[1], self.targets[0].hue) + hue_distance(hues[0], self.targets[1].hue):
                hues.reverse()
            colors = [rgb_from_hue(hues[0]), rgb_from_hue(hues[1])]
        blob_v = float(np.median([b.brightness for b in all_blobs]))
        background = float(np.median(bg_v))
        new = TrackingConfig.from_dict(cfg.to_dict())
        new.mask_lights = True
        new.min_brightness = int(max(80, min(240, (background + blob_v) / 2.0 if blob_v > background + 30 else blob_v - 40)))
        new.min_saturation = int(max(40, min(180, cfg.min_saturation)))
        sep = hue_distance(hues[0], hues[1]) if len(hues) == 2 else 90.0
        new.hue_tolerance = int(max(10, min(40, sep / 2.0 - 4)))
        report = (f"Found {len(clusters)} colour(s) in {len(frames)} frames: hues {[round(x) for x in hues]}"
                  + (" (only one colour: light the second controller too)" if len(hues) < 2 else "")
                  + f". Sphere brightness ~{blob_v:.0f}, background ~{background:.0f} -> brightness threshold {new.min_brightness}, hue tolerance ±{new.hue_tolerance}.")
        if background > blob_v - 40:
            report += " The room is nearly as bright as the spheres: lower the camera exposure / gain or dim the lights."
        return {"colors": colors, "config": new, "report": report}

    # ------------------------------------------------------------ drawing
    def mask_view(self, frame: np.ndarray) -> np.ndarray:
        """What the tracker sees: the lit-pixel mask over the tracking area, dark elsewhere."""
        if cv2 is None:
            return frame
        h, w = frame.shape[:2]
        out = np.zeros_like(frame)
        cx0, cy0, cx1, cy1 = self.crop_px(w, h)
        mask = self.last_mask
        if mask is not None and mask.size:
            up = cv2.resize(mask, (cx1 - cx0, cy1 - cy0), interpolation=cv2.INTER_NEAREST)
            roi = out[cy0:cy1, cx0:cx1]
            roi[up > 0] = (255, 255, 255)
            colour = frame[cy0:cy1, cx0:cx1]
            roi[up > 0] = colour[up > 0]
        return self.draw_overlay(out, copy=False)

    def draw_overlay(self, frame: np.ndarray, copy: bool = True) -> np.ndarray:
        if cv2 is None:
            return frame
        out = frame.copy() if copy else frame
        h, w = frame.shape[:2]
        cx0, cy0, cx1, cy1 = self.crop_px(w, h)
        if (cx0, cy0, cx1, cy1) != (0, 0, w, h):
            shade = out.copy()
            shade[:] = shade // 3
            shade[cy0:cy1, cx0:cx1] = out[cy0:cy1, cx0:cx1]
            out = shade
            cv2.rectangle(out, (cx0, cy0), (cx1 - 1, cy1 - 1), (255, 255, 255), 1)
            cv2.putText(out, "tracking area", (cx0 + 4, cy0 + 14), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)
        if self.config.zone_enabled:
            zx0, zy0, zx1, zy1 = self.zone_px(w, h)
            cv2.rectangle(out, (zx0, zy0), (zx1 - 1, zy1 - 1), (80, 255, 120), 2)
            cv2.putText(out, "trigger zone", (zx0 + 4, zy1 - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (80, 255, 120), 1, cv2.LINE_AA)
        for i, det in enumerate(self.last_detections):
            if det is None:
                continue
            rgb = self.targets[i].rgb
            bgr = (int(rgb[2]), int(rgb[1]), int(rgb[0]))
            ring = (255, 255, 255) if det.in_zone else (90, 90, 90)
            cv2.circle(out, (int(det.x), int(det.y)), max(2, int(det.radius)), ring, 2)
            cv2.circle(out, (int(det.x), int(det.y)), max(int(det.radius) + 4, 6), bgr, 2)
            st = self.states[i]
            label = f"P{i + 1} d={st.depth:.2f} h{det.hue_error:.0f}" + ("" if det.in_zone else " out")
            cv2.putText(out, label, (int(det.x) - 30, int(det.y) - int(det.radius) - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
        return out
