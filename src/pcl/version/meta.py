"""版本 JSON 的规则求值与库条目解析。

实现 Minecraft 官方 version.json 语义：
- ``rules`` 的 allow / disallow、``os.name`` / ``os.arch`` / ``os.version`` 正则与 ``features`` 判定；
- ``libraries`` 的 maven 坐标 → 路径转换、artifact / classifiers 选择、natives 分类器；
- ``extract.exclude`` 与所需 Java 主版本。
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from pcl.core.paths import current_arch, current_os_name, current_os_version

#: 版本规则中可能出现的特性开关（默认全部为 False）
FEATURE_KEYS = (
    "is_demo_user",
    "has_custom_resolution",
    "has_quick_plays_support",
    "has_quick_play_singleplayer",
    "has_quick_play_multiplayer",
    "is_quick_play_realms",
)

DEFAULT_FEATURES: dict[str, bool] = {key: False for key in FEATURE_KEYS}

_ARCH_ALIASES = {
    "x86_64": "x64",
    "amd64": "x64",
    "i386": "x86",
    "i686": "x86",
    "aarch64": "arm64",
}


def _match_os(rule_os: Mapping[str, Any], os_name: str, os_arch: str, os_version: str) -> bool:
    name = rule_os.get("name")
    if name and name != os_name:
        return False

    arch = rule_os.get("arch")
    if arch:
        expected = _ARCH_ALIASES.get(arch.lower(), arch.lower())
        actual = _ARCH_ALIASES.get(os_arch.lower(), os_arch.lower())
        if expected != actual:
            return False

    version_pattern = rule_os.get("version")
    if version_pattern:
        try:
            if not re.search(version_pattern, os_version or ""):
                return False
        except re.error:
            return False
    return True


def _match_features(rule_features: Mapping[str, Any], features: Mapping[str, bool]) -> bool:
    for key, expected in rule_features.items():
        if bool(features.get(key, False)) != bool(expected):
            return False
    return True


def _rule_matches(
    rule: Mapping[str, Any],
    features: Mapping[str, bool],
    os_name: str,
    os_arch: str,
    os_version: str,
) -> bool:
    rule_os = rule.get("os")
    if rule_os and not _match_os(rule_os, os_name, os_arch, os_version):
        return False
    rule_features = rule.get("features")
    if rule_features and not _match_features(rule_features, features):
        return False
    return True


def evaluate_rules(
    rules: Iterable[Mapping[str, Any]] | None,
    *,
    features: Mapping[str, bool] | None = None,
    os_name: str | None = None,
    os_arch: str | None = None,
    os_version: str | None = None,
) -> bool:
    """判断带 ``rules`` 的条目是否适用于当前环境。

    语义与 HMCL / 官方启动器一致：无 ``rules`` 视为始终适用；有 ``rules`` 时
    依次匹配，**第一条命中的规则**决定 allow / disallow，全部不命中则不允许。
    """
    if rules is None:
        return True
    rules = list(rules)
    if not rules:
        return True

    features = features or DEFAULT_FEATURES
    os_name = os_name or current_os_name()
    os_arch = os_arch or current_arch()
    os_version = os_version or current_os_version()

    for rule in rules:
        if not isinstance(rule, Mapping):
            continue
        if _rule_matches(rule, features, os_name, os_arch, os_version):
            return rule.get("action", "allow") == "allow"
    return False


def library_allowed(
    library: Mapping[str, Any],
    features: Mapping[str, bool] | None = None,
    *,
    os_name: str | None = None,
    os_arch: str | None = None,
    os_version: str | None = None,
) -> bool:
    """判断库条目是否适用于指定平台（默认当前平台）。"""
    return evaluate_rules(
        library.get("rules"),
        features=features,
        os_name=os_name,
        os_arch=os_arch,
        os_version=os_version,
    )


def maven_path(name: str) -> str | None:
    """把 maven 坐标转换为仓库内相对路径。

    ``org.lwjgl:lwjgl:3.3.1`` -> ``org/lwjgl/lwjgl/3.3.1/lwjgl-3.3.1.jar``
    ``group:artifact:version:classifier@ext`` 同时支持分类器与扩展名。
    """
    if not name or ":" not in name:
        return None

    extension = "jar"
    body = name
    if "@" in body:
        body, extension = body.rsplit("@", 1)

    parts = body.split(":")
    if len(parts) == 3:
        group, artifact, version = parts
        classifier = ""
    elif len(parts) >= 4:
        group, artifact, version, classifier = parts[0], parts[1], parts[2], parts[3]
    else:
        return None

    filename = f"{artifact}-{version}"
    if classifier:
        filename += f"-{classifier}"
    filename += f".{extension}"
    return "/".join((*group.split("."), artifact, version, filename))


def library_artifact(library: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """返回库的主 artifact 下载信息（无则返回 ``None``）。"""
    downloads = library.get("downloads")
    if isinstance(downloads, Mapping):
        artifact = downloads.get("artifact")
        if isinstance(artifact, Mapping):
            return artifact
    return None


def library_target_path(library: Mapping[str, Any]) -> str | None:
    """返回库文件在 ``libraries/`` 下的相对路径。"""
    artifact = library_artifact(library)
    if artifact and artifact.get("path"):
        return str(artifact["path"])
    return maven_path(str(library.get("name", "")))


def library_url(library: Mapping[str, Any]) -> str | None:
    """返回库文件下载地址（优先 downloads.artifact，其次拼接仓库地址）。"""
    artifact = library_artifact(library)
    if artifact and artifact.get("url"):
        return str(artifact["url"])
    path = library_target_path(library)
    base = library.get("url")
    if path and base:
        return f"{str(base).rstrip('/')}/{path}"
    return None


def native_classifier(library: Mapping[str, Any], os_name: str | None = None) -> str | None:
    """返回 natives 分类器名，如 ``natives-linux``。"""
    natives = library.get("natives")
    if not isinstance(natives, Mapping):
        return None
    classifier = natives.get(os_name or current_os_name())
    if not classifier:
        return None
    bits = "64" if current_arch() == "x64" else "32"
    return str(classifier).replace("${arch}", bits)


def native_download(library: Mapping[str, Any], os_name: str | None = None) -> Mapping[str, Any] | None:
    """返回当前平台对应的 natives 压缩包下载信息。"""
    classifier = native_classifier(library, os_name)
    if not classifier:
        return None
    downloads = library.get("downloads")
    if isinstance(downloads, Mapping):
        classifiers = downloads.get("classifiers")
        if isinstance(classifiers, Mapping):
            entry = classifiers.get(classifier)
            if isinstance(entry, Mapping):
                return entry
    return None


def extract_excludes(library: Mapping[str, Any]) -> list[str]:
    """返回解压时需要排除的前缀（通常是 ``META-INF/``）。"""
    extract = library.get("extract")
    if isinstance(extract, Mapping):
        excludes = extract.get("exclude")
        if isinstance(excludes, list):
            return [str(item) for item in excludes]
    return []


def required_java_major(version_json: Mapping[str, Any], default: int = 8) -> int:
    """返回该版本建议使用的 Java 主版本。"""
    java_version = version_json.get("javaVersion")
    if isinstance(java_version, Mapping):
        major = java_version.get("majorVersion")
        if isinstance(major, int):
            return major
        if isinstance(major, str) and major.isdigit():
            return int(major)
    return default


def is_snapshot(version_json: Mapping[str, Any]) -> bool:
    return str(version_json.get("type", "")).lower() in ("snapshot", "pending")


def format_release_time(value: str | None) -> str:
    """把 ISO 时间压缩为 ``YYYY-MM-DD``。"""
    if not value:
        return ""
    return value.split("T")[0]


def version_type_label(version_type: str) -> str:
    """版本类型的中文标签。"""
    return {
        "release": "正式版",
        "snapshot": "快照",
        "old_beta": "远古 Beta",
        "old_alpha": "远古 Alpha",
        "pending": "待发布",
    }.get(version_type, version_type or "未知")
