"""后台任务与事件桥接：所有 IO 在工作线程执行，UI 只接收信号。

事件总线（:mod:`pcl.core.events`）在工作线程 emit，这里转成 Qt 信号，
Qt 的跨线程连接会自动排队到主线程，因此不需要额外的线程同步代码。
"""

from __future__ import annotations

import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from pcl.core.errors import PclError, friendly_message
from pcl.core.events import (
    EVENT_DOWNLOAD_PROGRESS,
    EVENT_GAME_LOG,
    EVENT_GAME_STATE,
    EVENT_INSTALL_STAGE,
    EVENT_TASK_STATE,
    EVENT_TOAST,
    bus,
)
from pcl.core.logging import get_logger

log = get_logger(__name__)


class TaskSignals(QObject):
    """后台任务的信号集合。"""

    finished = Signal(object)
    failed = Signal(str)
    finished_message = Signal(str)


class FunctionTask(QRunnable):
    """把普通函数包装为可取消跟踪的线程池任务。"""

    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = TaskSignals()
        self.setAutoDelete(True)

    def run(self) -> None:  # noqa: D401
        try:
            result = self.fn(*self.args, **self.kwargs)
        except PclError as error:
            log.warning("后台任务失败：%s", error)
            self._emit(self.signals.failed, str(error))
        except Exception as error:  # 兜底：把栈写入日志便于排查
            log.error("后台任务异常：%s\n%s", error, traceback.format_exc())
            self._emit(self.signals.failed, friendly_message(error))
        else:
            self._emit(self.signals.finished, result)

    @staticmethod
    def _emit(signal, value: object) -> None:
        """窗口提前关闭时信号宿主可能已销毁，忽略这类异常。"""
        try:
            signal.emit(value)
        except RuntimeError:
            pass


class EventBridge(QObject):
    """把领域事件转换为 Qt 信号。"""

    download_progress = Signal(object)
    task_state = Signal(dict)
    install_stage = Signal(object)
    game_log = Signal(str)
    game_state = Signal(object)
    toast = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._unsubscribers: list[Callable[[], None]] = []
        self._unsubscribers.append(bus.subscribe(EVENT_DOWNLOAD_PROGRESS, self.download_progress.emit))
        self._unsubscribers.append(bus.subscribe(EVENT_TASK_STATE, self.task_state.emit))
        self._unsubscribers.append(bus.subscribe(EVENT_INSTALL_STAGE, self.install_stage.emit))
        self._unsubscribers.append(
            bus.subscribe(EVENT_GAME_LOG, lambda payload: self.game_log.emit(str(payload.get("line", ""))))
        )
        self._unsubscribers.append(bus.subscribe(EVENT_GAME_STATE, self.game_state.emit))
        self._unsubscribers.append(bus.subscribe(EVENT_TOAST, lambda payload: self.toast.emit(str(payload))))

    def detach(self) -> None:
        for unsubscribe in self._unsubscribers:
            try:
                unsubscribe()
            except Exception:
                pass
        self._unsubscribers.clear()


class TaskRunner(QObject):
    """线程池门面：统一提交与回收任务。"""

    def __init__(self, parent: QObject | None = None, *, max_threads: int = 6) -> None:
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(max(2, max_threads))
        self._tasks: list[FunctionTask] = []

    def submit(
        self,
        fn: Callable[..., Any],
        *args: Any,
        on_done: Callable[[Any], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        **kwargs: Any,
    ) -> FunctionTask:
        task = FunctionTask(fn, *args, **kwargs)
        if on_done is not None:
            task.signals.finished.connect(on_done)
        if on_error is not None:
            task.signals.failed.connect(on_error)
        task.signals.finished.connect(lambda _result, t=task: self._forget(t))
        task.signals.failed.connect(lambda _msg, t=task: self._forget(t))
        self._tasks.append(task)
        self.pool.start(task)
        return task

    def _forget(self, task: FunctionTask) -> None:
        if task in self._tasks:
            self._tasks.remove(task)

    def shutdown(self, wait_ms: int = 3000) -> None:
        self.pool.clear()
        self.pool.waitForDone(wait_ms)
        self._tasks.clear()


@Slot()
def noop() -> None:  # pragma: no cover - 占位，便于调试连接
    return None
