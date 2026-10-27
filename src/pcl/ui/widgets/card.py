"""卡片容器：圆角、柔和阴影与悬停上浮。"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFrame, QGraphicsDropShadowEffect, QVBoxLayout, QWidget


class Card(QFrame):
    """带阴影的卡片，可作为普通容器使用。"""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        hover_lift: bool = False,
        padding: int = 16,
        spacing: int = 10,
        object_name: str = "Card",
    ) -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        self._hover_lift = hover_lift
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(padding, padding, padding, padding)
        self._layout.setSpacing(spacing)

        self._shadow = QGraphicsDropShadowEffect(self)
        self._shadow.setBlurRadius(18)
        self._shadow.setOffset(0, 4)
        self._shadow.setColor(QColor(24, 38, 62, 26))
        self.setGraphicsEffect(self._shadow)

        self._animation = QPropertyAnimation(self._shadow, b"blurRadius", self)
        self._animation.setDuration(160)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    # ---------- 布局 ----------
    @property
    def body(self) -> QVBoxLayout:
        return self._layout

    def add(self, widget: QWidget, stretch: int = 0, alignment=None) -> None:
        if alignment is None:
            self._layout.addWidget(widget, stretch)
        else:
            self._layout.addWidget(widget, stretch, alignment)

    def add_layout(self, layout, stretch: int = 0) -> None:
        self._layout.addLayout(layout, stretch)

    def addStretch(self, stretch: int = 1) -> None:  # noqa: N802 - 与 Qt 布局 API 保持一致
        self._layout.addStretch(stretch)

    add_stretch = addStretch

    # 兼容直接当布局使用的写法
    def addWidget(self, widget: QWidget, stretch: int = 0) -> None:  # noqa: N802
        self._layout.addWidget(widget, stretch)

    def addLayout(self, layout, stretch: int = 0) -> None:  # noqa: N802
        self._layout.addLayout(layout, stretch)

    def insertWidget(self, index: int, widget: QWidget, stretch: int = 0) -> None:  # noqa: N802
        self._layout.insertWidget(index, widget, stretch)

    def count(self) -> int:
        return self._layout.count()

    def takeAt(self, index: int):  # noqa: N802
        return self._layout.takeAt(index)

    def setSpacing(self, spacing: int) -> None:  # noqa: N802
        self._layout.setSpacing(spacing)

    def setContentsMargins(self, *args: int) -> None:  # noqa: N802
        self._layout.setContentsMargins(*args)

    def set_padding(self, padding: int) -> None:
        self._layout.setContentsMargins(padding, padding, padding, padding)

    # ---------- 悬停 ----------
    def enterEvent(self, event) -> None:  # noqa: N802
        if self._hover_lift:
            self._animate(26, 8)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self._hover_lift:
            self._animate(18, 4)
        super().leaveEvent(event)

    def _animate(self, blur: float, offset: float) -> None:
        self._animation.stop()
        self._animation.setStartValue(self._shadow.blurRadius())
        self._animation.setEndValue(blur)
        self._animation.start()
        self._shadow.setOffset(0, offset)

    def set_shadow_color(self, color: QColor) -> None:
        self._shadow.setColor(color)

    def hide_shadow(self) -> None:
        self.setGraphicsEffect(None)


class ContentCard(Card):
    """内容卡片：更宽松的内边距，适合放置列表或表单。"""

    def __init__(self, parent: QWidget | None = None, **kwargs) -> None:
        kwargs.setdefault("padding", 20)
        kwargs.setdefault("spacing", 12)
        super().__init__(parent, **kwargs)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
