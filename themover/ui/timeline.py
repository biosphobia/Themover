"""Timeline widget: recorded motion, detected hits / gestures, mapping actions and tags on one navigable strip."""
from __future__ import annotations

from typing import Optional

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QLabel, QWidget

from themover.ai.motion_capture import MotionSession

KIND_COLOR = {"don": QColor("#ff5252"), "kat": QColor("#4fc3f7"), "other": QColor("#fbbf24"), "nothing": QColor("#9e9e9e"), "note": QColor("#fbbf24")}
_PALETTE = [QColor(c) for c in ("#a78bfa", "#34d399", "#f472b6", "#fb923c", "#facc15", "#22d3ee", "#c084fc", "#4ade80")]
HAND_COLOR = {0: QColor("#ff3fb4"), 1: QColor("#33e6ff")}
ACTION_COLOR = QColor("#e2e8f0")


def kind_color(kind: str) -> QColor:
    """Stable colour per tag / event kind (fixed for don, kat, nothing; hashed for the rest)."""
    if kind in KIND_COLOR:
        return KIND_COLOR[kind]
    return _PALETTE[sum(ord(ch) for ch in kind) % len(_PALETTE)]


class TimelineWidget(QWidget):
    cursor_changed = Signal(float)

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(220)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.session: Optional[MotionSession] = None
        self.cursor = 0.0
        self.view_start = 0.0
        self.view_span = 10.0
        self.replay_hits: list[dict] = []  # hits / gestures from a replay with candidate tuning (drawn hollow)
        self._drag = False
        self._pan_origin: Optional[tuple[float, float]] = None
        self._cache: dict = {}

    # ------------------------------------------------------------ model
    def set_session(self, session: Optional[MotionSession]) -> None:
        self.session = session
        self.cursor = 0.0
        self.view_start = 0.0
        self.view_span = max(1.0, min(10.0, session.duration)) if session else 10.0
        self.replay_hits = []
        self._cache = {}
        self.update()

    def set_cursor(self, t: float, emit: bool = True) -> None:
        if self.session is None:
            return
        t = max(0.0, min(self.session.duration, float(t)))
        self.cursor = t
        if t < self.view_start or t > self.view_start + self.view_span:
            self.view_start = max(0.0, t - self.view_span / 2)
        self.update()
        if emit:
            self.cursor_changed.emit(t)

    def zoom(self, factor: float, at: Optional[float] = None) -> None:
        if self.session is None:
            return
        at = self.cursor if at is None else at
        frac = (at - self.view_start) / max(self.view_span, 1e-6)
        self.view_span = max(0.25, min(max(1.0, self.session.duration), self.view_span * factor))
        self.view_start = max(0.0, min(self.session.duration - self.view_span, at - frac * self.view_span))
        self.update()

    def pan(self, seconds: float) -> None:
        if self.session is None:
            return
        self.view_start = max(0.0, min(max(0.0, self.session.duration - self.view_span), self.view_start + seconds))
        self.update()

    # ------------------------------------------------------------ geometry
    def _x(self, t: float, w: float) -> float:
        return (t - self.view_start) / max(self.view_span, 1e-6) * w

    def _t(self, x: float, w: float) -> float:
        return self.view_start + x / max(w, 1.0) * self.view_span

    # ------------------------------------------------------------ paint
    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor("#0f1015"))
        if self.session is None:
            p.setPen(QColor("#6b7086"))
            p.drawText(self.rect(), Qt.AlignCenter, "Record a session (or load one) to see the timeline")
            return
        s = self.session
        axis_h = 18
        tracks_top = axis_h + 4
        lane_h = 22 if s.actions else 0
        track_h = (h - tracks_top - 6 - lane_h) / 2
        # time axis
        p.setPen(QColor("#4b4f63"))
        p.setFont(QFont("Segoe UI", 8))
        step = _nice_step(self.view_span / max(4, w / 90))
        t = (self.view_start // step) * step
        while t <= self.view_start + self.view_span:
            x = self._x(t, w)
            p.drawLine(QPointF(x, 0), QPointF(x, h))
            p.drawText(QPointF(x + 2, 12), f"{t:.2f}s")
            t += step
        # tracks
        for hand in (0, 1):
            top = tracks_top + hand * track_h
            rect = QRectF(0, top, w, track_h - 4)
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(QColor("#161821")))
            p.drawRoundedRect(rect, 4, 4)
            p.setPen(QColor("#9aa0b4"))
            p.drawText(QPointF(6, top + 14), "right hand (c0)" if hand == 0 else "left hand (c1)")
            self._draw_track(p, hand, rect)
        if lane_h:
            self._draw_actions(p, QRectF(0, tracks_top + 2 * track_h, w, lane_h - 4))
        # tags
        for i, tag in enumerate(s.tags):
            x = self._x(tag.t, w)
            if 0 <= x <= w:
                col = kind_color(tag.kind)
                p.setPen(QPen(col, 2, Qt.DashLine))
                p.drawLine(QPointF(x, axis_h), QPointF(x, h))
                p.setPen(col)
                p.drawText(QPointF(x + 3, h - 6), f"{i}:{tag.label()}")
        # cursor
        x = self._x(self.cursor, w)
        p.setPen(QPen(QColor("#ffffff"), 2))
        p.drawLine(QPointF(x, 0), QPointF(x, h))
        p.setPen(QColor("#ffffff"))
        p.drawText(QPointF(min(x + 4, w - 70), h - 20), f"{self.cursor:.3f}s")

    def _draw_track(self, p: QPainter, hand: int, rect: QRectF) -> None:
        s = self.session
        t = s.t[hand]
        if len(t) < 2:
            p.setPen(QColor("#6b7086"))
            p.drawText(rect, Qt.AlignCenter, "no data")
            return
        w = self.width()
        i0 = max(0, s.index_at(hand, self.view_start) - 1)
        i1 = min(len(t), s.index_at(hand, self.view_start + self.view_span) + 2)
        if i1 - i0 < 2:
            return
        mid = rect.center().y() + 10
        scale = (rect.height() - 24) / 8.0  # ±4 g
        lm = s.linear_magnitude(hand)
        d = s.data[hand]
        # trigger band
        trig = d[i0:i1, 6]
        if trig.max() > 0.05:
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(QColor(255, 255, 255, 25)))
            for j in range(i0, i1 - 1):
                if d[j, 6] > 0.5:
                    p.drawRect(QRectF(self._x(t[j], w), rect.top() + 18, max(1.0, self._x(t[j + 1], w) - self._x(t[j], w)), rect.height() - 20))
        # components (thin) and |a|-1g (thick)
        stride = max(1, (i1 - i0) // max(200, int(w * 2)))
        for col, colour in ((0, QColor(255, 82, 82, 110)), (1, QColor(76, 175, 80, 110)), (2, QColor(79, 195, 247, 110))):
            path = QPainterPath()
            first = True
            for j in range(i0, i1, stride):
                y = mid - float(d[j, col]) * scale
                pt = QPointF(self._x(t[j], w), y)
                if first:
                    path.moveTo(pt)
                    first = False
                else:
                    path.lineTo(pt)
            p.setPen(QPen(colour, 1))
            p.drawPath(path)
        path = QPainterPath()
        first = True
        for j in range(i0, i1, stride):
            pt = QPointF(self._x(t[j], w), mid - float(lm[j]) * scale)
            if first:
                path.moveTo(pt)
                first = False
            else:
                path.lineTo(pt)
        p.setPen(QPen(HAND_COLOR[hand], 2))
        p.drawPath(path)
        p.setPen(QPen(QColor("#33364a"), 1, Qt.DotLine))
        p.drawLine(QPointF(rect.left(), mid), QPointF(rect.right(), mid))
        # events: recorded (filled) and replay (hollow); hits = triangles, gestures = diamonds with a label
        y = rect.top() + 22
        for events, filled in ((s.hits, True), (s.gestures, True), (self.replay_hits, False)):
            for ev in events:
                if ev.get("hand") != hand:
                    continue
                x = self._x(ev["t"], w)
                if x < 0 or x > w:
                    continue
                col = kind_color(ev["kind"])
                shape = QPainterPath()
                if ev.get("family", "gesture" if ev["kind"] not in ("don", "kat") else "hit") == "hit":
                    shape.moveTo(x, y + 10); shape.lineTo(x - 6, y); shape.lineTo(x + 6, y)
                else:
                    shape.moveTo(x, y - 2); shape.lineTo(x - 6, y + 5); shape.lineTo(x, y + 12); shape.lineTo(x + 6, y + 5)
                    if filled:
                        p.setPen(col)
                        p.drawText(QPointF(x + 8, y + 10), ev["kind"])
                shape.closeSubpath()
                p.setPen(QPen(col, 1.5))
                p.setBrush(QBrush(col) if filled else Qt.NoBrush)
                p.drawPath(shape)

    def _draw_actions(self, p: QPainter, rect: QRectF) -> None:
        """Bars from press to release for every button target the mapping sent while recording."""
        s = self.session
        w = self.width()
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor("#161821")))
        p.drawRoundedRect(rect, 4, 4)
        p.setPen(QColor("#9aa0b4"))
        p.drawText(QPointF(6, rect.top() + 14), "sent to game")
        open_at: dict[str, float] = {}
        end = self.view_start + self.view_span
        bars: list[tuple[str, float, float]] = []
        for a in s.actions:
            if a.get("down"):
                open_at[a["target"]] = a["t"]
            else:
                t0 = open_at.pop(a["target"], None)
                if t0 is not None:
                    bars.append((a["target"], t0, a["t"]))
        bars += [(k, t0, s.duration) for k, t0 in open_at.items()]
        for target, t0, t1 in bars:
            if t1 < self.view_start or t0 > end:
                continue
            x0, x1 = self._x(t0, w), self._x(t1, w)
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(ACTION_COLOR))
            p.drawRect(QRectF(x0, rect.top() + 4, max(2.0, x1 - x0), rect.height() - 8))
            p.setPen(QColor("#0f1015"))
            p.drawText(QPointF(x0 + 2, rect.top() + 14), target.split(".", 1)[-1])

    # ------------------------------------------------------------ input
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._drag = True
            self.set_cursor(self._t(event.position().x(), self.width()))
        elif event.button() == Qt.MiddleButton:
            self._pan_origin = (event.position().x(), self.view_start)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag:
            self.set_cursor(self._t(event.position().x(), self.width()))
        elif self._pan_origin is not None:
            dx = event.position().x() - self._pan_origin[0]
            self.view_start = self._pan_origin[1] - dx / max(self.width(), 1) * self.view_span
            self.view_start = max(0.0, min(max(0.0, (self.session.duration if self.session else 0) - self.view_span), self.view_start))
            self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag = False
        self._pan_origin = None

    def wheelEvent(self, event) -> None:  # noqa: N802
        delta = event.angleDelta().y()
        if event.modifiers() & Qt.ShiftModifier:
            self.pan(-delta / 120.0 * self.view_span * 0.1)
        else:
            self.zoom(0.8 if delta > 0 else 1.25, at=self._t(event.position().x(), self.width()))


def _nice_step(raw: float) -> float:
    for step in (0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 30, 60):
        if step >= raw:
            return step
    return 60.0


class VideoView(QLabel):
    def __init__(self, title: str) -> None:
        super().__init__()
        self.title = title
        self.setMinimumSize(240, 160)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("background:#000; border-radius:6px; color:#6b7086;")
        self.setText(f"{title}: no video")

    def show_frame(self, frame) -> None:
        if frame is None:
            return
        from PySide6.QtGui import QImage, QPixmap

        h, w = frame.shape[:2]
        rgb = np.ascontiguousarray(frame[:, :, ::-1])
        img = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888)
        self.setPixmap(QPixmap.fromImage(img).scaled(self.width(), self.height(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
