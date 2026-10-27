"""HTTP 基础设施：客户端工厂、JSON 请求、断点续传下载与限速。

本模块不感知"下载任务"概念，只提供单文件级别的可靠读写能力，
调度与重试策略在 :mod:`pcl.net.downloader` 中组合。
"""

from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable

import httpx

from pcl import USER_AGENT
from pcl.core.errors import CancelledError, ChecksumError, NetworkError
from pcl.core.logging import get_logger

log = get_logger(__name__)

CHUNK_SIZE = 256 * 1024
DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)
ACCEPT_ENCODING = "identity"  # 保证 Range 请求的字节偏移可用


class HttpStatusError(NetworkError):
    """服务端返回了非 2xx 状态码。"""

    def __init__(self, status_code: int, url: str) -> None:
        self.status_code = status_code
        self.url = url
        super().__init__(f"服务器返回状态码 {status_code}", detail=url)


class NotFoundError(HttpStatusError):
    """404 / 410：该候选地址没有此文件，应直接尝试下一个源。"""

    def __init__(self, status_code: int, url: str) -> None:
        super().__init__(status_code, url)
        self.message = "文件不存在"


def create_client(*, concurrency: int = 16, timeout: httpx.Timeout | None = None) -> httpx.Client:
    """创建带连接池的同步客户端（线程安全，可被多个下载线程共享）。"""
    limits = httpx.Limits(
        max_connections=max(4, concurrency * 2),
        max_keepalive_connections=max(4, concurrency),
    )
    return httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept-Encoding": ACCEPT_ENCODING},
        timeout=timeout or DEFAULT_TIMEOUT,
        limits=limits,
        follow_redirects=True,
    )


def sha1_file(path: Path) -> str:
    """计算文件 sha1。"""
    digest = hashlib.sha1()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_matches(path: Path, sha1: str | None, size: int | None) -> bool:
    """判断磁盘上的文件是否满足校验要求。"""
    try:
        if not path.is_file():
            return False
        actual_size = path.stat().st_size
        if size is not None and actual_size != size:
            return False
        if sha1 and sha1_file(path).lower() != sha1.lower():
            return False
        return True
    except OSError:
        return False


class SpeedLimiter:
    """跨线程共享的令牌桶限速器（0 表示不限速）。"""

    def __init__(self, kbps: int = 0) -> None:
        self.rate = max(0, int(kbps)) * 1024
        self._allowance = 0.0
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def consume(self, amount: int, cancel: threading.Event | None = None) -> None:
        if self.rate <= 0 or amount <= 0:
            return
        while True:
            with self._lock:
                now = time.monotonic()
                self._allowance = min(
                    self.rate * 0.5,
                    self._allowance + (now - self._last) * self.rate,
                )
                self._last = now
                if self._allowance >= amount:
                    self._allowance -= amount
                    return
                wait = (amount - self._allowance) / self.rate
            if cancel is not None and cancel.is_set():
                raise CancelledError()
            time.sleep(min(max(wait, 0.005), 0.1))


def get_json(
    client: httpx.Client,
    url: str,
    *,
    retries: int = 3,
    cancel: threading.Event | None = None,
) -> Any:
    """获取并解析 JSON，带指数退避重试。"""
    last_error: Exception | None = None
    for attempt in range(max(1, retries + 1)):
        if cancel is not None and cancel.is_set():
            raise CancelledError()
        try:
            response = client.get(url)
            if response.status_code >= 400:
                if response.status_code in (404, 410):
                    raise NotFoundError(response.status_code, url)
                raise HttpStatusError(response.status_code, url)
            return response.json()
        except NotFoundError:
            raise
        except (httpx.HTTPError, ValueError, NetworkError) as error:
            last_error = error
            if attempt:
                time.sleep(min(0.4 * 2**attempt, 3.0))
    raise NetworkError("获取网络数据失败。", detail=str(last_error)) from last_error


def get_text(client: httpx.Client, url: str, *, retries: int = 3) -> str:
    """获取文本内容（用于校验文件、简单页面）。"""
    last_error: Exception | None = None
    for attempt in range(max(1, retries + 1)):
        try:
            response = client.get(url)
            if response.status_code >= 400:
                raise HttpStatusError(response.status_code, url)
            return response.text
        except httpx.HTTPError as error:
            last_error = error
            if attempt:
                time.sleep(min(0.4 * 2**attempt, 3.0))
    raise NetworkError("获取网络数据失败。", detail=str(last_error)) from last_error


def download_file(
    client: httpx.Client,
    url: str,
    dest: Path,
    *,
    sha1: str | None = None,
    size: int | None = None,
    resume: bool = True,
    progress: Callable[[int], None] | None = None,
    cancel: threading.Event | None = None,
    limiter: SpeedLimiter | None = None,
) -> int:
    """下载单个文件到 ``dest``。

    先写入 ``<dest>.part``，校验通过后原子改名，避免半成品被当作有效缓存。
    已存在且校验通过的文件会直接跳过（返回 0）。

    :returns: 本次新写入的字节数
    """
    if sha1 or size is not None:
        if file_matches(dest, sha1, size):
            return 0

    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")

    offset = part.stat().st_size if (resume and part.exists()) else 0
    if offset and size is not None and offset >= size:
        part.unlink(missing_ok=True)
        offset = 0

    headers: dict[str, str] = {}
    if offset:
        headers["Range"] = f"bytes={offset}-"

    written = 0
    try:
        with client.stream("GET", url, headers=headers) as response:
            if response.status_code in (404, 410):
                raise NotFoundError(response.status_code, url)
            if response.status_code == 416:
                # 断点信息失效，清空后由上层重试完整下载
                part.unlink(missing_ok=True)
                raise NetworkError("断点续传范围无效，将重新下载。", detail=url)
            if response.status_code not in (200, 206):
                raise HttpStatusError(response.status_code, url)

            mode = "ab" if (response.status_code == 206 and offset) else "wb"
            if mode == "wb":
                written = 0
            with part.open(mode) as stream:
                for chunk in response.iter_bytes(CHUNK_SIZE):
                    if cancel is not None and cancel.is_set():
                        raise CancelledError()
                    if not chunk:
                        continue
                    stream.write(chunk)
                    written += len(chunk)
                    if limiter is not None:
                        limiter.consume(len(chunk), cancel)
                    if progress is not None:
                        progress(len(chunk))
    except httpx.HTTPError as error:
        raise NetworkError("下载中断，请重试。", detail=str(error)) from error

    total = part.stat().st_size
    if size is not None and total != size:
        part.unlink(missing_ok=True)
        raise ChecksumError("文件大小不符，已丢弃。", path=str(dest))

    if sha1:
        actual = sha1_file(part)
        if actual.lower() != sha1.lower():
            part.unlink(missing_ok=True)
            raise ChecksumError("文件 sha1 校验失败，已丢弃。", path=str(dest))

    dest.unlink(missing_ok=True)
    part.replace(dest)
    return written


def remove_parts(paths: Iterable[Path]) -> None:
    """清理残留的 ``.part`` 文件。"""
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
