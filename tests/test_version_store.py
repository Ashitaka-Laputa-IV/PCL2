"""已安装版本自动登记、加载器识别与重名派生。"""

from __future__ import annotations

import json
from pathlib import Path

from pcl.version.store import VersionStore


def _write_version(root: Path, version_id: str, document: dict, *, jar: bool = True) -> None:
    directory = root / "versions" / version_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{version_id}.json").write_text(json.dumps(document), encoding="utf-8")
    if jar:
        (directory / f"{version_id}.jar").write_bytes(b"jar")


def test_auto_register_versions(paths) -> None:
    _write_version(paths.game, "1.20.1", {"id": "1.20.1", "type": "release"})
    _write_version(
        paths.game,
        "1.20.1-fabric-0.15.7",
        {"id": "1.20.1-fabric-0.15.7", "inheritsFrom": "1.20.1", "mainClass": "net.fabricmc.loader.impl.launch.knot.KnotClient"},
        jar=False,
    )
    # 只有 JSON、没有 jar 也没有继承关系的目录视为不完整，不登记
    _write_version(paths.game, "broken", {"id": "broken"}, jar=False)

    store = VersionStore(paths)
    versions = store.list_versions()
    assert {item.name for item in versions} == {"1.20.1 原版", "1.20.1 Fabric 0.15.7"}
    assert {item.id for item in versions} == {"1.20.1", "1.20.1-fabric-0.15.7"}
    assert all(paths.version_meta(item.id).is_file() for item in versions)

    # 再次扫描不应重复登记
    assert len(store.list_versions()) == 2


def test_duplicate_install_gets_suffix(paths) -> None:
    _write_version(paths.game, "1.20.1", {"id": "1.20.1"})
    store = VersionStore(paths)

    first = store.ensure("1.20.1")
    assert first.name == "1.20.1 原版"

    second = store.adopt("1.20.1")
    assert second.id == "1.20.1-1"
    assert second.name == "1.20.1 原版-1"
    assert paths.version_json("1.20.1-1").is_file()
    assert json.loads(paths.version_json("1.20.1-1").read_text(encoding="utf-8"))["inheritsFrom"] == "1.20.1"

    third = store.adopt("1.20.1")
    assert third.name == "1.20.1 原版-2"
    assert {item.name for item in store.list_versions()} == {"1.20.1 原版", "1.20.1 原版-1", "1.20.1 原版-2"}


def test_describe_loader(paths) -> None:
    _write_version(paths.game, "1.20.1", {"id": "1.20.1"})
    _write_version(paths.game, "1.20.1-forge-47.2.0", {"id": "1.20.1-forge-47.2.0", "inheritsFrom": "1.20.1"}, jar=False)

    store = VersionStore(paths)
    version = store.ensure("1.20.1-forge-47.2.0")
    assert version.name == "1.20.1 Forge 47.2.0"
    assert version.mc_version == "1.20.1"
    assert version.loader == "forge"
    assert version.loader_version == "47.2.0"


def test_rename_keeps_unique_names(paths) -> None:
    _write_version(paths.game, "1.20.1", {"id": "1.20.1"})
    _write_version(paths.game, "1.20.1-1", {"id": "1.20.1-1", "inheritsFrom": "1.20.1"}, jar=False)
    store = VersionStore(paths)
    store.list_versions()

    renamed = store.rename("1.20.1-1", "1.20.1 原版")
    assert renamed.name == "1.20.1 原版-1"


def test_migrate_legacy_instance(paths) -> None:
    _write_version(paths.game, "1.20.1", {"id": "1.20.1"})
    legacy = paths.root / "instances" / "demo"
    (legacy / ".minecraft" / "mods").mkdir(parents=True, exist_ok=True)
    (legacy / "instance.json").write_text(
        json.dumps(
            {
                "id": "demo",
                "name": "老实例",
                "mc_version": "1.20.1",
                "version_id": "1.20.1",
                "isolated": True,
                "memory_mb": 4096,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    store = VersionStore(paths)
    migrated = store.migrate_legacy()
    assert [item.name for item in migrated] == ["老实例"]
    version = store.get("1.20.1")
    assert version.memory_mb == 4096
    assert version.game_dir(paths) == legacy / ".minecraft"
    assert not (legacy / "instance.json").exists()
