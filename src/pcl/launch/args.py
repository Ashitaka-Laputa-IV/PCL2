"""启动命令拼装：classpath 顺序、占位符替换与 JVM / 游戏参数。

实现要点：
- classpath = 各库 artifact（按版本 JSON 顺序）+ 客户端 jar（自身或继承链上的）；
- 同时支持新版 ``arguments`` 对象与旧版 ``minecraftArguments`` 字符串；
- 条件参数（``{"rules":..., "value": [...]}``）按当前环境与特性开关展开。
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from pcl import LAUNCHER_NAME, LAUNCHER_VERSION
from pcl.core.config import LaunchOptions
from pcl.core.errors import LaunchError, VersionError
from pcl.core.logging import get_logger
from pcl.core.paths import AppPaths, classpath_separator
from pcl.launch.auth import Account
from pcl.launch.java import JavaRuntime
from pcl.version import meta
from pcl.version.merge import client_jar, resolve_version

log = get_logger(__name__)

DEFAULT_JVM_ARGS = [
    "-Djava.library.path=${natives_directory}",
    "-Dminecraft.launcher.brand=${launcher_name}",
    "-Dminecraft.launcher.version=${launcher_version}",
    "-cp",
    "${classpath}",
]


@dataclass
class LaunchPlan:
    """一次启动的完整执行计划。"""

    command: list[str] = field(default_factory=list)
    game_dir: Path = Path(".")
    natives_dir: Path = Path(".")
    version_id: str = ""
    assets_dir: Path | None = None
    asset_index: str = ""
    java: JavaRuntime | None = None
    env: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def display_command(self) -> str:
        return " ".join(shlex.quote(part) for part in self.command)


def build_features(options: LaunchOptions) -> dict[str, bool]:
    """根据启动设置生成版本规则中的 features 开关。"""
    features = {key: False for key in meta.FEATURE_KEYS}
    features["has_custom_resolution"] = True
    features["is_demo_user"] = False
    return features


def _flatten_arguments(
    raw: Iterable[Any],
    features: Mapping[str, bool],
    variables: Mapping[str, str],
) -> list[str]:
    """展开 ``arguments`` 列表中的字符串与条件对象。"""
    result: list[str] = []
    for item in raw:
        if isinstance(item, str):
            result.append(_substitute(item, variables))
            continue
        if isinstance(item, Mapping):
            if not meta.evaluate_rules(item.get("rules"), features=features):
                continue
            value = item.get("value")
            if isinstance(value, str):
                result.append(_substitute(value, variables))
            elif isinstance(value, list):
                result.extend(_substitute(str(entry), variables) for entry in value)
    return result


def _substitute(text: str, variables: Mapping[str, str]) -> str:
    for key, value in variables.items():
        token = "${" + key + "}"
        if token in text:
            text = text.replace(token, value)
    return text


def build_classpath(paths: AppPaths, version_doc: Mapping[str, Any], features: Mapping[str, bool]) -> list[str]:
    """按官方顺序拼装 classpath 条目。"""
    entries: list[str] = []
    for library in version_doc.get("libraries") or []:
        if not isinstance(library, Mapping):
            continue
        if not meta.library_allowed(library, features):
            continue
        relative = meta.library_target_path(library)
        if not relative:
            continue
        path = paths.libraries / relative
        if path.is_file():
            entries.append(str(path))
        else:
            log.debug("classpath 中缺少库文件：%s", path)

    version_id = str(version_doc.get("id", ""))
    jar = paths.version_jar(version_id) if version_id else None
    if not (jar and jar.is_file()):
        jar = client_jar(paths, version_id) if version_id else None
    if jar is not None and jar.is_file():
        entries.append(str(jar))
    return entries


def build_plan(
    paths: AppPaths,
    version_id: str,
    account: Account,
    options: LaunchOptions,
    java: JavaRuntime,
    *,
    game_dir: Path | None = None,
    assets_dir: Path | None = None,
    natives_dir: Path | None = None,
    version_doc: Mapping[str, Any] | None = None,
    extra_jvm_args: Iterable[str] = (),
    extra_game_args: Iterable[str] = (),
) -> LaunchPlan:
    """生成启动计划（不启动进程）。"""
    doc = dict(version_doc) if version_doc is not None else resolve_version(paths, version_id)
    if not doc.get("mainClass"):
        raise VersionError(f"版本 {version_id} 缺少 mainClass，无法启动。")

    features = build_features(options)
    game_dir = Path(game_dir) if game_dir else paths.game
    assets_dir = Path(assets_dir) if assets_dir else paths.assets
    natives_dir = Path(natives_dir) if natives_dir else paths.cache / "natives" / version_id
    natives_dir.mkdir(parents=True, exist_ok=True)

    asset_index = str(doc.get("assets") or doc.get("assetIndex", {}).get("id") or "legacy")
    if isinstance(doc.get("assetIndex"), Mapping):
        asset_index = str(doc["assetIndex"].get("id") or asset_index)

    classpath = build_classpath(paths, doc, features)
    if not classpath:
        raise VersionError(f"版本 {version_id} 的依赖库不完整，请重新安装。")

    variables: dict[str, str] = {
        "auth_player_name": account.name,
        "auth_uuid": account.uuid_compact,
        "auth_access_token": account.access_token,
        "auth_session": f"token:{account.access_token}:{account.uuid_compact}",
        "auth_xuid": "0",
        "clientid": "",
        "user_type": "legacy",
        "user_properties": "{}",
        "version_name": version_id,
        "version_type": str(doc.get("type", "release")),
        "game_directory": str(game_dir),
        "assets_root": str(assets_dir),
        "game_assets": str(paths.asset_virtual / asset_index),
        "assets_index_name": asset_index,
        "natives_directory": str(natives_dir),
        "launcher_name": LAUNCHER_NAME,
        "launcher_version": LAUNCHER_VERSION,
        "classpath": classpath_separator().join(classpath),
        "resolution_width": str(options.window_width),
        "resolution_height": str(options.window_height),
    }

    arguments = doc.get("arguments")
    if isinstance(arguments, Mapping):
        jvm_args = _flatten_arguments(arguments.get("jvm") or [], features, variables)
        game_args = _flatten_arguments(arguments.get("game") or [], features, variables)
    else:
        jvm_args = []
        legacy = str(doc.get("minecraftArguments") or "")
        game_args = [_substitute(item, variables) for item in shlex.split(legacy)]

    if not jvm_args:
        jvm_args = [_substitute(item, variables) for item in DEFAULT_JVM_ARGS]

    if "-cp" not in jvm_args and "-classpath" not in jvm_args:
        jvm_args.extend(["-cp", variables["classpath"]])

    memory = max(512, int(options.memory_mb))
    jvm_args.insert(0, f"-Xmx{memory}m")
    jvm_args.insert(1, f"-Xms{min(memory, 512)}m")

    jvm_args.extend(str(item) for item in extra_jvm_args if item)
    jvm_args.extend(options.jvm_args)

    if options.fullscreen:
        if "--fullscreen" not in game_args:
            game_args.append("--fullscreen")
    elif "--width" not in game_args:
        game_args.extend(
            ["--width", str(options.window_width), "--height", str(options.window_height)]
        )

    game_args.extend(str(item) for item in extra_game_args if item)
    game_args.extend(options.game_args)

    command = [str(java.path), *jvm_args, str(doc["mainClass"]), *game_args]

    warnings: list[str] = []
    if not (assets_dir / "indexes" / f"{asset_index}.json").is_file():
        warnings.append(f"缺少资源索引 {asset_index}.json，游戏内可能出现无声音/无语言的问题。")

    return LaunchPlan(
        command=command,
        game_dir=game_dir,
        natives_dir=natives_dir,
        version_id=version_id,
        assets_dir=assets_dir,
        asset_index=asset_index,
        java=java,
        warnings=warnings,
    )


def log_config_argument(doc: Mapping[str, Any], paths: AppPaths) -> str | None:
    """返回 log4j 配置文件的 JVM 参数（若版本声明了 logging.client）。"""
    logging_conf = doc.get("logging")
    if not isinstance(logging_conf, Mapping):
        return None
    client = logging_conf.get("client")
    if not isinstance(client, Mapping):
        return None
    argument = client.get("argument")
    file_info = client.get("file")
    if not isinstance(argument, str) or not isinstance(file_info, Mapping):
        return None
    file_id = str(file_info.get("id") or "")
    if not file_id:
        return None
    target = paths.assets / "log_configs" / file_id
    if not target.is_file():
        return None
    return argument.replace("${path}", str(target))


def java_major_hint(version_doc: Mapping[str, Any]) -> int:
    """版本建议的 Java 主版本。"""
    return meta.required_java_major(version_doc)


def ensure_launchable(paths: AppPaths, version_id: str) -> None:
    """启动前的快速自检，抛出带有可读原因的异常。"""
    try:
        doc = resolve_version(paths, version_id)
    except VersionError:
        raise
    if not doc.get("mainClass"):
        raise LaunchError(f"版本 {version_id} 缺少 mainClass，可能安装不完整。")
    if not build_classpath(paths, doc, {}):
        raise LaunchError(f"版本 {version_id} 的库文件缺失，请重新安装。")
