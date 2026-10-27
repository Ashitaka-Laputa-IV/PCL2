"""一键安装 Java：URL 构造与解压定位（使用本地假压缩包）。"""

from __future__ import annotations

import tarfile
from pathlib import Path

from pcl.core.paths import AppPaths
from pcl.launch.java_installer import _finalize, _safe_extract, adoptium_url, installed_runtime


def _make_java_archive(root: Path, major: int = 17) -> Path:
    home = root / f"jdk-{major}.0.9+9"
    (home / "bin").mkdir(parents=True)
    executable = home / "bin" / "java"
    executable.write_text("#!/bin/sh\necho 'openjdk version \"17.0.9\" 2023' >&2\n", encoding="utf-8")
    executable.chmod(0o755)
    archive = root / "java.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(home, arcname=home.name)
    return archive


def test_adoptium_url_contains_major() -> None:
    url = adoptium_url(21)
    assert "/latest/21/ga/" in url
    assert url.startswith("https://api.adoptium.net/")


def test_extract_and_finalize(tmp_path) -> None:
    archive = _make_java_archive(tmp_path)
    paths = AppPaths(tmp_path / "data").ensure()
    work = tmp_path / "work"
    _safe_extract(archive, work)
    runtime = _finalize(work, paths.root / "java" / "jre-17")
    assert runtime.major == 17
    assert runtime.path.name == "java"
    assert installed_runtime(paths, 17) is not None
    assert installed_runtime(paths, 8) is None


def test_select_java_refuses_lower_major() -> None:
    from pcl.launch.java import JavaRuntime, select_java

    runtime = JavaRuntime(path=Path("/usr/bin/java"), version="17.0.9", major=17)
    assert select_java([runtime], required_major=25) is None
    assert select_java([runtime], required_major=17).major == 17
