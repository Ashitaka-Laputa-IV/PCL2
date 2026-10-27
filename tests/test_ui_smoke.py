"""界面冒烟测试：离屏创建主窗口、切换页面、切换主题。"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from pcl.core.config import Config  # noqa: E402
from pcl.instance.manager import InstanceManager  # noqa: E402
from pcl.launch.auth import AccountStore  # noqa: E402
from pcl.services import AppContext  # noqa: E402
from pcl.ui.main_window import MainWindow  # noqa: E402

pytestmark = pytest.mark.gui


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
        instances=InstanceManager(paths),
    )


def test_main_window_navigation(qapp, context) -> None:
    context.instances.create("冒烟实例", "1.20.1", loader="fabric", loader_version="0.15.7")
    window = MainWindow(context)
    window.resize(1100, 720)
    window.show()
    qapp.processEvents()

    assert set(window.pages) == {"home", "instances", "download", "content", "settings", "more", "detail"}
    for key in ("instances", "download", "content", "settings", "home"):
        window.navigate(key)
        qapp.processEvents()
        assert window.stack.currentWidget() is window.pages[key]

    # 页面内实例下拉应包含刚创建的实例
    combo = window.pages["content"]._instance_combo
    assert combo.count() == 1
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


def test_new_instance_dialog_minimal(qapp, context) -> None:
    from pcl.ui.dialogs.new_instance import NewInstanceDialog
    from pcl.ui.workers import EventBridge, TaskRunner

    bridge = EventBridge()
    tasks = TaskRunner(max_threads=2)
    dialog = NewInstanceDialog(context, tasks, bridge)
    dialog.show()
    qapp.processEvents()
    assert dialog._version.count() >= len(context.installed_versions())
    if not context.installed_versions() and not dialog._packs:
        assert not dialog._ok.isEnabled()
    dialog.close()
    bridge.detach()
    tasks.shutdown(500)
