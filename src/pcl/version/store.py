"""已安装版本仓库：扫描 ``versions/`` 并维护每个版本的 ``pcl.json``。

启动器不再有"实例"这一层：``versions/`` 下每个完整版本就是一个可直接启动的条目，
附加设置（Java、内存、图标等）写在版本目录内的 ``pcl.json`` 里，
删除版本时随目录一起消失。
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import time
from pathlib import Path
from typing import Iterable

from pcl.core.errors import VersionError
from pcl.core.logging import get_logger
from pcl.core.paths import AppPaths
from pcl.version.local import LocalVersion, default_name

log = get_logger(__name__)

META_NAME = "pcl.json"


class VersionStore:
    """版本仓库：以 ``versions/<id>/<id>.json`` 判定已安装，以 ``pcl.json`` 存设置。"""

    def __init__(self, paths: AppPaths) -> None:
        self.paths = paths

    # ---------- 查询 ----------
    def meta_path(self, version_id: str) -> Path:
        return self.paths.version_dir(version_id) / META_NAME

    def installed_ids(self) -> list[str]:
        """返回完整安装的版本 id（有 JSON，且本体存在或声明了继承）。"""
        result: list[str] = []
        if not self.paths.versions.is_dir():
            return result
        for directory in sorted(self.paths.versions.iterdir()):
            if not directory.is_dir():
                continue
            document = self._read_version_json(directory.name)
            if document is None:
                continue
            if not (directory / f"{directory.name}.jar").is_file() and not document.get("inheritsFrom"):
                continue
            result.append(directory.name)
        return result

    def list_versions(self) -> list[LocalVersion]:
        """返回全部版本（按最近游玩排序），缺失的元数据会自动补齐。"""
        result: list[LocalVersion] = []
        taken: set[str] = set()
        pending: list[str] = []
        for version_id in self.installed_ids():
            meta = self._read_meta(version_id)
            if meta is None:
                pending.append(version_id)
                continue
            meta.id = version_id
            if not meta.name:
                meta.name = self._unique_name(default_name(meta.mc_version, meta.loader, meta.loader_version), taken)
                self.save(meta)
            taken.add(meta.name)
            result.append(meta)

        for version_id in pending:
            mc_version, loader, loader_version = self.describe(version_id)
            version = LocalVersion(
                id=version_id,
                name=self._unique_name(default_name(mc_version, loader, loader_version), taken),
                mc_version=mc_version,
                loader=loader,
                loader_version=loader_version,
            )
            self.save(version)
            taken.add(version.name)
            result.append(version)

        if pending:
            log.info("已登记 %d 个新版本", len(pending))
        # 玩过的按最近时间排前面，其余按名称排，避免同一秒创建时顺序乱跳
        played = sorted((item for item in result if item.last_played_at), key=lambda item: item.last_played_at, reverse=True)
        rest = sorted((item for item in result if not item.last_played_at), key=lambda item: item.name)
        return played + rest

    def get(self, version_id: str) -> LocalVersion:
        """按 id（或展示名）查找版本。"""
        versions = self.list_versions()
        for version in versions:
            if version.id == version_id:
                return version
        lowered = (version_id or "").lower()
        for version in versions:
            if version.name.lower() == lowered:
                return version
        raise VersionError(f"找不到版本：{version_id}")

    def exists(self, version_id: str) -> bool:
        return self.meta_path(version_id).is_file()

    def unique_name(self, base: str) -> str:
        return self._unique_name(base, {item.name for item in self.list_versions()})

    # ---------- 写入 ----------
    def save(self, version: LocalVersion) -> LocalVersion:
        path = self.meta_path(version.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(version.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)
        return version

    def ensure(self, version_id: str, *, name: str = "") -> LocalVersion:
        """确保某个已安装版本有条目；已存在则原样返回。"""
        # 先扫描一次：自动补登记时已经写好了 pcl.json，避免重复命名成 -1
        for item in self.list_versions():
            if item.id == version_id:
                return item
        mc_version, loader, loader_version = self.describe(version_id)
        version = LocalVersion(
            id=version_id,
            name=self.unique_name(name or default_name(mc_version, loader, loader_version)),
            mc_version=mc_version,
            loader=loader,
            loader_version=loader_version,
        )
        return self.save(version)

    def adopt(self, version_id: str) -> LocalVersion:
        """安装完成后登记版本；若该版本已有条目，则派生一个 ``-1`` 副本。"""
        if self.exists(version_id):
            return self.duplicate(version_id)
        return self.ensure(version_id)

    def duplicate(self, version_id: str, *, name: str = "") -> LocalVersion:
        """把版本派生为一个新条目（通过 ``inheritsFrom`` 共享游戏文件）。"""
        source = self.get(version_id)
        new_id = self._unique_version_id(version_id)
        # 先算好名字：写入 stub 后它也会被扫描到，名字会再多加一层后缀
        new_name = self._unique_name(name or source.name, {item.name for item in self.list_versions()})
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
        document = {"id": new_id, "inheritsFrom": version_id, "type": "release", "time": stamp, "releaseTime": stamp}
        from pcl.version.merge import save_version_json

        save_version_json(self.paths, new_id, document)
        clone = dataclasses.replace(
            source,
            id=new_id,
            name=new_name,
            created_at=time.time(),
            last_played_at=0.0,
            play_count=0,
        )
        self.save(clone)
        log.info("已派生版本 %s -> %s", source.id, new_id)
        return clone

    def rename(self, version_id: str, new_name: str) -> LocalVersion:
        version = self.get(version_id)
        cleaned = (new_name or "").strip()
        if not cleaned:
            raise VersionError("版本名称不能为空。")
        taken = {item.name for item in self.list_versions() if item.id != version.id}
        version.name = self._unique_name(cleaned, taken)
        return self.save(version)

    def update(self, version: LocalVersion) -> LocalVersion:
        if not self.exists(version.id):
            raise VersionError(f"版本不存在：{version.id}")
        return self.save(version)

    def touch(self, version_id: str) -> LocalVersion:
        version = self.get(version_id)
        version.touch_played()
        return self.save(version)

    def delete(self, version_id: str, *, remove_files: bool = True) -> None:
        """删除版本；默认连游戏文件一起删除。"""
        version = self.get(version_id)
        directory = self.paths.version_dir(version.id)
        if remove_files:
            try:
                shutil.rmtree(directory)
            except OSError as error:
                raise VersionError("删除版本文件失败。", detail=str(error)) from error
        else:
            self.meta_path(version.id).unlink(missing_ok=True)
        log.info("已删除版本 %s", version.name)

    # ---------- 版本描述 ----------
    def describe(self, version_id: str) -> tuple[str, str, str]:
        """从版本 JSON 的继承链推断 MC 版本与加载器。"""
        from pcl.version.merge import version_chain

        chain = version_chain(self.paths, version_id)
        if not chain:
            return version_id, "vanilla", ""
        mc_version = chain[-1]
        for candidate in chain:
            document = self._read_version_json(candidate) or {}
            loader, loader_version = self._loader_of(candidate, document)
            if loader != "vanilla":
                return mc_version, loader, loader_version
        return mc_version, "vanilla", ""

    @staticmethod
    def _loader_of(version_id: str, document: dict) -> tuple[str, str]:
        lowered = version_id.lower()
        main_class = str(document.get("mainClass") or "").lower()
        parts = version_id.split("-")
        if "fabric" in lowered or "fabric" in main_class:
            return "fabric", (parts[2] if len(parts) > 2 else "")
        if "quilt" in lowered:
            return "quilt", (parts[2] if len(parts) > 2 else "")
        if "neoforge" in lowered:
            return "neoforge", version_id.split("neoforge-")[-1]
        if "forge" in lowered:
            return "forge", version_id.split("forge-")[-1]
        return "vanilla", ""

    def _read_version_json(self, version_id: str) -> dict | None:
        path = self.paths.version_json(version_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _read_meta(self, version_id: str) -> LocalVersion | None:
        path = self.meta_path(version_id)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            log.warning("版本设置损坏 %s: %s", path, error)
            return None
        if not isinstance(data, dict):
            return None
        data.setdefault("id", version_id)
        try:
            return LocalVersion.from_dict(data)
        except (TypeError, ValueError) as error:
            log.warning("版本设置无法解析 %s: %s", path, error)
            return None

    # ---------- 名称 ----------
    @staticmethod
    def _unique_name(base: str, taken: Iterable[str]) -> str:
        used = set(taken)
        name = (base or "").strip() or "未命名版本"
        if name not in used:
            return name
        counter = 1
        while f"{name}-{counter}" in used:
            counter += 1
        return f"{name}-{counter}"

    def _unique_version_id(self, base: str) -> str:
        existing = set(self.installed_ids())
        counter = 1
        candidate = f"{base}-{counter}"
        while candidate in existing or self.paths.version_dir(candidate).exists():
            counter += 1
            candidate = f"{base}-{counter}"
        return candidate

    # ---------- 旧数据迁移 ----------
    def migrate_legacy(self) -> list[LocalVersion]:
        """把旧版 ``instances/*/instance.json`` 的设置并入对应版本。

        旧实例的隔离目录会记录到 ``game_root``，游戏文件与存档不会丢失。
        """
        legacy_root = self.paths.instances
        if not legacy_root.is_dir():
            return []
        migrated: list[LocalVersion] = []
        for directory in sorted(legacy_root.iterdir()):
            meta_file = directory / "instance.json"
            if not meta_file.is_file():
                continue
            try:
                data = json.loads(meta_file.read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                log.warning("旧实例数据损坏 %s: %s", meta_file, error)
                continue
            if not isinstance(data, dict):
                continue
            version_id = str(data.get("version_id") or data.get("mc_version") or "")
            if not version_id or not self.paths.version_json(version_id).is_file():
                continue
            if self.exists(version_id):
                meta_file.rename(meta_file.with_suffix(".json.migrated"))
                continue
            game_root = ""
            if data.get("isolated"):
                game_root = str(directory / ".minecraft")
            elif data.get("game_root"):
                game_root = str(data["game_root"])
            mc_version, loader, loader_version = self.describe(version_id)
            version = LocalVersion(
                id=version_id,
                name=self.unique_name(str(data.get("name") or default_name(mc_version, loader, loader_version))),
                mc_version=mc_version,
                loader=loader,
                loader_version=loader_version,
                game_root=game_root,
                java_path=str(data.get("java_path") or ""),
                memory_mb=int(data.get("memory_mb") or 0),
                window_width=int(data.get("window_width") or 0),
                window_height=int(data.get("window_height") or 0),
                jvm_args=[str(item) for item in data.get("jvm_args") or []],
                game_args=[str(item) for item in data.get("game_args") or []],
                icon_path=str(data.get("icon_path") or ""),
                server=str(data.get("server") or ""),
                notes=str(data.get("notes") or ""),
                created_at=float(data.get("created_at") or time.time()),
                last_played_at=float(data.get("last_played_at") or 0),
                play_count=int(data.get("play_count") or 0),
            )
            self.save(version)
            meta_file.rename(meta_file.with_suffix(".json.migrated"))
            migrated.append(version)

        if migrated:
            log.info("已迁移 %d 个旧实例到版本条目", len(migrated))
        return migrated
