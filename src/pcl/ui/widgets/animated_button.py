"""自绘按钮：渐变主按钮、悬停提亮、按压回弹与加载态。"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QRectF, QSize, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QPushButton, QSizePolicy, QWidget

from pcl.ui import icons


def _mix(first: QColor, second: QColor, ratio: float) -> QColor:
    ratio = max(0.0, min(1.0, ratio))
    return QColor(
        int(first.red() + (second.red() - first.red()) * ratio),
        int(first.green() + (second.green() - first.green()) * ratio),
        int(first.blue() + (second.blue() - first.blue()) * ratio),
        int(first.alpha() + (second.alpha() - first.alpha()) * ratio),
    )


class AnimatedButton(QPushButton):
    """带微动效的按钮。

    ``variant`` 可选：``primary`` / ``secondary`` / ``ghost`` / ``danger`` / ``hero``。
    """

    _VARIANTS = ("primary", "secondary", "ghost", "danger", "hero")

    def __init__(
        self,
        text: str = "",
        parent: QWidget | None = None,
        *,
        variant: str = "secondary",
        icon_name: str = "",
        radius: int = 8,
        height: int | None = None,
    ) -> None:
        super().__init__(text, parent)
        self._variant = variant if variant in self._VARIANTS else "secondary"
        self._icon_name = icon_name
        self._radius = radius
        self._hover = 0.0
        self._press = 0.0
        self._loading = False
        self._angle = 0
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        if height:
            self.setFixedHeight(height)
        else:
            self.setMinimumHeight(56 if self._variant == "hero" else 34)

        self._hover_anim = QVariantAnimation(self)
        self._hover_anim.setDuration(140)
        self._hover_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._hover_anim.valueChanged.connect(self._on_hover)

        self._press_anim = QVariantAnimation(self)
        self._press_anim.setDuration(110)
        self._press_anim.valueChanged.connect(self._on_press)

        self._spin = QTimer(self)
        self._spin.setInterval(16)
        self._spin.timeout.connect(self._rotate)

    # ---------- 属性 ----------
    def set_variant(self, variant: str) -> None:
        self._variant = variant if variant in self._VARIANTS else "secondary"
        self.update()

    def set_icon_name(self, name: str) -> None:
        self._icon_name = name
        self.update()

    def set_radius(self, radius: int) -> None:
        self._radius = max(0, radius)
        self.update()

    @property
    def loading(self) -> bool:
        return self._loading

    def set_loading(self, loading: bool, text: str | None = None) -> None:
        self._loading = loading
        if text is not None:
            self.setText(text)
        if loading:
            self._spin.start()
        else:
            self._spin.stop()
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        metrics = self.fontMetrics()
        width = metrics.horizontalAdvance(self.text()) + 44
        if self._icon_name:
            width += 26
        height = 56 if self._variant == "hero" else 36
        return QSize(max(width, 88), height)

    # ---------- 动画 ----------
    def _on_hover(self, value) -> None:
        self._hover = float(value)
        self.update()

    def _on_press(self, value) -> None:
        self._press = float(value)
        self.update()

    def _rotate(self) -> None:
        self._angle = (self._angle + 8) % 360
        self.update()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._animate_hover(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._animate_hover(0.0)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._animate_press(1.0)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._animate_press(0.0)
        super().mouseReleaseEvent(event)

    def _animate_hover(self, target: float) -> None:
        self._hover_anim.stop()
        self._hover_anim.setStartValue(self._hover)
        self._hover_anim.setEndValue(target)
        self._hover_anim.start()

    def _animate_press(self, target: float) -> None:
        self._press_anim.stop()
        self._press_anim.setStartValue(self._press)
        self._press_anim.setEndValue(target)
        self._press_anim.start()

    # ---------- 绘制 ----------
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        inset = 1.5 + self._press * 1.2
        rect = QRectF(self.rect()).adjusted(inset, inset, -inset, -inset)
        radius = self._radius + (6 if self._variant == "hero" else 0)

        accent = self.palette().highlight().color()
        text_color = self.palette().windowText().color()
        border_color = _mix(QColor("#D3D9E3"), accent, 0.0)
        danger = QColor("#EF4444")

        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)

        if not self.isEnabled():
            fill = QColor("#E9EDF4") if not self._is_dark() else QColor("#2A3242")
            painter.fillPath(path, fill)
            pen_color = QColor("#A6AFBD")
        elif self._variant in ("primary", "hero"):
            base = accent
            top = _mix(base, QColor("#FFFFFF"), 0.16 + 0.08 * self._hover)
            bottom = _mix(base, QColor("#000000"), 0.06 + 0.05 * self._press + 0.03 * self._hover)
            gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            gradient.setColorAt(0.0, top)
            gradient.setColorAt(1.0, bottom)
            painter.fillPath(path, gradient)
            pen_color = QColor(255, 255, 255, 40)
        elif self._variant == "danger":
            fill = QColor(danger)
            fill.setAlpha(int(30 + 30 * self._hover))
            painter.fillPath(path, fill)
            pen_color = danger
        elif self._variant == "ghost":
            fill = accent
            fill.setAlpha(int(120 * self._hover))
            painter.fillPath(path, fill)
            pen_color = QColor(0, 0, 0, 0)
        else:
            surface = QColor("#FFFFFF") if not self._is_dark() else QColor("#2A3242")
            fill = _mix(surface, accent, 0.10 * self._hover)
            painter.fillPath(path, fill)
            pen_color = border_color

        if pen_color.alpha() > 0:
            pen = QPen(pen_color)
            pen.setWidthF(1.0)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)

        if self._variant in ("primary", "hero", "danger"):
            content_color = QColor("#FFFFFF") if self._variant != "danger" else danger
        else:
            content_color = text_color if self.isEnabled() else QColor("#A6AFBD")

        if self.hasFocus():
            focus = QPen(accent)
            focus.setWidthF(1.6)
            focus.setStyle(Qt.PenStyle.DotLine)
            painter.setPen(focus)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(2, 2, -2, -2), radius, radius)

        icon_width = 0
        if self._icon_name:
            size = 30 if self._variant == "hero" else 18
            pixmap = icons.pixmap(self._icon_name, content_color.name(), size)
            if not pixmap.isNull():
                icon_width = size + 10
                icon_x = rect.center().x() - (self.fontMetrics().horizontalAdvance(self.text()) + icon_width) / 2
                painter.drawPixmap(
                    int(icon_x),
                    int(rect.center().y() - size / 2),
                    pixmap,
                )

        if self._loading:
            spinner_size = 16
            center_y = rect.center().y()
            spinner_x = rect.center().x() - spinner_size / 2 - (
                self.fontMetrics().horizontalAdvance(self.text()) / 2 if self.text() else 0
            ) - 6
            spin_rect = QRectF(spinner_x - 8, center_y - spinner_size / 2, spinner_size, spinner_size)
            pen = QPen(content_color)
            pen.setWidthF(2.0)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawArc(spin_rect, int(-self._angle * 16), int(270 * 16))

        font = QFont(self.font())
        if self._variant in ("primary", "hero"):
            font.setWeight(QFont.Weight.DemiBold)
        if self._variant == "hero":
            font.setPixelSize(max(15, font.pixelSize() + 3))
        painter.setFont(font)
        painter.setPen(content_color)
        text_rect = rect.toRect()
        if self._icon_name or self._loading:
            text_rect = text_rect.adjusted(int(icon_width / 2) + (10 if self._loading else 0), 0, 0, 0)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, self.text())

    def _is_dark(self) -> bool:
        from PySide6.QtWidgets import QApplication

        window = QApplication.palette().window().color()
        return window.lightness() < 128
