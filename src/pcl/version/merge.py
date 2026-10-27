"""版本 JSON 的加载与 ``inheritsFrom`` 递归合并。

加载器（Forge / NeoForge / Fabric / Quilt）生成的版本通过 ``inheritsFrom``
指向原版，运行前需要按父 → 子顺序合并库、参数与主类。合并规则与官方启动器一致：
- ``libraries`` 以库坐标为键去重，子版本覆盖父版本；
- ``arguments`` 按 jvm / game 分别拼接，父在前；
- 其余字段子版本优先。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from pcl.core.errors import VersionError, VersionNotFoundError
from pcl.core.logging import get_logger
from pcl.core.paths import AppPaths

log = get_logger(__name__)


def load_version_json(paths: AppPaths, version_id: str) -> dict[str, Any]:
    """读取本地已安装版本的 JSON。"""
    path = paths.version_json(version_id)
    if not path.is_file():
        raise VersionNotFoundError(f"版本 {version_id} 尚未安装。", detail=str(path))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise VersionError(f"版本 {version_id} 的 JSON 无法解析。", detail=str(error)) from error
    if not isinstance(data, dict):
        raise VersionError(f"版本 {version_id} 的 JSON 格式不正确。")
    return data


def save_version_json(paths: AppPaths, version_id: str, data: Mapping[str, Any]) -> Path:
    """原子写入版本 JSON。"""
    path = paths.version_json(version_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
    return path


def version_chain(paths: AppPaths, version_id: str) -> list[str]:
    """返回从子到父的版本 id 链（用于定位客户端 jar）。"""
    chain: list[str] = []
    current = version_id
    seen: set[str] = set()
    while current and current not in seen:
        seen.add(current)
        chain.append(current)
        try:
            data = load_version_json(paths, current)
        except VersionError:
            break
        parent = data.get("inheritsFrom")
        current = str(parent) if parent else ""
    return chain


def _dedupe_libraries(parent: list[Any], child: list[Any]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for item in [*parent, *child]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", ""))
        key = name or f"__anon_{len(order)}"
        if key not in merged:
            order.append(key)
        merged[key] = item  # 子版本在后，自然覆盖同名库
    return [merged[key] for key in order]


def _merge_arguments(
    parent: Mapping[str, Any] | None,
    child: Mapping[str, Any] | None,
) -> dict[str, list[Any]]:
    result: dict[str, list[Any]] = {"game": [], "jvm": []}
    for source in (parent, child):
        if not isinstance(source, Mapping):
            continue
        for key in ("game", "jvm"):
            values = source.get(key)
            if isinstance(values, list):
                result[key].extend(values)
    if not result["game"]:
        del result["game"]
    if not result["jvm"]:
        del result["jvm"]
    return result


def merge_version(parent: Mapping[str, Any], child: Mapping[str, Any]) -> dict[str, Any]:
    """把子版本合并进父版本，返回新字典。"""
    merged: dict[str, Any] = dict(parent)

    for key, value in child.items():
        if key in ("libraries", "arguments", "inheritsFrom", "downloads"):
            continue
        merged[key] = value

    merged["libraries"] = _dedupe_libraries(
        list(parent.get("libraries") or []),
        list(child.get("libraries") or []),
    )

    arguments = _merge_arguments(parent.get("arguments"), child.get("arguments"))
    if arguments:
        merged["arguments"] = arguments
    elif "arguments" in child:
        merged["arguments"] = child["arguments"]

    downloads: dict[str, Any] = {}
    if isinstance(parent.get("downloads"), Mapping):
        downloads.update(parent["downloads"])  # type: ignore[arg-type]
    if isinstance(child.get("downloads"), Mapping):
        downloads.update(child["downloads"])  # type: ignore[arg-type]
    if downloads:
        merged["downloads"] = downloads
    else:
        merged.pop("downloads", None)

    # 旧版本使用 minecraftArguments 字符串，子版本可覆盖
    if "minecraftArguments" in child:
        merged["minecraftArguments"] = child["minecraftArguments"]
    elif "minecraftArguments" not in merged and "arguments" in merged:
        merged.pop("minecraftArguments", None)

    merged["id"] = str(child.get("id") or parent.get("id") or "")
    merged.pop("inheritsFrom", None)
    return merged


def resolve_version(paths: AppPaths, version_id: str, _seen: set[str] | None = None) -> dict[str, Any]:
    """加载并递归合并版本 JSON，得到可直接启动的最终描述。"""
    seen = _seen if _seen is not None else set()
    if version_id in seen:
        raise VersionError(f"版本 {version_id} 存在循环继承。")
    seen.add(version_id)

    data = load_version_json(paths, version_id)
    parent_id = data.get("inheritsFrom")
    if not parent_id:
        return dict(data)

    parent = resolve_version(paths, str(parent_id), seen)
    return merge_version(parent, data)


def client_jar(paths: AppPaths, version_id: str) -> Path | None:
    """定位客户端 jar：自身没有时向上查找继承链。"""
    for candidate in version_chain(paths, version_id):
        jar = paths.version_jar(candidate)
        if jar.is_file():
            return jar
    return None


def installed_versions(paths: AppPaths) -> list[str]:
    """列出本地已安装（存在 JSON）的版本 id。"""
    result: list[str] = []
    if not paths.versions.is_dir():
        return result
    for directory in sorted(paths.versions.iterdir()):
        if (directory / f"{directory.name}.json").is_file():
            result.append(directory.name)
    return result
