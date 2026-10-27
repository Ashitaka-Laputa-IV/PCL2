"""Fabric / Quilt 安装：直接从 meta 服务获取 profile JSON 并落盘。

这两个加载器的 profile 已包含 ``mainClass``、``arguments`` 与 ``libraries``，
并通过 ``inheritsFrom`` 复用原版文件，因此无需运行安装器进程。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import httpx

from pcl.core.config import DownloadOptions
from pcl.core.errors import LoaderError, NetworkError
from pcl.core.logging import get_logger
from pcl.core.paths import AppPaths
from pcl.net import http
from pcl.net.downloader import DownloadManager
from pcl.version import merge
from pcl.version.installer import VanillaInstaller, is_installed
from pcl.version.loaders.util import library_tasks
from pcl.version.manifest import VersionEntry

log = get_logger(__name__)

FABRIC_META = "https://meta.fabricmc.net"
FABRIC_MAVEN = "https://maven.fabricmc.net/"
QUILT_META = "https://meta.quiltmc.org"
QUILT_MAVEN = "https://maven.quiltmc.org/repository/release/"


@dataclass(frozen=True)
class LoaderVersion:
    """加载器版本条目。"""

    version: str
    stable: bool = True

    @property
    def label(self) -> str:
        return f"{self.version}{'' if self.stable else '（测试版）'}"


class ProfileLoaderInstaller:
    """通过 meta 服务安装 Fabric / Quilt。"""

    def __init__(
        self,
        paths: AppPaths,
        options: DownloadOptions | None = None,
        *,
        kind: str = "fabric",
        client: httpx.Client | None = None,
    ) -> None:
        if kind not in ("fabric", "quilt"):
            raise ValueError(f"不支持的加载器: {kind}")
        self.paths = paths
        self.options = options or DownloadOptions()
        self.kind = kind
        self._client = client

    # ---------- meta 请求 ----------
    @property
    def meta_base(self) -> str:
        return FABRIC_META if self.kind == "fabric" else QUILT_META

    @property
    def default_maven(self) -> str:
        return FABRIC_MAVEN if self.kind == "fabric" else QUILT_MAVEN

    @property
    def api_version(self) -> str:
        return "v2" if self.kind == "fabric" else "v3"

    def _get_json(self, url: str) -> Any:
        owns = self._client is None
        client = self._client or http.create_client(concurrency=8)
        try:
            return http.get_json(client, url, retries=2)
        except NetworkError as error:
            raise LoaderError(f"获取 {self.kind} 版本信息失败。", detail=str(error)) from error
        finally:
            if owns:
                client.close()

    def list_versions(self, mc_version: str) -> list[LoaderVersion]:
        """列出某游戏版本可用的加载器版本。"""
        url = f"{self.meta_base}/{self.api_version}/versions/loader/{mc_version}"
        data = self._get_json(url)
        if not isinstance(data, list):
            raise LoaderError(f"{self.kind} 返回的版本列表格式异常。")
        result: list[LoaderVersion] = []
        for item in data:
            if not isinstance(item, Mapping):
                continue
            loader = item.get("loader")
            if not isinstance(loader, Mapping):
                continue
            version = str(loader.get("version", ""))
            if not version:
                continue
            result.append(LoaderVersion(version=version, stable=bool(loader.get("stable", True))))
        return result

    def list_game_versions(self) -> list[str]:
        """列出支持的 Minecraft 版本。"""
        data = self._get_json(f"{self.meta_base}/{self.api_version}/versions/game")
        if not isinstance(data, list):
            return []
        return [str(item.get("version")) for item in data if isinstance(item, Mapping) and item.get("version")]

    def profile(self, mc_version: str, loader_version: str) -> dict[str, Any]:
        """获取完整的 profile JSON（可直接作为版本 JSON 使用）。"""
        url = f"{self.meta_base}/{self.api_version}/versions/loader/{mc_version}/{loader_version}/profile/json"
        data = self._get_json(url)
        if not isinstance(data, dict) or not data.get("id"):
            raise LoaderError(f"{self.kind} profile 格式异常。")
        return data

    # ---------- 安装 ----------
    def install(
        self,
        mc_version: str,
        loader_version: str,
        *,
        vanilla: VanillaInstaller | None = None,
        vanilla_entry: VersionEntry | None = None,
    ) -> str:
        """安装并返回生成的版本 id。"""
        if not is_installed(self.paths, mc_version):
            if vanilla is None:
                raise LoaderError(f"请先安装原版 {mc_version} 再安装 {self.kind}。")
            vanilla.install(mc_version, vanilla_entry)

        profile = self.profile(mc_version, loader_version)
        version_id = str(profile["id"])
        profile.setdefault("inheritsFrom", mc_version)
        profile.setdefault("type", "release")

        tasks = library_tasks(
            self.paths,
            profile.get("libraries") or [],
            priority=10,
            default_repo=profile.get("url") or self.default_maven,
        )
        if tasks:
            manager = DownloadManager(self.options)
            summary = manager.run(tasks)
            if summary.failures:
                detail = "; ".join(
                    f"{item.task.display_name}: {item.error}" for item in summary.failures[:3]
                )
                raise LoaderError(f"{self.kind} 依赖下载失败。", detail=detail)

        merge.save_version_json(self.paths, version_id, profile)
        log.info("已安装 %s %s（Minecraft %s）：%s", self.kind, loader_version, mc_version, version_id)
        return version_id


def install(
    paths: AppPaths,
    options: DownloadOptions,
    mc_version: str,
    loader_version: str,
    *,
    kind: str = "fabric",
    java_path: str | None = None,  # 预留：profile 安装不需要 Java
    vanilla_entry: VersionEntry | None = None,
) -> str:
    """模块级安装入口（供统一调度调用）。"""
    installer = ProfileLoaderInstaller(paths, options, kind=kind)
    return installer.install(mc_version, loader_version, vanilla_entry=vanilla_entry)
