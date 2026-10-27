"""并发下载引擎：任务模型、去重调度、校验、限速、进度聚合与取消。

所有下载行为（原版安装、加载器安装、资源补全、整合包解析）
都复用本模块，业务层只需构造 :class:`DownloadTask` 列表。
"""

from __future__ import annotations

import threading
import time
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

import httpx

from pcl.core.config import DownloadOptions
from pcl.core.errors import CancelledError, ChecksumError, NetworkError, friendly_message
from pcl.core.events import EVENT_DOWNLOAD_PROGRESS, EVENT_TASK_STATE, bus
from pcl.core.logging import get_logger
from pcl.net import http
from pcl.net.mirror import DEFAULT_MIRROR_BASE, candidates
from pcl.net.http import SpeedLimiter

log = get_logger(__name__)


@dataclass(frozen=True)
class DownloadTask:
    """一个文件的下载描述。"""

    url: str
    dest: Path
    sha1: str | None = None
    size: int | None = None
    mirrors: tuple[str, ...] = ()
    priority: int = 0
    name: str = ""
    optional: bool = False
    resume: bool = True

    @property
    def display_name(self) -> str:
        return self.name or self.dest.name

    def with_mirrors(self, extra: Iterable[str]) -> DownloadTask:
        merged = tuple(dict.fromkeys((*self.mirrors, *extra)))
        return DownloadTask(
            url=self.url,
            dest=self.dest,
            sha1=self.sha1,
            size=self.size,
            mirrors=merged,
            priority=self.priority,
            name=self.name,
            optional=self.optional,
            resume=self.resume,
        )


@dataclass
class TaskResult:
    """单个任务的执行结果。"""

    task: DownloadTask
    ok: bool = False
    skipped: bool = False
    bytes_written: int = 0
    attempts: int = 0
    error: str | None = None


@dataclass
class DownloadProgress:
    """聚合后的下载进度（UI 直接消费）。"""

    total_tasks: int = 0
    finished_tasks: int = 0
    failed_tasks: int = 0
    total_bytes: int = 0
    done_bytes: int = 0
    speed: float = 0.0  # bytes/s
    active: int = 0
    eta: float | None = None
    current: str = ""

    @property
    def percent(self) -> float:
        if self.total_bytes <= 0:
            return 100.0 if self.total_tasks and self.finished_tasks >= self.total_tasks else 0.0
        return min(100.0, self.done_bytes * 100.0 / self.total_bytes)


@dataclass
class DownloadSummary:
    """一次批量下载的汇总。"""

    results: list[TaskResult] = field(default_factory=list)
    elapsed: float = 0.0
    cancelled: bool = False

    @property
    def failures(self) -> list[TaskResult]:
        return [item for item in self.results if not item.ok and not item.task.optional]

    @property
    def ok(self) -> bool:
        return not self.failures


class DownloadManager:
    """线程池下载调度器。

    - 按目标路径去重，避免同一文件被并发写入；
    - 校验失败/网络错误自动切换候选地址（含镜像回退）；
    - 进度按字节聚合，并以约 10Hz 频率向 UI 推送，避免信号风暴。
    """

    def __init__(
        self,
        options: DownloadOptions | None = None,
        *,
        client: httpx.Client | None = None,
        concurrency: int | None = None,
        source: str | None = None,
        mirror_base: str | None = None,
        retry: int | None = None,
        speed_limit_kbps: int | None = None,
        progress_interval: float = 0.1,
    ) -> None:
        options = options or DownloadOptions()
        self.concurrency = max(1, min(64, concurrency if concurrency is not None else options.concurrency))
        self.source = source or options.source
        self.mirror_base = mirror_base or options.mirror_base or DEFAULT_MIRROR_BASE
        self.retry = options.retry if retry is None else max(0, retry)
        kbps = options.speed_limit_kbps if speed_limit_kbps is None else speed_limit_kbps
        self.limiter = SpeedLimiter(kbps)
        self._client = client
        self._progress_interval = progress_interval

        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._done_bytes = 0
        self._total_bytes = 0
        self._total_tasks = 0
        self._finished = 0
        self._failed = 0
        self._active = 0
        self._current = ""
        self._samples: deque[tuple[float, int]] = deque(maxlen=32)
        self._last_emit = 0.0
        self._results: list[TaskResult] = []

    # ---------- 生命周期 ----------
    @property
    def cancel_event(self) -> threading.Event:
        return self._cancel

    def cancel(self) -> None:
        """请求取消：工作线程会在下一个分块处退出。"""
        self._cancel.set()

    def reset(self) -> None:
        """重置统计，便于复用同一管理器。"""
        with self._lock:
            self._cancel = threading.Event()
            self._done_bytes = 0
            self._total_bytes = 0
            self._total_tasks = 0
            self._finished = 0
            self._failed = 0
            self._active = 0
            self._current = ""
            self._samples.clear()
            self._results = []

    # ---------- 数量统计 ----------
    def _emit_progress(self, force: bool = False) -> None:
        now = time.monotonic()
        with self._lock:
            if not force and now - self._last_emit < self._progress_interval:
                return
            self._last_emit = now
            self._samples.append((now, self._done_bytes))
            while len(self._samples) > 2 and now - self._samples[0][0] > 3.0:
                self._samples.popleft()
            speed = 0.0
            if len(self._samples) >= 2:
                t0, b0 = self._samples[0]
                t1, b1 = self._samples[-1]
                if t1 > t0:
                    speed = max(0.0, (b1 - b0) / (t1 - t0))
            remaining = self._total_bytes - self._done_bytes
            eta = (remaining / speed) if (speed > 0 and remaining > 0 and self._total_bytes > 0) else None
            progress = DownloadProgress(
                total_tasks=self._total_tasks,
                finished_tasks=self._finished,
                failed_tasks=self._failed,
                total_bytes=self._total_bytes,
                done_bytes=min(self._done_bytes, self._total_bytes) if self._total_bytes else self._done_bytes,
                speed=speed,
                active=self._active,
                eta=eta,
                current=self._current,
            )
        bus.emit(EVENT_DOWNLOAD_PROGRESS, progress)

    def _emit_task_state(self, task: DownloadTask, state: str, error: str | None = None) -> None:
        bus.emit(
            EVENT_TASK_STATE,
            {"dest": str(task.dest), "name": task.display_name, "state": state, "error": error},
        )

    # ---------- 任务执行 ----------
    def _download_one(self, task: DownloadTask, client: httpx.Client) -> TaskResult:
        result = TaskResult(task=task)
        if task.sha1 or task.size is not None:
            if http.file_matches(task.dest, task.sha1, task.size):
                result.ok = True
                result.skipped = True
                return result

        urls = candidates(
            task.url,
            source=self.source,
            mirror_base=self.mirror_base,
            extra_mirrors=task.mirrors,
        )

        last_error: Exception | None = None
        for attempt in range(self.retry + 1):
            if self._cancel.is_set():
                result.error = "已取消"
                return result
            for url in urls:
                if self._cancel.is_set():
                    result.error = "已取消"
                    return result
                written = 0

                def on_chunk(count: int) -> None:
                    nonlocal written
                    written += count
                    with self._lock:
                        self._done_bytes += count
                    self._emit_progress()

                try:
                    result.attempts += 1
                    downloaded = http.download_file(
                        client,
                        url,
                        task.dest,
                        sha1=task.sha1,
                        size=task.size,
                        resume=task.resume,
                        progress=on_chunk,
                        cancel=self._cancel,
                        limiter=self.limiter,
                    )
                    result.ok = True
                    result.bytes_written += downloaded
                    return result
                except CancelledError:
                    result.error = "已取消"
                    return result
                except http.NotFoundError as error:
                    last_error = error
                    log.debug("候选地址不可用 %s: %s", url, error)
                    continue
                except (ChecksumError, NetworkError) as error:
                    last_error = error
                    # 丢弃本次已计入的字节，避免进度虚高
                    with self._lock:
                        self._done_bytes = max(0, self._done_bytes - written)
                    log.debug("下载失败，尝试下一个源 %s: %s", url, error)
                    continue
            if attempt < self.retry:
                time.sleep(min(0.5 * 2**attempt, 4.0))

        result.error = friendly_message(last_error) if last_error else "下载失败"
        return result

    def run(self, tasks: Sequence[DownloadTask], *, concurrency: int | None = None) -> DownloadSummary:
        """下载全部任务并返回汇总结果（阻塞直到完成或取消）。"""
        started = time.monotonic()
        unique = dedupe_tasks(tasks)

        with self._lock:
            self._total_tasks = len(unique)
            self._total_bytes = sum(task.size or 0 for task in unique)
            self._current = unique[0].display_name if unique else ""

        if not unique:
            self._emit_progress(force=True)
            return DownloadSummary(results=[], elapsed=0.0)

        workers = max(1, min(64, concurrency or self.concurrency))
        owns_client = self._client is None
        client = self._client or http.create_client(concurrency=workers)

        ordered = sorted(unique, key=lambda item: item.priority, reverse=True)
        try:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="pcl-dl") as pool:
                futures: dict[Future[TaskResult], DownloadTask] = {}
                for task in ordered:
                    if self._cancel.is_set():
                        break
                    futures[pool.submit(self._download_one, task, client)] = task
                    with self._lock:
                        self._active += 1
                        self._current = task.display_name

                results: list[TaskResult] = []
                for future in futures:
                    try:
                        result = future.result()
                    except Exception as error:  # 兜底：不让单个任务炸掉整批
                        result = TaskResult(task=futures[future], error=friendly_message(error))
                    results.append(result)
                    with self._lock:
                        self._active = max(0, self._active - 1)
                        if result.ok:
                            self._finished += 1
                        elif not result.task.optional:
                            self._failed += 1
                    state = "skipped" if result.skipped else ("done" if result.ok else "failed")
                    self._emit_task_state(result.task, state, result.error)
                    self._emit_progress(force=True)
                self._results = results
        finally:
            if owns_client:
                client.close()

        elapsed = time.monotonic() - started
        cancelled = self._cancel.is_set()
        summary = DownloadSummary(results=self._results, elapsed=elapsed, cancelled=cancelled)
        self._emit_progress(force=True)
        if summary.failures:
            log.warning("下载完成，但有 %d 个任务失败", len(summary.failures))
        return summary


def dedupe_tasks(tasks: Iterable[DownloadTask]) -> list[DownloadTask]:
    """按目标路径去重；保留信息更完整的任务并合并镜像候选。"""
    seen: dict[str, DownloadTask] = {}
    for task in tasks:
        key = str(task.dest)
        existing = seen.get(key)
        if existing is None:
            seen[key] = task
            continue
        if existing.sha1 is None and task.sha1 is not None:
            merged = DownloadTask(
                url=task.url,
                dest=task.dest,
                sha1=task.sha1,
                size=task.size if task.size is not None else existing.size,
                mirrors=tuple(dict.fromkeys((*existing.mirrors, *task.mirrors))),
                priority=max(existing.priority, task.priority),
                name=existing.name or task.name,
                optional=existing.optional and task.optional,
                resume=existing.resume,
            )
            seen[key] = merged
        else:
            seen[key] = existing.with_mirrors(task.mirrors)
    return list(seen.values())


def total_size(tasks: Iterable[DownloadTask]) -> int:
    """统计任务总字节数（未知大小按 0 计）。"""
    return sum(task.size or 0 for task in tasks)
