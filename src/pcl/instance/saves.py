"""存档（世界）扫描：读取 ``level.dat`` 中的名称、版本与游玩时间。"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from pcl.core.errors import InstanceError
from pcl.core.logging import get_logger
from pcl.core.nbt import load_nbt_file

log = get_logger(__name__)

MAX_ICON_BYTES = 512 * 1024


@dataclass
class WorldInfo:
    """一个存档目录。"""

    path: Path
    name: str
    folder: str
    game_version: str = ""
    last_played: float = 0.0
    size_bytes: int = 0
    icon: bytes | None = None
    error: str | None = None

    @property
    def last_played_label(self) -> str:
        if not self.last_played:
            return "未知"
        try:
            return datetime.fromtimestamp(self.last_played / 1000).strftime("%Y-%m-%d %H:%M")
        except (OSError, ValueError, OverflowError):
            return "未知"

    @property
    def size_label(self) -> str:
        size = float(self.size_bytes)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
            size /= 1024
        return f"{size:.1f} GB"


def _read_level(path: Path) -> Mapping[str, Any] | None:
    try:
        root = load_nbt_file(path)
    except (OSError, ValueError) as error:
        log.debug("读取 level.dat 失败 %s: %s", path, error)
        return None
    if not isinstance(root, Mapping):
        return None
    data = root.get("Data")
    return data if isinstance(data, Mapping) else None


def _directory_size(path: Path) -> int:
    total = 0
    try:
        for child in path.rglob("*"):
            if child.is_file():
                try:
                    total += child.stat().st_size
                except OSError:
                    continue
    except OSError:
        return total
    return total


def read_world(path: Path) -> WorldInfo:
    """读取单个存档目录的元数据。"""
    folder = path.name
    info = WorldInfo(path=path, name=folder, folder=folder)
    level = path / "level.dat"
    if not level.is_file():
        info.error = "缺少 level.dat"
        return info

    data = _read_level(level)
    if data is not None:
        info.name = str(data.get("LevelName") or folder)
        version = data.get("Version")
        if isinstance(version, Mapping):
            info.game_version = str(version.get("Name") or "")
        info.last_played = float(data.get("LastPlayed") or 0)

    icon = path / "icon.png"
    if icon.is_file():
        try:
            if icon.stat().st_size <= MAX_ICON_BYTES:
                info.icon = icon.read_bytes()
        except OSError:
            pass

    info.size_bytes = _directory_size(path)
    return info


def scan_worlds(game_dir: Path) -> list[WorldInfo]:
    """扫描 ``saves`` 目录下所有世界，按最近游玩排序。"""
    saves = game_dir / "saves"
    if not saves.is_dir():
        return []
    worlds: list[WorldInfo] = []
    for child in saves.iterdir():
        if child.is_dir() and (child / "level.dat").is_file():
            worlds.append(read_world(child))
    worlds.sort(key=lambda item: item.last_played or 0, reverse=True)
    return worlds


def delete_world(world: WorldInfo) -> None:
    """删除存档目录。"""
    try:
        shutil.rmtree(world.path)
    except OSError as error:
        raise InstanceError("删除存档失败。", detail=str(error)) from error


def backup_world(world: WorldInfo, dest: Path) -> Path:
    """把存档目录打包为 zip。"""
    import zipfile

    dest.parent.mkdir(parents=True, exist_ok=True)
    temp = dest.with_suffix(dest.suffix + ".tmp")
    try:
        with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for child in world.path.rglob("*"):
                if child.is_file():
                    archive.write(child, child.relative_to(world.path.parent).as_posix())
        temp.replace(dest)
    except OSError as error:
        temp.unlink(missing_ok=True)
        raise InstanceError("备份存档失败。", detail=str(error)) from error
    return dest
