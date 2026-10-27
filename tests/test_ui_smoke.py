"""界面冒烟测试：离屏创建主窗口、切换页面、切换主题。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from pcl.core.config import Config  # noqa: E402
from pcl.launch.auth import AccountStore  # noqa: E402
from pcl.services import AppContext  # noqa: E402
from pcl.ui.main_window import MainWindow  # noqa: E402
from pcl.version.store import VersionStore  # noqa: E402

pytestmark = pytest.mark.gui


def write_version(paths, version_id: str = "1.20.1", document: dict | None = None) -> None:
    """在共享目录写入一个最小可识别版本。"""
    directory = paths.version_dir(version_id)
    directory.mkdir(parents=True, exist_ok=True)
    payload = document or {"id": version_id, "type": "release"}
    (directory / f"{version_id}.json").write_text(json.dumps(payload), encoding="utf-8")
    (directory / f"{version_id}.jar").write_bytes(b"jar")


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def context(paths):
    accounts = AccountStore(paths.accounts_file)
    accounts.load()
    accounts.ensure_default()
    return AppContext(
        paths=paths,
        config=Config(),
        accounts=accounts,
        versions=VersionStore(paths),
    )


def test_main_window_navigation(qapp, context, paths) -> None:
    write_version(paths)
    window = MainWindow(context)
    window.resize(1100, 720)
    window.show()
    qapp.processEvents()

    assert set(window.pages) == {"home", "versions", "download", "content", "settings", "more", "detail"}
    for key in ("versions", "download", "content", "settings", "home"):
        window.navigate(key)
        qapp.processEvents()
        assert window.stack.currentWidget() is window.pages[key]

    # 页面内版本下拉应包含刚安装的版本
    combo = window.pages["content"]._version_combo
    assert combo.count() == 1
    window.close()


def test_version_detail_navigation(qapp, context, paths) -> None:
    write_version(paths)
    window = MainWindow(context)
    window.show()
    qapp.processEvents()

    version = context.list_versions()[0]
    window.open_version_detail(version.id)
    qapp.processEvents()
    assert window.stack.currentWidget() is window.pages["detail"]
    assert window.pages["detail"].version is not None
    window.close()


def test_theme_switch(qapp, context) -> None:
    window = MainWindow(context)
    window.show()
    qapp.processEvents()

    context.config.ui.theme = "dark"
    context.config.ui.accent = "#22C55E"
    window.apply_theme()
    qapp.processEvents()
    assert "#22C55E".lower() in qapp.styleSheet().lower() or "#22c55e" in qapp.styleSheet().lower()

    context.config.ui.theme = "light"
    window.apply_theme()
    qapp.processEvents()
    window.close()


def test_toast_and_empty_state(qapp, context) -> None:
    from pcl.ui.widgets.toast import show_toast

    window = MainWindow(context)
    window.show()
    qapp.processEvents()

    toast = show_toast(window, "冒烟提示", "success", duration=50)
    assert toast.isVisible()
    qapp.processEvents()
    window.close()


def test_import_modpack_dialog_minimal(qapp, context, tmp_path: Path) -> None:
    from pcl.ui.dialogs.import_modpack import ImportModpackDialog
    from pcl.ui.workers import EventBridge, TaskRunner

    bridge = EventBridge()
    tasks = TaskRunner(max_threads=2)
    dialog = ImportModpackDialog(context, tasks, bridge)
    dialog.show()
    qapp.processEvents()
    assert dialog._path.text() == ""
    dialog.close()
    bridge.detach()
    tasks.shutdown(500)
