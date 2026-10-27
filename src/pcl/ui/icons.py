"""SVG 图标加载与着色（按需缓存）。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

ICON_DIR = Path(__file__).resolve().parent.parent / "resources" / "icons"
_cache: dict[tuple[str, str, int], QPixmap] = {}


def pixmap(name: str, color: str = "#5F6B7A", size: int = 22) -> QPixmap:
    """把图标渲染成指定颜色的位图。"""
    key = (name, color, size)
    cached = _cache.get(key)
    if cached is not None:
        return cached

    path = ICON_DIR / f"{name}.svg"
    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        return QPixmap()

    source = source.replace("currentColor", color).replace("#000", color)
    renderer = QSvgRenderer(QByteArray(source.encode("utf-8")))
    result = QPixmap(QSize(size, size) * 2)  # 2x 渲染，适配高分屏
    result.setDevicePixelRatio(2.0)
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    # 显式给出逻辑尺寸目标矩形，避免高分屏下按设备像素放大导致裁切
    renderer.render(painter, QRectF(0.0, 0.0, float(size), float(size)))
    painter.end()
    _cache[key] = result
    return result


def icon(name: str, color: str = "#5F6B7A", size: int = 22) -> QIcon:
    """返回 QIcon。"""
    return QIcon(pixmap(name, color, size))


def has_icon(name: str) -> bool:
    return (ICON_DIR / f"{name}.svg").is_file()
