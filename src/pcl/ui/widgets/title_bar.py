"""自绘标题栏：应用标识、标题、窗口按钮与拖拽移动。"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget


class _ElidedLabel(QLabel):
    """按可用宽度省略显示的标签：字体放大时不会把标题栏顶宽。"""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._full_text = text
        self.setToolTip(text)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def setText(self, text: str) -> None:  # noqa: N802 - 与 QLabel API 保持一致
        self._full_text = text
        self.setToolTip(text)
        self._apply_elide()

    def full_text(self) -> str:
        return self._full_text

    def sizeHint(self):  # noqa: N802 - 按完整文本计算，避免省略后反复缩窄
        height = self.fontMetrics().height()
        return QSize(self.fontMetrics().horizontalAdvance(self._full_text) + 4, height)

    def minimumSizeHint(self):  # noqa: N802 - 允许被压缩到 0，由省略号兜底
        return QSize(0, self.fontMetrics().height())

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply_elide()

    def _apply_elide(self) -> None:
        available = max(0, self.contentsRect().width())
        super().setText(self.fontMetrics().elidedText(self._full_text, Qt.TextElideMode.ElideRight, available))


class _Mark(QWidget):
    """左上角应用标识（圆角方块 + 字母）。"""

    def __init__(self, parent: QWidget | None = None, *, size: int = 28) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)

    def paintEvent(self, event) -> None:  # noqa: N802
        from pcl.ui import icons

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pixmap = icons.pixmap("logo", "#2E7CF6", self.width())
        if not pixmap.isNull():
            painter.drawPixmap(0, 0, pixmap)
        painter.end()


class _WindowButton(QPushButton):
    """最小化 / 最大化 / 关闭按钮。"""

    def __init__(self, text: str, parent: QWidget | None = None, *, danger: bool = False) -> None:
        super().__init__(text, parent)
        self._danger = danger
        self.setFixedSize(38, 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFlat(True)
        self.setStyleSheet(
            """
            QPushButton {
                background: transparent;
                border: none;
                border-radius: 7px;
                color: %s;
                font-size: 15px;
                font-weight: 500;
            }
            QPushButton:hover { background: %s; color: %s; }
            """
            % (
                "#5F6B7A",
                "rgba(239, 68, 68, 0.16)" if danger else "rgba(120, 140, 170, 0.18)",
                "#EF4444" if danger else "#1F2329",
            )
        )


class TitleBar(QWidget):
    """可拖拽的标题栏。"""

    maximize_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None, *, title: str = "PCL2", subtitle: str = "") -> None:
        super().__init__(parent)
        self.setObjectName("TitleBar")
        # 高度随字号放大，避免两行标题被裁切
        self.setMinimumHeight(56)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self._drag_offset: QPoint | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 8, 10, 6)
        layout.setSpacing(10)
        layout.addWidget(_Mark(self))

        texts = QVBoxLayout()
        texts.setContentsMargins(0, 0, 0, 0)
        texts.setSpacing(0)
        self._title = _ElidedLabel(title, self)
        self._title.setObjectName("TitleBarText")
        self._subtitle = _ElidedLabel(subtitle, self)
        self._subtitle.setObjectName("TitleBarSubtitle")
        self._subtitle.setVisible(bool(subtitle))
        texts.addWidget(self._title)
        texts.addWidget(self._subtitle)
        layout.addLayout(texts)
        layout.addStretch(1)

        self._minimize = _WindowButton("—", self)
        self._maximize = _WindowButton("▢", self)
        self._close = _WindowButton("✕", self, danger=True)
        for button in (self._minimize, self._maximize, self._close):
            layout.addWidget(button)

        self._minimize.clicked.connect(lambda: self.window().showMinimized())
        self._maximize.clicked.connect(self.toggle_maximize)
        self._close.clicked.connect(lambda: self.window().close())

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        line = QColor(self.palette().mid().color())
        line.setAlpha(38)
        painter.fillRect(0, self.height() - 1, self.width(), 1, line)
        painter.end()

    # ---------- 文本 ----------
    def refit(self) -> None:
        """字号变化后重新按内容排布，避免标签沿用旧宽度被省略。"""
        layout = self.layout()
        if layout is not None:
            layout.invalidate()
            layout.activate()
        self.updateGeometry()

    def set_title(self, title: str) -> None:
        self._title.setText(title)

    def set_subtitle(self, subtitle: str) -> None:
        self._subtitle.setText(subtitle)
        self._subtitle.setVisible(bool(subtitle))

    # ---------- 窗口操作 ----------
    def toggle_maximize(self) -> None:
        window = self.window()
        if window.isMaximized():
            window.showNormal()
            self._maximize.setText("▢")
        else:
            window.showMaximized()
            self._maximize.setText("❐")
        self.maximize_changed.emit(window.isMaximized())

    # ---------- 拖拽 ----------
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            window = self.window()
            handle = window.windowHandle()
            if handle is not None and hasattr(handle, "startSystemMove"):
                if handle.startSystemMove():
                    event.accept()
                    return
            self._drag_offset = event.globalPosition().toPoint() - window.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            window = self.window()
            if window.isMaximized():
                window.showNormal()
            window.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_maximize()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)
