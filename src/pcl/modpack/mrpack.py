"""Modrinth 整合包（``.mrpack``）解析。"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any, Mapping

from pcl.core.errors import ModpackError
from pcl.modpack.base import PackFile, ParsedModpack

INDEX_NAME = "modrinth.index.json"
LOADER_KEYS = {
    "fabric-loader": "fabric",
    "quilt-loader": "quilt",
    "forge": "forge",
    "neoforge": "neoforge",
}


def _loader_from_dependencies(dependencies: Mapping[str, Any]) -> tuple[str, str]:
    for key, kind in LOADER_KEYS.items():
        value = dependencies.get(key)
        if value:
            return kind, str(value)
    return "vanilla", ""


def parse(path: Path) -> ParsedModpack:
    """解析 .mrpack（本质是 zip）。"""
    if not zipfile.is_zipfile(path):
        raise ModpackError("该文件不是有效的 Modrinth 整合包。")

    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        if INDEX_NAME not in names:
            raise ModpackError("缺少 modrinth.index.json。")
        try:
            index = json.loads(archive.read(INDEX_NAME).decode("utf-8-sig", errors="replace"))
        except ValueError as error:
            raise ModpackError("modrinth.index.json 解析失败。", detail=str(error)) from error

    if not isinstance(index, Mapping):
        raise ModpackError("modrinth.index.json 格式不正确。")

    dependencies = index.get("dependencies")
    dependencies = dependencies if isinstance(dependencies, Mapping) else {}
    loader, loader_version = _loader_from_dependencies(dependencies)
    mc_version = str(dependencies.get("minecraft", "") or "")
    if not mc_version:
        raise ModpackError("整合包没有声明 Minecraft 版本。")

    files: list[PackFile] = []
    warnings: list[str] = []
    for item in index.get("files") or []:
        if not isinstance(item, Mapping):
            continue
        env = item.get("env")
        if isinstance(env, Mapping) and str(env.get("client", "required")) == "unsupported":
            continue
        relative = str(item.get("path", "") or "")
        if not relative:
            continue
        downloads = item.get("downloads") or []
        url = str(downloads[0]) if downloads else ""
        hashes = item.get("hashes")
        sha1 = str(hashes.get("sha1", "")) if isinstance(hashes, Mapping) else ""
        if not url:
            # 允许「无下载地址」的条目（本启动器导出的包会把文件放进 overrides）
            warnings.append(f"文件条目缺少下载地址：{relative}，将使用包内文件")
        files.append(
            PackFile(
                url=url,
                path=relative,
                sha1=sha1 or None,
                size=int(item.get("fileSize") or 0) or None,
                optional=not url,
            )
        )

    overrides = [prefix for prefix in ("overrides", "client-overrides") if any(
        name.startswith(prefix + "/") for name in names
    )]

    return ParsedModpack(
        kind="modrinth",
        path=path,
        name=str(index.get("name") or path.stem),
        mc_version=mc_version,
        loader=loader,
        loader_version=loader_version,
        description=str(index.get("summary") or ""),
        version=str(index.get("versionId") or ""),
        files=files,
        overrides=overrides,
        warnings=warnings,
    )
