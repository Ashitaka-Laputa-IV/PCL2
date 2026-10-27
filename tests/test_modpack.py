"""四种整合包的识别、导入与导出。"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from pcl.core.config import DownloadOptions
from pcl.core.errors import ModpackError
from pcl.instance.model import Instance
from pcl.modpack import detect_format, export_modpack, import_modpack, parse_modpack
from tests.conftest import make_fabric_mod


def _make_hmcl(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "modpack.json",
            json.dumps(
                {
                    "manifest": {
                        "type": "minecraftModpack",
                        "version": 1,
                        "name": "HMCL 测试包",
                        "gameVersion": "1.20.1",
                        "loaderType": "fabric",
                        "loaderVersion": "0.15.7",
                    }
                }
            ),
        )
        archive.writestr(".minecraft/config/demo.toml", "key=1")
    return path


def _make_pcl(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("PCL/Setup.ini", "[Setup]\nName=PCL 测试包\nVersion=1.19.2\nLoader=Forge|43.3.0\n")
        archive.writestr(".minecraft/mods/demo.jar", b"x")
    return path


def _make_mrpack(path: Path) -> Path:
    index = {
        "formatVersion": 1,
        "game": "minecraft",
        "versionId": "1.0",
        "name": "Modrinth 测试包",
        "summary": "摘要",
        "files": [
            {
                "path": "mods/example.jar",
                "hashes": {"sha1": "a" * 40},
                "env": {"client": "required"},
                "downloads": ["https://example.invalid/example.jar"],
                "fileSize": 10,
            },
            {
                "path": "mods/server-only.jar",
                "hashes": {"sha1": "b" * 40},
                "env": {"client": "unsupported"},
                "downloads": ["https://example.invalid/server-only.jar"],
            },
        ],
        "dependencies": {"minecraft": "1.21", "neoforge": "21.1.72"},
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("modrinth.index.json", json.dumps(index))
        archive.writestr("overrides/config/a.txt", "1")
        archive.writestr("client-overrides/options.txt", "guiScale:2")
    return path


def _make_curseforge(path: Path) -> Path:
    manifest = {
        "minecraft": {"version": "1.20.1", "modLoaders": [{"id": "forge-47.2.0", "primary": True}]},
        "manifestType": "minecraftModpack",
        "manifestVersion": 1,
        "name": "CF 测试包",
        "version": "1.0.0",
        "author": "tester",
        "files": [{"projectID": 1, "fileID": 2, "required": True}],
        "overrides": "overrides",
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("overrides/config/b.txt", "2")
    return path


def test_detect_formats(tmp_path) -> None:
    assert detect_format(_make_hmcl(tmp_path / "a.zip")) == "hmcl"
    assert detect_format(_make_pcl(tmp_path / "b.zip")) == "pcl"
    assert detect_format(_make_mrpack(tmp_path / "c.mrpack")) == "modrinth"
    assert detect_format(_make_curseforge(tmp_path / "d.zip")) == "curseforge"

    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as archive:
        archive.writestr("readme.txt", "x")
    with pytest.raises(ModpackError):
        detect_format(bad)


def test_parse_hmcl_and_pcl(tmp_path) -> None:
    hmcl = parse_modpack(_make_hmcl(tmp_path / "hmcl.zip"))
    assert (hmcl.kind, hmcl.name, hmcl.mc_version, hmcl.loader, hmcl.loader_version) == (
        "hmcl",
        "HMCL 测试包",
        "1.20.1",
        "fabric",
        "0.15.7",
    )

    pcl = parse_modpack(_make_pcl(tmp_path / "pcl.zip"))
    assert (pcl.kind, pcl.mc_version, pcl.loader, pcl.loader_version) == ("pcl", "1.19.2", "forge", "43.3.0")


def test_parse_mrpack_skips_server_only(tmp_path) -> None:
    parsed = parse_modpack(_make_mrpack(tmp_path / "pack.mrpack"))
    assert parsed.loader == "neoforge"
    assert parsed.mc_version == "1.21"
    assert [item.path for item in parsed.files] == ["mods/example.jar"]
    assert set(parsed.overrides) == {"overrides", "client-overrides"}


def test_parse_curseforge_without_key(tmp_path) -> None:
    parsed = parse_modpack(_make_curseforge(tmp_path / "cf.zip"))
    assert parsed.loader == "forge"
    assert parsed.loader_version == "47.2.0"
    assert parsed.requires_manual is True
    assert parsed.warnings


def test_import_modpack_without_network(paths, tmp_path) -> None:
    result = import_modpack(
        _make_hmcl(tmp_path / "hmcl.zip"),
        paths,
        DownloadOptions(),
        install_game=False,
    )
    assert result.instance.name == "HMCL 测试包"
    assert result.version_id == "1.20.1"
    game_dir = result.instance.game_dir(paths)
    assert (game_dir / "config" / "demo.toml").is_file()
    assert (paths.instance_meta(result.instance.id)).is_file()


def test_export_and_reparse(paths, tmp_path) -> None:
    instance = Instance(id="export", name="导出测试", mc_version="1.20.1", loader="fabric", loader_version="0.15.7")
    game_dir = instance.game_dir(paths)
    (game_dir / "mods").mkdir(parents=True, exist_ok=True)
    make_fabric_mod(game_dir / "mods" / "demo.jar")
    (game_dir / "options.txt").write_text("guiScale:3", encoding="utf-8")

    hmcl_path = export_modpack(instance, paths, tmp_path / "out_hmcl.zip", "hmcl").path
    assert hmcl_path.is_file()
    assert detect_format(hmcl_path) == "hmcl"
    parsed = parse_modpack(hmcl_path)
    assert parsed.name == "导出测试"
    assert parsed.loader == "fabric"

    pcl_path = export_modpack(instance, paths, tmp_path / "out_pcl.zip", "pcl").path
    assert detect_format(pcl_path) == "pcl"
    assert parse_modpack(pcl_path).mc_version == "1.20.1"


def test_export_modrinth_best_effort(paths, tmp_path) -> None:
    instance = Instance(id="export2", name="MR 导出", mc_version="1.20.1")
    game_dir = instance.game_dir(paths)
    (game_dir / "mods").mkdir(parents=True, exist_ok=True)
    make_fabric_mod(game_dir / "mods" / "demo.jar")

    result = export_modpack(instance, paths, tmp_path / "out.mrpack", "modrinth")
    assert result.kind == "modrinth"
    assert result.warnings
    parsed = parse_modpack(result.path)
    assert parsed.name == "MR 导出"
    assert parsed.files and parsed.files[0].path == "mods/demo.jar"
