"""Modrinth API 客户端（公开接口，无需 API Key）。

只实现启动器需要的能力：搜索、项目详情、版本列表。
文档：https://docs.modrinth.com/api/
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping

import httpx

from pcl import USER_AGENT
from pcl.core.errors import NetworkError
from pcl.core.logging import get_logger

log = get_logger(__name__)

API_BASE = "https://api.modrinth.com/v2"
PROJECT_TYPES = ("mod", "resourcepack", "shader", "modpack", "datapack")
SORT_OPTIONS = ("relevance", "downloads", "follows", "newest", "updated")


@dataclass
class VersionFile:
    """版本内的一个可下载文件。"""

    url: str
    filename: str
    sha1: str = ""
    size: int = 0
    primary: bool = False
    file_type: str = ""


@dataclass
class ProjectDependency:
    """版本声明的依赖。"""

    project_id: str = ""
    version_id: str = ""
    dependency_type: str = "required"  # required / optional / incompatible / embedded


@dataclass
class ProjectVersion:
    """项目的一个版本。"""

    id: str
    project_id: str = ""
    name: str = ""
    version_number: str = ""
    version_type: str = "release"
    game_versions: list[str] = field(default_factory=list)
    loaders: list[str] = field(default_factory=list)
    date_published: str = ""
    downloads: int = 0
    files: list[VersionFile] = field(default_factory=list)
    dependencies: list[ProjectDependency] = field(default_factory=list)

    def primary_file(self) -> VersionFile | None:
        for item in self.files:
            if item.primary:
                return item
        return self.files[0] if self.files else None

    @property
    def label(self) -> str:
        parts = [self.version_number or self.name or self.id]
        if self.loaders:
            parts.append("/".join(loader.capitalize() for loader in self.loaders[:2]))
        if self.game_versions:
            parts.append(self.game_versions[0])
        return " · ".join(parts)


@dataclass
class Project:
    """搜索结果中的项目。"""

    id: str
    slug: str = ""
    title: str = ""
    description: str = ""
    project_type: str = "mod"
    icon_url: str = ""
    downloads: int = 0
    follows: int = 0
    author: str = ""
    categories: list[str] = field(default_factory=list)
    versions: list[str] = field(default_factory=list)
    client_side: str = ""
    server_side: str = ""

    @property
    def downloads_label(self) -> str:
        count = self.downloads
        if count >= 1_000_000:
            return f"{count / 1_000_000:.1f}M"
        if count >= 1_000:
            return f"{count / 1_000:.1f}K"
        return str(count)


def _parse_project(raw: Mapping[str, Any]) -> Project:
    return Project(
        id=str(raw.get("project_id") or raw.get("id") or ""),
        slug=str(raw.get("slug") or ""),
        title=str(raw.get("title") or ""),
        description=str(raw.get("description") or ""),
        project_type=str(raw.get("project_type") or "mod"),
        icon_url=str(raw.get("icon_url") or ""),
        downloads=int(raw.get("downloads") or 0),
        follows=int(raw.get("follows") or 0),
        author=str(raw.get("author") or ""),
        categories=[str(item) for item in raw.get("categories") or []],
        versions=[str(item) for item in raw.get("versions") or []],
        client_side=str(raw.get("client_side") or ""),
        server_side=str(raw.get("server_side") or ""),
    )


def _parse_version(raw: Mapping[str, Any], project_id: str = "") -> ProjectVersion:
    files = []
    for item in raw.get("files") or []:
        if not isinstance(item, Mapping):
            continue
        hashes = item.get("hashes") or {}
        files.append(
            VersionFile(
                url=str(item.get("url") or ""),
                filename=str(item.get("filename") or ""),
                sha1=str(hashes.get("sha1", "") or "") if isinstance(hashes, Mapping) else "",
                size=int(item.get("size") or 0),
                primary=bool(item.get("primary")),
                file_type=str(item.get("file_type") or ""),
            )
        )
    dependencies = []
    for item in raw.get("dependencies") or []:
        if not isinstance(item, Mapping):
            continue
        dependencies.append(
            ProjectDependency(
                project_id=str(item.get("project_id") or ""),
                version_id=str(item.get("version_id") or ""),
                dependency_type=str(item.get("dependency_type") or "required"),
            )
        )
    return ProjectVersion(
        id=str(raw.get("id") or ""),
        project_id=str(raw.get("project_id") or project_id),
        name=str(raw.get("name") or ""),
        version_number=str(raw.get("version_number") or ""),
        version_type=str(raw.get("version_type") or "release"),
        game_versions=[str(item) for item in raw.get("game_versions") or []],
        loaders=[str(item) for item in raw.get("loaders") or []],
        date_published=str(raw.get("date_published") or ""),
        downloads=int(raw.get("downloads") or 0),
        files=files,
        dependencies=dependencies,
    )


class ModrinthClient:
    """轻量 Modrinth 客户端（同步，供后台线程调用）。"""

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client
        self._owns = client is None

    def _get(self, url: str, params: Mapping[str, str] | None = None, retries: int = 2) -> Any:
        client = self._client
        if client is None:
            client = httpx.Client(
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=httpx.Timeout(15.0),
                follow_redirects=True,
            )
            self._client = client
            self._owns = True

        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                response = client.get(url, params=dict(params or {}))
                if response.status_code == 404:
                    raise NetworkError("资源不存在或已下架。")
                if response.status_code >= 400:
                    raise NetworkError(f"Modrinth 返回状态码 {response.status_code}。")
                return response.json()
            except (httpx.HTTPError, ValueError) as error:
                last_error = error
                if attempt:
                    import time

                    time.sleep(0.6 * (attempt + 1))
        raise NetworkError("请求 Modrinth 失败，请检查网络。", detail=str(last_error))

    def close(self) -> None:
        if self._client is not None and self._owns:
            self._client.close()
        self._client = None

    # ---------- 搜索 ----------
    def search(
        self,
        query: str = "",
        *,
        project_type: str = "mod",
        game_version: str = "",
        loader: str = "",
        offset: int = 0,
        limit: int = 20,
        sort: str = "relevance",
    ) -> tuple[list[Project], int]:
        """搜索项目，返回 ``(结果列表, 总数)``。"""
        facets: list[list[str]] = [[f"project_type:{project_type}"]]
        if loader and project_type in ("mod", "modpack"):
            facets.append([f"categories:{loader}"])
        if game_version:
            facets.append([f"versions:{game_version}"])

        params = {
            "limit": str(max(1, min(100, limit))),
            "offset": str(max(0, offset)),
            "index": sort if sort in SORT_OPTIONS else "relevance",
            "facets": json.dumps(facets, separators=(",", ":")),
        }
        if query.strip():
            params["query"] = query.strip()

        raw = self._get(f"{API_BASE}/search", params)
        hits = raw.get("hits") if isinstance(raw, Mapping) else None
        projects = [_parse_project(item) for item in (hits or []) if isinstance(item, Mapping)]
        total = int(raw.get("total_hits") or len(projects)) if isinstance(raw, Mapping) else len(projects)
        log.info("Modrinth 搜索 %r：%d / %d", query, len(projects), total)
        return projects, total

    # ---------- 版本 ----------
    def versions(
        self,
        project_id: str,
        *,
        game_version: str = "",
        loader: str = "",
    ) -> list[ProjectVersion]:
        """列出项目的版本，可按游戏版本与加载器过滤。"""
        params: dict[str, str] = {}
        if game_version:
            params["game_versions"] = json.dumps([game_version])
        if loader:
            params["loaders"] = json.dumps([loader])
        raw = self._get(f"{API_BASE}/project/{project_id}/version", params)
        if not isinstance(raw, list):
            return []
        return [_parse_version(item, project_id) for item in raw if isinstance(item, Mapping)]

    def project(self, project_id: str) -> Project | None:
        raw = self._get(f"{API_BASE}/project/{project_id}")
        if not isinstance(raw, Mapping):
            return None
        project = _parse_project(raw)
        if not project.id:
            project.id = project_id
        return project
