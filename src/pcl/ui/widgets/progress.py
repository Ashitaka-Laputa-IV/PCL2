"""进度控件：圆角渐变进度条与环形进度。"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QConicalGradient, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget


class GradientProgressBar(QWidget):
    """圆角渐变进度条，支持不确定态。"""

    def __init__(self, parent: QWidget | None = None, *, height: int = 8) -> None:
        super().__init__(parent)
        self._value = 0.0
        self._indeterminate = False
        self._phase = 0.0
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    def set_value(self, value: float) -> None:
        self._value = max(0.0, min(100.0, float(value)))
        self.update()

    def value(self) -> float:
        return self._value

    def set_indeterminate(self, enabled: bool) -> None:
        self._indeterminate = enabled
        if enabled:
            self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def _tick(self) -> None:
        self._phase = (self._phase + 0.012) % 1.0
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        radius = self.height() / 2
        background = QColor("#DFE5EE") if not self._is_dark() else QColor("#2C3444")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(background)
        painter.drawRoundedRect(QRectF(self.rect()), radius, radius)

        accent = self.palette().highlight().color()
        lighter = QColor(accent)
        lighter = lighter.lighter(125)
        gradient = QLinearGradient(0, 0, self.width(), 0)
        gradient.setColorAt(0.0, lighter)
        gradient.setColorAt(1.0, accent)

        if self._indeterminate:
            width = max(60.0, self.width() * 0.28)
            offset = (self.width() + width) * self._phase - width
            chunk = QRectF(offset, 0, width, self.height())
        else:
            width = self.width() * self._value / 100.0
            chunk = QRectF(0, 0, max(0.0, width), self.height())
        if chunk.width() > 0:
            painter.setBrush(gradient)
            painter.drawRoundedRect(chunk, radius, radius)
        painter.end()

    def _is_dark(self) -> bool:
        from PySide6.QtWidgets import QApplication

        return QApplication.palette().window().color().lightness() < 128


class RingProgress(QWidget):
    """环形进度（主页启动按钮外圈）。"""

    def __init__(self, parent: QWidget | None = None, *, diameter: int = 148, thickness: int = 7) -> None:
        super().__init__(parent)
        self._value = 0.0
        self._diameter = diameter
        self._thickness = thickness
        self._track: QColor | None = None
        self.setFixedSize(diameter, diameter)

    def set_track_color(self, color: QColor) -> None:
        """自定义轨道颜色（用于深色背景上）。"""
        self._track = QColor(color)
        self.update()

    def set_value(self, value: float) -> None:
        self._value = max(0.0, min(100.0, float(value)))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        margin = self._thickness / 2 + 2
        rect = QRectF(margin, margin, self.width() - margin * 2, self.height() - margin * 2)

        track = self._track or (QColor("#E4E9F2") if not self._is_dark() else QColor("#2B3342"))
        pen = QPen(track)
        pen.setWidthF(self._thickness)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawArc(rect, 0, 360 * 16)

        if self._value > 0:
            accent = self.palette().highlight().color()
            gradient = QConicalGradient(rect.center(), 90)
            gradient.setColorAt(0.0, accent)
            gradient.setColorAt(0.6, accent.lighter(135))
            gradient.setColorAt(1.0, accent)
            pen = QPen(gradient, self._thickness)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            span = int(360 * 16 * self._value / 100)
            painter.drawArc(rect, 90 * 16, -span)
        painter.end()

    def _is_dark(self) -> bool:
        from PySide6.QtWidgets import QApplication

        return QApplication.palette().window().color().lightness() < 128
