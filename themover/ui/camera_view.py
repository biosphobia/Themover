"""Widget that shows camera frames, reports clicks in frame pixels, and lets the player drag rectangles."""
from __future__ import annotations

from typing import Optional

import numpy as np
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QLabel


class CameraView(QLabel):
    clicked = Signal(int, int)  # x, y in frame pixels
    rect_drawn = Signal(float, float, float, float)  # x0, y0, x1, y1 as fractions of the frame (mode 'crop' / 'zone')

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(320, 240)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("background:#000; border-radius:8px;")
        self.setText("no camera")
        self.mode = "click"  # click | crop | zone
        self._frame_size: Optional[tuple[int, int]] = None
        self._drawn: Optional[tuple[int, int, int, int]] = None  # x, y, w, h of the drawn pixmap
        self._drag_start: Optional[tuple[float, float]] = None
        self._drag_cur: Optional[tuple[float, float]] = None

    def show_frame(self, frame: Optional[np.ndarray]) -> None:
        if frame is None:
            return
        h, w = frame.shape[:2]
        self._frame_size = (w, h)
        rgb = np.ascontiguousarray(frame[:, :, ::-1])
        img = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888)
        pix = QPixmap.fromImage(img).scaled(self.width(), self.height(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.setPixmap(pix)
        x = (self.width() - pix.width()) // 2
        y = (self.height() - pix.height()) // 2
        self._drawn = (x, y, pix.width(), pix.height())

    # ------------------------------------------------------------ geometry
    def _to_fraction(self, pos) -> Optional[tuple[float, float]]:
        if not self._frame_size or not self._drawn:
            return None
        x0, y0, dw, dh = self._drawn
        fx = (pos.x() - x0) / max(1, dw)
        fy = (pos.y() - y0) / max(1, dh)
        return max(0.0, min(1.0, fx)), max(0.0, min(1.0, fy))

    def _to_pixels(self, pos) -> Optional[tuple[int, int]]:
        frac = self._to_fraction(pos)
        if frac is None:
            return None
        x0, y0, dw, dh = self._drawn
        px, py = pos.x() - x0, pos.y() - y0
        if not (0 <= px < dw and 0 <= py < dh):
            return None
        return int(frac[0] * self._frame_size[0]), int(frac[1] * self._frame_size[1])

    # ------------------------------------------------------------ input
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            if self.mode in ("crop", "zone"):
                frac = self._to_fraction(event.position())
                if frac is not None:
                    self._drag_start = self._drag_cur = frac
                    self.update()
            else:
                px = self._to_pixels(event.position())
                if px is not None:
                    self.clicked.emit(*px)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_start is not None:
            self._drag_cur = self._to_fraction(event.position())
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._drag_start is not None and self._drag_cur is not None:
            (ax, ay), (bx, by) = self._drag_start, self._drag_cur
            self._drag_start = self._drag_cur = None
            self.update()
            if abs(bx - ax) > 0.02 and abs(by - ay) > 0.02:
                self.rect_drawn.emit(min(ax, bx), min(ay, by), max(ax, bx), max(ay, by))
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if self._drag_start is None or self._drag_cur is None or not self._drawn:
            return
        x0, y0, dw, dh = self._drawn
        (ax, ay), (bx, by) = self._drag_start, self._drag_cur
        rect = QRectF(x0 + min(ax, bx) * dw, y0 + min(ay, by) * dh, abs(bx - ax) * dw, abs(by - ay) * dh)
        p = QPainter(self)
        colour = QColor("#ffffff") if self.mode == "crop" else QColor("#50ff78")
        p.setPen(QPen(colour, 2, Qt.DashLine))
        p.drawRect(rect)
        p.setPen(colour)
        p.drawText(rect.topLeft() + type(rect.topLeft())(4, 14), "tracking area" if self.mode == "crop" else "trigger zone")
