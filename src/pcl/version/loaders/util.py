"""加载器安装的共享工具：库条目转下载任务、maven 地址拼接。"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from pcl.core.paths import AppPaths
from pcl.net.downloader import DownloadTask
from pcl.version import meta


def library_tasks(
    paths: AppPaths,
    libraries: Iterable[Mapping[str, Any]],
    *,
    priority: int = 0,
    default_repo: str | None = None,
) -> list[DownloadTask]:
    """把版本/加载器 JSON 的 ``libraries`` 转为下载任务。

    加载器 profile 通常只提供 ``name`` 与 ``url``，没有 ``downloads`` 字段，
    因此需要按 maven 坐标拼接路径；已有 sha1/size 的条目会一并保留用于校验。
    """
    tasks: list[DownloadTask] = []
    for library in libraries:
        if not isinstance(library, Mapping):
            continue
        if not meta.library_allowed(library):
            continue

        artifact = meta.library_artifact(library) or {}
        path = meta.library_target_path(library)
        url = meta.library_url(library)
        if not url:
            repo = library.get("url") or default_repo
            if repo and path:
                url = f"{str(repo).rstrip('/')}/{path}"
        if not path or not url:
            continue

        tasks.append(
            DownloadTask(
                url=url,
                dest=paths.libraries / path,
                sha1=str(artifact.get("sha1") or "") or None,
                size=int(artifact.get("size") or 0) or None,
                priority=priority,
                name=str(library.get("name", path)),
            )
        )

        native = meta.native_download(library)
        if isinstance(native, Mapping) and native.get("url"):
            classifier = meta.native_classifier(library)
            native_path = str(native.get("path") or "")
            if not native_path and classifier:
                native_path = meta.maven_path(f"{library.get('name')}:{classifier}") or ""
            if native_path:
                tasks.append(
                    DownloadTask(
                        url=str(native["url"]),
                        dest=paths.libraries / native_path,
                        sha1=str(native.get("sha1") or "") or None,
                        size=int(native.get("size") or 0) or None,
                        priority=max(0, priority - 5),
                        name=f"{library.get('name')}:{classifier}",
                    )
                )
    return tasks


def maven_coord_path(coord: str) -> str | None:
    """maven 坐标 → 仓库相对路径。"""
    return meta.maven_path(coord)
