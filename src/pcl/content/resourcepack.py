"""资源包与光影包识别。"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from pcl.core.logging import get_logger
from pcl.content.mods import DISABLED_SUFFIX

log = get_logger(__name__)

MAX_ICON_BYTES = 2 * 1024 * 1024
SHADER_PROBES = ("shaders/", "shaders.properties", "shaderpack.properties")
SHADER_SUFFIXES = (".fsh", ".vsh", ".glsl", ".csh", ".gsh")


@dataclass
class PackInfo:
    """资源包 / 光影包元数据。"""

    path: Path
    kind: str = "resourcepack"  # resourcepack / shader
    name: str = ""
    description: str = ""
    pack_format: int = 0
    enabled: bool = True
    icon: bytes | None = None
    size: int = 0
    error: str | None = None

    @property
    def file_name(self) -> str:
        name = self.path.name
        return name[: -len(DISABLED_SUFFIX)] if name.endswith(DISABLED_SUFFIX) else name

    @property
    def display_name(self) -> str:
        return self.name or Path(self.file_name).stem

    @property
    def format_label(self) -> str:
        return f"格式 {self.pack_format}" if self.pack_format else ""


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        if "text" in value:
            return str(value["text"])
        if "translate" in value:
            return str(value["translate"])
        # 1.20.3+ 支持 ["", {"text": ...}] 形式
    if isinstance(value, list):
        parts = [_as_text(item) for item in value]
        return "".join(part for part in parts if part)
    return ""


def is_shader_pack(path: Path) -> bool:
    """判断是否为光影包（OptiFine / Iris 结构）。"""
    if path.is_dir():
        if (path / "shaders").is_dir():
            return True
        return any(child.suffix.lower() in SHADER_SUFFIXES for child in path.rglob("*") if child.is_file())
    if path.suffix.lower() not in (".zip", ".disabled"):
        return False
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    except (OSError, zipfile.BadZipFile):
        return False
    return any(name.startswith(SHADER_PROBES) for name in names)


def parse_pack(path: Path, kind: str = "resourcepack") -> PackInfo:
    """解析资源包或光影包。"""
    enabled = not path.name.endswith(DISABLED_SUFFIX)
    info = PackInfo(path=path, kind=kind, enabled=enabled, name=Path(info_stem(path)).name)
    try:
        info.size = path.stat().st_size if path.is_file() else 0
    except OSError:
        info.size = 0

    try:
        if path.is_dir():
            meta_path = path / "pack.mcmeta"
            if meta_path.is_file():
                _apply_mcmeta(info, meta_path.read_bytes())
            icon_path = path / "pack.png"
            if icon_path.is_file() and icon_path.stat().st_size <= MAX_ICON_BYTES:
                info.icon = icon_path.read_bytes()
        elif path.is_file() and path.suffix.lower() in (".zip", ".disabled"):
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
                if "pack.mcmeta" in names:
                    _apply_mcmeta(info, archive.read("pack.mcmeta"))
                if "pack.png" in names:
                    entry = archive.getinfo("pack.png")
                    if entry.file_size <= MAX_ICON_BYTES:
                        info.icon = archive.read(entry)
    except (OSError, zipfile.BadZipFile, ValueError) as error:
        info.error = str(error)
    return info


def _apply_mcmeta(info: PackInfo, raw: bytes) -> None:
    try:
        data = json.loads(raw.decode("utf-8-sig", errors="replace"))
    except ValueError:
        info.error = "pack.mcmeta 解析失败"
        return
    if not isinstance(data, Mapping):
        return
    pack = data.get("pack")
    if isinstance(pack, Mapping):
        info.pack_format = int(pack.get("pack_format", 0) or 0)
        info.description = _as_text(pack.get("description"))
    if not info.description:
        info.description = _as_text(data.get("description"))


def info_stem(path: Path) -> str:
    """返回去掉 ``.disabled`` 的文件名（用于展示）。"""
    name = path.name
    if name.endswith(DISABLED_SUFFIX):
        name = name[: -len(DISABLED_SUFFIX)]
    return name


def scan_packs(directory: Path, kind: str = "resourcepack") -> list[PackInfo]:
    """扫描资源包 / 光影目录。"""
    if not directory.is_dir():
        return []
    result: list[PackInfo] = []
    for path in sorted(directory.iterdir()):
        if path.is_dir() or path.suffix.lower() in (".zip", ".disabled"):
            result.append(parse_pack(path, kind=kind))
    result.sort(key=lambda item: item.display_name.lower())
    return result
