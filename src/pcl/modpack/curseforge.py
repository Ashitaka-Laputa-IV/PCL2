"""CurseForge 整合包（zip + manifest.json）解析。

CurseForge 的文件下载地址未随整合包分发，需通过官方 API 换取；
未配置 API Key 时只能完成「元数据解析 + 覆盖文件释放」，并对缺失模组给出明确提示。
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any, Mapping

import httpx

from pcl.core.errors import ModpackError
from pcl.core.logging import get_logger
from pcl.modpack.base import PackFile, ParsedModpack

log = get_logger(__name__)

MANIFEST_NAME = "manifest.json"
API_BASE = "https://api.curseforge.com/v1"
LOADER_IDS = {"forge": "forge", "fabric": "fabric", "quilt": "quilt", "neoforge": "neoforge"}


def _parse_loader(mod_loaders: Any) -> tuple[str, str]:
    entries = [item for item in (mod_loaders or []) if isinstance(item, Mapping)]
    primary = next((item for item in entries if item.get("primary")), entries[0] if entries else None)
    if not primary:
        return "vanilla", ""
    raw = str(primary.get("id", "") or "")
    for prefix, kind in LOADER_IDS.items():
        if raw.lower().startswith(prefix + "-"):
            return kind, raw[len(prefix) + 1 :]
    return "vanilla", ""


def parse(path: Path) -> ParsedModpack:
    """解析 CurseForge 整合包（不访问网络）。"""
    if not zipfile.is_zipfile(path):
        raise ModpackError("该文件不是有效的 CurseForge 整合包。")
    try:
        with zipfile.ZipFile(path) as archive:
            if MANIFEST_NAME not in set(archive.namelist()):
                raise ModpackError("缺少 manifest.json。")
            manifest = json.loads(archive.read(MANIFEST_NAME).decode("utf-8-sig", errors="replace"))
            names = set(archive.namelist())
    except ValueError as error:
        raise ModpackError("manifest.json 解析失败。", detail=str(error)) from error

    if not isinstance(manifest, Mapping):
        raise ModpackError("manifest.json 格式不正确。")

    minecraft = manifest.get("minecraft")
    minecraft = minecraft if isinstance(minecraft, Mapping) else {}
    mc_version = str(minecraft.get("version", "") or "")
    if not mc_version:
        raise ModpackError("整合包没有声明 Minecraft 版本。")
    loader, loader_version = _parse_loader(minecraft.get("modLoaders"))

    override_dir = str(manifest.get("overrides") or "overrides")
    overrides = [prefix for prefix in (override_dir, "client-overrides") if any(
        name.startswith(prefix.rstrip("/") + "/") for name in names
    )]

    return ParsedModpack(
        kind="curseforge",
        path=path,
        name=str(manifest.get("name") or path.stem),
        mc_version=mc_version,
        loader=loader,
        loader_version=loader_version,
        description=str(manifest.get("author") or ""),
        version=str(manifest.get("version") or ""),
        overrides=overrides,
        requires_manual=True,
    )


def resolve_files(
    parsed: ParsedModpack,
    api_key: str,
    *,
    client: httpx.Client | None = None,
) -> ParsedModpack:
    """用 CurseForge API 把 projectID/fileID 解析为可下载地址。

    需要 ``api_key``；缺失时原样返回并追加提示，由导入流程告知用户手动安装。
    """
    try:
        with zipfile.ZipFile(parsed.path) as archive:
            manifest = json.loads(archive.read(MANIFEST_NAME).decode("utf-8-sig", errors="replace"))
    except (OSError, ValueError, KeyError) as error:
        raise ModpackError("无法重新读取整合包清单。", detail=str(error)) from error

    entries = [item for item in (manifest.get("files") or []) if isinstance(item, Mapping)]
    if not entries:
        parsed.requires_manual = False
        return parsed

    if not api_key:
        parsed.warnings.append(
            f"未配置 CurseForge API Key，{len(entries)} 个模组需要手动下载后放入 mods 目录。"
        )
        return parsed

    owns = client is None
    http_client = client or httpx.Client(
        headers={"x-api-key": api_key, "Accept": "application/json"},
        timeout=httpx.Timeout(15.0),
        follow_redirects=True,
    )
    try:
        for entry in entries:
            project_id = entry.get("projectID")
            file_id = entry.get("fileID")
            if project_id is None or file_id is None:
                continue
            url = f"{API_BASE}/mods/{project_id}/files/{file_id}"
            try:
                response = http_client.get(url)
                if response.status_code >= 400:
                    parsed.warnings.append(f"模组 {project_id}/{file_id} 查询失败（HTTP {response.status_code}）")
                    continue
                data = response.json().get("data") or {}
            except (httpx.HTTPError, ValueError) as error:
                parsed.warnings.append(f"模组 {project_id}/{file_id} 查询失败：{error}")
                continue

            download_url = str(data.get("downloadUrl") or "")
            file_name = str(data.get("fileName") or "")
            if not download_url or not file_name:
                parsed.warnings.append(f"模组 {file_name or project_id} 没有公开下载地址")
                continue
            hashes = data.get("hashes") or []
            sha1 = ""
            for item in hashes if isinstance(hashes, list) else []:
                if isinstance(item, Mapping) and int(item.get("algo", 0) or 0) == 1:
                    sha1 = str(item.get("value", ""))
            parsed.files.append(
                PackFile(
                    url=download_url,
                    path=f"mods/{file_name}",
                    sha1=sha1 or None,
                    size=int(data.get("fileLength") or 0) or None,
                )
            )
        parsed.requires_manual = False
    finally:
        if owns:
            http_client.close()
    return parsed
