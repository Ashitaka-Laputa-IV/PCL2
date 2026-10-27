"""实例的增删改查、目录隔离与外部 .minecraft 导入。"""

from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Iterable

from pcl.core.errors import InstanceError
from pcl.core.logging import get_logger
from pcl.core.paths import AppPaths
from pcl.instance.model import Instance, slugify

log = get_logger(__name__)


class InstanceManager:
    """实例仓库：以 ``instances/<id>/instance.json`` 为准。"""

    def __init__(self, paths: AppPaths) -> None:
        self.paths = paths

    # ---------- 查询 ----------
    def list_instances(self) -> list[Instance]:
        """读取全部实例，按最近游玩时间排序。"""
        result: list[Instance] = []
        root = self.paths.instances
        if not root.is_dir():
            return result
        for directory in sorted(root.iterdir()):
            meta = directory / "instance.json"
            if not meta.is_file():
                continue
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                log.warning("实例元数据损坏 %s: %s", meta, error)
                continue
            if not isinstance(data, dict):
                continue
            data.setdefault("id", directory.name)
            try:
                result.append(Instance.from_dict(data))
            except (TypeError, ValueError) as error:
                log.warning("实例数据无法解析 %s: %s", meta, error)
        result.sort(key=lambda item: (item.last_played_at, item.created_at), reverse=True)
        return result

    def get(self, instance_id: str) -> Instance:
        """按 id 或名称查找实例。"""
        candidates = self.list_instances()
        for instance in candidates:
            if instance.id == instance_id:
                return instance
        lowered = instance_id.lower()
        for instance in candidates:
            if instance.name.lower() == lowered:
                return instance
        raise InstanceError(f"找不到实例：{instance_id}")

    def exists(self, instance_id: str) -> bool:
        return (self.paths.instance_dir(instance_id) / "instance.json").is_file()

    # ---------- 写入 ----------
    def save(self, instance: Instance) -> Instance:
        directory = self.paths.instance_dir(instance.id)
        directory.mkdir(parents=True, exist_ok=True)
        meta = directory / "instance.json"
        temp = meta.with_suffix(".json.tmp")
        temp.write_text(json.dumps(instance.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(meta)
        return instance

    def create(
        self,
        name: str,
        mc_version: str,
        *,
        version_id: str = "",
        loader: str = "vanilla",
        loader_version: str = "",
        isolated: bool = True,
        game_root: str = "",
        memory_mb: int = 0,
    ) -> Instance:
        """新建实例并创建隔离目录。"""
        cleaned = (name or "").strip() or f"{mc_version} 实例"
        cleaned = self._unique_name(cleaned)
        instance_id = self._unique_id(cleaned)
        instance = Instance(
            id=instance_id,
            name=cleaned,
            mc_version=mc_version,
            version_id=version_id or mc_version,
            loader=loader,
            loader_version=loader_version,
            isolated=isolated,
            game_root=game_root,
            memory_mb=memory_mb,
        )
        self.save(instance)
        if isolated:
            game_dir = self.paths.instance_game_dir(instance.id)
            for sub in ("mods", "resourcepacks", "shaderpacks", "saves", "config"):
                (game_dir / sub).mkdir(parents=True, exist_ok=True)
        log.info("已创建实例 %s（%s）", instance.name, instance.mc_version)
        return instance

    def _unique_name(self, name: str) -> str:
        taken = {item.name for item in self.list_instances()}
        candidate = name
        counter = 2
        while candidate in taken:
            candidate = f"{name} ({counter})"
            counter += 1
        return candidate

    def _unique_id(self, name: str) -> str:
        base = slugify(name) or "instance"
        candidate = f"{base}-{uuid.uuid4().hex[:6]}"
        while self.exists(candidate):
            candidate = f"{base}-{uuid.uuid4().hex[:6]}"
        return candidate

    def update(self, instance: Instance) -> Instance:
        if not self.exists(instance.id):
            raise InstanceError(f"实例不存在：{instance.name}")
        return self.save(instance)

    def rename(self, instance_id: str, new_name: str) -> Instance:
        instance = self.get(instance_id)
        cleaned = (new_name or "").strip()
        if not cleaned:
            raise InstanceError("实例名称不能为空。")
        instance.name = cleaned
        return self.save(instance)

    def duplicate(self, instance_id: str, new_name: str = "") -> Instance:
        """复制实例（游戏目录一并复制，版本文件复用共享目录）。"""
        source = self.get(instance_id)
        name = (new_name or f"{source.name} 副本").strip()
        clone = Instance(
            id=self._unique_id(name),
            name=name,
            mc_version=source.mc_version,
            version_id=source.version_id,
            loader=source.loader,
            loader_version=source.loader_version,
            isolated=source.isolated,
            java_path=source.java_path,
            memory_mb=source.memory_mb,
            jvm_args=list(source.jvm_args),
            game_args=list(source.game_args),
            icon=source.icon,
            notes=source.notes,
        )
        self.save(clone)
        if source.isolated:
            src_dir = source.game_dir(self.paths)
            dst_dir = clone.game_dir(self.paths)
            if src_dir.is_dir():
                dst_dir.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)
                except OSError as error:
                    raise InstanceError("复制实例文件失败。", detail=str(error)) from error
        log.info("已复制实例 %s -> %s", source.name, name)
        return clone

    def delete(self, instance_id: str, *, remove_files: bool = True) -> None:
        """删除实例；``remove_files`` 为真时一并删除隔离目录。"""
        instance = self.get(instance_id)
        directory = self.paths.instance_dir(instance.id)
        if remove_files and directory.exists():
            try:
                shutil.rmtree(directory)
            except OSError as error:
                raise InstanceError("删除实例文件失败。", detail=str(error)) from error
        elif not remove_files:
            (directory / "instance.json").unlink(missing_ok=True)
        log.info("已删除实例 %s", instance.name)

    # ---------- 已安装版本登记 ----------
    def sync_installed(self, ignored: Iterable[str] = ()) -> list[Instance]:
        """把共享目录下已安装、但还没有实例的版本登记为实例。

        ``ignored`` 用于记住用户主动删除的自动实例，避免下次刷新又出现。
        """
        game_root = self.paths.game
        versions = self.detect_versions(game_root)
        if not versions:
            return []
        existing = self.list_instances()
        covered = {item.version_id for item in existing if not item.game_root}
        ignored_set = {str(value) for value in ignored}
        taken_names = {item.name for item in existing}
        created: list[Instance] = []
        for version_id in versions:
            if version_id in covered or version_id in ignored_set:
                continue
            document = self._read_version(game_root, version_id)
            if document is None:
                continue
            jar = game_root / "versions" / version_id / f"{version_id}.jar"
            if not jar.is_file() and not document.get("inheritsFrom"):
                continue
            mc_version, loader, loader_version = self.describe_version(game_root, version_id)
            name = self._auto_instance_name(mc_version, loader, loader_version)
            base = name
            counter = 2
            while name in taken_names:
                name = f"{base} ({counter})"
                counter += 1
            taken_names.add(name)
            instance = self.create(
                name,
                mc_version,
                version_id=version_id,
                loader=loader,
                loader_version=loader_version,
                isolated=False,
            )
            instance.auto_version = True
            self.save(instance)
            created.append(instance)
        if created:
            log.info("自动登记 %d 个已安装版本", len(created))
        return created

    @staticmethod
    def _auto_instance_name(mc_version: str, loader: str, loader_version: str) -> str:
        if not loader or loader == "vanilla":
            return f"{mc_version} 原版"
        suffix = f"{loader.capitalize()} {loader_version}".strip()
        return f"{mc_version} {suffix}"

    # ---------- 导入 ----------
    def detect_versions(self, game_root: Path) -> list[str]:
        """列出某个 .minecraft 下已安装的版本 id。"""
        versions_dir = game_root / "versions"
        if not versions_dir.is_dir():
            return []
        return sorted(
            child.name
            for child in versions_dir.iterdir()
            if (child / f"{child.name}.json").is_file()
        )

    def import_minecraft(self, source: Path, name: str = "") -> Instance:
        """把已有 .minecraft 注册为实例（不复制文件，直接引用）。"""
        source = source.expanduser().resolve()
        if not source.is_dir():
            raise InstanceError(f"目录不存在：{source}")

        versions = self.detect_versions(source)
        if not versions:
            raise InstanceError("该目录下没有找到任何已安装版本（versions/*/*.json）。")

        version_id = self._pick_primary_version(versions)
        mc_version, loader, loader_version = self.describe_version(source, version_id)
        instance_name = (name or source.parent.name or "导入的实例").strip()
        instance = self.create(
            instance_name,
            mc_version,
            version_id=version_id,
            loader=loader,
            loader_version=loader_version,
            isolated=False,
            game_root=str(source),
        )
        log.info("已导入实例 %s -> %s", instance_name, source)
        return instance

    @staticmethod
    def _pick_primary_version(versions: Iterable[str]) -> str:
        items = list(versions)
        # 优先正式版号（纯数字点号），其次最长（通常是加载器版本）
        plain = [item for item in items if item[0].isdigit() and all(part.isdigit() for part in item.split("."))]
        if plain:
            return sorted(plain, key=lambda item: [int(part) for part in item.split(".")])[-1]
        return sorted(items)[-1]

    @staticmethod
    def _read_version(game_root: Path, version_id: str) -> dict | None:
        path = game_root / "versions" / version_id / f"{version_id}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def describe_version(game_root: Path, version_id: str) -> tuple[str, str, str]:
        """从版本 JSON 推断 MC 版本与加载器。"""
        data = InstanceManager._read_version(game_root, version_id)
        if data is None:
            return version_id, "vanilla", ""

        if not isinstance(data, dict):
            return version_id, "vanilla", ""

        parent = str(data.get("inheritsFrom") or "")
        lowered = version_id.lower()
        loader = "vanilla"
        loader_version = ""
        if "fabric" in lowered or "fabric" in str(data.get("mainClass", "")).lower():
            loader = "fabric"
            parts = version_id.split("-")
            loader_version = parts[2] if len(parts) > 2 else ""
        elif "quilt" in lowered:
            loader = "quilt"
            parts = version_id.split("-")
            loader_version = parts[2] if len(parts) > 2 else ""
        elif "neoforge" in lowered:
            loader = "neoforge"
            loader_version = version_id.split("neoforge-")[-1]
        elif "forge" in lowered:
            loader = "forge"
            loader_version = version_id.split("forge-")[-1]

        mc_version = parent or version_id
        return mc_version, loader, loader_version

    # ---------- 便捷 ----------
    def touch(self, instance_id: str) -> Instance:
        """记录一次启动。"""
        instance = self.get(instance_id)
        instance.touch_played()
        return self.save(instance)

    def with_overrides(self, instance: Instance, **changes: object) -> Instance:
        """返回应用了部分字段的实例副本（不落盘）。"""
        return replace(instance, **changes)  # type: ignore[arg-type]
