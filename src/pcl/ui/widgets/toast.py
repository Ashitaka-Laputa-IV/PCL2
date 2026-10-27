"""轻提示：底部居中的浮层消息，自动淡出。"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QGraphicsOpacityEffect, QHBoxLayout, QLabel, QWidget

KIND_COLORS = {
    "info": "#2E7CF6",
    "success": "#22C55E",
    "warning": "#F59E0B",
    "error": "#EF4444",
}


class Toast(QWidget):
    """单条提示浮层。"""

    def __init__(self, parent: QWidget, text: str, kind: str = "info", duration: int = 2600) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        color = KIND_COLORS.get(kind, KIND_COLORS["info"])
        self.setStyleSheet(
            f"""
            Toast {{
                background: rgba(28, 34, 46, 235);
                border-radius: 10px;
            }}
            QLabel {{ color: #F4F7FC; font-size: 13px; }}
            QLabel#Dot {{ color: {color}; font-size: 16px; }}
            """
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 18, 10)
        layout.setSpacing(10)
        dot = QLabel("●", self)
        dot.setObjectName("Dot")
        message = QLabel(text, self)
        message.setWordWrap(True)
        layout.addWidget(dot)
        layout.addWidget(message)

        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(0.0)
        self.setGraphicsEffect(self._effect)
        self._fade = QPropertyAnimation(self._effect, b"opacity", self)
        self._fade.setDuration(180)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._duration = duration
        self.adjustSize()
        self.setMaximumWidth(max(320, parent.width() - 80))

    def _place(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        self.adjustSize()
        x = (parent.width() - self.width()) // 2
        y = parent.height() - self.height() - 28
        self.move(max(16, x), max(16, y))

    def popup(self) -> None:
        self._place()
        self.show()
        self.raise_()
        self._fade.stop()
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()
        QTimer.singleShot(self._duration, self._dismiss)

    def _dismiss(self) -> None:
        self._fade.stop()
        self._fade.setStartValue(self._effect.opacity())
        self._fade.setEndValue(0.0)
        self._fade.finished.connect(self.close)
        self._fade.start()


def show_toast(parent: QWidget, text: str, kind: str = "info", duration: int = 2600) -> Toast:
    """在父窗口底部显示一条提示。"""
    toast = Toast(parent, text, kind=kind, duration=duration)
    toast.popup()
    return toast
