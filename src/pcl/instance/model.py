"""实例元数据模型。

一个实例 = 一份 ``instance.json`` + 一个游戏目录（默认实例目录下的 ``.minecraft``）。
``version_id`` 指向共享 ``versions/`` 下真正用于启动的版本（可能是加载器版本）。
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pcl import INSTANCE_SCHEMA_VERSION
from pcl.core.paths import AppPaths


def slugify(name: str) -> str:
    """把名称转换为安全的目录名。"""
    cleaned = re.sub(r"[^\w\u4e00-\u9fff.-]+", "-", name.strip(), flags=re.UNICODE).strip("-._")
    if not cleaned:
        cleaned = "instance"
    return cleaned[:40]


@dataclass
class Instance:
    """启动器实例。"""

    id: str
    name: str
    mc_version: str
    version_id: str = ""
    loader: str = "vanilla"
    loader_version: str = ""
    isolated: bool = True
    game_root: str = ""  # 非空表示使用外部游戏目录（导入已有 .minecraft）
    java_path: str = ""
    memory_mb: int = 0  # 0 表示跟随全局设置
    jvm_args: list[str] = field(default_factory=list)
    game_args: list[str] = field(default_factory=list)
    icon: str = ""
    notes: str = ""
    created_at: float = field(default_factory=time.time)
    last_played_at: float = 0.0
    play_count: int = 0
    schema_version: int = INSTANCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.version_id:
            self.version_id = self.mc_version
        if not self.id:
            self.id = f"{slugify(self.name)}-{uuid.uuid4().hex[:6]}"

    # ---------- 路径 ----------
    def game_dir(self, paths: AppPaths) -> Path:
        """返回实例使用的游戏目录。"""
        if self.game_root:
            return Path(self.game_root).expanduser()
        if self.isolated:
            return paths.instance_game_dir(self.id)
        return paths.game

    def data_dir(self, paths: AppPaths) -> Path:
        return paths.instance_dir(self.id)

    @property
    def label(self) -> str:
        if self.loader and self.loader != "vanilla":
            return f"{self.mc_version} · {self.loader.capitalize()}"
        return self.mc_version

    # ---------- 序列化 ----------
    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": INSTANCE_SCHEMA_VERSION,
            "id": self.id,
            "name": self.name,
            "mc_version": self.mc_version,
            "version_id": self.version_id,
            "loader": self.loader,
            "loader_version": self.loader_version,
            "isolated": self.isolated,
            "game_root": self.game_root,
            "java_path": self.java_path,
            "memory_mb": self.memory_mb,
            "jvm_args": list(self.jvm_args),
            "game_args": list(self.game_args),
            "icon": self.icon,
            "notes": self.notes,
            "created_at": self.created_at,
            "last_played_at": self.last_played_at,
            "play_count": self.play_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Instance:
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
            version_id=str(data.get("version_id", "")),
            loader=str(data.get("loader", "vanilla")),
            loader_version=str(data.get("loader_version", "")),
            isolated=bool(data.get("isolated", True)),
            game_root=str(data.get("game_root", "")),
            java_path=str(data.get("java_path", "")),
            memory_mb=_int(data.get("memory_mb", 0)),
            jvm_args=_str_list(data.get("jvm_args")),
            game_args=_str_list(data.get("game_args")),
            icon=str(data.get("icon", "")),
            notes=str(data.get("notes", "")),
            created_at=_float(data.get("created_at", time.time()), time.time()),
            last_played_at=_float(data.get("last_played_at", 0)),
            play_count=_int(data.get("play_count", 0)),
        )

    def touch_played(self) -> None:
        self.last_played_at = time.time()
        self.play_count += 1
