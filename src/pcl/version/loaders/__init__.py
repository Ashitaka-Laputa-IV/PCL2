"""模组加载器统一入口：Fabric / Quilt / Forge / NeoForge。"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pcl.core.config import DownloadOptions
from pcl.core.errors import LoaderError
from pcl.core.paths import AppPaths
from pcl.version.installer import VanillaInstaller
from pcl.version.loaders.fabric import LoaderVersion as MetaLoaderVersion
from pcl.version.loaders.fabric import ProfileLoaderInstaller
from pcl.version.loaders.forge import ForgeInstaller
from pcl.version.loaders.forge import LoaderVersion as ForgeLoaderVersion
from pcl.version.manifest import VersionEntry

LoadVersion = MetaLoaderVersion | ForgeLoaderVersion

LOADERS: tuple[str, ...] = ("vanilla", "fabric", "quilt", "forge", "neoforge")

LOADER_LABELS: dict[str, str] = {
    "vanilla": "原版",
    "fabric": "Fabric",
    "quilt": "Quilt",
    "forge": "Forge",
    "neoforge": "NeoForge",
}


@runtime_checkable
class LoaderInstaller(Protocol):
    """加载器安装器的最小接口。"""

    kind: str

    def list_versions(self, mc_version: str) -> list[Any]: ...

    def install(self, mc_version: str, loader_version: str, **kwargs: Any) -> str: ...


def loader_label(kind: str) -> str:
    """返回加载器中文标签。"""
    return LOADER_LABELS.get(kind, kind)


def create_installer(
    paths: AppPaths,
    options: DownloadOptions | None = None,
    *,
    kind: str = "fabric",
    java_path: str | None = None,
) -> ProfileLoaderInstaller | ForgeInstaller:
    """按加载器类型创建安装器实例。"""
    options = options or DownloadOptions()
    if kind in ("fabric", "quilt"):
        return ProfileLoaderInstaller(paths, options, kind=kind)
    if kind in ("forge", "neoforge"):
        return ForgeInstaller(paths, options, kind=kind, java_path=java_path)
    raise LoaderError(f"不支持的加载器类型：{kind}")


def list_versions(
    paths: AppPaths,
    options: DownloadOptions | None = None,
    *,
    kind: str,
    mc_version: str,
    java_path: str | None = None,
) -> list[LoadVersion]:
    """列出某 Minecraft 版本可用的加载器版本。"""
    installer = create_installer(paths, options, kind=kind, java_path=java_path)
    return installer.list_versions(mc_version)


def install_loader(
    paths: AppPaths,
    options: DownloadOptions,
    *,
    kind: str,
    mc_version: str,
    loader_version: str,
    java_path: str | None = None,
    vanilla_entry: VersionEntry | None = None,
) -> str:
    """统一安装入口：自动先补装原版，再安装加载器，返回版本 id。"""
    if kind not in ("fabric", "quilt", "forge", "neoforge"):
        raise LoaderError(f"不支持的加载器类型：{kind}")

    vanilla = VanillaInstaller(paths, options)
    installer = create_installer(paths, options, kind=kind, java_path=java_path)
    if isinstance(installer, ProfileLoaderInstaller):
        return installer.install(
            mc_version,
            loader_version,
            vanilla=vanilla,
            vanilla_entry=vanilla_entry,
        )
    return installer.install(
        mc_version,
        loader_version,
        vanilla=vanilla,
        vanilla_entry=vanilla_entry,
        java_path=java_path,
    )


def default_version_id(kind: str, mc_version: str, loader_version: str) -> str:
    """预测安装后的版本 id（用于 UI 预展示）。"""
    if kind == "fabric":
        return f"fabric-loader-{loader_version}-{mc_version}"
    if kind == "quilt":
        return f"quilt-loader-{loader_version}-{mc_version}"
    return f"{mc_version}-{kind}-{loader_version}"


__all__ = [
    "LOADERS",
    "LOADER_LABELS",
    "LoaderInstaller",
    "LoadVersion",
    "create_installer",
    "default_version_id",
    "install_loader",
    "list_versions",
    "loader_label",
]
