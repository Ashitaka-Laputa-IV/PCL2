"""Mod 元数据解析：Fabric / Forge / NeoForge / 旧版 Forge 四种来源。"""

from __future__ import annotations

import json
import tomllib
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from pcl.core.logging import get_logger

log = get_logger(__name__)

DISABLED_SUFFIX = ".disabled"
MAX_ICON_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class ModDependency:
    """依赖声明。"""

    mod_id: str
    version_range: str = ""
    mandatory: bool = True
    ordering: str = ""

    @property
    def label(self) -> str:
        if self.version_range:
            return f"{self.mod_id} {self.version_range}"
        return self.mod_id


@dataclass
class ModInfo:
    """一个 Mod 文件的元数据。"""

    path: Path
    enabled: bool = True
    name: str = ""
    version: str = ""
    mod_id: str = ""
    loader: str = "unknown"
    description: str = ""
    authors: list[str] = field(default_factory=list)
    icon: bytes | None = None
    size: int = 0
    mc_versions: list[str] = field(default_factory=list)
    dependencies: list[ModDependency] = field(default_factory=list)
    error: str | None = None

    @property
    def file_name(self) -> str:
        name = self.path.name
        return name[: -len(DISABLED_SUFFIX)] if name.endswith(DISABLED_SUFFIX) else name

    @property
    def display_name(self) -> str:
        return self.name or Path(self.file_name).stem

    @property
    def version_label(self) -> str:
        return self.version or "未知版本"


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _read_icon(archive: zipfile.ZipFile, name: str) -> bytes | None:
    if not name:
        return None
    normalized = name.lstrip("/")
    try:
        info = archive.getinfo(normalized)
    except KeyError:
        return None
    if info.file_size > MAX_ICON_BYTES:
        return None
    try:
        return archive.read(info)
    except (KeyError, OSError, RuntimeError):
        return None


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        if "text" in value:
            return str(value["text"])
        if "translate" in value:
            return str(value["translate"])
    return ""


def _as_authors(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            if isinstance(item, str):
                result.append(item)
            elif isinstance(item, Mapping) and item.get("name"):
                result.append(str(item["name"]))
        return result
    return []


def _parse_fabric(raw: bytes) -> dict[str, Any] | None:
    try:
        data = json.loads(_decode(raw))
    except ValueError:
        return None
    if not isinstance(data, dict) or not data.get("id"):
        return None

    dependencies: list[ModDependency] = []
    depends = data.get("depends")
    if isinstance(depends, Mapping):
        for mod_id, version in depends.items():
            dependencies.append(
                ModDependency(mod_id=str(mod_id), version_range=str(version or ""), mandatory=True)
            )
    breaks = data.get("breaks")
    if isinstance(breaks, Mapping):
        for mod_id, version in breaks.items():
            dependencies.append(
                ModDependency(mod_id=str(mod_id), version_range=str(version or ""), mandatory=True, ordering="breaks")
            )

    icon_spec = data.get("icon")
    icon_name = ""
    if isinstance(icon_spec, str):
        icon_name = icon_spec
    elif isinstance(icon_spec, Mapping):
        icon_name = str(icon_spec.get("64") or icon_spec.get("128") or next(iter(icon_spec.values()), "") or "")

    return {
        "loader": "fabric",
        "mod_id": str(data.get("id", "")),
        "name": str(data.get("name") or data.get("id") or ""),
        "version": str(data.get("version", "")),
        "description": _as_text(data.get("description")),
        "authors": _as_authors(data.get("authors") or data.get("contributors")),
        "icon_name": icon_name,
        "dependencies": dependencies,
        "mc_versions": [],
    }


def _parse_forge_toml(raw: bytes) -> dict[str, Any] | None:
    try:
        data = tomllib.loads(_decode(raw))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        return None

    mods = data.get("mods")
    if not isinstance(mods, list) or not mods:
        return None
    primary = mods[0] if isinstance(mods[0], Mapping) else {}
    if not primary:
        return None

    mod_id = str(primary.get("modId", ""))
    if not mod_id:
        return None

    dependencies: list[ModDependency] = []
    depends = data.get("dependencies")
    if isinstance(depends, Mapping):
        for key, entries in depends.items():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, Mapping):
                    continue
                target = str(entry.get("modId", key))
                if target in ("minecraft", "forge", "neoforge", "fml"):
                    continue
                dependencies.append(
                    ModDependency(
                        mod_id=target,
                        version_range=str(entry.get("versionRange", "") or ""),
                        mandatory=bool(entry.get("mandatory", True)),
                        ordering=str(entry.get("ordering", "") or ""),
                    )
                )

    authors = primary.get("authors")
    if isinstance(authors, str):
        author_list = [authors]
    elif isinstance(authors, list):
        author_list = [str(item) for item in authors]
    else:
        author_list = []

    return {
        "loader": "neoforge" if "neoforge" in data else "forge",
        "mod_id": mod_id,
        "name": str(primary.get("displayName") or mod_id),
        "version": str(primary.get("version", "")),
        "description": str(primary.get("description", "") or ""),
        "authors": author_list,
        "icon_name": str(primary.get("logoFile", "") or ""),
        "dependencies": dependencies,
        "mc_versions": [],
    }


def _parse_mcmod_info(raw: bytes) -> dict[str, Any] | None:
    try:
        data = json.loads(_decode(raw))
    except ValueError:
        return None
    if isinstance(data, Mapping):
        data = data.get("modList") or data.get("modlist") or []
    if not isinstance(data, list) or not data:
        return None
    primary = data[0] if isinstance(data[0], Mapping) else None
    if not primary or not primary.get("modid"):
        return None
    return {
        "loader": "forge",
        "mod_id": str(primary.get("modid", "")),
        "name": str(primary.get("name") or primary.get("modid") or ""),
        "version": str(primary.get("version", "")),
        "description": str(primary.get("description", "") or ""),
        "authors": _as_authors(primary.get("authorList") or primary.get("author")),
        "icon_name": str(primary.get("logoFile", "") or ""),
        "dependencies": [],
        "mc_versions": [str(item) for item in (primary.get("mcversion") or [])]
        if isinstance(primary.get("mcversion"), list)
        else ([str(primary["mcversion"])] if primary.get("mcversion") else []),
    }


def parse_mod(path: Path) -> ModInfo:
    """解析 Mod 文件；无法识别的 jar 仍返回基础信息。"""
    enabled = not path.name.endswith(DISABLED_SUFFIX)
    info = ModInfo(path=path, enabled=enabled)
    try:
        info.size = path.stat().st_size
    except OSError:
        info.size = 0

    if not path.is_file():
        info.error = "文件不存在"
        return info

    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as error:
        info.error = f"不是有效的 jar：{error}"
        info.name = Path(info.file_name).stem
        return info

    with archive:
        names = set(archive.namelist())
        candidates: list[tuple[str, Any]] = [
            ("fabric.mod.json", _parse_fabric),
            ("META-INF/neoforge.mods.toml", _parse_forge_toml),
            ("META-INF/mods.toml", _parse_forge_toml),
            ("mcmod.info", _parse_mcmod_info),
        ]
        parsed: dict[str, Any] | None = None
        for name, parser in candidates:
            if name not in names:
                continue
            try:
                parsed = parser(archive.read(name))
            except (KeyError, OSError, RuntimeError):
                parsed = None
            if parsed:
                break

        if parsed is None:
            info.error = "未找到 Mod 元数据"
            info.name = Path(info.file_name).stem
            info.loader = "unknown"
            return info

        info.loader = str(parsed.get("loader", "unknown"))
        info.mod_id = str(parsed.get("mod_id", ""))
        info.name = str(parsed.get("name", "")) or Path(info.file_name).stem
        info.version = str(parsed.get("version", ""))
        info.description = str(parsed.get("description", ""))
        info.authors = list(parsed.get("authors") or [])
        info.mc_versions = list(parsed.get("mc_versions") or [])
        info.dependencies = list(parsed.get("dependencies") or [])
        info.icon = _read_icon(archive, str(parsed.get("icon_name", "")))
    return info


def scan_mods(directory: Path) -> list[ModInfo]:
    """扫描 mods 目录（含已禁用文件）。"""
    if not directory.is_dir():
        return []
    result: list[ModInfo] = []
    for path in sorted(directory.iterdir()):
        if path.is_dir():
            continue
        if path.suffix.lower() not in (".jar", ".disabled"):
            continue
        result.append(parse_mod(path))
    result.sort(key=lambda item: item.display_name.lower())
    return result
