"""网络图片加载（项目图标），带内存缓存与并发合并。

UI 只会在主线程调用 :meth:`ImageLoader.get`；网络请求由 Qt 的事件循环处理，
回调也在主线程触发，因此可以直接更新控件。
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QByteArray, QObject, QUrl
from PySide6.QtGui import QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from pcl import USER_AGENT

MAX_IMAGE_BYTES = 4 * 1024 * 1024
Callback = Callable[[QPixmap], None]


class ImageLoader(QObject):
    """简单的异步图片加载器。"""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._manager = QNetworkAccessManager(self)
        self._cache: dict[str, QPixmap] = {}
        self._pending: dict[str, list[Callback]] = {}
        self._failed: set[str] = set()

    def cached(self, url: str) -> QPixmap | None:
        return self._cache.get(url)

    def get(self, url: str, callback: Callback) -> None:
        """请求图片；命中缓存时立即回调。"""
        if not url:
            return
        cached = self._cache.get(url)
        if cached is not None:
            callback(cached)
            return
        if url in self._failed:
            return
        if url in self._pending:
            self._pending[url].append(callback)
            return

        self._pending[url] = [callback]
        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b"User-Agent", USER_AGENT.encode("utf-8"))
        reply = self._manager.get(request)
        reply.finished.connect(lambda: self._on_finished(url, reply))

    def _on_finished(self, url: str, reply: QNetworkReply) -> None:
        callbacks = self._pending.pop(url, [])
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self._failed.add(url)
                return
            data = reply.readAll()
            if data.size() <= 0 or data.size() > MAX_IMAGE_BYTES:
                self._failed.add(url)
                return
            pixmap = QPixmap()
            if not pixmap.loadFromData(QByteArray(data)):
                self._failed.add(url)
                return
            self._cache[url] = pixmap
            for callback in callbacks:
                try:
                    callback(pixmap)
                except RuntimeError:
                    # 控件已被销毁
                    continue
        finally:
            reply.deleteLater()

    def clear(self) -> None:
        self._cache.clear()
        self._failed.clear()
