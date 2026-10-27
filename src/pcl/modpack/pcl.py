"""PCL 整合包解析：``PCL/Setup.ini`` + ``.minecraft/`` 目录。"""

from __future__ import annotations

import configparser
import io
import zipfile
from pathlib import Path
from typing import Any, Mapping

from pcl.core.errors import ModpackError
from pcl.modpack.base import ParsedModpack

SETUP_PATH = "PCL/Setup.ini"
GAME_PREFIXES = (".minecraft", "minecraft")

LOADER_KEYS = {
    "fabric": "fabric",
    "quilt": "quilt",
    "forge": "forge",
    "neoforge": "neoforge",
    "neoforged": "neoforge",
}


def _parse_ini(text: str) -> dict[str, str]:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str  # 保留原始大小写
    try:
        parser.read_file(io.StringIO(text))
    except configparser.Error:
        # 极少数 PCL 导出缺少 section 头，补一个再试
        try:
            parser.read_file(io.StringIO("[Setup]\n" + text))
        except configparser.Error as error:
            raise ModpackError("Setup.ini 解析失败。", detail=str(error)) from error

    result: dict[str, str] = {}
    for section in parser.sections():
        for key, value in parser.items(section):
            result[f"{section}.{key}".lower()] = value
            result.setdefault(key.lower(), value)
    return result


def _detect_loader(values: Mapping[str, str]) -> tuple[str, str]:
    explicit = (values.get("loader") or values.get("modloader") or values.get("loader.type") or "").lower()
    version = values.get("loaderversion") or values.get("loader.version") or ""

    if explicit:
        for key, kind in LOADER_KEYS.items():
            if explicit.startswith(key):
                if "|" in explicit:
                    _, _, explicit_version = explicit.partition("|")
                    return kind, explicit_version or version
                if ":" in explicit:
                    _, _, explicit_version = explicit.partition(":")
                    return kind, explicit_version or version
                return kind, version

    for key, kind in LOADER_KEYS.items():
        if values.get(f"{key}.version"):
            return kind, values[f"{key}.version"]
    return "vanilla", ""


def parse(path: Path) -> ParsedModpack:
    """解析 PCL 整合包（zip）。"""
    if not zipfile.is_zipfile(path):
        raise ModpackError("该文件不是有效的 PCL 整合包。")

    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        setup_name = SETUP_PATH if SETUP_PATH in names else next(
            (name for name in names if name.lower().endswith("setup.ini")), ""
        )
        if not setup_name:
            raise ModpackError("缺少 PCL/Setup.ini。")
        text = archive.read(setup_name).decode("utf-8-sig", errors="replace")
        overrides = [prefix for prefix in GAME_PREFIXES if any(
            name.startswith(prefix + "/") for name in names
        )]
        if not overrides:
            overrides = ["overrides"]

    values = _parse_ini(text)
    mc_version = values.get("version") or values.get("gameversion") or values.get("mcversion") or ""
    if not mc_version:
        raise ModpackError("Setup.ini 没有声明游戏版本。")

    loader, loader_version = _detect_loader(values)

    return ParsedModpack(
        kind="pcl",
        path=path,
        name=values.get("name") or path.stem,
        mc_version=mc_version,
        loader=loader,
        loader_version=loader_version,
        description=values.get("description", ""),
        version=values.get("version", ""),
        overrides=overrides,
    )


def build_setup_ini(
    name: str,
    mc_version: str,
    loader: str = "vanilla",
    loader_version: str = "",
) -> str:
    """生成 PCL ``Setup.ini`` 内容（导出用）。"""
    lines = ["[Setup]", f"Name={name}", f"Version={mc_version}"]
    if loader and loader != "vanilla":
        lines.append(f"Loader={loader.capitalize()}")
        lines.append(f"LoaderVersion={loader_version}")
    return "\n".join(lines) + "\n"
