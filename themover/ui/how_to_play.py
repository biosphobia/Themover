"""Animated "how to play" illustration derived from a profile's bindings.

Two stylised PS Move controllers (handle + glowing sphere in the profile's
colours) act out the motions the profile uses: turning a wheel, tilting,
swinging, striking a drum, pointing at the screen, squeezing the trigger.
Rumble is drawn as vibration arcs and LED flashes as the sphere lighting up,
so the picture also shows what feedback the profile gives.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from themover.mapping.humanize import describe_binding
from themover.mapping.profile import Profile

MOTION_SECONDS = 1.7


@dataclass
class Motion:
    kind: str  # wheel | tilt_roll | tilt_pitch | turn | swing_side | swing_up | strike | thrust | shake | point | trigger | lean | idle
    caption: str
    rumble: bool = False
    flash: bool = False


_SOURCE_KINDS = {
    "hit.don": "strike", "hit.any": "strike", "hit.kat": "swing_side",
    "gesture.swing_left": "swing_side", "gesture.swing_right": "swing_side",
    "gesture.swing_up": "swing_up", "gesture.swing_down": "strike", "gesture.swing_any": "swing_side",
    "gesture.thrust": "thrust", "gesture.pull": "thrust", "gesture.shake": "shake", "gesture.flick": "swing_up",
    "orient.roll": "tilt_roll", "orient.pitch": "tilt_pitch", "orient.yaw": "turn",
    "gyro.z": "turn", "gyro.x": "tilt_pitch", "gyro.y": "tilt_roll",
    "track.x": "point", "track.y": "point", "track.vx": "point", "track.vy": "point", "track.depth": "thrust",
    "trigger": "trigger",
}


def animation_program(profile: Profile) -> tuple[list[Motion], list[Motion]]:
    """Per hand (right = 0, left = 1): the motions to act out, in binding order, de-duplicated."""
    hands: tuple[list[Motion], list[Motion]] = ([], [])
    seen: tuple[set[str], set[str]] = (set(), set())
    feedback_sources = {f.when for f in profile.feedback if f.when} | {f.rumble_from for f in profile.feedback if f.rumble_from}
    flash_sources = {f.when for f in profile.feedback if f.when and f.led}

    def add(hand: int, kind: str, caption: str, source: str) -> None:
        if kind in seen[hand]:
            return
        seen[hand].add(kind)
        fb = source in feedback_sources or f"c{hand}.hit.any" in feedback_sources and source.startswith(f"c{hand}.hit.")
        flash = source in flash_sources or f"c{hand}.hit.any" in flash_sources and source.startswith(f"c{hand}.hit.")
        hands[hand].append(Motion(kind, caption, rumble=fb, flash=flash))

    for b in profile.bindings:
        if not b.enabled:
            continue
        what, game, _how = describe_binding(b)
        caption = f"{what} → {game}"
        src = b.source
        if src == "wheel.angle":
            for h in (0, 1):
                add(h, "wheel", "Hold both like a wheel and turn → " + game, src)
            continue
        if src.startswith("both."):
            for h in (0, 1):
                add(h, "lean", caption, src)
            continue
        parts = src.split(".", 1)
        if len(parts) != 2 or not parts[0].startswith("c"):
            continue
        try:
            hand = int(parts[0][1:])
        except ValueError:
            continue
        if hand not in (0, 1):
            continue
        kind = _SOURCE_KINDS.get(parts[1])
        if kind is None and parts[1].startswith("track."):
            kind = "point"
        if kind is None:
            continue
        add(hand, kind, caption, src)
    for h in (0, 1):
        if not hands[h]:
            hands[h].append(Motion("idle", "Buttons only" if any(b.source.startswith(f"c{h}.button") for b in profile.bindings) else "Not used", False, False))
    return hands


class HowToPlayWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(190)
        self.program: tuple[list[Motion], list[Motion]] = ([Motion("idle", "")], [Motion("idle", "")])
        self.colors = [(255, 0, 255), (0, 255, 255)]
        self._t = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)
        self.running = True

    def set_profile(self, profile: Profile) -> None:
        self.program = animation_program(profile)
        self.colors = [tuple(c.color) for c in profile.controllers[:2]] or self.colors
        self._t = 0.0
        self.update()

    def set_running(self, on: bool) -> None:
        self.running = on
        if on and not self._timer.isActive():
            self._timer.start(33)
        elif not on:
            self._timer.stop()

    def _tick(self) -> None:
        self._t += 0.033
        self.update()

    # ------------------------------------------------------------- drawing
    def _current(self, hand: int) -> tuple[Motion, float]:
        motions = self.program[hand] or [Motion("idle", "")]
        n = len(motions)
        cycle = self._t / MOTION_SECONDS
        idx = int(cycle) % n
        phase = cycle - int(cycle)
        return motions[idx], phase

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor("#0f1015"))
        # The screen the player faces.
        screen = QRectF(w * 0.25, 8, w * 0.5, h * 0.22)
        p.setPen(QPen(QColor("#33364a"), 2))
        p.setBrush(QBrush(QColor("#1a1b23")))
        p.drawRoundedRect(screen, 6, 6)
        p.setPen(QColor("#4b4f63"))
        p.setFont(QFont("Segoe UI", 8))
        p.drawText(screen, Qt.AlignCenter, "game")

        base_y = h * 0.62
        centers = {0: QPointF(w * 0.64, base_y), 1: QPointF(w * 0.36, base_y)}
        wheel_pair = self._current(0)[0].kind == "wheel" and self._current(1)[0].kind == "wheel"
        for hand in (1, 0):  # left first so the right hand draws on top
            motion, phase = self._current(hand)
            self._draw_controller(p, hand, motion, phase, centers[hand], wheel_pair, w, h, screen)
        # Captions.
        p.setFont(QFont("Segoe UI", 9))
        for hand, x in ((1, w * 0.02), (0, w * 0.52)):
            motion, _ = self._current(hand)
            p.setPen(QColor("#e8e9f0"))
            label = ("Left hand: " if hand == 1 else "Right hand: ") + motion.caption
            p.drawText(QRectF(x, h - 34, w * 0.47, 32), Qt.AlignLeft | Qt.AlignVCenter | Qt.TextWordWrap, label)

    def _draw_controller(self, p: QPainter, hand: int, motion: Motion, phase: float, center: QPointF,
                         wheel_pair: bool, w: float, h: float, screen: QRectF) -> None:
        kind = motion.kind
        s = math.sin(2 * math.pi * phase)
        angle = 0.0
        dx = dy = 0.0
        scale = 1.0
        impact = False
        squeeze = False
        if kind == "wheel" and wheel_pair:
            a = math.radians(35 * s)
            cx, cy = w * 0.5, h * 0.62
            r = w * 0.14
            side = 1 if hand == 0 else -1
            dx = cx + side * r * math.cos(a) - center.x() + (-side * 0)
            dy = cy + side * r * math.sin(a) - center.y()
            angle = 35 * s
        elif kind == "tilt_roll":
            angle = 32 * s
        elif kind == "tilt_pitch":
            scale = 1.0 - 0.25 * max(0.0, s)
            dy = 10 * s
        elif kind == "turn":
            scale = 1.0
            dx = 14 * s
            angle = 12 * s
        elif kind == "swing_side":
            dx = (42 if hand == 0 else -42) * max(0.0, s)
            angle = (-20 if hand == 0 else 20) * max(0.0, s)
            impact = 0.45 < phase < 0.6
        elif kind == "swing_up":
            dy = -46 * max(0.0, s)
            impact = 0.2 < phase < 0.32
        elif kind == "strike":
            # slow lift, snap down, rest
            if phase < 0.45:
                dy = -40 * (phase / 0.45)
            elif phase < 0.55:
                dy = -40 + 48 * ((phase - 0.45) / 0.10)
            else:
                dy = 8 - 8 * ((phase - 0.55) / 0.45)
            angle = (-25 if hand == 0 else 25) * (dy / -40 if dy < 0 else 0)
            impact = 0.55 <= phase < 0.68
        elif kind == "thrust":
            scale = 1.0 + 0.3 * max(0.0, s)
            impact = 0.2 < phase < 0.35
        elif kind == "shake":
            dx = 7 * math.sin(2 * math.pi * 7 * phase)
            impact = True
        elif kind == "point":
            dx = 18 * math.cos(2 * math.pi * phase)
            dy = 10 * math.sin(2 * math.pi * phase)
            angle = -8 * math.cos(2 * math.pi * phase)
        elif kind == "trigger":
            squeeze = 0.3 < phase < 0.7
            impact = squeeze
        elif kind == "lean":
            dx = 26 * s
            angle = 8 * s

        x, y = center.x() + dx, center.y() + dy
        color = self.colors[hand] if hand < len(self.colors) else (255, 255, 255)
        flashing = impact and motion.flash
        rumbling = impact and motion.rumble
        p.save()
        p.translate(x, y)
        p.rotate(angle)
        p.scale(scale, scale)
        # Handle
        handle = QRectF(-9, -8, 18, 62)
        p.setPen(QPen(QColor("#3a3e50"), 1.5))
        p.setBrush(QBrush(QColor("#2a2d3a")))
        p.drawRoundedRect(handle, 8, 8)
        # Trigger
        p.setBrush(QBrush(QColor("#ff3fb4" if squeeze else "#4b4f63")))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(-4, 14 if squeeze else 18, 8, 12), 3, 3)
        # Move button
        p.setBrush(QBrush(QColor("#5b5f75")))
        p.drawEllipse(QPointF(0, 4), 4, 4)
        # Sphere
        col = QColor(255, 255, 255) if flashing else QColor(*color)
        glow = QColor(col)
        glow.setAlpha(70)
        p.setBrush(QBrush(glow))
        p.drawEllipse(QPointF(0, -24), 22, 22)
        p.setBrush(QBrush(col))
        p.drawEllipse(QPointF(0, -24), 15, 15)
        p.setBrush(QBrush(QColor(255, 255, 255, 120)))
        p.drawEllipse(QPointF(-5, -29), 4, 4)
        # Rumble arcs
        if rumbling:
            p.setPen(QPen(QColor("#fbbf24"), 2))
            p.setBrush(Qt.NoBrush)
            for r in (26, 34):
                p.drawArc(QRectF(-r, 20 - r, 2 * r, 2 * r), 20 * 16, 50 * 16)
                p.drawArc(QRectF(-r, 20 - r, 2 * r, 2 * r), 110 * 16, 50 * 16)
        p.restore()
        # Pointing line to the screen
        if kind == "point":
            p.setPen(QPen(QColor(col.red(), col.green(), col.blue(), 110), 1, Qt.DashLine))
            p.drawLine(QPointF(x, y - 24), QPointF(screen.center().x() + dx * 2.5, screen.center().y() + dy * 0.6))
