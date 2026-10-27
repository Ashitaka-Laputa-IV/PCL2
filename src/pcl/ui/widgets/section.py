"""可折叠的设置分组卡片（图标 + 标题 + 内容）。"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from pcl.ui import icons
from pcl.ui.widgets.animated_button import AnimatedButton
from pcl.ui.widgets.card import Card


class SectionCard(Card):
    """带折叠动画的分组卡片。"""

    def __init__(self, title: str, icon_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent, padding=18, spacing=8)
        header = QWidget(self)
        row = QHBoxLayout(header)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        badge = QLabel(header)
        badge.setPixmap(icons.pixmap(icon_name, self.palette().highlight().color().name(), 18))
        heading = QLabel(title, header)
        heading.setObjectName("SectionTitle")
        self._toggle = AnimatedButton("收起", header, variant="ghost", height=26)
        self._toggle.setFixedWidth(52)
        self._toggle.clicked.connect(self.toggle_collapsed)
        row.addWidget(badge)
        row.addWidget(heading)
        row.addStretch(1)
        row.addWidget(self._toggle)
        Card.add(self, header)

        self._body = QWidget(self)
        self._content_layout = QVBoxLayout(self._body)
        self._content_layout.setContentsMargins(0, 2, 0, 0)
        self._content_layout.setSpacing(8)
        self._body.setMaximumHeight(16777215)
        Card.add(self, self._body)

        self._anim = QPropertyAnimation(self._body, b"maximumHeight", self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._collapsed = False

    def add(self, widget: QWidget, stretch: int = 0, alignment=None) -> None:
        widget.setParent(self._body)
        if alignment is None:
            self._content_layout.addWidget(widget, stretch)
        else:
            self._content_layout.addWidget(widget, stretch, alignment)

    def add_layout(self, layout, stretch: int = 0) -> None:
        self._content_layout.addLayout(layout, stretch)

    def toggle_collapsed(self) -> None:
        self._anim.stop()
        if self._collapsed:
            self._body.setVisible(True)
            self._body.setMaximumHeight(0)
            self._anim.setStartValue(0)
            self._anim.setEndValue(max(0, self._body.sizeHint().height()))
            self._collapsed = False
            self._toggle.setText("收起")
        else:
            self._anim.setStartValue(self._body.height())
            self._anim.setEndValue(0)
            self._collapsed = True
            self._toggle.setText("展开")
        self._anim.start()
