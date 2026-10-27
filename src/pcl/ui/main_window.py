"""主窗口：无边框外观、左侧导航、页面切换动画与全局任务条。"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPainterPath, QShortcut
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QSizeGrip,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from pcl.core.errors import friendly_message
from pcl.core.logging import get_logger
from pcl.services import AppContext
from pcl.ui import icons, theme
from pcl.ui.dialogs.crash_report import show_crash_report
from pcl.ui.dialogs.log_viewer import LogWindow
from pcl.ui.pages.base import BasePage
from pcl.ui.pages.content import ContentPage
from pcl.ui.pages.download import DownloadPage
from pcl.ui.pages.home import HomePage
from pcl.ui.pages.instance_detail import InstanceDetailPage
from pcl.ui.pages.instances import InstancesPage
from pcl.ui.pages.more import MorePage
from pcl.ui.pages.settings import SettingsPage
from pcl.ui.widgets.progress import GradientProgressBar
from pcl.ui.widgets.title_bar import TitleBar
from pcl.ui.widgets.toast import show_toast
from pcl.ui.workers import EventBridge, TaskRunner

log = get_logger(__name__)

NAV_ITEMS = (
    ("home", "home", "主页"),
    ("instances", "instances", "版本管理"),
    ("download", "download", "下载"),
    ("content", "content", "资源管理"),
    ("settings", "settings", "设置"),
    ("more", "more", "更多"),
)


class NavButton(QAbstractButton):
    """侧栏图标按钮：选中高亮、悬停位移与左侧指示条。"""

    def __init__(self, name: str, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.icon_name = name
        self.label = label
        self.setCheckable(True)
        self.setFixedSize(44, 44)
        self.setToolTip(label)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        accent = self.palette().highlight().color()
        side = 38.0
        cx = self.width() / 2.0
        cy = self.height() / 2.0
        rect = QRectF(cx - side / 2, cy - side / 2, side, side)

        if self.isChecked() or self.underMouse():
            background = QColor(accent)
            background.setAlpha(34 if self.isChecked() else 16)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(background)
            painter.drawRoundedRect(rect, 11, 11)

        if self.isChecked():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(accent)
            painter.drawRoundedRect(QRectF(0.0, cy - 10.0, 3.0, 20.0), 1.5, 1.5)

        color = accent if self.isChecked() else QColor("#7C8899")
        pixmap = icons.pixmap(self.icon_name, color.name(), 22)
        if not pixmap.isNull():
            painter.drawPixmap(
                int(rect.center().x() - pixmap.deviceIndependentSize().width() / 2),
                int(rect.center().y() - pixmap.deviceIndependentSize().height() / 2),
                pixmap,
            )
        painter.end()


class Sidebar(QWidget):
    """左侧导航栏。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(68)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 18, 6, 18)
        layout.setSpacing(8)
        self.buttons: dict[str, NavButton] = {}
        for key, icon_name, label in NAV_ITEMS:
            button = NavButton(icon_name, label, self)
            self.buttons[key] = button
            layout.addWidget(button, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(1)

    def set_active(self, key: str) -> None:
        for name, button in self.buttons.items():
            button.setChecked(name == key)
            button.update()


class MainWindow(QMainWindow):
    """应用主窗口。"""

    def __init__(self, context: AppContext, *, bridge: EventBridge | None = None, tasks: TaskRunner | None = None) -> None:
        super().__init__()
        self.context = context
        self.bridge = bridge or EventBridge(self)
        self.tasks = tasks or TaskRunner(self)
        self._log_window: LogWindow | None = None
        self._current_page = ""
        self._animation: QPropertyAnimation | None = None

        self.setWindowTitle("PCL2 · Plain Craft Launcher (Python)")
        self.setMinimumSize(940, 660)
        self.resize(1120, 720)
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)

        root = QWidget(self)
        root.setObjectName("RootSurface")
        self.setCentralWidget(root)

        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.title_bar = TitleBar(root, title="PCL2", subtitle="Plain Craft Launcher · Python Edition")
        outer.addWidget(self.title_bar)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.sidebar = Sidebar(root)
        body.addWidget(self.sidebar)

        container = QWidget(root)
        container.setObjectName("ContentArea")
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        self.stack = QStackedWidget(container)
        self.stack.setObjectName("ContentArea")
        container_layout.addWidget(self.stack, 1)
        container_layout.addWidget(self._build_status_strip(container))
        body.addWidget(container, 1)
        outer.addLayout(body, 1)

        grip = QSizeGrip(root)
        grip.setFixedSize(16, 16)
        grip.setStyleSheet("background: transparent;")
        outer.addWidget(grip, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)

        self.pages: dict[str, BasePage] = {}
        self._register_pages()
        self.sidebar.buttons["home"].clicked.connect(lambda: self.navigate("home"))
        for key in self.pages:
            if key in self.sidebar.buttons:
                self.sidebar.buttons[key].clicked.connect(lambda _=False, target=key: self.navigate(target))

        self.apply_theme()
        self.navigate("home")

        for index, (key, _icon, _label) in enumerate(NAV_ITEMS, start=1):
            shortcut = QShortcut(QKeySequence(f"Ctrl+{index}"), self)
            shortcut.activated.connect(lambda target=key: self.navigate(target))

        self.bridge.game_state.connect(self._on_game_state)
        self.bridge.download_progress.connect(self._on_download_progress)
        self.bridge.toast.connect(lambda text: show_toast(self, text, "info"))

    # ---------- 状态条 ----------
    def _build_status_strip(self, parent: QWidget) -> QWidget:
        strip = QWidget(parent)
        strip.setObjectName("StatusStrip")
        strip.setFixedHeight(34)
        strip.setStyleSheet("QWidget#StatusStrip { background: transparent; }")
        layout = QHBoxLayout(strip)
        layout.setContentsMargins(28, 4, 24, 8)
        layout.setSpacing(12)

        self._status_label = QLabel("", strip)
        self._status_label.setObjectName("Muted")
        self._status_bar = GradientProgressBar(strip, height=6)
        self._status_bar.setFixedWidth(200)
        layout.addWidget(self._status_bar)
        layout.addWidget(self._status_label, 1)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(4000)
        self._hide_timer.timeout.connect(self._hide_status)

        strip.setVisible(False)
        self._status_strip = strip
        return strip

    def _on_download_progress(self, progress) -> None:
        percent = float(getattr(progress, "percent", 0.0))
        active = int(getattr(progress, "active", 0))
        total = int(getattr(progress, "total_tasks", 0))
        finished = int(getattr(progress, "finished_tasks", 0))
        failed = int(getattr(progress, "failed_tasks", 0))
        current = str(getattr(progress, "current", ""))
        speed = float(getattr(progress, "speed", 0.0))

        if total == 0:
            return
        self._status_strip.setVisible(True)
        self._status_bar.set_value(percent)
        speed_text = f"{speed / 1024 / 1024:.1f} MB/s" if speed > 0 else ""
        parts = [f"{finished}/{total} 个文件", f"{percent:.1f}%"]
        if speed_text:
            parts.append(speed_text)
        if failed:
            parts.append(f"{failed} 个失败")
        if current and active:
            parts.append(current)
        self._status_label.setText(" · ".join(parts))
        if active == 0 and finished + failed >= total:
            self._hide_timer.start()

    def _hide_status(self) -> None:
        self._status_strip.setVisible(False)
        self._status_bar.set_value(0)

    # ---------- 页面 ----------
    def _register_pages(self) -> None:
        factories = {
            "home": HomePage,
            "instances": InstancesPage,
            "download": DownloadPage,
            "content": ContentPage,
            "settings": SettingsPage,
            "more": MorePage,
            "detail": InstanceDetailPage,
        }
        for key, factory in factories.items():
            page = factory(self.context, self.bridge, self.tasks, self)
            self.pages[key] = page
            self.stack.addWidget(page)

    def open_instance_detail(self, instance_id: str) -> None:
        """打开指定实例的详情页。"""
        try:
            instance = self.context.instances.get(instance_id)
        except Exception as error:
            show_toast(self, friendly_message(error), "error")
            return
        self.context.select_instance(instance.id)
        detail = self.pages.get("detail")
        if detail is not None and hasattr(detail, "open_instance"):
            detail.open_instance(instance)
        self.navigate("detail")

    def navigate(self, key: str) -> None:
        page = self.pages.get(key)
        if page is None:
            return
        if key == "detail" and hasattr(page, "instance") and page.instance is None:
            instance = self.context.current_instance()
            if instance is None:
                show_toast(self, "还没有实例，请先新建一个。", "warning")
                self.navigate("instances")
                return
            page.open_instance(instance)
        if key == self._current_page:
            page.refresh()
            return

        direction = 1
        if key in self.pages and self._current_page in self.pages:
            order = list(self.pages)
            direction = 1 if order.index(key) > order.index(self._current_page) else -1

        self.stack.setCurrentWidget(page)
        self.sidebar.set_active(key)
        self._current_page = key
        page.refresh()
        self._animate_page(page, direction)

    def _animate_page(self, page: QWidget, direction: int) -> None:
        if not self.context.config.ui.animations:
            return
        effect = QGraphicsOpacityEffect(page)
        page.setGraphicsEffect(effect)
        fade = QPropertyAnimation(effect, b"opacity", self)
        fade.setDuration(190)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation = fade
        fade.start()
        QTimer.singleShot(220, lambda: page.setGraphicsEffect(None))

    # ---------- 主题 ----------
    def apply_theme(self) -> None:
        config = self.context.config
        app = QApplication.instance()
        if app is None:
            return
        theme.apply_theme(
            app,
            theme=config.ui.theme,
            accent=config.ui.accent,
            corner_radius=config.ui.corner_radius,
            font_scale=config.ui.font_scale,
        )
        for button in self.sidebar.buttons.values():
            button.update()

    # ---------- 日志窗口 ----------
    def show_log_window(self, runner=None) -> None:
        if self._log_window is None:
            self._log_window = LogWindow(self.context, self.bridge, self)
        self._log_window.show()
        self._log_window.raise_()
        self._log_window.activateWindow()
        state = self.context.runner.state if self.context.runner is not None else None
        if state is not None:
            self._log_window.update_state(state)

    def _on_game_state(self, state) -> None:
        page = self.pages.get(self._current_page)
        if page is not None:
            page.on_game_state(state)
        running = bool(getattr(state, "running", False))
        code = getattr(state, "exit_code", None)
        if running or code in (None, 0):
            return
        if not self.context.config.advanced.auto_analyze_crash:
            return
        self.tasks.submit(
            self.context.crash_report,
            None,
            on_done=self._on_crash_analyzed,
            on_error=lambda message: log.warning("崩溃分析失败：%s", message),
        )

    def _on_crash_analyzed(self, report) -> None:
        if report is None or not report.reasons:
            return
        show_crash_report(self, report)

    # ---------- 关闭 ----------
    def closeEvent(self, event) -> None:  # noqa: N802
        try:
            self.context.stop_game()
            self.bridge.detach()
            self.tasks.shutdown(2000)
            self.context.close()
        except Exception as error:  # 关闭阶段不阻塞退出
            log.warning("关闭时出现问题：%s", friendly_message(error))
        if self._log_window is not None:
            self._log_window.close()
        super().closeEvent(event)
