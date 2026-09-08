"""Small shared widgets."""
from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QLabel, QSizePolicy


class WrapLabel(QLabel):
    """A word-wrapping label whose minimum height follows its current width.

    Plain ``QLabel`` with word wrap reports a minimum height based on a guessed
    width, so inside scroll areas / nested layouts the text gets clipped.  This
    subclass re-computes the height for the real width after every resize.
    """

    def __init__(self, text: str = "", muted: bool = True) -> None:
        super().__init__(text)
        self.setWordWrap(True)
        if muted:
            self.setObjectName("muted")
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.updateGeometry()

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        width = max(self.width(), 200)
        return QSize(0, self.heightForWidth(width))

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSizeHint()
