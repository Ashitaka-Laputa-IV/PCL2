"""轻量事件总线：下载进度、任务状态、游戏日志等跨线程事件。

业务层（worker 线程）只负责 ``emit``，UI 层通过订阅回调接收，
再把回调结果转成 Qt 信号，避免业务层依赖 Qt。
"""

from __future__ import annotations

import threading
from typing import Any, Callable

# ---- 事件名常量 ----
EVENT_DOWNLOAD_PROGRESS = "download.progress"
EVENT_TASK_STATE = "download.task_state"
EVENT_INSTALL_STAGE = "install.stage"
EVENT_GAME_LOG = "game.log"
EVENT_GAME_STATE = "game.state"
EVENT_TOAST = "ui.toast"

Handler = Callable[[Any], None]


class EventBus:
    """线程安全的最小事件总线。"""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = {}
        self._lock = threading.RLock()

    def subscribe(self, event: str, handler: Handler) -> Callable[[], None]:
        """订阅事件，返回取消订阅函数。"""
        with self._lock:
            self._handlers.setdefault(event, []).append(handler)

        def unsubscribe() -> None:
            with self._lock:
                handlers = self._handlers.get(event)
                if handlers and handler in handlers:
                    handlers.remove(handler)

        return unsubscribe

    def emit(self, event: str, payload: Any = None) -> None:
        with self._lock:
            handlers = tuple(self._handlers.get(event, ()))
        for handler in handlers:
            try:
                handler(payload)
            except Exception:  # 单个订阅者异常不应中断事件分发
                continue

    def clear(self) -> None:
        with self._lock:
            self._handlers.clear()


bus = EventBus()


def reset_for_tests() -> None:
    """清空全局总线（仅测试使用）。"""
    bus.clear()
