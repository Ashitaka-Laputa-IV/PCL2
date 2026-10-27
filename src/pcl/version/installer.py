"""原版安装编排：客户端 jar、依赖库、资源索引与资源对象的下载与落盘。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import httpx

from pcl.core.config import DownloadOptions
from pcl.core.errors import NetworkError, VersionError
from pcl.core.logging import get_logger
from pcl.core.paths import AppPaths
from pcl.net import http
from pcl.net.downloader import DownloadManager, DownloadSummary, DownloadTask
from pcl.net.mirror import DEFAULT_MIRROR_BASE, candidates
from pcl.version import assets, meta
from pcl.version.manifest import VersionEntry, fetch_manifest
from pcl.version.merge import save_version_json

log = get_logger(__name__)

LOG_CONFIG_DIR = "log_configs"


@dataclass
class InstallReport:
    """安装结果。"""

    version_id: str
    version_json: dict[str, Any] = field(default_factory=dict)
    task_count: int = 0
    downloaded_bytes: int = 0
    elapsed: float = 0.0
    failures: list[str] = field(default_factory=list)
    skipped: bool = False
    java_major: int = 8
    asset_objects: int = 0

    @property
    def ok(self) -> bool:
        return not self.failures


def is_installed(paths: AppPaths, version_id: str) -> bool:
    """判断版本是否已具备启动条件（JSON 与客户端 jar 均存在）。"""
    if not paths.version_json(version_id).is_file():
        return False
    # 部分极老版本/仅库版本可能没有 jar，此时以 JSON 存在为准
    return paths.version_jar(version_id).is_file()


class VanillaInstaller:
    """原版版本安装器。"""

    def __init__(
        self,
        paths: AppPaths,
        options: DownloadOptions | None = None,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.paths = paths
        self.options = options or DownloadOptions()
        self._client = client

    # ---------- 网络 ----------
    def _fetch_json(self, url: str) -> Any:
        owns = self._client is None
        client = self._client or http.create_client(concurrency=self.options.concurrency)
        try:
            last_error: Exception | None = None
            for candidate in candidates(
                url,
                source=self.options.source,
                mirror_base=self.options.mirror_base or DEFAULT_MIRROR_BASE,
            ):
                try:
                    return http.get_json(client, candidate, retries=1)
                except Exception as error:
                    last_error = error
            raise NetworkError("获取版本信息失败。", detail=str(last_error))
        finally:
            if owns:
                client.close()

    def resolve_entry(self, version_id: str, entry: VersionEntry | None = None) -> VersionEntry:
        """获取版本条目（必要时拉取版本清单）。"""
        if entry is not None:
            return entry
        owns = self._client is None
        client = self._client or http.create_client(concurrency=self.options.concurrency)
        try:
            manifest = fetch_manifest(
                client,
                self.paths.cache / "version_manifest_v2.json",
                source=self.options.source,
                mirror_base=self.options.mirror_base or DEFAULT_MIRROR_BASE,
            )
        finally:
            if owns:
                client.close()
        found = manifest.find(version_id)
        if found is None:
            raise VersionError(f"版本清单中没有 {version_id}。")
        return found

    def load_doc(self, version_id: str, entry: VersionEntry | None = None) -> dict[str, Any]:
        """读取版本 JSON：已安装则读本地，否则从官方拉取。"""
        local = self.paths.version_json(version_id)
        if local.is_file():
            try:
                import json

                data = json.loads(local.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("id"):
                    return data
            except (OSError, ValueError):
                log.warning("本地版本 JSON 损坏，将重新下载：%s", local)

        entry = self.resolve_entry(version_id, entry)
        if not entry.url:
            raise VersionError(f"版本 {version_id} 缺少下载地址。")
        doc = self._fetch_json(entry.url)
        if not isinstance(doc, dict):
            raise VersionError(f"版本 {version_id} 的元数据格式不正确。")
        return doc

    # ---------- 任务构造 ----------
    def build_tasks(self, doc: Mapping[str, Any]) -> tuple[list[DownloadTask], Mapping[str, Any] | None]:
        """构造第一阶段任务（jar / 库 / 日志配置 / 资源索引）。"""
        version_id = str(doc.get("id", ""))
        tasks: list[DownloadTask] = []

        downloads = doc.get("downloads")
        client_info = downloads.get("client") if isinstance(downloads, Mapping) else None
        if isinstance(client_info, Mapping) and client_info.get("url"):
            tasks.append(
                DownloadTask(
                    url=str(client_info["url"]),
                    dest=self.paths.version_jar(version_id),
                    sha1=str(client_info.get("sha1") or "") or None,
                    size=int(client_info.get("size") or 0) or None,
                    priority=20,
                    name=f"客户端 {version_id}",
                )
            )

        logging_conf = doc.get("logging")
        if isinstance(logging_conf, Mapping):
            logging_client = logging_conf.get("client")
            if isinstance(logging_client, Mapping):
                file_info = logging_client.get("file")
                if isinstance(file_info, Mapping) and file_info.get("url"):
                    file_id = str(file_info.get("id") or "client-log4j.xml")
                    tasks.append(
                        DownloadTask(
                            url=str(file_info["url"]),
                            dest=self.paths.assets / LOG_CONFIG_DIR / file_id,
                            sha1=str(file_info.get("sha1") or "") or None,
                            size=int(file_info.get("size") or 0) or None,
                            priority=15,
                            name="日志配置",
                        )
                    )

        for library in doc.get("libraries") or []:
            if not isinstance(library, Mapping) or not meta.library_allowed(library):
                continue
            path = meta.library_target_path(library)
            url = meta.library_url(library)
            artifact = meta.library_artifact(library)
            if path and url:
                tasks.append(
                    DownloadTask(
                        url=url,
                        dest=self.paths.libraries / path,
                        sha1=str((artifact or {}).get("sha1") or "") or None,
                        size=int((artifact or {}).get("size") or 0) or None,
                        priority=10,
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
                            dest=self.paths.libraries / native_path,
                            sha1=str(native.get("sha1") or "") or None,
                            size=int(native.get("size") or 0) or None,
                            priority=5,
                            name=f"{library.get('name')}:{classifier}",
                        )
                    )

        index_info = doc.get("assetIndex")
        asset_index_ref: Mapping[str, Any] | None = None
        if isinstance(index_info, Mapping) and index_info.get("url"):
            index_id = str(index_info.get("id") or doc.get("assets") or "legacy")
            asset_index_ref = index_info
            tasks.append(
                assets.index_task(
                    self.paths,
                    index_id,
                    str(index_info["url"]),
                    str(index_info.get("sha1") or ""),
                    int(index_info.get("size") or 0),
                )
            )
        return tasks, asset_index_ref

    # ---------- 安装 ----------
    def install(
        self,
        version_id: str,
        entry: VersionEntry | None = None,
        *,
        force: bool = False,
        game_dir: Path | None = None,
    ) -> InstallReport:
        """下载并安装原版版本，返回安装报告。"""
        started = time.monotonic()
        if not force and is_installed(self.paths, version_id):
            report = InstallReport(version_id=version_id, skipped=True, java_major=8)
            report.elapsed = time.monotonic() - started
            log.info("版本 %s 已安装，跳过下载", version_id)
            return report

        doc = self.load_doc(version_id, entry)
        if not doc.get("id"):
            doc = {**doc, "id": version_id}

        manager = DownloadManager(self.options)
        tasks, asset_index_ref = self.build_tasks(doc)

        summary: DownloadSummary = manager.run(tasks)
        failures = [f"{item.task.display_name}: {item.error}" for item in summary.failures]

        asset_count = 0
        if asset_index_ref is not None:
            index_id = str(asset_index_ref.get("id") or doc.get("assets") or "legacy")
            index_path = self.paths.asset_indexes / f"{index_id}.json"
            if index_path.is_file():
                index = assets.load_index(index_path, index_id)
                asset_count = len(index.objects)
                object_tasks = assets.build_tasks(self.paths, index)
                object_summary = manager.run(object_tasks)
                failures.extend(
                    f"{item.task.display_name}: {item.error}" for item in object_summary.failures[:10]
                )
                if object_summary.failures:
                    log.warning("资源文件有 %d 个下载失败", len(object_summary.failures))
                assets.materialize(self.paths, index, game_dir or self.paths.game)

        if failures:
            raise VersionError(
                f"版本 {version_id} 安装未完成。",
                detail="; ".join(failures[:3]),
            )

        save_version_json(self.paths, version_id, doc)

        java_major = meta.required_java_major(doc)
        report = InstallReport(
            version_id=version_id,
            version_json=doc,
            task_count=len(tasks) + asset_count,
            downloaded_bytes=sum(item.bytes_written for item in summary.results),
            elapsed=time.monotonic() - started,
            failures=failures,
            java_major=java_major,
            asset_objects=asset_count,
        )
        log.info(
            "版本 %s 安装完成：%d 个任务，用时 %.1fs",
            version_id,
            report.task_count,
            report.elapsed,
        )
        return report
