"""Java 运行时探测：扫描常见安装位置、解析版本、按主版本挑选。

所有平台差异（安装目录、可执行名）集中在此，业务层只拿到 :class:`JavaRuntime`。
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from pcl.core.logging import get_logger
from pcl.core.paths import IS_LINUX, IS_MACOS, IS_WINDOWS, java_executable

log = get_logger(__name__)

_VERSION_RE = re.compile(r'version\s+"([^"]+)"', re.IGNORECASE)
_BIT_RE = re.compile(r"(\d+)-Bit", re.IGNORECASE)


@dataclass(frozen=True)
class JavaRuntime:
    """一个可用的 Java 运行时。"""

    path: Path
    version: str = ""
    major: int = 0
    vendor: str = ""
    arch: str = ""

    @property
    def label(self) -> str:
        vendor = f" {self.vendor}" if self.vendor else ""
        version = self.version or "未知版本"
        arch = f" {self.arch}" if self.arch else ""
        return f"Java {version}{vendor}{arch}"

    def to_dict(self) -> dict[str, str]:
        return {"path": str(self.path), "version": self.version, "major": str(self.major), "vendor": self.vendor}


def _candidate_java_paths() -> Iterable[Path]:
    """产出可能存在的 java 可执行文件路径。"""
    exe = "java.exe" if IS_WINDOWS else "java"

    env_home = os.environ.get("JAVA_HOME")
    if env_home:
        yield Path(env_home) / "bin" / exe

    for entry in (os.environ.get("PATH") or "").split(os.pathsep):
        if entry:
            yield Path(entry) / exe

    roots: list[Path] = []
    if IS_LINUX:
        roots += [Path("/usr/lib/jvm"), Path("/usr/lib64/jvm"), Path("/opt/java"), Path("/opt/jdk")]
        roots += [Path.home() / ".sdkman" / "candidates" / "java", Path.home() / ".jdks", Path.home() / ".local" / "share" / "jdks"]
    elif IS_MACOS:
        roots += [Path("/Library/Java/JavaVirtualMachines"), Path.home() / "Library" / "Java" / "JavaVirtualMachines"]
    elif IS_WINDOWS:
        for base in (
            os.environ.get("ProgramFiles") or r"C:\Program Files",
            os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)",
            os.environ.get("LOCALAPPDATA") or "",
        ):
            if not base:
                continue
            roots += [
                Path(base) / "Java",
                Path(base) / "Eclipse Adoptium",
                Path(base) / "AdoptOpenJDK",
                Path(base) / "Zulu",
                Path(base) / "Microsoft" / "jdk",
                Path(base) / "Amazon Corretto",
                Path(base) / "BellSoft" / "LibericaJDK",
            ]

    for root in roots:
        if not root.is_dir():
            continue
        try:
            children = sorted(root.iterdir())
        except OSError:
            continue
        for child in children:
            if IS_MACOS and child.suffix == ".jdk":
                yield child / "Contents" / "Home" / "bin" / exe
            else:
                yield child / "bin" / exe
                yield child / "Contents" / "Home" / "bin" / exe


def probe_java(path: Path) -> JavaRuntime | None:
    """执行 ``java -version`` 并解析结果。"""
    if not path.is_file():
        return None
    try:
        proc = subprocess.run(
            [str(path), "-version"],
            capture_output=True,
            timeout=8,
            text=True,
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return None

    output = f"{proc.stderr}\n{proc.stdout}"
    match = _VERSION_RE.search(output)
    if not match:
        return None
    version = match.group(1)
    major = parse_major(version)
    vendor = ""
    for name in ("Temurin", "Corretto", "Zulu", "Liberica", "GraalVM", "OpenJ9", "Microsoft", "Oracle"):
        if name.lower() in output.lower():
            vendor = name
            break
    bits = _BIT_RE.search(output)
    arch = f"{bits.group(1)}-bit" if bits else ("64-bit" if "64" in output else "")
    return JavaRuntime(path=path, version=version, major=major, vendor=vendor, arch=arch)


def parse_major(version: str) -> int:
    """从版本号提取主版本：``1.8.0_392`` → 8，``17.0.9`` → 17。"""
    parts = version.split(".")
    if not parts or not parts[0].isdigit():
        return 0
    if parts[0] == "1" and len(parts) > 1 and parts[1].isdigit():
        return int(parts[1])
    return int(parts[0])


def scan_java(extra_paths: Iterable[str | Path] = ()) -> list[JavaRuntime]:
    """扫描本机 Java，按主版本从新到旧返回（已去重）。"""
    seen: set[str] = set()
    runtimes: list[JavaRuntime] = []
    candidates = [Path(item) for item in extra_paths]
    candidates.extend(_candidate_java_paths())
    for candidate in candidates:
        try:
            resolved = str(candidate.resolve())
        except OSError:
            resolved = str(candidate)
        if resolved in seen:
            continue
        seen.add(resolved)
        runtime = probe_java(candidate)
        if runtime is not None:
            runtimes.append(runtime)
    runtimes.sort(key=lambda item: (item.major, item.version), reverse=True)
    log.debug("扫描到 %d 个 Java 运行时", len(runtimes))
    return runtimes


def select_java(
    runtimes: Iterable[JavaRuntime],
    required_major: int | None = None,
    preferred: str | None = None,
) -> JavaRuntime | None:
    """按推荐主版本挑选 Java：优先完全匹配，其次选择不低于要求的最低版本。"""
    if preferred:
        path = Path(preferred)
        resolved = probe_java(path)
        if resolved is not None and (required_major is None or resolved.major >= required_major):
            return resolved

    items = list(runtimes)
    if not items:
        return None
    if required_major is None:
        return items[0]

    exact = [item for item in items if item.major == required_major]
    if exact:
        return exact[0]
    higher = sorted((item for item in items if item.major > required_major), key=lambda item: item.major)
    if higher:
        return higher[0]
    # 没有满足要求的运行时：返回 None，由上层提示安装，避免用低版本硬启动
    return None


def find_java(required_major: int | None = None, preferred: str | None = None) -> JavaRuntime | None:
    """便捷函数：用户指定路径满足要求时优先，否则扫描本机挑选。"""
    if preferred:
        runtime = probe_java(Path(preferred))
        if runtime is not None and (required_major is None or runtime.major >= required_major):
            return runtime
    return select_java(scan_java(), required_major=required_major)


def java_executable_name() -> str:
    """当前平台的无窗口 Java 可执行名。"""
    return java_executable()
