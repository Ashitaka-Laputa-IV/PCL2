"""整合包解析的公共数据结构与安全释放工具。"""

from __future__ import annotations

import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from pcl.core.errors import ModpackError
from pcl.core.logging import get_logger

log = get_logger(__name__)


@dataclass
class PackFile:
    """整合包声明需要下载的文件。"""

    url: str
    path: str  # 相对于游戏目录
    sha1: str | None = None
    size: int | None = None
    optional: bool = False

    @property
    def target(self) -> PurePosixPath:
        return PurePosixPath(self.path)


@dataclass
class ParsedModpack:
    """任意格式整合包解析后的统一描述。"""

    kind: str
    path: Path
    name: str
    mc_version: str
    loader: str = "vanilla"
    loader_version: str = ""
    description: str = ""
    version: str = ""
    files: list[PackFile] = field(default_factory=list)
    overrides: list[str] = field(default_factory=list)  # zip 内需要释放的目录前缀
    warnings: list[str] = field(default_factory=list)
    requires_manual: bool = False

    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def download_bytes(self) -> int:
        return sum(item.size or 0 for item in self.files)


def safe_join(root: Path, relative: str) -> Path:
    """把 zip 内路径安全地拼接到目标目录，阻止路径穿越。"""
    rel = PurePosixPath(relative.replace("\\", "/"))
    if rel.is_absolute() or ".." in rel.parts:
        raise ModpackError(f"整合包内存在非法路径：{relative}")
    return root.joinpath(*rel.parts)


def extract_prefixes(archive: zipfile.ZipFile, prefixes: list[str], target_root: Path) -> int:
    """把指定前缀目录的内容释放到目标目录（去掉前缀）。"""
    count = 0
    for info in archive.infolist():
        if info.is_dir():
            continue
        name = info.filename.replace("\\", "/")
        for prefix in prefixes:
            normalized = prefix.rstrip("/") + "/"
            if not name.startswith(normalized):
                continue
            relative = name[len(normalized) :]
            if not relative:
                continue
            destination = safe_join(target_root, relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                with archive.open(info) as source, destination.open("wb") as output:
                    shutil.copyfileobj(source, output)
                count += 1
            except OSError as error:
                log.warning("释放整合包文件失败 %s: %s", relative, error)
            break
    return count


def read_json_member(archive: zipfile.ZipFile, name: str) -> str:
    """读取 zip 内的文本成员。"""
    try:
        return archive.read(name).decode("utf-8-sig", errors="replace")
    except KeyError as error:
        raise ModpackError(f"整合包缺少 {name}。") from error
