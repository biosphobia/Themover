"""Run blocking work (API calls, recordings) off the GUI thread."""
from __future__ import annotations

import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot


class WorkerSignals(QObject):
    finished = Signal(object)
    error = Signal(str)
    text = Signal(str)
    progress = Signal(float, float)
    tool = Signal(str, object, str)


class Worker(QRunnable):
    """Call ``fn(signals, *args)`` on the thread pool."""

    def __init__(self, fn: Callable[..., Any], *args: Any) -> None:
        super().__init__()
        self.fn = fn
        self.args = args
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = self.fn(self.signals, *self.args)
        except Exception as exc:  # surfaced to the UI
            self.signals.error.emit(f"{exc}\n\n{traceback.format_exc(limit=3)}")
            return
        self.signals.finished.emit(result)


_ACTIVE: set[Worker] = set()


def run_in_background(worker: Worker) -> None:
    """Start the worker and keep it referenced until its signals have fired."""
    _ACTIVE.add(worker)
    worker.setAutoDelete(False)
    worker.signals.finished.connect(lambda _r, w=worker: _ACTIVE.discard(w))
    worker.signals.error.connect(lambda _e, w=worker: _ACTIVE.discard(w))
    QThreadPool.globalInstance().start(worker)
