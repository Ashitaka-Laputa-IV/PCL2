"""日志系统：轮转文件 + 环形缓冲 + UI 订阅。

UI 层不直接读取日志文件，而是订阅 :class:`RingBuffer` 获取实时行，
用于日志窗口与状态提示；文件端负责持久化以便排查。
"""

from __future__ import annotations

import logging as _logging
import sys
import threading
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable

LOG_FORMAT = "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

Listener = Callable[[str], None]


class RingBuffer:
    """线程安全的环形日志缓冲，支持订阅新行。"""

    def __init__(self, capacity: int = 5000) -> None:
        self._buffer: deque[str] = deque(maxlen=max(1, capacity))
        self._listeners: list[Listener] = []
        self._lock = threading.RLock()

    def append(self, line: str) -> None:
        with self._lock:
            self._buffer.append(line)
            listeners = tuple(self._listeners)
        for listener in listeners:
            try:
                listener(line)
            except Exception:  # 订阅者异常不能影响日志线程
                continue

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        """注册监听器，返回取消订阅函数。"""
        with self._lock:
            self._listeners.append(listener)

        def unsubscribe() -> None:
            with self._lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)

        return unsubscribe

    def lines(self) -> list[str]:
        with self._lock:
            return list(self._buffer)

    def tail(self, count: int) -> list[str]:
        with self._lock:
            if count <= 0:
                return []
            return list(self._buffer)[-count:]

    def clear(self) -> None:
        with self._lock:
            self._buffer.clear()


class _RingHandler(_logging.Handler):
    """把格式化后的日志写入 :class:`RingBuffer`。"""

    def __init__(self, buffer: RingBuffer) -> None:
        super().__init__()
        self._buffer = buffer

    def emit(self, record: _logging.LogRecord) -> None:
        try:
            self._buffer.append(self.format(record))
        except Exception:  # pragma: no cover - 防御性
            self.handleError(record)


_ring = RingBuffer()
_configured = False


def get_ring_buffer() -> RingBuffer:
    """返回全局环形日志缓冲。"""
    return _ring


def setup_logging(log_file: Path | None = None, *, level: int = _logging.INFO, console: bool = True) -> _logging.Logger:
    """初始化根日志器；重复调用时会先移除旧的处理器。"""
    global _configured
    root = _logging.getLogger()
    root.setLevel(level)

    if _configured:
        return root

    formatter = _logging.Formatter(LOG_FORMAT, DATE_FORMAT)

    ring_handler = _RingHandler(_ring)
    ring_handler.setFormatter(formatter)
    ring_handler.setLevel(level)
    root.addHandler(ring_handler)

    if console:
        stream = _logging.StreamHandler(sys.stderr)
        stream.setFormatter(formatter)
        stream.setLevel(level)
        root.addHandler(stream)

    if log_file is not None:
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                log_file,
                maxBytes=2 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            file_handler.setLevel(level)
            root.addHandler(file_handler)
        except OSError as error:  # 无写权限时仅退化为控制台日志
            root.warning("无法写入日志文件 %s: %s", log_file, error)

    _configured = True
    return root


def get_logger(name: str) -> _logging.Logger:
    """获取带命名空间的日志器。"""
    return _logging.getLogger(name)


def is_configured() -> bool:
    """测试辅助：判断日志是否已初始化。"""
    return _configured


def reset_for_tests() -> None:
    """清空全局状态（仅测试使用）。"""
    global _configured
    root = _logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    _ring.clear()
    _configured = False
