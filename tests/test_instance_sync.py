"""已安装版本自动登记为实例。"""

from __future__ import annotations

import json
from pathlib import Path

from pcl.instance.manager import InstanceManager


def _write_version(root: Path, version_id: str, document: dict, *, jar: bool = True) -> None:
    directory = root / "versions" / version_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{version_id}.json").write_text(json.dumps(document), encoding="utf-8")
    if jar:
        (directory / f"{version_id}.jar").write_bytes(b"jar")


def _game_root(paths) -> Path:
    return paths.game


def test_sync_registers_versions(paths) -> None:
    root = _game_root(paths)
    _write_version(root, "1.20.1", {"id": "1.20.1", "type": "release"})
    _write_version(
        root,
        "1.20.1-fabric-0.15.7",
        {"id": "1.20.1-fabric-0.15.7", "inheritsFrom": "1.20.1", "mainClass": "net.fabricmc.loader.impl.launch.knot.KnotClient"},
        jar=False,
    )
    # 只有 JSON、没有 jar 也没有继承关系的目录视为不完整，不登记
    _write_version(root, "broken", {"id": "broken"}, jar=False)

    manager = InstanceManager(paths)
    created = manager.sync_installed()
    names = {item.name for item in created}
    assert names == {"1.20.1 原版", "1.20.1 Fabric 0.15.7"}
    assert all(item.auto_version for item in created)
    assert {item.version_id for item in created} == {"1.20.1", "1.20.1-fabric-0.15.7"}
    assert all(item.isolated is False for item in created)

    # 第二次扫描不应重复登记
    assert manager.sync_installed() == []


def test_sync_skips_covered_and_ignored(paths) -> None:
    root = _game_root(paths)
    _write_version(root, "1.20.1", {"id": "1.20.1"})
    _write_version(root, "1.19.2", {"id": "1.19.2"})

    manager = InstanceManager(paths)
    manager.create("手动的 1.20.1", "1.20.1", version_id="1.20.1", isolated=False)
    created = manager.sync_installed(ignored=["1.19.2"])
    assert created == []
    assert {item.version_id for item in manager.list_instances()} == {"1.20.1"}

    created = manager.sync_installed()
    assert [item.version_id for item in created] == ["1.19.2"]


def test_friendly_names_follow_loader(paths) -> None:
    root = _game_root(paths)
    _write_version(root, "1.20.1-forge-47.2.0", {"id": "1.20.1-forge-47.2.0", "inheritsFrom": "1.20.1"}, jar=False)
    manager = InstanceManager(paths)
    created = manager.sync_installed()
    assert created[0].name == "1.20.1 Forge 47.2.0"
    assert created[0].loader == "forge"
    assert created[0].loader_version == "47.2.0"
