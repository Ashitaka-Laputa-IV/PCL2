"""应用入口：初始化日志、配置、主题与主窗口。"""

from __future__ import annotations

import sys
import traceback

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from pcl import APP_NAME, __version__
from pcl.core.errors import friendly_message
from pcl.core.logging import get_logger, get_ring_buffer, setup_logging
from pcl.core.paths import AppPaths, default_data_root, set_paths
from pcl.services import AppContext
from pcl.ui.main_window import MainWindow

log = get_logger(__name__)


def _install_excepthook() -> None:
    """未捕获异常统一记录并提示，避免界面静默崩溃。"""

    def hook(exc_type, exc_value, exc_tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        log.error("未捕获的异常：\n%s", text)
        try:
            QMessageBox.critical(
                None,
                f"{APP_NAME} 出现错误",
                f"{friendly_message(exc_value)}\n\n详细信息已写入日志。",
            )
        except Exception:
            pass

    sys.excepthook = hook


def create_application(argv: list[str] | None = None) -> QApplication:
    """创建并配置 QApplication。"""
    app = QApplication.instance()
    if app is None:
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)
        app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName("PCL2-Python")
    return app  # type: ignore[return-value]


def bootstrap_context() -> AppContext:
    """初始化日志与全局上下文。"""
    root = default_data_root()
    paths = AppPaths(root).ensure()
    set_paths(paths)
    setup_logging(paths.launcher_log, console=True)
    log.info("%s %s 启动", APP_NAME, __version__)
    context = AppContext.bootstrap()
    set_paths(context.paths)
    return context


def main(argv: list[str] | None = None) -> int:
    """程序主入口。"""
    app = create_application(argv)
    _install_excepthook()

    try:
        context = bootstrap_context()
    except Exception as error:
        log.exception("初始化失败")
        QMessageBox.critical(None, f"{APP_NAME} 启动失败", friendly_message(error))
        return 1

    window = MainWindow(context)
    window.show()
    log.info("界面已就绪（日志缓冲 %d 行）", len(get_ring_buffer().lines()))
    return app.exec()


def run() -> None:
    """``pcl2`` 命令行入口。"""
    raise SystemExit(main())


if __name__ == "__main__":  # pragma: no cover
    run()
