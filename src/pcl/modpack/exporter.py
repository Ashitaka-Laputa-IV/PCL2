"""实例导出：HMCL / PCL 通用 zip，以及尽力而为的 Modrinth 格式。"""

from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from pcl.core.errors import ModpackError
from pcl.core.logging import get_logger
from pcl.core.paths import AppPaths
from pcl.instance.model import Instance
from pcl.modpack import hmcl, pcl
from pcl.net.http import sha1_file

log = get_logger(__name__)

CONTENT_DIRS = ("mods", "config", "resourcepacks", "shaderpacks")
CONTENT_FILES = ("options.txt", "servers.dat")


@dataclass
class ExportResult:
    path: Path
    kind: str
    file_count: int
    size: int
    warnings: list[str]


def _iter_content(game_dir: Path, include_saves: bool) -> Iterable[tuple[Path, str]]:
    """产出 (绝对路径, 相对游戏目录的 posix 路径)。"""
    for directory in CONTENT_DIRS:
        root = game_dir / directory
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            yield path, path.relative_to(game_dir).as_posix()
    if include_saves:
        saves = game_dir / "saves"
        if saves.is_dir():
            for path in saves.rglob("*"):
                if path.is_file():
                    yield path, path.relative_to(game_dir).as_posix()
    for name in CONTENT_FILES:
        path = game_dir / name
        if path.is_file():
            yield path, name


def _write_zip(dest: Path, prefix: str, entries: Iterable[tuple[Path, str]], extra: dict[str, str]) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    temp = dest.with_suffix(dest.suffix + ".tmp")
    with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, content in extra.items():
            archive.writestr(name, content)
        for source, relative in entries:
            target = f"{prefix}/{relative}" if prefix else relative
            try:
                archive.write(source, target)
                count += 1
            except OSError as error:
                log.warning("写入整合包失败 %s: %s", source, error)
    temp.replace(dest)
    return count


def export_hmcl(
    instance: Instance,
    paths: AppPaths,
    dest: Path,
    *,
    include_saves: bool = False,
) -> ExportResult:
    """导出为 HMCL 整合包。"""
    game_dir = instance.game_dir(paths)
    if not game_dir.is_dir():
        raise ModpackError("实例目录不存在，无法导出。")
    manifest = hmcl.build_manifest(
        instance.name,
        instance.mc_version,
        instance.loader,
        instance.loader_version,
        instance.notes,
    )
    extra = {"modpack.json": json.dumps(manifest, ensure_ascii=False, indent=2)}
    count = _write_zip(dest, ".minecraft", _iter_content(game_dir, include_saves), extra)
    result = ExportResult(dest, "hmcl", count, dest.stat().st_size, [])
    log.info("已导出 HMCL 整合包：%s（%d 个文件）", dest, count)
    return result


def export_pcl(
    instance: Instance,
    paths: AppPaths,
    dest: Path,
    *,
    include_saves: bool = False,
) -> ExportResult:
    """导出为 PCL 整合包。"""
    game_dir = instance.game_dir(paths)
    if not game_dir.is_dir():
        raise ModpackError("实例目录不存在，无法导出。")
    setup = pcl.build_setup_ini(instance.name, instance.mc_version, instance.loader, instance.loader_version)
    extra = {"PCL/Setup.ini": setup}
    count = _write_zip(dest, ".minecraft", _iter_content(game_dir, include_saves), extra)
    result = ExportResult(dest, "pcl", count, dest.stat().st_size, [])
    log.info("已导出 PCL 整合包：%s（%d 个文件）", dest, count)
    return result


def export_modrinth(
    instance: Instance,
    paths: AppPaths,
    dest: Path,
    *,
    include_saves: bool = False,
) -> ExportResult:
    """尽力导出为 Modrinth 格式。

    本地文件缺少原始下载地址，``downloads`` 只能留空；这样的整合包可被解析，
    但重新安装时仍需手动补齐文件，因此结果中会附带说明。
    """
    game_dir = instance.game_dir(paths)
    if not game_dir.is_dir():
        raise ModpackError("实例目录不存在，无法导出。")

    entries = list(_iter_content(game_dir, include_saves))
    files: list[dict[str, object]] = []
    warnings: list[str] = ["Modrinth 格式需要原始下载地址，本地文件无法还原 downloads 字段。"]

    for source, relative in entries:
        if not relative.startswith("mods/"):
            continue
        digest = sha1_file(source)
        files.append(
            {
                "path": relative,
                "hashes": {"sha1": digest, "sha512": hashlib.sha512(source.read_bytes()).hexdigest()},
                "env": {"client": "required", "server": "optional"},
                "downloads": [],
                "fileSize": source.stat().st_size,
            }
        )

    index = {
        "formatVersion": 1,
        "game": "minecraft",
        "versionId": instance.name,
        "name": instance.name,
        "summary": instance.notes,
        "files": files,
        "dependencies": {"minecraft": instance.mc_version},
    }
    if instance.loader == "fabric" and instance.loader_version:
        index["dependencies"]["fabric-loader"] = instance.loader_version
    elif instance.loader == "quilt" and instance.loader_version:
        index["dependencies"]["quilt-loader"] = instance.loader_version
    elif instance.loader in ("forge", "neoforge") and instance.loader_version:
        index["dependencies"][instance.loader] = instance.loader_version

    count = _write_zip(
        dest,
        "overrides",
        entries,
        {"modrinth.index.json": json.dumps(index, ensure_ascii=False, indent=2)},
    )
    result = ExportResult(dest, "modrinth", count, dest.stat().st_size, warnings)
    log.info("已导出 Modrinth 整合包：%s（%d 个文件）", dest, count)
    return result


EXPORTERS = {
    "hmcl": export_hmcl,
    "pcl": export_pcl,
    "modrinth": export_modrinth,
}


def export(instance: Instance, paths: AppPaths, dest: Path, kind: str = "hmcl", **kwargs: object) -> ExportResult:
    """按格式导出。"""
    exporter = EXPORTERS.get(kind)
    if exporter is None:
        raise ModpackError(f"不支持的导出格式：{kind}")
    return exporter(instance, paths, dest, **kwargs)  # type: ignore[arg-type]
