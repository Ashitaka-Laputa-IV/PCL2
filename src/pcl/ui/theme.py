"""主题系统：颜色 token、QSS 渲染与亮/暗切换。"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

from pcl.core.logging import get_logger

log = get_logger(__name__)

STYLE_FILE = Path(__file__).resolve().parent.parent / "resources" / "styles" / "base.qss"

LIGHT: dict[str, str] = {
    "bg": "#F4F6F9",
    "bg_alt": "#EAEEF5",
    "surface": "#FFFFFF",
    "surface_alt": "#F1F4F9",
    "surface_hover": "#E9F0FB",
    "text": "#1F2329",
    "text_muted": "#5F6B7A",
    "text_disabled": "#A6AFBD",
    "border": "#E5E8EE",
    "border_strong": "#D3D9E3",
    "success": "#22C55E",
    "warning": "#F59E0B",
    "danger": "#EF4444",
    "shadow": "rgba(31, 41, 55, 0.10)",
    "overlay": "rgba(15, 20, 30, 0.35)",
    "scroll": "#C9D1DE",
}

DARK: dict[str, str] = {
    "bg": "#151922",
    "bg_alt": "#1B2029",
    "surface": "#222936",
    "surface_alt": "#2A3242",
    "surface_hover": "#2F3A4D",
    "text": "#EDF1F7",
    "text_muted": "#9BA7B8",
    "text_disabled": "#5F6B7C",
    "border": "#333C4C",
    "border_strong": "#414C5F",
    "success": "#34D399",
    "warning": "#FBBF24",
    "danger": "#F87171",
    "shadow": "rgba(0, 0, 0, 0.45)",
    "overlay": "rgba(5, 8, 14, 0.55)",
    "scroll": "#404B5E",
}


def tokens_for(theme: str, accent: str, corner_radius: int = 8, font_scale: float = 1.0) -> dict[str, str]:
    """组合出完整 token 表。"""
    tokens = dict(DARK if theme == "dark" else LIGHT)
    tokens["accent"] = accent
    tokens["accent_hover"] = _lighten(accent, 0.12)
    tokens["accent_pressed"] = _darken(accent, 0.14)
    tokens["accent_soft"] = _alpha(accent, 0.14 if theme != "dark" else 0.22)
    tokens["radius"] = f"{int(corner_radius)}px"
    base = 13 * float(font_scale)
    tokens["font_size"] = f"{base:.1f}px"
    tokens["font_size_small"] = f"{base - 1:.1f}px"
    tokens["font_size_large"] = f"{base + 2:.1f}px"
    tokens["font_size_title"] = f"{base + 12:.1f}px"
    return tokens


def _clamp(value: float) -> int:
    return max(0, min(255, int(round(value))))


def _shift(color: str, factor: float) -> str:
    qcolor = QColor(color)
    if not qcolor.isValid():
        return color
    if factor >= 0:
        r = _clamp(qcolor.red() + (255 - qcolor.red()) * factor)
        g = _clamp(qcolor.green() + (255 - qcolor.green()) * factor)
        b = _clamp(qcolor.blue() + (255 - qcolor.blue()) * factor)
    else:
        r = _clamp(qcolor.red() * (1 + factor))
        g = _clamp(qcolor.green() * (1 + factor))
        b = _clamp(qcolor.blue() * (1 + factor))
    return f"#{r:02X}{g:02X}{b:02X}"


def _lighten(color: str, factor: float) -> str:
    return _shift(color, abs(factor))


def _darken(color: str, factor: float) -> str:
    return _shift(color, -abs(factor))


def _alpha(color: str, alpha: float) -> str:
    qcolor = QColor(color)
    if not qcolor.isValid():
        return color
    return f"rgba({qcolor.red()}, {qcolor.green()}, {qcolor.blue()}, {alpha:.2f})"


def load_template() -> str:
    """读取 QSS 模板（使用 ``%TOKEN%`` 占位符）。"""
    try:
        return STYLE_FILE.read_text(encoding="utf-8")
    except OSError as error:
        log.error("无法读取样式模板 %s: %s", STYLE_FILE, error)
        return ""


def render_stylesheet(tokens: Mapping[str, str], template: str | None = None) -> str:
    qss = template if template is not None else load_template()
    for key, value in tokens.items():
        qss = qss.replace(f"%{key.upper()}%", str(value))
    return qss


def apply_theme(
    app: QApplication,
    *,
    theme: str = "light",
    accent: str = "#2E7CF6",
    corner_radius: int = 8,
    font_scale: float = 1.0,
) -> dict[str, str]:
    """应用主题并返回使用的 token（供自绘控件读取）。"""
    tokens = tokens_for(theme, accent, corner_radius, font_scale)
    app.setStyleSheet(render_stylesheet(tokens))

    font = QFont()
    font.setFamilies(["PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", "Source Han Sans SC", "Segoe UI"])
    font.setPixelSize(max(11, int(13 * float(font_scale))))
    app.setFont(font)

    palette = app.palette()
    if theme == "dark":
        palette.setColor(QPalette.ColorRole.Window, QColor(tokens["bg"]))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(tokens["text"]))
        palette.setColor(QPalette.ColorRole.Base, QColor(tokens["surface"]))
        palette.setColor(QPalette.ColorRole.Text, QColor(tokens["text"]))
    else:
        palette.setColor(QPalette.ColorRole.Window, QColor(tokens["bg"]))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(tokens["text"]))
        palette.setColor(QPalette.ColorRole.Base, QColor(tokens["surface"]))
        palette.setColor(QPalette.ColorRole.Text, QColor(tokens["text"]))
    app.setPalette(palette)
    return tokens
