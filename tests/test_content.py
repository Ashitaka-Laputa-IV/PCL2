"""Mod / 资源包 / 光影解析、启停与冲突检查。"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from pcl.content.conflict import check_conflicts, compare_versions, satisfies
from pcl.content.manager import ContentManager
from pcl.content.mods import parse_mod, scan_mods
from pcl.content.resourcepack import is_shader_pack, parse_pack
from pcl.core.errors import ContentError
from pcl.version.local import LocalVersion
from tests.conftest import make_fabric_mod, make_forge_mod, make_resource_pack, make_shader_pack


def test_parse_fabric_mod(tmp_path) -> None:
    path = make_fabric_mod(tmp_path / "sodium.jar", depends={"minecraft": ">=1.20.1", "fabricloader": ">=0.14"})
    info = parse_mod(path)
    assert info.loader == "fabric"
    assert info.mod_id == "sodium"
    assert info.display_name == "Sodium"
    assert info.version == "0.5.8"
    assert info.enabled is True
    assert {(dep.mod_id, dep.version_range) for dep in info.dependencies} == {
        ("minecraft", ">=1.20.1"),
        ("fabricloader", ">=0.14"),
    }


def test_parse_forge_mod(tmp_path) -> None:
    path = make_forge_mod(tmp_path / "create.jar", depends={"flywheel": "[0.6.9,)"})
    info = parse_mod(path)
    assert info.loader == "forge"
    assert info.mod_id == "create"
    assert info.version == "0.5.1"
    assert info.dependencies[0].mod_id == "flywheel"
    assert info.dependencies[0].mandatory is True


def test_disabled_and_unknown(tmp_path) -> None:
    disabled = make_fabric_mod(tmp_path / "abc.jar.disabled")
    assert parse_mod(disabled).enabled is False

    plain = tmp_path / "plain.jar"
    with zipfile.ZipFile(plain, "w") as archive:
        archive.writestr("readme.txt", "nothing")
    info = parse_mod(plain)
    assert info.error is not None
    assert info.loader == "unknown"

    not_zip = tmp_path / "broken.jar"
    not_zip.write_bytes(b"not a zip")
    assert parse_mod(not_zip).error is not None


def test_scan_mods_sorted(tmp_path) -> None:
    mods_dir = tmp_path / "mods"
    make_fabric_mod(mods_dir / "zeta.jar", mod_id="zeta", name="Zeta")
    make_fabric_mod(mods_dir / "alpha.jar", mod_id="alpha", name="Alpha")
    (mods_dir / "ignore.txt").write_text("x")
    names = [item.display_name for item in scan_mods(mods_dir)]
    assert names == ["Alpha", "Zeta"]


def test_resource_and_shader_pack(tmp_path) -> None:
    pack = make_resource_pack(tmp_path / "Faithful.zip")
    info = parse_pack(pack)
    assert info.display_name == "Faithful"
    assert info.pack_format == 15
    assert info.description == "经典材质"
    assert info.kind == "resourcepack"

    shader = make_shader_pack(tmp_path / "BSL.zip")
    assert is_shader_pack(shader) is True
    shader_info = parse_pack(shader, kind="shader")
    assert shader_info.kind == "shader"
    assert shader_info.display_name == "BSL"


def test_content_manager_toggle_and_delete(paths, tmp_path) -> None:
    version = LocalVersion(id="demo", name="演示", mc_version="1.20.1")
    manager = ContentManager(paths, version)
    mods_dir = manager.directory("mods")
    mods_dir.mkdir(parents=True, exist_ok=True)
    mod = make_fabric_mod(mods_dir / "demo.jar", name="Demo")

    assert [item.display_name for item in manager.mods()] == ["Demo"]

    disabled = ContentManager.toggle(mod)
    assert disabled.name.endswith(".disabled")
    assert manager.mods()[0].enabled is False

    enabled = ContentManager.toggle(disabled)
    assert enabled.name == "demo.jar"
    assert manager.mods()[0].enabled is True

    assert ContentManager.set_enabled(enabled, True) == enabled
    renamed = ContentManager.set_enabled(enabled, False)
    assert renamed.name.endswith(".disabled")
    assert ContentManager.set_enabled(renamed, False) == renamed
    assert ContentManager.set_enabled(renamed, True).name == "demo.jar"

    target = manager.install_files([make_fabric_mod(tmp_path / "other.jar")])
    assert target[0].parent == mods_dir
    ContentManager.delete(target[0])
    assert not target[0].exists()


def test_install_conflict_naming(paths, tmp_path) -> None:
    version = LocalVersion(id="demo2", name="演示2", mc_version="1.20.1")
    manager = ContentManager(paths, version)
    source = make_fabric_mod(tmp_path / "same.jar")
    first = manager.install_files([source])[0]
    second = manager.install_files([source])[0]
    assert first != second
    assert second.name.startswith("same (1)")


def test_detect_kind(paths, tmp_path) -> None:
    version = LocalVersion(id="demo3", name="演示3", mc_version="1.20.1")
    manager = ContentManager(paths, version)
    assert manager.detect_kind(make_fabric_mod(tmp_path / "m.jar")) == "mods"
    assert manager.detect_kind(make_resource_pack(tmp_path / "p.zip")) == "resourcepacks"
    assert manager.detect_kind(make_shader_pack(tmp_path / "s.zip")) == "shaderpacks"
    with pytest.raises(ContentError):
        manager.detect_kind(tmp_path / "unknown.xyz")


def test_version_range_matching() -> None:
    assert satisfies("1.20.1", "[1.20,1.21)") is True
    assert satisfies("1.21", "[1.20,1.21)") is False
    assert satisfies("1.20.1", ">=1.20 <1.21") is True
    assert satisfies("2.0.0", "^1.2.3") is False
    assert satisfies("1.9.9", "^1.2.3") is True
    assert satisfies("1.20.1", "1.20") is True
    assert satisfies("0.5.8", "") is True
    assert compare_versions("1.10", "1.9") == 1
    assert compare_versions("1.0.0", "1") == 0


def test_conflict_report_missing_and_duplicates() -> None:
    from pcl.content.mods import ModDependency, ModInfo

    core = ModInfo(path=Path("/tmp/core.jar"), name="Core", version="1.0.0", mod_id="core", enabled=True)
    consumer = ModInfo(
        path=Path("/tmp/consumer.jar"),
        name="Consumer",
        version="1.0.0",
        mod_id="consumer",
        enabled=True,
        dependencies=[ModDependency("core", "[1.0,2.0)")],
    )
    report = check_conflicts([core, consumer], "1.20.1")
    assert report.ok

    lonely = ModInfo(
        path=Path("/tmp/lonely.jar"),
        name="Lonely",
        version="1.0.0",
        mod_id="lonely",
        enabled=True,
        dependencies=[ModDependency("absent", ">=1.0")],
    )
    report = check_conflicts([lonely], "1.20.1")
    assert not report.ok
    assert report.missing[0][1].mod_id == "absent"
    assert "依赖缺失" in report.summary()

    duplicate = ModInfo(path=Path("/tmp/core2.jar"), name="Core2", version="1.2.0", mod_id="core", enabled=True)
    report = check_conflicts([core, duplicate], "1.20.1")
    assert "core" in report.duplicates

    mismatched = ModInfo(
        path=Path("/tmp/old.jar"),
        name="Old",
        version="1.0.0",
        mod_id="old",
        enabled=True,
        mc_versions=["1.19.2"],
    )
    report = check_conflicts([mismatched], "1.20.1")
    assert report.mc_mismatch
