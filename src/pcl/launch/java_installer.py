"""一键安装 Java：从 Adoptium（Temurin）下载对应大版本的 JRE 并解压。

下载的是免安装压缩包（Linux/macOS 为 tar.gz，Windows 为 zip），解压到
``<数据根目录>/java/jre-<major>``，随后注册到运行环境列表。
"""

from __future__ import annotations

import shutil
import tarfile
import zipfile
from pathlib import Path
from typing import Callable

from pcl.core.config import DownloadOptions
from pcl.core.errors import JavaNotFoundError, NetworkError
from pcl.core.logging import get_logger
from pcl.core.paths import IS_MACOS, IS_WINDOWS, AppPaths, current_arch
from pcl.launch.java import JavaRuntime, probe_java
from pcl.net.downloader import DownloadManager, DownloadTask

log = get_logger(__name__)

ADOPTIUM_API = "https://api.adoptium.net/v3/binary/latest/{major}/ga/{os}/{arch}/jre/hotspot/normal/eclipse"
SUPPORTED_MAJORS = (8, 11, 17, 21, 25)


def platform_parts() -> tuple[str, str]:
    """返回 Adoptium 使用的 (os, arch) 标识。"""
    os_name = "windows" if IS_WINDOWS else ("mac" if IS_MACOS else "linux")
    arch = "aarch64" if current_arch() == "arm64" else "x64"
    return os_name, arch


def adoptium_url(major: int) -> str:
    """构造某个大版本的下载地址。"""
    os_name, arch = platform_parts()
    return ADOPTIUM_API.format(major=major, os=os_name, arch=arch)


def java_root(paths: AppPaths) -> Path:
    return paths.root / "java"


def installed_runtime(paths: AppPaths, major: int) -> JavaRuntime | None:
    """检查该版本是否已安装并可用。"""
    home = java_root(paths) / f"jre-{major}"
    for candidate in _candidate_executables(home):
        runtime = probe_java(candidate)
        if runtime is not None:
            return runtime
    return None


def _candidate_executables(home: Path) -> list[Path]:
    exe = "java.exe" if IS_WINDOWS else "java"
    return [
        home / "bin" / exe,
        home / "Contents" / "Home" / "bin" / exe,
    ]


def _safe_extract(archive: Path, target: Path) -> None:
    """解压 zip / tar.gz，阻止路径穿越。"""
    target.mkdir(parents=True, exist_ok=True)
    if archive.suffix.lower() == ".zip":
        with zipfile.ZipFile(archive) as bundle:
            for info in bundle.infolist():
                name = info.filename.replace("\\", "/")
                if name.startswith("/") or ".." in name.split("/"):
                    raise JavaNotFoundError("Java 压缩包包含非法路径。")
            bundle.extractall(target)
        return

    with tarfile.open(archive, "r:gz") as bundle:
        try:
            bundle.extractall(target, filter="data")  # Python 3.12+
        except TypeError:  # 旧版本 Python
            for member in bundle.getmembers():
                if member.name.startswith("/") or ".." in member.name.split("/"):
                    raise JavaNotFoundError("Java 压缩包包含非法路径。")
            bundle.extractall(target)


def _finalize(work: Path, target: Path) -> JavaRuntime:
    found: Path | None = None
    for candidate in work.rglob("bin/java*"):
        if candidate.is_file() and candidate.name in ("java", "java.exe"):
            found = candidate
            break
    if found is None:
        raise JavaNotFoundError("下载的 Java 包中没有找到可执行文件。")

    home = found.parent.parent
    if (home / "Contents" / "Home" / "bin").is_dir():
        home = home / "Contents" / "Home"

    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    shutil.move(str(home), str(target))
    shutil.rmtree(work, ignore_errors=True)

    for candidate in _candidate_executables(target):
        runtime = probe_java(candidate)
        if runtime is not None:
            log.info("Java %s 安装完成：%s", runtime.major, candidate)
            return runtime
    raise JavaNotFoundError("Java 安装后无法运行，可能缺少系统依赖。")


def install_java(
    paths: AppPaths,
    major: int,
    options: DownloadOptions | None = None,
    *,
    progress: Callable[[float], None] | None = None,
) -> JavaRuntime:
    """下载并安装指定大版本的 Java，返回可用运行时。"""
    existing = installed_runtime(paths, major)
    if existing is not None:
        return existing

    os_name, arch = platform_parts()
    extension = ".zip" if IS_WINDOWS else ".tar.gz"
    archive = paths.cache / "java" / f"temurin-{major}-{os_name}-{arch}{extension}"
    log.info("开始下载 Java %s：%s", major, adoptium_url(major))

    task = DownloadTask(
        url=adoptium_url(major),
        dest=archive,
        priority=20,
        name=f"Java {major}",
        resume=False,
    )
    summary = DownloadManager(options).run([task])
    if not summary.ok:
        detail = summary.failures[0].error if summary.failures else "下载失败"
        raise NetworkError(f"下载 Java {major} 失败。", detail=detail)

    if progress is not None:
        progress(90.0)
    work = paths.cache / "java" / f"extract-{major}"
    shutil.rmtree(work, ignore_errors=True)
    try:
        _safe_extract(archive, work)
    except (OSError, tarfile.TarError, zipfile.BadZipFile) as error:
        raise JavaNotFoundError("解压 Java 失败。", detail=str(error)) from error

    runtime = _finalize(work, java_root(paths) / f"jre-{major}")
    if progress is not None:
        progress(100.0)
    return runtime
