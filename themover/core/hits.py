"""Drum-hit detection tuned for rhythm games (osu!taiko, Taiko no Tatsujin).

Why not the generic gesture detector?  A rhythm game needs the hit to register
at a *consistent* moment of the stroke, with as little lag as possible, and it
needs fast same-hand repeats.  An air-drum stroke looks like this on the
accelerometer (gravity removed):

    onset lobe   - acceleration in the direction of motion (the arm speeds up)
    quiet gap    - peak velocity, little acceleration
    stop lobe    - a sharp spike *opposite* to the motion (the arm stops)
    rebound      - a gentle lobe pair as the hand comes back up

The stop is what a drummer feels as the hit, and it is by far the sharpest
feature, so the hit fires on the first sample of the stop lobe.  Because it is
so sharp, the threshold crossing lands within a sample or two of the true peak
whether the stroke was soft or hard, which keeps timing jitter low.

The onset direction (relative to gravity) decides the note type: straight down
is *don*, anything diagonal/sideways is *kat*.  Upward strokes (the rebound)
are tracked so their stop cannot be mistaken for a new onset, but never fire.
Holding the trigger forces kat and holding the Move button forces don, for
players who prefer a modifier over angling the swing.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from themover.core.state import Vec3

HIT_KINDS = ("don", "kat", "any")


@dataclass
class HitConfig:
    onset_g: float = 0.9  # linear acceleration that starts a stroke
    stop_g: float = 1.3  # opposite-direction acceleration that counts as the impact
    max_stroke_s: float = 0.25  # onset -> stop must happen within this window
    refractory_s: float = 0.045  # ignore everything briefly after a hit
    kat_angle_deg: float = 40.0  # stroke more than this far from straight down = kat
    up_angle_deg: float = 110.0  # stroke more than this far from down = upward (ignored)
    pulse_s: float = 0.08  # how long the hit signal stays at 1.0 for the mapping engine
    gravity_alpha: float = 0.02  # gravity low-pass (only while the controller is calm)
    onset_frames: int = 2  # an onset must last this many consecutive frames (rejects single-frame blips)
    onset_cos: float = 0.7  # ...pointing in a consistent direction
    min_proto_cos: float = 0.35  # prototype mode: a stop this far from every learned direction is not a hit (rebound)
    kind_mode: str = "auto"  # auto | angle | prototype  (auto = prototype when prototypes exist for the hand)
    # A drum stroke is a wrist rotation: the accelerometer sees a large centripetal component along the
    # stick (the controller's y axis) that peaks at the impact and hides the stop.  "tangential" looks
    # only at the plane perpendicular to the stick for onset and stop (recommended); "full" uses all axes.
    axis_mode: str = "tangential"
    decision_delay_s: float = 0.008  # reversal mode: after the stop threshold, gather the impact direction this long before firing
    # Envelope modes (default).  A real stroke is a wrist snap: the sensor rotates during the stroke, so
    # directions in the controller frame swing around and the classic onset/stop reversal is unreliable.
    # What is reliable is the *size* of the acceleration lobe: it climbs to 5-13 g and peaks at the impact.
    #   "peak": fire the moment the lobe starts to fall (the impact itself; ~1-2 samples of latency)
    #   "rise": fire the moment the lobe crosses hit_g on the way up (earliest, a little more jitter)
    #   "reversal": the old onset/stop model
    hit_mode: str = "peak"
    hit_g: float = 3.0  # lobe size (g, gravity removed) that counts as a stroke
    proto_w_rise: float = 1.0  # weight of the lobe-start direction in the prototype match (0 = ignore)
    proto_w_pose: float = 0.0  # weight of the pre-stroke pose (gravity direction) in the prototype match
    peak_drop: float = 0.85  # peak mode: fire once the lobe has fallen to this fraction of its maximum
    max_lobe_s: float = 0.08  # peak mode: fire anyway this long after the crossing (very long lobes)
    # An impact towers over the wind-up; the centripetal hump of a fast swing can also poke above
    # hit_g but stays small.  A lobe only counts when its peak climbs at least min_rise_g above the
    # level it started from (the last sample below hit_g), whatever the frame rate.
    min_rise_g: float = 2.0
    # Learned impact directions per hand, {"0": {"don": [x, y, z], "kat": [x, y, z]}, "1": {...}} (unit vectors in the
    # controller frame).  Fitted from a tagged recording; this is what tells don from kat for a real player.
    prototypes: dict = field(default_factory=dict)


HIT_CONFIG_FIELDS = ("onset_g", "stop_g", "max_stroke_s", "refractory_s", "kat_angle_deg", "up_angle_deg", "pulse_s", "gravity_alpha", "onset_frames", "onset_cos", "min_proto_cos", "decision_delay_s", "hit_g", "peak_drop", "max_lobe_s", "proto_w_rise", "proto_w_pose", "min_rise_g")
HIT_CONFIG_EXTRA = ("kind_mode", "prototypes", "axis_mode", "hit_mode")


def normalise_prototypes(data) -> dict:
    """{"0": {"don": [x,y,z], ...}, "1": {...}} with unit vectors; anything malformed is dropped."""
    out: dict = {}
    if not isinstance(data, dict):
        return out
    for hand, kinds in data.items():
        if str(hand) not in ("0", "1") or not isinstance(kinds, dict):
            continue
        clean = {}
        for kind, vec in kinds.items():
            if kind not in ("don", "kat"):
                continue
            parts = vec if isinstance(vec, dict) else {"dir": vec}
            entry = {}
            for name in ("dir", "rise", "pose"):
                v = parts.get(name) if isinstance(parts, dict) else None
                if not isinstance(v, (list, tuple)) or len(v) != 3:
                    continue
                try:
                    x, y, z = (float(c) for c in v)
                except (TypeError, ValueError):
                    continue
                n = math.sqrt(x * x + y * y + z * z)
                if n > 1e-6:
                    entry[name] = [round(x / n, 4), round(y / n, 4), round(z / n, 4)]
            if "dir" in entry:
                clean[kind] = entry
        if clean:
            out[str(hand)] = clean
    return out


def hit_config_from_dict(data: Optional[dict]) -> HitConfig:
    cfg = HitConfig()
    for k, v in (data or {}).items():
        if k in HIT_CONFIG_FIELDS and isinstance(v, (int, float)):
            setattr(cfg, k, int(v) if k == "onset_frames" else float(v))
        elif k == "kind_mode" and v in ("auto", "angle", "prototype"):
            cfg.kind_mode = v
        elif k == "axis_mode" and v in ("tangential", "full"):
            cfg.axis_mode = v
        elif k == "hit_mode" and v in ("peak", "rise", "reversal"):
            cfg.hit_mode = v
        elif k == "prototypes":
            cfg.prototypes = normalise_prototypes(v)
    cfg.min_proto_cos = max(-1.0, min(0.95, cfg.min_proto_cos))
    cfg.decision_delay_s = max(0.0, min(0.05, cfg.decision_delay_s))
    cfg.hit_g = max(0.8, min(12.0, cfg.hit_g))
    cfg.proto_w_rise = max(0.0, min(5.0, cfg.proto_w_rise))
    cfg.min_rise_g = max(0.0, min(10.0, cfg.min_rise_g))
    cfg.proto_w_pose = max(0.0, min(5.0, cfg.proto_w_pose))
    cfg.peak_drop = max(0.5, min(0.99, cfg.peak_drop))
    cfg.max_lobe_s = max(0.01, min(0.3, cfg.max_lobe_s))
    cfg.onset_frames = max(1, min(6, cfg.onset_frames))
    cfg.onset_g = max(0.2, min(4.0, cfg.onset_g))
    cfg.stop_g = max(0.3, min(8.0, cfg.stop_g))
    cfg.refractory_s = max(0.01, min(0.5, cfg.refractory_s))
    cfg.max_stroke_s = max(0.05, min(1.0, cfg.max_stroke_s))
    cfg.kat_angle_deg = max(5.0, min(89.0, cfg.kat_angle_deg))
    cfg.up_angle_deg = max(cfg.kat_angle_deg + 1.0, min(179.0, cfg.up_angle_deg))
    return cfg


def hit_config_to_dict(cfg: HitConfig) -> dict:
    out = {k: getattr(cfg, k) for k in HIT_CONFIG_FIELDS}
    out["kind_mode"] = cfg.kind_mode
    out["axis_mode"] = cfg.axis_mode
    out["hit_mode"] = cfg.hit_mode
    out["prototypes"] = {h: {k: {n: list(vec) for n, vec in v.items()} for k, v in kinds.items()} for h, kinds in cfg.prototypes.items()}
    return out


def _unit(v) -> tuple:
    n = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
    return (v[0] / n, v[1] / n, v[2] / n) if n > 1e-6 else (0.0, 0.0, 0.0)


def _similarity(a, b, tangential: bool) -> float:
    if tangential:
        na = math.sqrt(a[0] ** 2 + a[2] ** 2)
        nb = math.sqrt(b[0] ** 2 + b[2] ** 2)
        if na < 1e-6 or nb < 1e-6:
            return 0.0
        return (a[0] * b[0] + a[2] * b[2]) / (na * nb)
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def prototype_from_dirs(dirs) -> Optional[list[float]]:
    """Unit mean of a set of direction vectors (None when empty or degenerate)."""
    sx = sy = sz = 0.0
    n = 0
    for d in dirs:
        m = math.sqrt(d[0] ** 2 + d[1] ** 2 + d[2] ** 2)
        if m > 1e-6:
            sx += d[0] / m
            sy += d[1] / m
            sz += d[2] / m
            n += 1
    mag = math.sqrt(sx * sx + sy * sy + sz * sz)
    if not n or mag < 1e-6:
        return None
    return [round(sx / mag, 4), round(sy / mag, 4), round(sz / mag, 4)]


@dataclass
class Hit:
    t: float
    kind: str  # don | kat
    strength: float  # g, peak of the stop lobe seen at detection time
    stroke_ms: float  # onset -> stop duration
    modifier: str = ""  # "trigger" / "move" when a button forced the kind
    stop_dir: tuple = (0.0, 0.0, 0.0)  # unit direction of the impact (top of the lobe) in the controller frame
    rise_dir: tuple = (0.0, 0.0, 0.0)  # unit direction of the acceleration when the lobe crossed hit_g
    pose: tuple = (0.0, 0.0, 0.0)  # unit gravity direction in the controller frame just before the stroke
    angle: float = 0.0  # stroke angle from straight down (degrees, angle mode)
    proto_cos: float = 0.0  # similarity to the winning prototype (prototype mode)


@dataclass
class DrumHitDetector:
    config: HitConfig = field(default_factory=HitConfig)
    on_hit: Optional[Callable[[Hit], None]] = None
    hand: int = 0  # which controller this detector serves (selects its prototypes)
    gravity: Vec3 = field(default_factory=lambda: Vec3(0.0, 1.0, 0.0))
    _init: bool = False
    _state: str = "idle"  # idle | moving | refractory
    _dir: tuple[float, float, float] = (0.0, 0.0, 0.0)
    _onset_t: float = 0.0
    _peak: float = 0.0
    _until: float = 0.0
    _pulses: dict[str, float] = field(default_factory=dict)
    _cand_dir: tuple[float, float, float] = (0.0, 0.0, 0.0)
    _cand_count: int = 0
    _cand_t: float = 0.0
    _last_t: float = 0.0
    _stop_t: float = 0.0  # when the stop threshold was first crossed (0 = not yet)
    _stop_acc: list = field(default_factory=lambda: [0.0, 0.0, 0.0])  # summed impact vector since then
    _stop_peak: float = 0.0
    _lobe_peak: float = 0.0  # envelope modes
    _lobe_peak_t: float = 0.0
    _lobe_cross_t: float = 0.0
    _lobe_acc: list = field(default_factory=lambda: [0.0, 0.0, 0.0])
    _lobe_rise: tuple = (0.0, 0.0, 0.0)
    _lobe_pose: tuple = (0.0, 0.0, 0.0)
    _pre_dir: tuple = (0.0, 0.0, 0.0)  # direction of the last onset-sized acceleration before the lobe
    _pre_t: float = -1.0
    _pre_start_t: float = -1.0  # when the current run of onset-sized acceleration began (stroke_ms)
    _prev_mag: float = 0.0  # |lin| of the previous sample
    _lobe_base: float = 0.0  # |lin| the current lobe started from
    last_hit: Optional[Hit] = None
    hit_count: int = 0
    strength: float = 0.0  # current linear acceleration magnitude

    # ------------------------------------------------------------- update
    def update(self, accel: Vec3, t: Optional[float] = None, trigger: float = 0.0, move: bool = False) -> Optional[Hit]:
        """Feed one accelerometer frame (in g). Returns a Hit when one fires."""
        t = time.monotonic() if t is None else t
        g = self.gravity
        if not self._init:
            g.x, g.y, g.z = accel.x, accel.y, accel.z
            self._init = True
            self._last_t = t
        dt = max(0.0, min(0.05, t - self._last_t))
        self._last_t = t
        lx, ly, lz = accel.x - g.x, accel.y - g.y, accel.z - g.z
        mag = math.sqrt(lx * lx + ly * ly + lz * lz)
        self.strength = mag
        cfg = self.config
        # Track gravity while the controller is calm: the *raw* magnitude near 1 g says "quasi-static"
        # whatever the current estimate is, so a wrong estimate always recovers (the old test on the
        # linear magnitude dead-locked when the detector started mid-stroke).  Rates are per second,
        # so the filter behaves the same at 85 Hz and at 470 Hz.
        raw = math.sqrt(accel.x * accel.x + accel.y * accel.y + accel.z * accel.z)
        if 0.8 <= raw <= 1.2:
            a = min(1.0, dt * cfg.gravity_alpha * 100.0)  # gravity_alpha 0.02 -> time constant 0.5 s
        else:
            a = min(1.0, dt * 0.15)  # very slow drift during strokes: cycles average out, keeps it sane
        g.x += (accel.x - g.x) * a
        g.y += (accel.y - g.y) * a
        g.z += (accel.z - g.z) * a
        full = (lx, ly, lz)
        if cfg.hit_mode in ("peak", "rise"):
            return self._update_envelope(t, mag, full, trigger, move)
        if cfg.axis_mode == "tangential":
            ly = 0.0  # drop the centripetal (along-the-stick) component for onset / stop logic
            mag = math.sqrt(lx * lx + lz * lz)

        if self._state == "refractory":
            if t >= self._until:
                self._state = "idle"
                self._cand_count = 0
            else:
                return None

        if self._state == "moving":
            # Wait for the stop lobe: acceleration opposite to the stroke direction.
            dx, dy, dz = self._dir
            proj = lx * dx + ly * dy + lz * dz
            if proj > self._peak:
                self._peak = proj
            if self._stop_t:
                # Threshold already crossed: gather the impact direction for a few ms, then fire.
                acc = self._stop_acc
                acc[0] += full[0]; acc[1] += full[1]; acc[2] += full[2]
                self._stop_peak = max(self._stop_peak, -proj)
                if t - self._stop_t >= cfg.decision_delay_s:
                    return self._finish_stroke(t, trigger, move)
                return None
            if proj <= -cfg.stop_g:
                self._stop_t = t
                self._stop_acc = [full[0], full[1], full[2]]
                self._stop_peak = -proj
                if cfg.decision_delay_s <= 0.0:
                    return self._finish_stroke(t, trigger, move)
                return None
            if t - self._onset_t > cfg.max_stroke_s:
                if proj >= cfg.onset_g:
                    self._onset_t = t  # still accelerating the same way (slow wind-up): keep waiting for the stop
                    return None
                self._state = "idle"
                self._cand_count = 0
            elif mag >= cfg.onset_g and -0.3 * mag < proj < 0.5 * mag:
                # Sustained acceleration in a genuinely *new* direction (sideways, not opposite):
                # the player changed stroke (don -> kat inside a stream). Re-arm below.
                # Opposite acceleration is the stop lobe building up - never re-arm on it.
                pass
            else:
                return None

        # idle (or moving in a different direction): look for a sustained onset.
        if mag < cfg.onset_g:
            self._cand_count = 0
            return None
        unit = (lx / mag, ly / mag, lz / mag)
        cx, cy, cz = self._cand_dir
        if self._cand_count and (unit[0] * cx + unit[1] * cy + unit[2] * cz) >= cfg.onset_cos:
            self._cand_count += 1
        else:
            self._cand_dir, self._cand_count, self._cand_t = unit, 1, t
        if self._cand_count >= cfg.onset_frames:
            self._dir = unit
            self._onset_t = self._cand_t
            self._peak = mag
            self._state = "moving"
            self._cand_count = 0
        return None

    def _update_envelope(self, t: float, mag: float, full: tuple, trigger: float, move: bool) -> Optional[Hit]:
        """Envelope modes: a stroke is one big lobe of |linear acceleration|."""
        cfg = self.config
        prev_mag, self._prev_mag = self._prev_mag, mag
        if self._state == "refractory":
            # Re-arm only after the lobe has clearly ended (hysteresis) and the refractory time passed.
            if t >= self._until and mag < 0.6 * cfg.hit_g:
                self._state = "idle"
                self._pre_start_t = -1.0
            return None
        if self._state == "idle":
            if mag < cfg.hit_g:
                if mag >= cfg.onset_g:
                    if self._pre_t < 0 or t - self._pre_t > 0.02:
                        self._pre_start_t = t  # a new run of stroke-sized acceleration
                    self._pre_dir, self._pre_t = _unit(full), t  # the stroke's own acceleration phase
                return None
            self._lobe_peak, self._lobe_peak_t, self._lobe_cross_t = mag, t, t
            self._lobe_base = min(prev_mag, cfg.hit_g)
            self._lobe_acc = [full[0], full[1], full[2]]
            self._lobe_rise = full
            g = self.gravity
            self._lobe_pose = (g.x, g.y, g.z)
            self._onset_t = self._pre_start_t if (self._pre_start_t >= 0 and t - self._pre_start_t <= cfg.max_stroke_s) else t
            if cfg.hit_mode == "rise" and mag - self._lobe_base >= cfg.min_rise_g:
                return self._finish_lobe(t, trigger, move)
            self._state = "rising"
            return None
        # rising: track the peak, gather the direction of the top of the lobe, fire when it turns down.
        acc = self._lobe_acc
        if mag >= 0.7 * self._lobe_peak:
            acc[0] += full[0]; acc[1] += full[1]; acc[2] += full[2]
        if mag > self._lobe_peak:
            self._lobe_peak, self._lobe_peak_t = mag, t
            if cfg.hit_mode == "rise" and mag - self._lobe_base >= cfg.min_rise_g:
                return self._finish_lobe(t, trigger, move)
        if mag <= cfg.peak_drop * self._lobe_peak or t - self._lobe_cross_t >= cfg.max_lobe_s:
            if self._lobe_peak - self._lobe_base >= cfg.min_rise_g:
                return self._finish_lobe(t, trigger, move)
            # A small hump (wind-up, centripetal plateau), not an impact: forget it and keep watching.
            self._lobe_peak, self._lobe_peak_t, self._lobe_cross_t = mag, t, t
            self._lobe_acc = [full[0], full[1], full[2]]
            if mag < cfg.hit_g:
                self._state = "idle"
            return None
        return None

    def _finish_lobe(self, t: float, trigger: float, move: bool) -> Optional[Hit]:
        acc = self._lobe_acc
        hit = self._fire(t, self._lobe_peak, (acc[0], acc[1], acc[2]), trigger, move)
        self._state = "refractory"
        self._until = t + self.config.refractory_s
        return hit

    def prototypes(self) -> dict:
        return self.config.prototypes.get(str(self.hand), {})

    def _finish_stroke(self, t: float, trigger: float, move: bool) -> Optional[Hit]:
        acc = self._stop_acc
        hit = self._fire(t, self._stop_peak, (acc[0], acc[1], acc[2]), trigger, move)
        self._state = "refractory"
        self._until = t + self.config.refractory_s
        self._cand_count = 0
        self._stop_t = 0.0
        return hit

    def _fire(self, t: float, stop_strength: float, stop: tuple[float, float, float], trigger: float, move: bool) -> Optional[Hit]:
        cfg = self.config
        g = self.gravity
        gm = math.sqrt(g.x * g.x + g.y * g.y + g.z * g.z) or 1.0
        # The accelerometer reads +1 g *against* gravity at rest, so "down" is -g.
        down = (-g.x / gm, -g.y / gm, -g.z / gm)
        dx, dy, dz = self._dir
        cos = max(-1.0, min(1.0, dx * down[0] + dy * down[1] + dz * down[2]))
        angle = math.degrees(math.acos(cos))
        sm = math.sqrt(stop[0] ** 2 + stop[1] ** 2 + stop[2] ** 2) or 1.0
        stop_dir = (stop[0] / sm, stop[1] / sm, stop[2] / sm)
        protos = self.prototypes() if cfg.kind_mode != "angle" else {}
        proto_cos = 0.0
        envelope = cfg.hit_mode in ("peak", "rise")
        if envelope:
            # Stroke angle for the fallback rule: the acceleration phase just before the lobe (the motion
            # direction) when there was one, else the direction the lobe started in.
            ref = self._pre_dir if (self._pre_t >= 0 and t - self._pre_t <= cfg.max_stroke_s) else _unit(self._lobe_rise)
            cos = max(-1.0, min(1.0, ref[0] * down[0] + ref[1] * down[1] + ref[2] * down[2]))
            angle = math.degrees(math.acos(cos))
        rise = _unit(self._lobe_rise) if envelope else (0.0, 0.0, 0.0)
        pose = _unit(self._lobe_pose) if envelope else _unit((g.x, g.y, g.z))
        if protos:
            # Learned stroke signatures: the closest one wins; far from all of them = not a hit (rebound, wobble).
            # In tangential mode the along-the-stick component is centripetal noise: compare in the x/z plane.
            tangential = cfg.axis_mode == "tangential" and not envelope
            scores = {}
            for k, v in protos.items():
                total = _similarity(stop_dir, v["dir"], tangential)
                weight = 1.0
                if "rise" in v and cfg.proto_w_rise > 0:
                    total += cfg.proto_w_rise * _similarity(rise, v["rise"], False)
                    weight += cfg.proto_w_rise
                if "pose" in v and cfg.proto_w_pose > 0:
                    total += cfg.proto_w_pose * _similarity(pose, v["pose"], False)
                    weight += cfg.proto_w_pose
                scores[k] = total / weight
            best_kind = max(scores, key=scores.get)
            proto_cos = scores[best_kind]
            if proto_cos < cfg.min_proto_cos:
                return None
            if len(scores) == 1:  # only one kind learned: anything unlike it is the other kind
                guess = best_kind
            else:
                guess = best_kind
        else:
            if angle > cfg.up_angle_deg and not envelope:
                return None  # upward stroke (rebound) - not a hit
            guess = "don" if angle <= cfg.kat_angle_deg else "kat"
        modifier = ""
        if trigger >= 0.5:
            kind, modifier = "kat", "trigger"
        elif move:
            kind, modifier = "don", "move"
        else:
            kind = guess
        hit = Hit(t=t, kind=kind, strength=stop_strength, stroke_ms=(t - self._onset_t) * 1000.0, modifier=modifier,
                  stop_dir=stop_dir, angle=angle, proto_cos=proto_cos, rise_dir=rise, pose=pose)
        self.last_hit = hit
        self.hit_count += 1
        self._pulses[kind] = t + cfg.pulse_s
        self._pulses["any"] = t + cfg.pulse_s
        if self.on_hit is not None:
            try:
                self.on_hit(hit)
            except Exception:
                pass
        return hit

    # -------------------------------------------------------------- query
    def value(self, kind: str, now: Optional[float] = None) -> float:
        now = time.monotonic() if now is None else now
        until = self._pulses.get(kind)
        return 1.0 if until is not None and now <= until else 0.0

    def reset(self) -> None:
        self._state = "idle"
        self._cand_count = 0
        self._stop_t = 0.0
        self._lobe_peak = 0.0
        self._prev_mag = 0.0
        self._pre_t = self._pre_start_t = -1.0
        self._pulses.clear()
