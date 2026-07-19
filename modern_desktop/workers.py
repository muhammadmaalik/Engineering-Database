"""Qt thread-pool helpers used by every potentially blocking operation."""

from __future__ import annotations

import traceback
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class WorkerSignals(QObject):
    started = Signal()
    progress = Signal(int, str)
    result = Signal(object)
    error = Signal(str)
    finished = Signal()


class Worker(QRunnable):
    """Run a callable on QThreadPool and marshal results to the UI thread.

    Set ``with_progress`` when the callable accepts ``progress(percent, text)``.
    """

    def __init__(
        self,
        function: Callable[..., Any],
        *args: Any,
        with_progress: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self.function = function
        self.args = args
        self.kwargs = kwargs
        self.with_progress = with_progress
        self.signals = WorkerSignals()
        self.setAutoDelete(True)

    @Slot()
    def run(self) -> None:
        self._emit(self.signals.started)
        try:
            if self.with_progress:
                self.kwargs["progress"] = lambda *args: self._emit(self.signals.progress, *args)
            value = self.function(*self.args, **self.kwargs)
        except Exception as exc:  # UI receives a useful concise error.
            message = f"{type(exc).__name__}: {exc}"
            detail = traceback.format_exc(limit=5)
            self._emit(self.signals.error, f"{message}\n{detail}")
        else:
            self._emit(self.signals.result, value)
        finally:
            self._emit(self.signals.finished)

    @staticmethod
    def _emit(signal: Any, *args: Any) -> None:
        try:
            signal.emit(*args)
        except RuntimeError:
            # The window can close while a network request is finishing.
            pass


class JobRunner(QObject):
    """Small owner for background jobs, preventing premature signal cleanup."""

    busy_changed = Signal(bool)

    def __init__(self, pool: Any, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.pool = pool
        self._active: set[Worker] = set()

    def submit(
        self,
        function: Callable[..., Any],
        *args: Any,
        on_result: Callable[[Any], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        on_progress: Callable[[int, str], None] | None = None,
        with_progress: bool = False,
        **kwargs: Any,
    ) -> Worker:
        worker = Worker(function, *args, with_progress=with_progress, **kwargs)
        self._active.add(worker)
        if on_result:
            worker.signals.result.connect(on_result)
        if on_error:
            worker.signals.error.connect(on_error)
        if on_progress:
            worker.signals.progress.connect(on_progress)
        worker.signals.finished.connect(lambda w=worker: self._release(w))
        self.busy_changed.emit(True)
        self.pool.start(worker)
        return worker

    def _release(self, worker: Worker) -> None:
        self._active.discard(worker)
        self.busy_changed.emit(bool(self._active))
