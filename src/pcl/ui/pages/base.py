"""页面基类：统一的标题区、滚动内容与刷新钩子。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from pcl.services import AppContext
from pcl.ui.workers import EventBridge, TaskRunner


class BasePage(QWidget):
    """所有页面的公共骨架。"""

    title = "页面"
    subtitle = ""

    def __init__(self, context: AppContext, bridge: EventBridge, tasks: TaskRunner, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.context = context
        self.bridge = bridge
        self.tasks = tasks
        self.setObjectName("ContentArea")

    # ---------- 标题 ----------
    def build_header(self, title: str | None = None, subtitle: str | None = None) -> QHBoxLayout:
        """构造标题 + 右侧操作区，返回该行布局。"""
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)

        texts = QVBoxLayout()
        texts.setContentsMargins(0, 0, 0, 0)
        texts.setSpacing(2)

        heading = QLabel(title or self.title, self)
        heading.setObjectName("PageTitle")
        texts.addWidget(heading)

        description = subtitle if subtitle is not None else self.subtitle
        if description:
            label = QLabel(description, self)
            label.setObjectName("PageSubtitle")
            texts.addWidget(label)
        row.addLayout(texts)
        row.addStretch(1)
        return row

    # ---------- 滚动 ----------
    def make_scroll(self) -> tuple[QScrollArea, QVBoxLayout]:
        """创建带透明背景的滚动区域与其内容布局。"""
        area = QScrollArea(self)
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setStyleSheet("QScrollArea { background: transparent; }")

        content = QWidget(area)
        content.setObjectName("ContentArea")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 8, 8)
        layout.setSpacing(16)
        area.setWidget(content)
        return area, layout

    # ---------- 生命周期 ----------
    def refresh(self) -> None:
        """页面被切换到前台时调用，子类覆写。"""

    def on_game_state(self, state: object) -> None:
        """游戏进程状态变化，子类可覆写。"""
