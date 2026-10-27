"""渐变封面控件（版本详情页头部）。"""

from __future__ import annotations

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath
from PySide6.QtWidgets import QWidget

from pcl.ui import icons


class Cover(QWidget):
    """带矢量水印的渐变圆角封面。"""

    def __init__(self, parent: QWidget | None = None, *, min_height: int = 120) -> None:
        super().__init__(parent)
        self.setMinimumHeight(min_height)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        accent = self.palette().highlight().color()
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0.0, QColor(accent).lighter(110))
        gradient.setColorAt(0.6, QColor(accent).darker(108))
        gradient.setColorAt(1.0, QColor(accent).darker(135))
        path = QPainterPath()
        path.addRoundedRect(rect, 16, 16)
        painter.fillPath(path, gradient)

        mark = icons.pixmap("logo", "#FFFFFF", 180)
        if not mark.isNull():
            painter.setOpacity(0.12)
            painter.drawPixmap(int(rect.right() - 190), int(rect.top() + 8), mark)
            painter.setOpacity(1.0)
        painter.end()
