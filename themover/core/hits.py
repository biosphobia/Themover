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


@dataclass
class Hit:
    t: float
    kind: str  # don | kat
    strength: float  # g, peak of the stop lobe seen at detection time
    stroke_ms: float  # onset -> stop duration
    modifier: str = ""  # "trigger" / "move" when a button forced the kind


@dataclass
class DrumHitDetector:
    config: HitConfig = field(default_factory=HitConfig)
    on_hit: Optional[Callable[[Hit], None]] = None
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
        lx, ly, lz = accel.x - g.x, accel.y - g.y, accel.z - g.z
        mag = math.sqrt(lx * lx + ly * ly + lz * lz)
        self.strength = mag
        cfg = self.config
        # Track gravity only while the controller is calm so strokes don't drag it.
        if mag < 0.35:
            a = cfg.gravity_alpha
            g.x += (accel.x - g.x) * a
            g.y += (accel.y - g.y) * a
            g.z += (accel.z - g.z) * a

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
            if proj <= -cfg.stop_g:
                hit = self._fire(t, -proj, trigger, move)
                self._state = "refractory"
                self._until = t + cfg.refractory_s
                self._cand_count = 0
                return hit
            if t - self._onset_t > cfg.max_stroke_s:
                self._state = "idle"
                self._cand_count = 0
            elif mag >= cfg.onset_g and proj < 0.5 * mag:
                # Sustained acceleration in a *new* direction: the player changed stroke
                # (e.g. don -> kat inside a stream). Re-arm with the new direction below.
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

    def _fire(self, t: float, stop_strength: float, trigger: float, move: bool) -> Optional[Hit]:
        cfg = self.config
        g = self.gravity
        gm = math.sqrt(g.x * g.x + g.y * g.y + g.z * g.z) or 1.0
        # The accelerometer reads +1 g *against* gravity at rest, so "down" is -g.
        down = (-g.x / gm, -g.y / gm, -g.z / gm)
        dx, dy, dz = self._dir
        cos = max(-1.0, min(1.0, dx * down[0] + dy * down[1] + dz * down[2]))
        angle = math.degrees(math.acos(cos))
        if angle > cfg.up_angle_deg:
            return None  # upward stroke (rebound) - not a hit
        modifier = ""
        if trigger >= 0.5:
            kind, modifier = "kat", "trigger"
        elif move:
            kind, modifier = "don", "move"
        else:
            kind = "don" if angle <= cfg.kat_angle_deg else "kat"
        hit = Hit(t=t, kind=kind, strength=stop_strength, stroke_ms=(t - self._onset_t) * 1000.0, modifier=modifier)
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
        self._pulses.clear()
