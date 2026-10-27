"""测试公共夹具：临时数据目录、示例版本与示例 Mod。"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from pcl.core.paths import AppPaths
from pcl.launch.java import JavaRuntime


@pytest.fixture()
def paths(tmp_path: Path) -> AppPaths:
    """隔离的数据目录，避免污染用户真实数据。"""
    return AppPaths(tmp_path / "data").ensure()


@pytest.fixture()
def java_runtime() -> JavaRuntime:
    """不依赖真实 Java 安装的运行时描述。"""
    return JavaRuntime(path=Path("/usr/bin/java"), version="17.0.9", major=17, vendor="Temurin")


def write_version(paths: AppPaths, version_id: str, data: dict[str, Any]) -> Path:
    """把版本 JSON 写入临时 versions 目录。"""
    path = paths.version_json(version_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def make_fabric_mod(path: Path, *, mod_id: str = "sodium", name: str = "Sodium", version: str = "0.5.8", depends=None) -> Path:
    """生成一个带 fabric.mod.json 的假 Mod。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "fabric.mod.json",
            json.dumps(
                {
                    "id": mod_id,
                    "name": name,
                    "version": version,
                    "description": "测试模组",
                    "authors": ["tester"],
                    "depends": depends or {},
                }
            ),
        )
    return path


def make_forge_mod(
    path: Path,
    *,
    mod_id: str = "create",
    name: str = "Create",
    version: str = "0.5.1",
    depends: dict[str, str] | None = None,
) -> Path:
    """生成一个带 META-INF/mods.toml 的假 Mod。"""
    lines = [
        'modLoader="javafml"',
        "[[mods]]",
        f'modId="{mod_id}"',
        f'displayName="{name}"',
        f'version="{version}"',
        'description="机械动力"',
    ]
    for dep_id, version_range in (depends or {}).items():
        lines += [
            f"[[dependencies.{mod_id}]]",
            f'modId="{dep_id}"',
            "mandatory=true",
            f'versionRange="{version_range}"',
        ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("META-INF/mods.toml", "\n".join(lines) + "\n")
    return path


def make_resource_pack(path: Path, *, description: str = "经典材质", pack_format: int = 15) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "pack.mcmeta",
            json.dumps({"pack": {"pack_format": pack_format, "description": description}}),
        )
    return path


def make_shader_pack(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("shaders/shader.fsh", "void main() {}")
        archive.writestr("shaders.properties", "sunPathRotation=0\n")
    return path


def sample_version(
    version_id: str = "1.20.1",
    *,
    library_path: str = "org/example/lib/1.0/lib-1.0.jar",
) -> dict[str, Any]:
    """构造一个可用于启动参数测试的最小版本描述。"""
    return {
        "id": version_id,
        "type": "release",
        "mainClass": "net.minecraft.client.main.Main",
        "assets": "5",
        "assetIndex": {"id": "5", "url": "https://example.invalid/5.json", "sha1": "a" * 40, "size": 1},
        "downloads": {"client": {"url": "https://example.invalid/client.jar", "sha1": "b" * 40, "size": 2}},
        "libraries": [
            {
                "name": "org.example:lib:1.0",
                "downloads": {"artifact": {"path": library_path, "url": "https://example.invalid/lib.jar"}},
            }
        ],
        "arguments": {
            "jvm": ["-Djava.library.path=${natives_directory}", "-cp", "${classpath}"],
            "game": [
                "--username",
                "${auth_player_name}",
                "--version",
                "${version_name}",
                "--gameDir",
                "${game_directory}",
                "--assetsDir",
                "${assets_root}",
                "--assetIndex",
                "${assets_index_name}",
                "--uuid",
                "${auth_uuid}",
                "--accessToken",
                "${auth_access_token}",
                "--userType",
                "${user_type}",
                {
                    "rules": [{"action": "allow", "features": {"has_custom_resolution": True}}],
                    "value": ["--width", "${resolution_width}", "--height", "${resolution_height}"],
                },
            ],
        },
    }


def prepare_version_files(paths: AppPaths, doc: dict[str, Any]) -> None:
    """按版本描述创建 jar 与库文件，使 classpath 自检通过。"""
    version_id = str(doc["id"])
    jar = paths.version_jar(version_id)
    jar.parent.mkdir(parents=True, exist_ok=True)
    jar.write_bytes(b"jar")
    for library in doc.get("libraries", []):
        relative = library.get("downloads", {}).get("artifact", {}).get("path")
        if relative:
            target = paths.libraries / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"lib")
