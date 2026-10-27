"""自绘开关：150ms 滑块过渡。"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QRectF, QSize, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import QAbstractButton, QWidget


class ToggleSwitch(QAbstractButton):
    """圆角开关。"""

    def __init__(self, parent: QWidget | None = None, *, width: int = 42, height: int = 23) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._w = width
        self._h = height
        self._pos = 0.0
        self.setFixedSize(width, height)

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_value)
        self.toggled.connect(self._animate)

    def _on_value(self, value) -> None:
        self._pos = float(value)
        self.update()

    def _animate(self, checked: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def setChecked(self, checked: bool) -> None:  # noqa: N802
        super().setChecked(checked)
        self._pos = 1.0 if checked else 0.0
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self._w, self._h)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        accent = self.palette().highlight().color()
        off = QColor("#C7CEDA") if not self._is_dark() else QColor("#3B4557")
        track_color = QColor(
            int(off.red() + (accent.red() - off.red()) * self._pos),
            int(off.green() + (accent.green() - off.green()) * self._pos),
            int(off.blue() + (accent.blue() - off.blue()) * self._pos),
        )

        rect = QRectF(0, 0, self._w, self._h)
        path = QPainterPath()
        path.addRoundedRect(rect, self._h / 2, self._h / 2)
        painter.fillPath(path, track_color)

        knob_d = self._h - 4
        travel = self._w - knob_d - 4
        x = 2 + travel * self._pos
        knob = QRectF(x, 2, knob_d, knob_d)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawEllipse(knob)

        if not self.isEnabled():
            painter.fillPath(path, QColor(255, 255, 255, 120))
        painter.end()

    def _is_dark(self) -> bool:
        from PySide6.QtWidgets import QApplication

        return QApplication.palette().window().color().lightness() < 128
