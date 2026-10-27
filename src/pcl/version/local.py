"""本地已安装版本的元数据模型。

一个版本 = ``versions/<id>/`` 目录 + 一份 ``pcl.json``（启动器附加设置）。
版本目录名即版本标识；``name`` 只是展示用的名称，可以重名时自动加 ``-1`` / ``-2``。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pcl import VERSION_SCHEMA_VERSION
from pcl.core.paths import AppPaths

LOADER_LABELS: dict[str, str] = {
    "vanilla": "原版",
    "fabric": "Fabric",
    "quilt": "Quilt",
    "forge": "Forge",
    "neoforge": "NeoForge",
}


def loader_label(kind: str) -> str:
    """返回加载器的展示名。"""
    return LOADER_LABELS.get((kind or "vanilla").lower(), (kind or "").capitalize())


def default_name(mc_version: str, loader: str, loader_version: str = "") -> str:
    """按「游戏版本 + 加载器」生成默认展示名。"""
    kind = (loader or "vanilla").lower()
    if kind == "vanilla":
        return f"{mc_version} 原版"
    suffix = f"{loader_label(kind)} {loader_version}".strip()
    return f"{mc_version} {suffix}"


@dataclass
class LocalVersion:
    """一个已安装、可直接启动的游戏版本。"""

    id: str  # versions/ 下的目录名，唯一
    name: str  # 展示名，列表内唯一
    mc_version: str
    loader: str = "vanilla"
    loader_version: str = ""
    isolated: bool = True  # 版本隔离：存档/Mod/配置放在 versions/<id> 内
    game_root: str = ""  # 非空表示使用外部游戏目录（迁移的旧实例 / 手动指定）
    java_path: str = ""
    memory_mb: int = 0  # 0 表示跟随全局设置
    window_width: int = 0
    window_height: int = 0
    jvm_args: list[str] = field(default_factory=list)
    game_args: list[str] = field(default_factory=list)
    icon_path: str = ""
    server: str = ""  # 启动后自动进入的服务器（ip 或 ip:port）
    notes: str = ""
    created_at: float = field(default_factory=time.time)
    last_played_at: float = 0.0
    play_count: int = 0
    schema_version: int = VERSION_SCHEMA_VERSION

    # ---------- 路径 ----------
    def game_dir(self, paths: AppPaths) -> Path:
        """返回该版本使用的游戏目录。

        默认开启版本隔离：存档、Mod、资源包与配置都放在 ``versions/<id>`` 里，
        不同版本互不影响；``isolated`` 关闭时才回落到共享的 ``.minecraft``。
        """
        if self.game_root:
            return Path(self.game_root).expanduser()
        if self.isolated:
            return paths.version_dir(self.id)
        return paths.game

    @property
    def label(self) -> str:
        if self.loader and self.loader != "vanilla":
            return f"{self.mc_version} · {loader_label(self.loader)}"
        return self.mc_version

    @property
    def loader_text(self) -> str:
        return loader_label(self.loader)

    # ---------- 序列化 ----------
    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": VERSION_SCHEMA_VERSION,
            "id": self.id,
            "name": self.name,
            "mc_version": self.mc_version,
            "loader": self.loader,
            "loader_version": self.loader_version,
            "isolated": self.isolated,
            "game_root": self.game_root,
            "java_path": self.java_path,
            "memory_mb": self.memory_mb,
            "window_width": self.window_width,
            "window_height": self.window_height,
            "jvm_args": list(self.jvm_args),
            "game_args": list(self.game_args),
            "icon_path": self.icon_path,
            "server": self.server,
            "notes": self.notes,
            "created_at": self.created_at,
            "last_played_at": self.last_played_at,
            "play_count": self.play_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LocalVersion:
        def _float(value: Any, default: float = 0.0) -> float:
            try:
                return float(value)
            except (TypeError, ValueError):
                return default

        def _int(value: Any, default: int = 0) -> int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        def _str_list(value: Any) -> list[str]:
            return [str(item) for item in value] if isinstance(value, list) else []

        return cls(
            id=str(data.get("id", "")),
            name=str(data.get("name", "")),
            mc_version=str(data.get("mc_version", "")),
            loader=str(data.get("loader", "vanilla")),
            loader_version=str(data.get("loader_version", "")),
            # 旧条目没有该字段：保持共享目录，避免升级后存档「消失」
            isolated=bool(data.get("isolated", False)),
            game_root=str(data.get("game_root", "")),
            java_path=str(data.get("java_path", "")),
            memory_mb=_int(data.get("memory_mb", 0)),
            window_width=_int(data.get("window_width", 0)),
            window_height=_int(data.get("window_height", 0)),
            jvm_args=_str_list(data.get("jvm_args")),
            game_args=_str_list(data.get("game_args")),
            icon_path=str(data.get("icon_path", "")),
            server=str(data.get("server", "")),
            notes=str(data.get("notes", "")),
            created_at=_float(data.get("created_at", time.time()), time.time()),
            last_played_at=_float(data.get("last_played_at", 0)),
            play_count=_int(data.get("play_count", 0)),
        )

    def touch_played(self) -> None:
        self.last_played_at = time.time()
        self.play_count += 1
