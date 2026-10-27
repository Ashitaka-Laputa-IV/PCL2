"""HMCL 整合包解析：``modpack.json`` + ``.minecraft/`` 目录。"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any, Mapping

from pcl.core.errors import ModpackError
from pcl.modpack.base import ParsedModpack

PACK_NAMES = ("modpack.json", "hmcl.json")
GAME_PREFIXES = (".minecraft", "minecraft")


def parse(path: Path) -> ParsedModpack:
    """解析 HMCL 整合包（zip）。"""
    if not zipfile.is_zipfile(path):
        raise ModpackError("该文件不是有效的 HMCL 整合包。")

    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        pack_name = next((name for name in PACK_NAMES if name in names), "")
        if not pack_name:
            raise ModpackError("缺少 modpack.json。")
        try:
            raw = json.loads(archive.read(pack_name).decode("utf-8-sig", errors="replace"))
        except ValueError as error:
            raise ModpackError("modpack.json 解析失败。", detail=str(error)) from error

        overrides = [prefix for prefix in GAME_PREFIXES if any(
            name.startswith(prefix + "/") for name in names
        )]
        if not overrides:
            overrides = ["overrides"]

    if not isinstance(raw, Mapping):
        raise ModpackError("modpack.json 格式不正确。")

    manifest = raw.get("manifest")
    manifest = manifest if isinstance(manifest, Mapping) else raw

    mc_version = str(manifest.get("gameVersion") or manifest.get("minecraft") or "")
    if not mc_version:
        raise ModpackError("整合包没有声明游戏版本。")

    loader = str(manifest.get("loaderType") or "vanilla").lower()
    if loader not in ("vanilla", "fabric", "quilt", "forge", "neoforge"):
        loader = "vanilla"

    return ParsedModpack(
        kind="hmcl",
        path=path,
        name=str(manifest.get("name") or path.stem),
        mc_version=mc_version,
        loader=loader,
        loader_version=str(manifest.get("loaderVersion") or ""),
        description=str(manifest.get("description") or ""),
        version=str(manifest.get("version") or ""),
        overrides=overrides,
    )


def build_manifest(
    name: str,
    mc_version: str,
    loader: str = "vanilla",
    loader_version: str = "",
    description: str = "",
) -> dict[str, Any]:
    """生成 HMCL ``modpack.json`` 内容（导出用）。"""
    return {
        "manifest": {
            "type": "minecraftModpack",
            "version": 1,
            "name": name,
            "description": description,
            "author": "PCL2 Python",
            "gameVersion": mc_version,
            "loaderType": loader,
            "loaderVersion": loader_version,
        }
    }
