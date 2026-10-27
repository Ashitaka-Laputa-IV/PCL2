"""Forge 处理器的变量替换：确保 {BINPATCH} 这类数据令牌被正确解析。"""

from __future__ import annotations

import json
import subprocess
import zipfile
from pathlib import Path

import pytest

from pcl.core.config import DownloadOptions
from pcl.core.errors import LoaderError
from pcl.version.loaders.forge import ForgeInstaller, _version_at_least


def _make_library(path: Path, main_class: str = "net.minecraftforge.binarypatcher.ConsoleTool") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("META-INF/MANIFEST.MF", f"Manifest-Version: 1.0\nMain-Class: {main_class}\n")


def _make_installer(target: Path) -> None:
    profile = {
        "spec": 1,
        "minecraft": "1.20.1",
        "version": "1.20.1-forge-47.2.0",
        "json": "/version.json",
        "path": "net.minecraftforge:forge:1.20.1-47.2.0",
        "libraries": [],
        "processors": [
            {
                "sides": ["client"],
                "jar": "net.minecraftforge:binarypatcher:1.3.4",
                "classpath": [],
                "args": ["--task", "BINPATCH", "--output", "{BINPATCH}", "--jar", "{MINECRAFT_JAR}"],
            }
        ],
        "data": {
            "BINPATCH": {"client": "/data/client.lzma", "server": "/data/server.lzma"},
            "MAPPINGS": {"client": "[net.minecraft:client:1.20.1:mappings@txt]"},
        },
    }
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("install_profile.json", json.dumps(profile))
        archive.writestr("version.json", json.dumps({"id": "1.20.1-forge-47.2.0", "inheritsFrom": "1.20.1"}))
        archive.writestr("data/client.lzma", b"patch")


def test_processor_args_resolve_data_tokens(paths: Path, tmp_path, monkeypatch) -> None:
    installer_path = tmp_path / "installer.jar"
    _make_installer(installer_path)

    jar = paths.libraries / "net/minecraftforge/binarypatcher/1.3.4/binarypatcher-1.3.4.jar"
    _make_library(jar)

    client_jar = paths.version_jar("1.20.1")
    client_jar.parent.mkdir(parents=True, exist_ok=True)
    client_jar.write_bytes(b"client")

    captured: dict[str, list[str]] = {}

    class _Runtime:
        path = Path("/usr/bin/java")
        version = "17.0.9"
        major = 17

    monkeypatch.setattr("pcl.version.loaders.forge.probe_java", lambda _path: _Runtime())
    monkeypatch.setattr("pcl.version.loaders.forge.find_java", lambda: _Runtime())

    def fake_run(command, *_args, **_kwargs):
        captured["command"] = list(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("pcl.version.loaders.forge.subprocess.run", fake_run)

    installer = ForgeInstaller(paths, DownloadOptions(), kind="forge")
    profile = installer.read_profile(installer_path, "1.20.1")
    work_dir = tmp_path / "work"
    installer._extract_embedded(installer_path, work_dir)
    installer._run_processors(profile, work_dir, installer_path)

    command = captured["command"]
    assert "{BINPATCH}" not in " ".join(command)
    assert str(work_dir / "data/client.lzma") in command
    assert str(client_jar) in command


def test_missing_data_token_raises(paths: Path, tmp_path, monkeypatch) -> None:
    installer_path = tmp_path / "installer2.jar"
    with zipfile.ZipFile(installer_path, "w") as archive:
        archive.writestr(
            "install_profile.json",
            json.dumps(
                {
                    "spec": 1,
                    "minecraft": "1.20.1",
                    "version": "1.20.1-forge-47.2.0",
                    "json": "/version.json",
                    "path": "net.minecraftforge:forge:1.20.1-47.2.0",
                    "processors": [
                        {
                            "sides": ["client"],
                            "jar": "net.minecraftforge:binarypatcher:1.3.4",
                            "args": ["--output", "{BINPATCH}"],
                        }
                    ],
                    "data": {},
                }
            ),
        )
        archive.writestr("version.json", json.dumps({"id": "1.20.1-forge-47.2.0"}))

    jar = paths.libraries / "net/minecraftforge/binarypatcher/1.3.4/binarypatcher-1.3.4.jar"
    _make_library(jar)
    client_jar = paths.version_jar("1.20.1")
    client_jar.parent.mkdir(parents=True, exist_ok=True)
    client_jar.write_bytes(b"client")

    class _Runtime:
        path = Path("/usr/bin/java")
        version = "17.0.9"
        major = 17

    monkeypatch.setattr("pcl.version.loaders.forge.probe_java", lambda _path: _Runtime())
    monkeypatch.setattr("pcl.version.loaders.forge.find_java", lambda: _Runtime())

    installer = ForgeInstaller(paths, DownloadOptions(), kind="forge")
    profile = installer.read_profile(installer_path, "1.20.1")
    with pytest.raises(LoaderError):
        installer._run_processors(profile, tmp_path / "work2", installer_path)


def test_version_compare() -> None:
    assert _version_at_least("26.3", "1.20.5") is True
    assert _version_at_least("1.20.6", "1.20.5") is True
    assert _version_at_least("1.16.5", "1.18") is False
    assert _version_at_least("1.21-pre1", "1.20.5") is True
