"""资源管理门面：扫描、启停、拖入安装与删除。"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path
from typing import Iterable

from pcl.content.mods import DISABLED_SUFFIX, ModInfo, scan_mods
from pcl.content.resourcepack import PackInfo, is_shader_pack, parse_pack, scan_packs
from pcl.core.errors import ContentError
from pcl.core.logging import get_logger
from pcl.core.paths import AppPaths
from pcl.instance.model import Instance

log = get_logger(__name__)

KINDS = ("mods", "resourcepacks", "shaderpacks")


class ContentManager:
    """实例内容管理器。"""

    def __init__(self, paths: AppPaths, instance: Instance) -> None:
        self.paths = paths
        self.instance = instance
        self.game_dir = instance.game_dir(paths)

    # ---------- 目录 ----------
    def directory(self, kind: str) -> Path:
        if kind == "mods":
            return self.game_dir / "mods"
        if kind == "resourcepacks":
            return self.game_dir / "resourcepacks"
        if kind == "shaderpacks":
            return self.game_dir / "shaderpacks"
        raise ContentError(f"未知的内容类型：{kind}")

    # ---------- 扫描 ----------
    def mods(self) -> list[ModInfo]:
        return scan_mods(self.directory("mods"))

    def resourcepacks(self) -> list[PackInfo]:
        return scan_packs(self.directory("resourcepacks"), kind="resourcepack")

    def shaderpacks(self) -> list[PackInfo]:
        return scan_packs(self.directory("shaderpacks"), kind="shader")

    # ---------- 启停 ----------
    @staticmethod
    def toggle(path: Path) -> Path:
        """启用/禁用：通过 ``.disabled`` 后缀切换，不移动文件。"""
        if path.name.endswith(DISABLED_SUFFIX):
            target = path.with_name(path.name[: -len(DISABLED_SUFFIX)])
            if target.exists():
                raise ContentError(f"同名文件已存在，无法启用：{target.name}")
            path.rename(target)
            return target
        target = path.with_name(path.name + DISABLED_SUFFIX)
        if target.exists():
            raise ContentError(f"同名文件已存在，无法禁用：{target.name}")
        path.rename(target)
        return target

    @staticmethod
    def set_enabled(path: Path, enabled: bool) -> Path:
        """把文件调整到指定启用状态，已满足时直接返回。"""
        is_enabled = not path.name.endswith(DISABLED_SUFFIX)
        if is_enabled == enabled:
            return path
        return ContentManager.toggle(path)

    # ---------- 删除 ----------
    @staticmethod
    def delete(path: Path) -> None:
        if not path.exists():
            return
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        except OSError as error:
            raise ContentError("删除文件失败。", detail=str(error)) from error

    # ---------- 安装 ----------
    def detect_kind(self, source: Path) -> str:
        """判断待安装文件属于哪一类内容。"""
        if source.is_dir():
            if (source / "pack.mcmeta").is_file():
                return "resourcepacks"
            return "shaderpacks"
        if source.suffix.lower() in (".mrpack", ".zip"):
            if is_shader_pack(source):
                return "shaderpacks"
            if self._zip_has(source, "pack.mcmeta"):
                return "resourcepacks"
            if self._zip_has(source, "modrinth.index.json"):
                raise ContentError("这是整合包，请使用「导入整合包」功能。")
            return "resourcepacks"
        if source.suffix.lower() in (".jar", ".disabled"):
            return "mods"
        raise ContentError(f"无法识别的文件类型：{source.name}")

    @staticmethod
    def _zip_has(path: Path, member: str) -> bool:
        try:
            with zipfile.ZipFile(path) as archive:
                return member in archive.namelist()
        except (OSError, zipfile.BadZipFile):
            return False

    def install_files(self, sources: Iterable[Path], kind: str | None = None) -> list[Path]:
        """把文件/目录复制到对应内容目录，返回最终路径列表。"""
        installed: list[Path] = []
        for source in sources:
            source = Path(source)
            if not source.exists():
                raise ContentError(f"文件不存在：{source}")
            target_kind = kind or self.detect_kind(source)
            target_dir = self.directory(target_kind)
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / source.name
            counter = 1
            while target.exists():
                stem = Path(source.stem) if source.is_file() else None
                if stem is None:
                    target = target_dir / f"{source.name} ({counter})"
                else:
                    suffix = source.suffix
                    target = target_dir / f"{stem} ({counter}){suffix}"
                counter += 1
            try:
                if source.is_dir():
                    shutil.copytree(source, target)
                else:
                    shutil.copy2(source, target)
            except OSError as error:
                raise ContentError("复制文件失败。", detail=str(error)) from error
            installed.append(target)
            log.info("已安装 %s -> %s", source.name, target)
        return installed

    def import_dropped(self, sources: Iterable[Path]) -> list[Path]:
        """拖入安装：自动识别类型。"""
        return self.install_files(sources)
