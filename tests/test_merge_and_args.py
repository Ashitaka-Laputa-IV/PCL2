"""版本继承合并与启动命令拼装。"""

from __future__ import annotations

import pytest

from pcl.core.config import LaunchOptions
from pcl.core.errors import LaunchError, VersionNotFoundError
from pcl.launch.args import build_plan, ensure_launchable
from pcl.launch.auth import Account
from pcl.version import merge
from tests.conftest import prepare_version_files, sample_version, write_version


def test_merge_libraries_and_arguments(paths) -> None:
    parent = sample_version()
    child = {
        "id": "1.20.1-test-loader",
        "inheritsFrom": "1.20.1",
        "mainClass": "loader.Bootstrap",
        "libraries": [
            {"name": "org.example:lib:1.0", "downloads": {"artifact": {"path": "org/example/lib/1.0/lib-1.0.jar", "url": "https://child"}}},
            {"name": "org.example:extra:2.0"},
        ],
        "arguments": {"game": ["--launchTarget", "test"]},
    }
    write_version(paths, "1.20.1", parent)
    write_version(paths, child["id"], child)

    merged = merge.resolve_version(paths, child["id"])
    assert merged["id"] == "1.20.1-test-loader"
    assert merged["mainClass"] == "loader.Bootstrap"
    assert [item["name"] for item in merged["libraries"]] == ["org.example:lib:1.0", "org.example:extra:2.0"]
    # 子版本覆盖同坐标库
    assert merged["libraries"][0]["downloads"]["artifact"]["url"] == "https://child"
    assert merged["arguments"]["game"][-2:] == ["--launchTarget", "test"]
    assert merged["arguments"]["jvm"][0].startswith("-Djava.library.path=")
    assert "inheritsFrom" not in merged
    assert merge.version_chain(paths, child["id"]) == [child["id"], "1.20.1"]


def test_resolve_missing_parent(paths) -> None:
    write_version(paths, "child", {"id": "child", "inheritsFrom": "missing", "mainClass": "x"})
    with pytest.raises(VersionNotFoundError):
        merge.resolve_version(paths, "child")


def test_client_jar_falls_back_to_parent(paths) -> None:
    write_version(paths, "1.20.1", sample_version())
    write_version(paths, "1.20.1-brain", {"id": "1.20.1-brain", "inheritsFrom": "1.20.1", "mainClass": "x"})
    jar = paths.version_jar("1.20.1")
    jar.parent.mkdir(parents=True, exist_ok=True)
    jar.write_bytes(b"jar")
    assert merge.client_jar(paths, "1.20.1-brain") == jar


def test_build_plan_arguments(paths, java_runtime) -> None:
    doc = sample_version()
    write_version(paths, "1.20.1", doc)
    prepare_version_files(paths, doc)

    account = Account(name="Steve")
    options = LaunchOptions(memory_mb=4096, window_width=1280, window_height=720)
    plan = build_plan(paths, "1.20.1", account, options, java_runtime, game_dir=paths.root / "game")

    command = plan.command
    assert command[0] == str(java_runtime.path)
    assert "-Xmx4096m" in command
    assert "-Xms512m" in command
    assert "-cp" in command
    classpath = command[command.index("-cp") + 1]
    assert classpath.count(":") == 1  # 库 + 客户端 jar
    assert str(paths.version_jar("1.20.1")) in classpath
    assert "net.minecraft.client.main.Main" in command
    assert account.uuid_compact in command
    assert "--width" in command and "1280" in command
    assert str(paths.root / "game") in command
    assert command[command.index("--accessToken") + 1] == account.access_token


def test_build_plan_legacy_minecraft_arguments(paths, java_runtime) -> None:
    doc = {
        "id": "1.7.10",
        "type": "release",
        "mainClass": "net.minecraft.client.main.Main",
        "minecraftArguments": "--username ${auth_player_name} --gameDir ${game_directory} --version ${version_name}",
        "libraries": [],
    }
    write_version(paths, "1.7.10", doc)
    jar = paths.version_jar("1.7.10")
    jar.parent.mkdir(parents=True, exist_ok=True)
    jar.write_bytes(b"jar")

    plan = build_plan(paths, "1.7.10", Account(name="Alex"), LaunchOptions(), java_runtime)
    assert "--username" in plan.command and "Alex" in plan.command
    assert "-cp" in plan.command
    assert any("java.library.path" in item for item in plan.command)


def test_build_plan_requires_libraries(paths, java_runtime) -> None:
    write_version(paths, "broken", {"id": "broken", "mainClass": "x", "libraries": []})
    with pytest.raises(Exception):
        build_plan(paths, "broken", Account(name="A"), LaunchOptions(), java_runtime)


def test_ensure_launchable(paths) -> None:
    doc = sample_version()
    write_version(paths, doc["id"], doc)
    prepare_version_files(paths, doc)
    ensure_launchable(paths, doc["id"])

    write_version(paths, "no-main", {"id": "no-main", "libraries": []})
    with pytest.raises(LaunchError):
        ensure_launchable(paths, "no-main")
