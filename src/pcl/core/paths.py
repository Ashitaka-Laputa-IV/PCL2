"""跨平台路径解析与平台差异抽象。

业务层禁止自行拼接分隔符、classpath 分隔符或 natives 分类器，
统一使用本模块暴露的常量与函数，保证 Windows / Linux / macOS 行为一致。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

IS_WINDOWS: Final[bool] = sys.platform.startswith("win")
IS_MACOS: Final[bool] = sys.platform == "darwin"
IS_LINUX: Final[bool] = sys.platform.startswith("linux")

#: 数据根目录可通过环境变量覆盖，便于测试与便携部署
ENV_DATA_ROOT: Final[str] = "PCL2_HOME"

#: 版本 JSON 中 os.name 字段的取值
OS_NAME: Final[str] = "windows" if IS_WINDOWS else "osx" if IS_MACOS else "linux"


def current_os_name() -> str:
    """返回 Mojang 版本规则中使用的操作系统名。"""
    return OS_NAME


def current_os_version() -> str:
    """返回操作系统版本号（尽力而为，取不到返回空串）。"""
    try:
        import platform

        return platform.version()
    except Exception:
        return ""


def current_arch() -> str:
    """返回 ``x86`` / ``x64`` / ``arm64`` 三种架构标识之一。"""
    machine = os.environ.get("PROCESSOR_ARCHITECTURE", "")
    if hasattr(os, "uname"):
        machine = os.uname().machine
    machine = machine.lower()
    if machine in ("x86_64", "amd64", "x64"):
        return "x64"
    if machine in ("aarch64", "arm64"):
        return "arm64"
    if machine in ("i386", "i686", "x86"):
        return "x86"
    return "x64" if sys.maxsize > 2**32 else "x86"


def classpath_separator() -> str:
    """classpath 分隔符：Windows 为 ``;``，其余平台为 ``:``。"""
    return ";" if IS_WINDOWS else ":"


def natives_classifier(os_name: str | None = None) -> str:
    """返回 natives 库分类器名，如 ``natives-linux``。"""
    return f"natives-{os_name or current_os_name()}"


def java_executable() -> str:
    """无控制台 Java 可执行名：Windows 为 ``javaw.exe``。"""
    return "javaw.exe" if IS_WINDOWS else "java"


def default_data_root() -> Path:
    """返回默认数据根目录。

    Linux: ``$XDG_DATA_HOME/pcl2`` 或 ``~/.local/share/pcl2``
    macOS: ``~/Library/Application Support/pcl2``
    Windows: ``%APPDATA%\\pcl2``
    """
    override = os.environ.get(ENV_DATA_ROOT)
    if override:
        return Path(override).expanduser()
    if IS_WINDOWS:
        base = os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")
        return Path(base) / "pcl2"
    if IS_MACOS:
        return Path.home() / "Library" / "Application Support" / "pcl2"
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else (Path.home() / ".local" / "share")
    return base / "pcl2"


@dataclass(frozen=True)
class AppPaths:
    """启动器使用的全部目录。

    ``root`` 为数据根目录；``game_root`` 是共享游戏文件根目录（versions / libraries / assets），
    未指定时落在数据根目录下的 ``.minecraft``。
    """

    root: Path
    game_root: Path | None = None

    # ---------- 共享游戏文件 ----------
    @property
    def game(self) -> Path:
        return self.game_root if self.game_root else (self.root / ".minecraft")

    @property
    def versions(self) -> Path:
        return self.game / "versions"

    @property
    def libraries(self) -> Path:
        return self.game / "libraries"

    @property
    def assets(self) -> Path:
        return self.game / "assets"

    @property
    def asset_indexes(self) -> Path:
        return self.assets / "indexes"

    @property
    def asset_objects(self) -> Path:
        return self.assets / "objects"

    @property
    def asset_virtual(self) -> Path:
        return self.assets / "virtual"

    # ---------- 启动器自身数据 ----------
    @property
    def instances(self) -> Path:
        """旧版「实例」目录，仅用于迁移到版本条目，不再写入。"""
        return self.root / "instances"

    @property
    def cache(self) -> Path:
        return self.root / "cache"

    @property
    def config_file(self) -> Path:
        return self.root / "config.json"

    @property
    def accounts_file(self) -> Path:
        return self.root / "accounts.json"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def launcher_log(self) -> Path:
        return self.logs / "launcher.log"

    @property
    def game_log(self) -> Path:
        return self.logs / "latest.log"

    @property
    def native_cache(self) -> Path:
        return self.cache / "natives"

    def version_dir(self, version_id: str) -> Path:
        """返回某个版本的目录 ``versions/<id>``。"""
        return self.versions / version_id

    def version_json(self, version_id: str) -> Path:
        """返回某个版本 JSON 的路径。"""
        return self.version_dir(version_id) / f"{version_id}.json"

    def version_jar(self, version_id: str) -> Path:
        """返回某个版本客户端 jar 的路径。"""
        return self.version_dir(version_id) / f"{version_id}.jar"

    def version_meta(self, version_id: str) -> Path:
        """返回版本附加设置（``pcl.json``）的路径。"""
        return self.version_dir(version_id) / "pcl.json"

    def ensure(self) -> AppPaths:
        """创建所有必需的目录。"""
        for directory in (
            self.root,
            self.game,
            self.versions,
            self.libraries,
            self.assets,
            self.asset_indexes,
            self.asset_objects,
            self.cache,
            self.logs,
            self.native_cache,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        return self


_paths: AppPaths | None = None


def set_paths(paths: AppPaths) -> AppPaths:
    """设置全局路径对象（通常由应用装配阶段调用）。"""
    global _paths
    _paths = paths
    return paths


def get_paths() -> AppPaths:
    """获取全局路径对象，未设置时按默认规则创建但不落盘。"""
    global _paths
    if _paths is None:
        _paths = AppPaths(default_data_root())
    return _paths


def reset_paths() -> None:
    """清空全局路径缓存（测试用）。"""
    global _paths
    _paths = None
