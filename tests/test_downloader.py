"""下载引擎：去重、校验、跳过与镜像回退（本地 HTTP 桩）。"""

from __future__ import annotations

import hashlib
import http.server
import socketserver
import threading
from pathlib import Path

import pytest

from pcl.net.downloader import DownloadManager, DownloadTask, dedupe_tasks, total_size
from pcl.net.mirror import candidates, mirror_url


@pytest.fixture()
def http_server(tmp_path: Path):
    """启动一个只读静态文件服务器。"""
    root = tmp_path / "www"
    root.mkdir()
    payload = b"hello pcl2 " * 512
    (root / "file.bin").write_bytes(payload)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(root), **kwargs)

        def log_message(self, *args):  # 静音
            return

    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {
            "base": f"http://127.0.0.1:{server.server_address[1]}",
            "sha1": hashlib.sha1(payload).hexdigest(),
            "size": len(payload),
        }
    finally:
        server.shutdown()
        server.server_close()


def test_mirror_mapping() -> None:
    assert (
        mirror_url("https://piston-meta.mojang.com/mc/game/version_manifest_v2.json")
        == "https://bmclapi2.bangbang93.com/mc/game/version_manifest_v2.json"
    )
    assert (
        mirror_url("https://resources.download.minecraft.net/ab/abcdef")
        == "https://bmclapi2.bangbang93.com/assets/ab/abcdef"
    )
    assert mirror_url("https://example.com/file") is None
    assert candidates("https://example.com/x", source="official") == ["https://example.com/x"]
    assert candidates("https://libraries.minecraft.net/a/b.jar", source="auto")[0].startswith(
        "https://bmclapi2.bangbang93.com/maven"
    )


def test_dedupe_tasks() -> None:
    first = DownloadTask(url="https://a", dest=Path("/tmp/x"), mirrors=("https://m1",))
    second = DownloadTask(url="https://b", dest=Path("/tmp/x"), sha1="a" * 40, mirrors=("https://m2",))
    third = DownloadTask(url="https://c", dest=Path("/tmp/y"))
    merged = dedupe_tasks([first, second, third])
    assert len(merged) == 2
    target = next(item for item in merged if item.dest == Path("/tmp/x"))
    assert target.sha1 == "a" * 40
    assert set(target.mirrors) == {"https://m1", "https://m2"}
    assert total_size([first, third]) == 0


def test_download_and_skip(http_server, tmp_path) -> None:
    dest = tmp_path / "out" / "file.bin"
    task = DownloadTask(
        url=f"{http_server['base']}/file.bin",
        dest=dest,
        sha1=http_server["sha1"],
        size=http_server["size"],
    )
    manager = DownloadManager(source="official", concurrency=4)
    summary = manager.run([task])
    assert summary.ok
    assert summary.results[0].bytes_written == http_server["size"]
    assert hashlib.sha1(dest.read_bytes()).hexdigest() == http_server["sha1"]

    # 已存在且校验通过 → 跳过
    summary = DownloadManager(source="official").run([task])
    assert summary.results[0].skipped
    assert summary.results[0].bytes_written == 0


def test_checksum_failure_removes_file(http_server, tmp_path) -> None:
    dest = tmp_path / "bad.bin"
    task = DownloadTask(
        url=f"{http_server['base']}/file.bin",
        dest=dest,
        sha1="0" * 40,
        size=http_server["size"],
    )
    summary = DownloadManager(source="official", retry=0).run([task])
    assert not summary.ok
    assert not dest.exists()
    assert not dest.with_name(dest.name + ".part").exists()


def test_fallback_to_second_candidate(http_server, tmp_path) -> None:
    dest = tmp_path / "fallback.bin"
    task = DownloadTask(
        url=f"{http_server['base']}/missing.bin",
        dest=dest,
        sha1=http_server["sha1"],
        size=http_server["size"],
        mirrors=(f"{http_server['base']}/file.bin",),
    )
    summary = DownloadManager(source="official", retry=0).run([task])
    assert summary.ok
    assert dest.is_file()


def test_cancel_before_start(http_server, tmp_path) -> None:
    manager = DownloadManager(source="official")
    manager.cancel()
    summary = manager.run(
        [DownloadTask(url=f"{http_server['base']}/file.bin", dest=tmp_path / "never.bin")]
    )
    assert summary.cancelled
    assert not (tmp_path / "never.bin").exists()
