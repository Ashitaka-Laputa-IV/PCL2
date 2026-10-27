"""自绘标题栏：应用标识、标题、窗口按钮与拖拽移动。"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


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
        self.setFixedHeight(56)
        self._drag_offset: QPoint | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 8, 10, 6)
        layout.setSpacing(10)
        layout.addWidget(_Mark(self))

        texts = QVBoxLayout()
        texts.setContentsMargins(0, 0, 0, 0)
        texts.setSpacing(0)
        self._title = QLabel(title, self)
        self._title.setObjectName("TitleBarText")
        self._subtitle = QLabel(subtitle, self)
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
