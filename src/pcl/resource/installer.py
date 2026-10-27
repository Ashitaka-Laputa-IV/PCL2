"""把 Modrinth 项目安装到游戏版本：主文件 + 必需前置。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pcl.core.config import DownloadOptions
from pcl.core.logging import get_logger
from pcl.version.local import LocalVersion
from pcl.net.downloader import DownloadManager, DownloadTask
from pcl.resource.modrinth import ModrinthClient, ProjectVersion

log = get_logger(__name__)

#: project_type -> 版本内目录
PROJECT_DIRS = {
    "mod": "mods",
    "resourcepack": "resourcepacks",
    "shader": "shaderpacks",
    "modpack": "modpacks",
    "datapack": "datapacks",
}


@dataclass
class ResourceInstallResult:
    """安装结果。"""

    installed: list[Path] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


def target_directory(game_dir: Path, project_type: str) -> Path:
    return game_dir / PROJECT_DIRS.get(project_type, "mods")


def _task_for(game_dir: Path, project_type: str, version: ProjectVersion, name: str = "") -> DownloadTask | None:
    file = version.primary_file()
    if file is None or not file.url or not file.filename:
        return None
    return DownloadTask(
        url=file.url,
        dest=target_directory(game_dir, project_type) / file.filename,
        sha1=file.sha1 or None,
        size=file.size or None,
        priority=10,
        name=name or file.filename,
    )


def install_version(
    version: ProjectVersion,
    project_type: str,
    game_dir: Path,
    *,
    options: DownloadOptions | None = None,
    client: ModrinthClient | None = None,
    game_version: str = "",
    loader: str = "",
    include_dependencies: bool = True,
) -> ResourceInstallResult:
    """下载一个版本及其必需前置，返回安装结果。"""
    options = options or DownloadOptions()
    result = ResourceInstallResult()

    main_task = _task_for(game_dir, project_type, version)
    if main_task is None:
        result.failures.append("该版本没有可下载文件")
        return result
    tasks: list[DownloadTask] = [main_task]

    owns_client = client is None
    client = client or ModrinthClient()
    try:
        if include_dependencies and version.dependencies:
            for dependency in version.dependencies:
                if dependency.dependency_type != "required" or not dependency.project_id:
                    continue
                try:
                    versions = client.versions(
                        dependency.project_id,
                        game_version=game_version,
                        loader=loader if project_type in ("mod", "modpack") else "",
                    )
                except Exception as error:
                    result.failures.append(f"前置 {dependency.project_id} 查询失败：{error}")
                    continue
                if not versions:
                    result.skipped.append(f"前置 {dependency.project_id} 没有匹配版本")
                    continue
                dep_version = versions[0]
                dep_task = _task_for(game_dir, "mod", dep_version, name=f"前置 {dep_version.name}")
                if dep_task is not None:
                    tasks.append(dep_task)
                    result.dependencies.append(dep_version.name or dep_version.id)
    finally:
        if owns_client:
            client.close()

    summary = DownloadManager(options).run(tasks)
    for item in summary.results:
        if item.ok and item.skipped:
            result.skipped.append(item.task.display_name)
        elif item.ok:
            result.installed.append(item.task.dest)
        else:
            result.failures.append(f"{item.task.display_name}: {item.error}")

    log.info(
        "资源安装完成：%d 成功，%d 跳过，%d 失败",
        len(result.installed),
        len(result.skipped),
        len(result.failures),
    )
    return result


def install_to_version(
    project_version: ProjectVersion,
    project_type: str,
    target: LocalVersion,
    paths,
    *,
    options: DownloadOptions | None = None,
    client: ModrinthClient | None = None,
) -> ResourceInstallResult:
    """便捷入口：安装到指定版本的游戏目录。"""
    game_dir = target.game_dir(paths)
    game_dir.mkdir(parents=True, exist_ok=True)
    return install_version(
        project_version,
        project_type,
        game_dir,
        options=options,
        client=client,
        game_version=target.mc_version,
        loader=target.loader if target.loader != "vanilla" else "",
    )
