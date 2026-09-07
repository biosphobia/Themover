"""Widget that shows camera frames and reports clicks in frame pixels."""
from __future__ import annotations

from typing import Optional

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QLabel


class CameraView(QLabel):
    clicked = Signal(int, int)  # x, y in frame pixels

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(320, 240)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("background:#000; border-radius:8px;")
        self.setText("no camera")
        self._frame_size: Optional[tuple[int, int]] = None
        self._drawn: Optional[tuple[int, int, int, int]] = None  # x, y, w, h of the drawn pixmap

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

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self._frame_size and self._drawn:
            x0, y0, dw, dh = self._drawn
            px = event.position().x() - x0
            py = event.position().y() - y0
            if 0 <= px < dw and 0 <= py < dh:
                fx = int(px / dw * self._frame_size[0])
                fy = int(py / dh * self._frame_size[1])
                self.clicked.emit(fx, fy)
        super().mousePressEvent(event)
