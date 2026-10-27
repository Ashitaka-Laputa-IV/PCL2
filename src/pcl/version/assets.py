"""资源索引（asset index）解析与对象池下载。

Minecraft 1.7 之后资源以 sha1 哈希命名存放在 ``assets/objects`` 下；
1.6 及更早的版本还需要把对象复制成原文件名（``virtual`` / ``map_to_resources``）。
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from pcl.core.errors import VersionError
from pcl.core.logging import get_logger
from pcl.core.paths import AppPaths
from pcl.net.downloader import DownloadTask

log = get_logger(__name__)

RESOURCES_BASE = "https://resources.download.minecraft.net"


@dataclass(frozen=True)
class AssetObject:
    """资源对象。"""

    name: str
    hash: str
    size: int

    @property
    def prefix(self) -> str:
        return self.hash[:2]


@dataclass
class AssetIndex:
    """解析后的资源索引。"""

    id: str
    objects: list[AssetObject]
    virtual: bool = False
    map_to_resources: bool = False

    @property
    def total_size(self) -> int:
        return sum(item.size for item in self.objects)


def object_rel_path(hash_value: str) -> str:
    """对象在 ``objects`` 下的相对路径。"""
    return f"{hash_value[:2]}/{hash_value}"


def object_url(hash_value: str) -> str:
    """对象下载地址。"""
    return f"{RESOURCES_BASE}/{hash_value[:2]}/{hash_value}"


def parse_index(raw: Mapping[str, Any], index_id: str) -> AssetIndex:
    """解析 asset index JSON。"""
    if not isinstance(raw, Mapping):
        raise VersionError("资源索引格式不正确。")
    objects_raw = raw.get("objects")
    if not isinstance(objects_raw, Mapping):
        raise VersionError("资源索引缺少 objects 字段。")

    objects: list[AssetObject] = []
    for name, item in objects_raw.items():
        if not isinstance(item, Mapping):
            continue
        hash_value = str(item.get("hash", ""))
        if len(hash_value) < 4:
            continue
        objects.append(AssetObject(name=str(name), hash=hash_value, size=int(item.get("size", 0) or 0)))

    virtual = bool(raw.get("virtual", False)) or index_id.startswith("legacy")
    map_to_resources = bool(raw.get("map_to_resources", False)) or (
        index_id.startswith("legacy") and not virtual
    )
    return AssetIndex(
        id=index_id,
        objects=objects,
        virtual=virtual,
        map_to_resources=map_to_resources,
    )


def load_index(path: Path, index_id: str) -> AssetIndex:
    """从磁盘读取资源索引。"""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise VersionError("资源索引无法读取。", detail=str(error)) from error
    return parse_index(raw, index_id)


def index_task(paths: AppPaths, index_id: str, url: str, sha1: str, size: int) -> DownloadTask:
    """构造资源索引文件的下载任务。"""
    return DownloadTask(
        url=url,
        dest=paths.asset_indexes / f"{index_id}.json",
        sha1=sha1 or None,
        size=size or None,
        priority=10,
        name=f"资源索引 {index_id}",
    )


def build_tasks(paths: AppPaths, index: AssetIndex) -> list[DownloadTask]:
    """把索引内的全部对象转换为下载任务。"""
    tasks: list[DownloadTask] = []
    for item in index.objects:
        tasks.append(
            DownloadTask(
                url=object_url(item.hash),
                dest=paths.asset_objects / item.prefix / item.hash,
                sha1=item.hash,
                size=item.size or None,
                name=item.name,
            )
        )
    return tasks


def materialize(paths: AppPaths, index: AssetIndex, game_dir: Path) -> int:
    """为旧版本把对象池还原为原始文件名结构，返回复制数量。"""
    if index.virtual:
        target_root = paths.asset_virtual / index.id
    elif index.map_to_resources:
        target_root = game_dir / "resources"
    else:
        return 0

    copied = 0
    for item in index.objects:
        source = paths.asset_objects / item.prefix / item.hash
        if not source.is_file():
            continue
        target = target_root / item.name
        if target.is_file() and target.stat().st_size == item.size:
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied += 1
        except OSError as error:
            log.debug("复制旧版资源失败 %s: %s", item.name, error)
    if copied:
        log.info("已还原 %d 个旧版资源文件到 %s", copied, target_root)
    return copied
