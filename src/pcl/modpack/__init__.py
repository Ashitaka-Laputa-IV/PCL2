"""整合包：格式探测、四种格式导入与通用导出。"""

from __future__ import annotations

import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from pcl.core.config import DownloadOptions
from pcl.core.errors import ModpackError
from pcl.core.logging import get_logger
from pcl.core.paths import AppPaths
from pcl.instance.manager import InstanceManager
from pcl.instance.model import Instance
from pcl.modpack import curseforge, exporter, hmcl, mrpack, pcl as pcl_pack
from pcl.modpack.base import ParsedModpack, extract_prefixes, safe_join
from pcl.net.downloader import DownloadManager, DownloadTask
from pcl.version import loaders
from pcl.version.installer import VanillaInstaller

log = get_logger(__name__)

FORMATS = ("modrinth", "curseforge", "hmcl", "pcl")
FORMAT_LABELS = {
    "modrinth": "Modrinth",
    "curseforge": "CurseForge",
    "hmcl": "HMCL",
    "pcl": "PCL",
}


@dataclass
class ImportResult:
    """整合包导入结果。"""

    instance: Instance
    kind: str = ""
    downloaded: int = 0
    failed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    elapsed: float = 0.0
    version_id: str = ""

    @property
    def ok(self) -> bool:
        return not self.failed


def detect_format(path: Path) -> str:
    """根据文件名与内部特征识别整合包格式。"""
    if not path.is_file():
        raise ModpackError(f"文件不存在：{path}")
    if path.suffix.lower() == ".mrpack":
        return "modrinth"
    if not zipfile.is_zipfile(path):
        raise ModpackError("该文件不是 zip 格式的整合包。")

    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
    except (OSError, zipfile.BadZipFile) as error:
        raise ModpackError("无法读取整合包。", detail=str(error)) from error

    if "modrinth.index.json" in names:
        return "modrinth"
    if "modpack.json" in names:
        return "hmcl"
    if "PCL/Setup.ini" in names or any(name.lower().endswith("setup.ini") for name in names):
        return "pcl"
    if "manifest.json" in names:
        return "curseforge"
    raise ModpackError("无法识别整合包格式。")


def parse_modpack(
    path: Path,
    *,
    api_key: str = "",
    resolve_curseforge: bool = True,
) -> ParsedModpack:
    """解析整合包并返回统一描述。"""
    kind = detect_format(path)
    if kind == "modrinth":
        return mrpack.parse(path)
    if kind == "curseforge":
        parsed = curseforge.parse(path)
        if resolve_curseforge:
            curseforge.resolve_files(parsed, api_key)
        return parsed
    if kind == "hmcl":
        return hmcl.parse(path)
    return pcl_pack.parse(path)


def _apply_overrides(parsed: ParsedModpack, game_dir: Path) -> int:
    if not parsed.overrides:
        return 0
    try:
        with zipfile.ZipFile(parsed.path) as archive:
            return extract_prefixes(archive, parsed.overrides, game_dir)
    except (OSError, zipfile.BadZipFile) as error:
        raise ModpackError("释放整合包覆盖文件失败。", detail=str(error)) from error


def _install_game_files(
    parsed: ParsedModpack,
    game_dir: Path,
    options: DownloadOptions,
) -> tuple[int, list[str]]:
    if not parsed.files:
        return 0, []
    tasks: list[DownloadTask] = []
    for item in parsed.files:
        try:
            destination = safe_join(game_dir, item.path)
        except ModpackError as error:
            parsed.warnings.append(str(error))
            continue
        tasks.append(
            DownloadTask(
                url=item.url,
                dest=destination,
                sha1=item.sha1,
                size=item.size,
                priority=5,
                name=item.target.name,
                optional=item.optional,
            )
        )

    if not tasks:
        return 0, []
    manager = DownloadManager(options)
    summary = manager.run(tasks)
    failures = [f"{item.task.display_name}: {item.error}" for item in summary.failures]
    succeeded = sum(1 for item in summary.results if item.ok)
    return succeeded, failures


def import_modpack(
    path: Path,
    paths: AppPaths,
    options: DownloadOptions | None = None,
    *,
    instance_manager: InstanceManager | None = None,
    api_key: str = "",
    java_path: str | None = None,
    install_game: bool = True,
    name_override: str = "",
) -> ImportResult:
    """把整合包还原为一个可启动实例。"""
    started = time.monotonic()
    options = options or DownloadOptions()
    manager = instance_manager or InstanceManager(paths)

    parsed = parse_modpack(path, api_key=api_key)
    if not parsed.mc_version:
        raise ModpackError("整合包没有声明游戏版本，无法导入。")

    name = (name_override or parsed.name or path.stem).strip()
    instance = manager.create(
        name,
        parsed.mc_version,
        version_id=parsed.mc_version,
        loader=parsed.loader,
        loader_version=parsed.loader_version,
    )
    game_dir = instance.game_dir(paths)
    game_dir.mkdir(parents=True, exist_ok=True)

    warnings = list(parsed.warnings)
    version_id = parsed.mc_version

    try:
        if install_game and parsed.loader != "vanilla" and parsed.loader_version:
            version_id = loaders.install_loader(
                paths,
                options,
                kind=parsed.loader,
                mc_version=parsed.mc_version,
                loader_version=parsed.loader_version,
                java_path=java_path,
            )
        elif install_game:
            report = VanillaInstaller(paths, options).install(parsed.mc_version)
            version_id = report.version_id
    except Exception as error:
        warnings.append(f"游戏本体安装未完成：{error}")

    instance.version_id = version_id
    manager.save(instance)

    _apply_overrides(parsed, game_dir)
    downloaded, failures = _install_game_files(parsed, game_dir, options)

    result = ImportResult(
        instance=instance,
        kind=parsed.kind,
        downloaded=downloaded,
        failed=failures,
        warnings=warnings,
        elapsed=time.monotonic() - started,
        version_id=version_id,
    )
    log.info(
        "整合包导入完成：%s（%s，%d 个下载文件，%d 个失败）",
        instance.name,
        parsed.kind,
        downloaded,
        len(failures),
    )
    return result


__all__ = [
    "FORMATS",
    "FORMAT_LABELS",
    "ImportResult",
    "ParsedModpack",
    "detect_format",
    "exporter",
    "import_modpack",
    "parse_modpack",
]
