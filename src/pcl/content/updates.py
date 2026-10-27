"""Modrinth 指纹比对：检测本地 Mod 是否有可用更新。

流程：
1. 计算本地 Mod 文件的 sha1；
2. 批量提交到 ``/v2/version_files`` 换取 Modrinth 版本记录；
3. 按项目拉取版本列表，筛出与当前游戏版本、加载器匹配的最新版本；
4. 若最新版本发布时间晚于本地指纹对应版本，则视为有更新。
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urlparse

import httpx

from pcl.core.errors import NetworkError
from pcl.core.logging import get_logger
from pcl.net.http import download_file, sha1_file

log = get_logger(__name__)

API = "https://api.modrinth.com/v2"
MAX_HASHES = 100
MAX_PROJECTS = 60
TYPE_ORDER = {"release": 0, "beta": 1, "alpha": 2}


@dataclass(frozen=True)
class ModUpdate:
    """一条可更新的 Mod 记录。"""

    path: Path
    file_name: str
    project_id: str
    current_version: str
    latest_version: str
    version_type: str
    download_url: str
    sha1: str
    latest_filename: str

    @property
    def page_url(self) -> str:
        return f"https://modrinth.com/project/{self.project_id}"


@dataclass
class UpdateReport:
    updates: list[ModUpdate] = field(default_factory=list)
    checked: int = 0
    matched: int = 0
    failed: int = 0
    error: str = ""

    def for_path(self, path: Path) -> ModUpdate | None:
        key = str(path)
        for update in self.updates:
            if str(update.path) == key:
                return update
        return None


def _primary_file(version: dict[str, Any]) -> dict[str, Any] | None:
    files = version.get("files") or []
    for item in files:
        if item.get("primary"):
            return item
    return files[0] if files else None


def _filename_from_url(url: str) -> str:
    name = unquote(urlparse(url).path.rsplit("/", 1)[-1])
    return name or "mod.jar"


def _matches(version: dict[str, Any], mc_version: str, loader: str) -> bool:
    if loader:
        loaders = {str(item).lower() for item in version.get("loaders") or []}
        if loaders and loader.lower() not in loaders:
            return False
    if mc_version:
        games = {str(item) for item in version.get("game_versions") or []}
        if games and mc_version not in games:
            return False
    return True


def _pick_latest(versions: Iterable[dict[str, Any]], mc_version: str, loader: str) -> dict[str, Any] | None:
    """按“正式版优先 + 发布时间最新”挑选候选版本。"""
    candidates = [item for item in versions if _matches(item, mc_version, loader)]
    if not candidates:
        return None
    candidates.sort(key=lambda item: TYPE_ORDER.get(str(item.get("version_type")), 3))
    best_type = TYPE_ORDER.get(str(candidates[0].get("version_type")), 3)
    same_type = [item for item in candidates if TYPE_ORDER.get(str(item.get("version_type")), 3) == best_type]
    same_type.sort(key=lambda item: str(item.get("date_published") or ""), reverse=True)
    return same_type[0]


def _fetch_project_versions(client: httpx.Client, project_id: str) -> list[dict[str, Any]]:
    response = client.get(f"{API}/project/{project_id}/version")
    if response.status_code >= 400:
        return []
    data = response.json()
    return data if isinstance(data, list) else []


def check_mod_updates(
    mods: Iterable[Any],
    client: httpx.Client,
    *,
    mc_version: str = "",
    loader: str = "",
    cancel: threading.Event | None = None,
) -> UpdateReport:
    """检查 Mod 更新。``mods`` 需要提供 ``path`` 与可选 ``file_name``。"""
    items = [mod for mod in mods if getattr(mod, "enabled", True)]
    report = UpdateReport(checked=len(items))
    if not items:
        return report

    by_hash: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=min(8, len(items))) as pool:
        digests = list(pool.map(lambda mod: sha1_file(mod.path), items))
    for mod, digest in zip(items, digests):
        by_hash.setdefault(digest.lower(), mod)

    hashes = list(by_hash)
    matched: dict[str, dict[str, Any]] = {}
    for start in range(0, len(hashes), MAX_HASHES):
        if cancel is not None and cancel.is_set():
            return report
        batch = hashes[start : start + MAX_HASHES]
        try:
            response = client.post(f"{API}/version_files", json={"hashes": batch, "algorithm": "sha1"})
        except httpx.HTTPError as error:
            report.error = str(error)
            log.warning("Modrinth 指纹请求失败：%s", error)
            break
        if response.status_code >= 400:
            report.error = f"HTTP {response.status_code}"
            break
        try:
            payload = response.json()
        except ValueError:
            report.error = "返回内容无法解析"
            break
        if isinstance(payload, dict):
            for digest, version in payload.items():
                if isinstance(version, dict):
                    matched[str(digest).lower()] = version

    projects: dict[str, dict[str, Any]] = {}
    for digest, version in matched.items():
        project_id = str(version.get("project_id") or "")
        if not project_id:
            continue
        mod = by_hash.get(digest)
        if mod is None:
            continue
        projects.setdefault(project_id, {"mod": mod, "version": version})
    report.matched = len(projects)
    if not projects:
        return report

    limited = list(projects.items())[:MAX_PROJECTS]
    with ThreadPoolExecutor(max_workers=min(6, len(limited))) as pool:
        futures = {project_id: pool.submit(_fetch_project_versions, client, project_id) for project_id, _ in limited}
        for project_id, info in limited:
            if cancel is not None and cancel.is_set():
                break
            try:
                versions = futures[project_id].result(timeout=30)
            except Exception as error:  # noqa: BLE001 - 单个项目失败不影响整体
                log.debug("获取 %s 版本失败：%s", project_id, error)
                report.failed += 1
                continue
            current = info["version"]
            latest = _pick_latest(versions, mc_version, loader)
            if latest is None:
                continue
            current_date = str(current.get("date_published") or "")
            latest_date = str(latest.get("date_published") or "")
            if latest_date <= current_date or latest.get("id") == current.get("id"):
                continue
            primary = _primary_file(latest)
            if primary is None:
                continue
            url = str(primary.get("url") or "")
            if not url:
                continue
            mod = info["mod"]
            report.updates.append(
                ModUpdate(
                    path=Path(mod.path),
                    file_name=str(getattr(mod, "file_name", mod.path.name)),
                    project_id=project_id,
                    current_version=str(current.get("version_number") or ""),
                    latest_version=str(latest.get("version_number") or ""),
                    version_type=str(latest.get("version_type") or "release"),
                    download_url=url,
                    sha1=str((primary.get("hashes") or {}).get("sha1") or ""),
                    latest_filename=str(primary.get("filename") or _filename_from_url(url)),
                )
            )
    report.updates.sort(key=lambda update: update.file_name.lower())
    return report


def download_update(
    client: httpx.Client,
    update: ModUpdate,
    dest: Path,
    *,
    cancel: threading.Event | None = None,
) -> Path:
    """下载更新文件到指定位置（带 sha1 校验）。"""
    if not update.download_url:
        raise NetworkError("该版本没有可用的下载地址。")
    download_file(client, update.download_url, dest, sha1=update.sha1 or None, cancel=cancel)
    return dest
