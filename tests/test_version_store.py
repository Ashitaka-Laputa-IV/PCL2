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


def test_new_version_is_isolated(paths) -> None:
    _write_version(paths.game, "1.20.1", {"id": "1.20.1"})
    store = VersionStore(paths)
    version = store.list_versions()[0]

    assert version.isolated is True
    assert version.game_dir(paths) == paths.version_dir("1.20.1")
    for name in ("mods", "resourcepacks", "shaderpacks", "saves", "config"):
        assert (paths.version_dir("1.20.1") / name).is_dir()


def test_upgrade_isolation_moves_shared_content(paths) -> None:
    _write_version(paths.game, "1.20.1", {"id": "1.20.1"})
    _write_version(paths.game, "1.21.5", {"id": "1.21.5"})
    # 模拟旧版本写下的 pcl.json：没有 isolated 字段，使用共享目录
    (paths.version_meta("1.20.1")).write_text(
        json.dumps({"id": "1.20.1", "name": "旧 1.20.1", "last_played_at": 10}), encoding="utf-8"
    )
    (paths.version_meta("1.21.5")).write_text(
        json.dumps({"id": "1.21.5", "name": "旧 1.21.5", "last_played_at": 30}), encoding="utf-8"
    )
    shared_save = paths.game / "saves" / "world" / "level.dat"
    shared_save.parent.mkdir(parents=True, exist_ok=True)
    shared_save.write_bytes(b"level")
    (paths.game / "options.txt").write_text("guiScale:2", encoding="utf-8")

    store = VersionStore(paths)
    upgraded = store.upgrade_isolation()
    assert {item.id for item in upgraded} == {"1.20.1", "1.21.5"}

    # 共享内容进入最近游玩的那个版本，另一个从空目录开始
    target = paths.version_dir("1.21.5")
    assert (target / "saves" / "world" / "level.dat").is_file()
    assert (target / "options.txt").is_file()
    assert not (paths.game / "saves" / "world" / "level.dat").exists()
    assert (paths.version_dir("1.20.1") / "saves").is_dir()
    assert not any((paths.version_dir("1.20.1") / "saves").iterdir())

    assert all(item.isolated for item in store.list_versions())
    # 只升级一次
    assert store.upgrade_isolation() == []


def test_normalize_legacy_layout(paths) -> None:
    _write_version(paths.game, "1.20.1-forge-47.4.10", {"id": "1.20.1-forge-47.4.10", "inheritsFrom": "1.20.1"}, jar=False)
    version_dir = paths.version_dir("1.20.1-forge-47.4.10")
    legacy = version_dir / ".minecraft"
    (legacy / "mods").mkdir(parents=True, exist_ok=True)
    (legacy / "mods" / "demo.jar").write_bytes(b"mod")
    (version_dir / "pcl.json").write_text(
        json.dumps(
            {
                "id": "1.20.1-forge-47.4.10",
                "name": "整合包版本",
                "loader": "forge",
                "isolated": True,
                "game_root": str(legacy),
            }
        ),
        encoding="utf-8",
    )

    store = VersionStore(paths)
    changed = store.normalize_layout()
    assert [item.id for item in changed] == ["1.20.1-forge-47.4.10"]
    version = store.get("1.20.1-forge-47.4.10")
    assert version.game_root == ""
    assert version.game_dir(paths) == version_dir
    assert (version_dir / "mods" / "demo.jar").is_file()
    assert not legacy.exists()


def test_rename_keeps_unique_names(paths) -> None:
    _write_version(paths.game, "1.20.1", {"id": "1.20.1"})
    _write_version(paths.game, "1.20.1-1", {"id": "1.20.1-1", "inheritsFrom": "1.20.1"}, jar=False)
    store = VersionStore(paths)
    store.list_versions()

    renamed = store.rename("1.20.1-1", "1.20.1 原版")
    assert renamed.name == "1.20.1 原版-1"


def _legacy_instance(paths, folder: str, name: str, version_id: str, *, played: float = 0) -> None:
    directory = paths.root / "instances" / folder
    (directory / ".minecraft" / "mods").mkdir(parents=True, exist_ok=True)
    (directory / "instance.json").write_text(
        json.dumps(
            {
                "name": name,
                "mc_version": "1.20.1",
                "version_id": version_id,
                "isolated": True,
                "last_played_at": played,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_migrate_multiple_instances_of_same_version(paths) -> None:
    _write_version(paths.game, "1.20.1", {"id": "1.20.1"})
    _write_version(paths.game, "1.20.1-fabric-0.15.7", {"id": "1.20.1-fabric-0.15.7", "inheritsFrom": "1.20.1"}, jar=False)
    _legacy_instance(paths, "fabric-a", "整合包 A", "1.20.1-fabric-0.15.7", played=10)
    _legacy_instance(paths, "fabric-b", "整合包 B", "1.20.1-fabric-0.15.7", played=20)
    _legacy_instance(paths, "vanilla", "原版生存", "1.20.1", played=30)

    store = VersionStore(paths)
    store.migrate_legacy()
    versions = {item.name: item for item in store.list_versions()}
    assert set(versions) == {"整合包 A", "整合包 B", "原版生存"}
    assert versions["整合包 A"].game_dir(paths) == paths.root / "instances" / "fabric-a" / ".minecraft"
    assert versions["整合包 B"].game_dir(paths) == paths.root / "instances" / "fabric-b" / ".minecraft"
    assert versions["原版生存"].game_dir(paths) == paths.root / "instances" / "vanilla" / ".minecraft"
    assert versions["整合包 B"].last_played_at == 20

    # 迁移可重复执行：不会再次派生副本
    store.migrate_legacy()
    assert len(store.list_versions()) == 3


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
