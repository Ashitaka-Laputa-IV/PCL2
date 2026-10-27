"""游戏进程运行：natives 解压、进程启动、日志采集与结束。"""

from __future__ import annotations

import os
import subprocess
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Mapping

from pcl.core.errors import LaunchError
from pcl.core.events import EVENT_GAME_LOG, EVENT_GAME_STATE, bus
from pcl.core.logging import get_logger
from pcl.core.paths import AppPaths
from pcl.launch.args import LaunchPlan, log_config_argument
from pcl.version import meta

log = get_logger(__name__)


@dataclass
class GameState:
    """游戏进程状态快照。"""

    running: bool = False
    pid: int | None = None
    exit_code: int | None = None
    started_at: float = 0.0
    stopped_at: float = 0.0
    error: str | None = None

    @property
    def duration(self) -> float:
        if not self.started_at:
            return 0.0
        end = self.stopped_at or time.time()
        return max(0.0, end - self.started_at)


def extract_natives(
    paths: AppPaths,
    version_doc: Mapping[str, Any],
    natives_dir: Path,
    *,
    features: Mapping[str, bool] | None = None,
) -> int:
    """把各库的 natives 压缩包解压到独立目录，返回解压文件数。"""
    natives_dir.mkdir(parents=True, exist_ok=True)
    extracted = 0
    for library in version_doc.get("libraries") or []:
        if not isinstance(library, Mapping):
            continue
        if not meta.library_allowed(library, features):
            continue
        native = meta.native_download(library)
        if not isinstance(native, Mapping):
            continue

        relative = str(native.get("path") or "")
        if not relative:
            classifier = meta.native_classifier(library)
            if not classifier:
                continue
            relative = meta.maven_path(f"{library.get('name')}:{classifier}") or ""
        archive_path = paths.libraries / relative
        if not relative or not archive_path.is_file():
            log.debug("缺少 natives 压缩包：%s", archive_path)
            continue

        excludes = tuple(meta.extract_excludes(library))
        try:
            with zipfile.ZipFile(archive_path) as archive:
                for info in archive.infolist():
                    if info.is_dir():
                        continue
                    if excludes and info.filename.startswith(excludes):
                        continue
                    if info.filename.startswith("META-INF/"):
                        continue
                    target = natives_dir / info.filename
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info) as source, target.open("wb") as output:
                        output.write(source.read())
                    extracted += 1
        except (OSError, zipfile.BadZipFile) as error:
            log.warning("解压 natives 失败 %s: %s", archive_path, error)
    if extracted:
        log.info("已解压 %d 个 natives 文件到 %s", extracted, natives_dir)
    return extracted


class GameRunner:
    """管理单次游戏进程的生命周期。"""

    def __init__(
        self,
        paths: AppPaths,
        plan: LaunchPlan,
        *,
        version_doc: Mapping[str, Any] | None = None,
        log_path: Path | None = None,
    ) -> None:
        self.paths = paths
        self.plan = plan
        self.version_doc = version_doc or {}
        self.log_path = log_path or paths.game_log
        self._process: subprocess.Popen[str] | None = None
        self._readers: list[threading.Thread] = []
        self._state = GameState()
        self._log_file: IO[str] | None = None
        self._lock = threading.RLock()

    # ---------- 状态 ----------
    @property
    def state(self) -> GameState:
        with self._lock:
            return self._state

    @property
    def is_running(self) -> bool:
        process = self._process
        return process is not None and process.poll() is None

    def _emit_state(self) -> None:
        bus.emit(EVENT_GAME_STATE, self.state)

    # ---------- 启动 ----------
    def _prepare_command(self) -> list[str]:
        command = list(self.plan.command)
        if self.version_doc:
            log_arg = log_config_argument(self.version_doc, self.paths)
            if log_arg:
                command.insert(1, log_arg)
            extract_natives(self.paths, self.version_doc, self.plan.natives_dir)
        self.plan.game_dir.mkdir(parents=True, exist_ok=True)
        return command

    def start(self) -> GameState:
        """启动游戏进程并开始采集日志。"""
        if self.is_running:
            raise LaunchError("游戏已经在运行中。")

        command = self._prepare_command()
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._log_file = self.log_path.open("w", encoding="utf-8", errors="replace")
        except OSError:
            self._log_file = None

        env = os.environ.copy()
        env.setdefault("JAVA_TOOL_OPTIONS", "")
        env.pop("JAVA_TOOL_OPTIONS", None)
        env.update(self.plan.env)

        creationflags = 0
        if os.name == "nt":  # pragma: no cover - Windows 专属
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

        log.info("启动游戏：%s", self.plan.display_command)
        try:
            process = subprocess.Popen(
                command,
                cwd=str(self.plan.game_dir),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
        except FileNotFoundError as error:
            self._close_log()
            raise LaunchError("找不到 Java 可执行文件，请在设置中重新指定。", detail=str(error)) from error
        except OSError as error:
            self._close_log()
            raise LaunchError("无法启动游戏进程。", detail=str(error)) from error

        with self._lock:
            self._process = process
            self._state = GameState(running=True, pid=process.pid, started_at=time.time())
        self._emit_state()

        reader = threading.Thread(target=self._pump_output, args=(process,), name="pcl-game-log", daemon=True)
        reader.start()
        self._readers.append(reader)

        watcher = threading.Thread(target=self._watch, args=(process,), name="pcl-game-watch", daemon=True)
        watcher.start()
        self._readers.append(watcher)
        return self.state

    def _pump_output(self, process: subprocess.Popen[str]) -> None:
        stream = process.stdout
        if stream is None:
            return
        try:
            for line in iter(stream.readline, ""):
                text = line.rstrip("\n")
                if not text:
                    continue
                if self._log_file is not None:
                    try:
                        self._log_file.write(text + "\n")
                        self._log_file.flush()
                    except OSError:
                        pass
                bus.emit(EVENT_GAME_LOG, {"line": text, "version": self.plan.version_id})
        finally:
            try:
                stream.close()
            except OSError:
                pass

    def _watch(self, process: subprocess.Popen[str]) -> None:
        code = process.wait()
        for reader in list(self._readers):
            if reader is not threading.current_thread() and reader.is_alive():
                reader.join(timeout=1.0)
        with self._lock:
            self._state = GameState(
                running=False,
                pid=process.pid,
                exit_code=code,
                started_at=self._state.started_at,
                stopped_at=time.time(),
            )
        self._close_log()
        if code == 0:
            log.info("游戏已退出（退出码 0）")
        else:
            log.warning("游戏异常退出，退出码 %s", code)
        self._emit_state()

    def _close_log(self) -> None:
        if self._log_file is not None:
            try:
                self._log_file.close()
            except OSError:
                pass
            self._log_file = None

    # ---------- 结束 ----------
    def stop(self, timeout: float = 8.0) -> None:
        """请求结束游戏进程：先温和终止，超时后强制结束。"""
        process = self._process
        if process is None or process.poll() is not None:
            return
        log.info("正在结束游戏进程 %s", process.pid)
        try:
            process.terminate()
        except OSError:
            return
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            log.warning("游戏未在 %.0fs 内退出，强制结束", timeout)
            try:
                process.kill()
            except OSError:
                pass

    def wait(self, timeout: float | None = None) -> int | None:
        process = self._process
        if process is None:
            return None
        try:
            return process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None
