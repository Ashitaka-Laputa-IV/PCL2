"""资源管理页：Mod / 资源包 / 光影的扫描、启停、导入与冲突提示。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from pcl.content.conflict import check_conflicts
from pcl.content.manager import ContentManager
from pcl.core.errors import friendly_message
from pcl.services import AppContext
from pcl.ui.dialogs.confirm import confirm
from pcl.ui.pages.base import BasePage
from pcl.ui.widgets.animated_button import AnimatedButton
from pcl.ui.widgets.card import Card
from pcl.ui.widgets.switch import ToggleSwitch
from pcl.ui.widgets.toast import show_toast

KIND_TABS = (("mods", "Mod"), ("resourcepacks", "资源包"), ("shaderpacks", "光影"))


def _letter_icon(text: str, size: int = 36) -> QPixmap:
    dpr = 2
    pixmap = QPixmap(size * dpr, size * dpr)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    rect = QRectF(0, 0, size * dpr, size * dpr)
    path = QPainterPath()
    path.addRoundedRect(rect.adjusted(0, 0, -1, -1), 8 * dpr, 8 * dpr)
    painter.fillPath(path, QColor("#E8EEF8"))
    painter.setPen(QColor("#5F6B7A"))
    font = QFont()
    font.setWeight(QFont.Weight.DemiBold)
    font.setPixelSize(int(size * dpr * 0.52))
    painter.setFont(font)
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, (text or "?")[:1].upper())
    painter.end()
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


class DropZone(QWidget):
    """空状态拖放区：虚线边框 + 说明文字。"""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(150)
        self.setMaximumHeight(190)
        self._text = text

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        accent = self.palette().highlight().color()
        rect = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        fill = QColor(accent)
        fill.setAlpha(10)
        painter.setBrush(fill)
        pen = QPen(accent)
        pen.setWidthF(1.4)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawRoundedRect(rect, 12, 12)

        painter.setPen(QColor(accent))
        font = QFont(self.font())
        font.setPixelSize(13)
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._text)
        painter.end()


class ContentRow(Card):
    """单条资源行。"""

    def __init__(self, page: "ContentPage", kind: str, item, parent: QWidget | None = None) -> None:
        super().__init__(parent, padding=12, spacing=0, object_name="Card")
        self.page = page
        self.kind = kind
        self.item = item

        row = QHBoxLayout()
        row.setSpacing(12)

        icon_label = QLabel(self)
        icon_label.setFixedSize(36, 36)
        if getattr(item, "icon", None):
            pixmap = QPixmap()
            if pixmap.loadFromData(item.icon):
                icon_label.setPixmap(pixmap.scaled(36, 36, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            else:
                icon_label.setPixmap(_letter_icon(item.display_name))
        else:
            icon_label.setPixmap(_letter_icon(item.display_name))
        row.addWidget(icon_label)

        texts = QVBoxLayout()
        texts.setSpacing(2)
        name = QLabel(item.display_name, self)
        name.setStyleSheet("font-size: 14px; font-weight: 600;")
        name.setToolTip(item.file_name)
        texts.addWidget(name)

        meta_parts: list[str] = []
        if getattr(item, "version_label", ""):
            meta_parts.append(item.version_label)
        if getattr(item, "loader", "") and item.loader not in ("unknown", ""):
            meta_parts.append(str(item.loader).capitalize())
        if getattr(item, "format_label", ""):
            meta_parts.append(item.format_label)
        if getattr(item, "error", None):
            meta_parts.append(f"⚠ {item.error}")
        meta = QLabel(" · ".join(meta_parts) or item.file_name, self)
        meta.setObjectName("Muted")
        meta.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        texts.addWidget(meta)
        row.addLayout(texts, 1)

        self._switch = ToggleSwitch(self)
        self._switch.setChecked(bool(item.enabled))
        self._switch.toggled.connect(self._on_toggled)
        row.addWidget(self._switch)

        remove = AnimatedButton("删除", self, variant="ghost", radius=8)
        remove.setFixedHeight(30)
        remove.clicked.connect(self._on_delete)
        row.addWidget(remove)

        container = QWidget(self)
        container.setLayout(row)
        container.setFixedHeight(56)
        self.body.addWidget(container)

    def _on_toggled(self, checked: bool) -> None:
        try:
            ContentManager.toggle(self.item.path)
        except Exception as error:
            show_toast(self.page, friendly_message(error), "error")
            self._switch.blockSignals(True)
            self._switch.setChecked(not checked)
            self._switch.blockSignals(False)
            return
        self.page.refresh()

    def _on_delete(self) -> None:
        if not confirm(self.page, "删除文件", f"确定删除「{self.item.display_name}」吗？", ok_text="删除", danger=True):
            return
        try:
            ContentManager.delete(self.item.path)
        except Exception as error:
            show_toast(self.page, friendly_message(error), "error")
            return
        show_toast(self.page, "已删除", "success")
        self.page.refresh()


class ContentPage(BasePage):
    """资源管理。"""

    title = "资源管理"
    subtitle = "管理当前实例的 Mod、资源包与光影"

    def __init__(self, context: AppContext, bridge, tasks, parent: QWidget | None = None) -> None:
        super().__init__(context, bridge, tasks, parent)
        self.setAcceptDrops(True)
        self._build()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 22, 24, 18)
        outer.setSpacing(12)

        header = self.build_header()
        self._instance_combo = QComboBox(self)
        self._instance_combo.setMinimumWidth(200)
        self._instance_combo.currentIndexChanged.connect(self._on_instance_changed)
        header.addWidget(self._instance_combo)

        open_dir = AnimatedButton("打开目录", self, variant="secondary")
        open_dir.clicked.connect(self._open_directory)
        import_button = AnimatedButton("导入文件", self, variant="primary")
        import_button.clicked.connect(self._import_files)
        header.addWidget(open_dir)
        header.addWidget(import_button)
        outer.addLayout(header)

        self._banner = Card(self, padding=12, spacing=0, object_name="CardFlat")
        self._banner_label = QLabel("", self._banner)
        self._banner_label.setWordWrap(True)
        self._banner.add(self._banner_label)
        self._banner.setVisible(False)
        outer.addWidget(self._banner)

        self._tabs = QTabWidget(self)
        self._pages: dict[str, tuple[QScrollArea, QVBoxLayout]] = {}
        for kind, label in KIND_TABS:
            area = QScrollArea(self)
            area.setWidgetResizable(True)
            area.setFrameShape(QScrollArea.Shape.NoFrame)
            area.setStyleSheet("QScrollArea { background: transparent; }")
            content = QWidget(area)
            layout = QVBoxLayout(content)
            layout.setContentsMargins(0, 10, 8, 8)
            layout.setSpacing(8)
            layout.addStretch(1)
            area.setWidget(content)
            self._tabs.addTab(area, label)
            self._pages[kind] = (area, layout)
        self._tabs.currentChanged.connect(lambda _index: self.refresh())
        outer.addWidget(self._tabs, 1)

        self._hint = QLabel("提示：可直接把 Mod / 资源包 / 光影文件拖入本页完成安装。", self)
        self._hint.setObjectName("Muted")
        outer.addWidget(self._hint)

    # ---------- 数据 ----------
    def _current_kind(self) -> str:
        index = self._tabs.currentIndex()
        return KIND_TABS[max(0, min(index, len(KIND_TABS) - 1))][0]

    def _manager(self) -> ContentManager | None:
        instance = self.context.current_instance()
        if instance is None:
            return None
        return ContentManager(self.context.paths, instance)

    def refresh(self) -> None:
        self._refresh_choices()
        manager = self._manager()
        kind = self._current_kind()
        _area, layout = self._pages[kind]

        # 清理旧行（保留末尾 stretch）
        while layout.count() > 1:
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        if manager is None:
            self._show_empty(layout, "还没有实例，请先在「版本管理」中创建。")
            self._banner.setVisible(False)
            return

        if kind == "mods":
            items = manager.mods()
            report = check_conflicts(items, manager.instance.mc_version)
            self._update_banner(report)
        elif kind == "resourcepacks":
            items = manager.resourcepacks()
            self._banner.setVisible(False)
        else:
            items = manager.shaderpacks()
            self._banner.setVisible(False)

        if not items:
            self._show_empty(layout, "把 Mod / 资源包 / 光影文件拖到这里，或点击右上角「导入文件」。")
            return

        for item in items:
            layout.insertWidget(layout.count() - 1, ContentRow(self, kind, item, self))
        self._hint.setText(f"目录：{manager.directory(kind)}")

    def _show_empty(self, layout: QVBoxLayout, text: str) -> None:
        zone = DropZone(text, self)
        layout.insertWidget(layout.count() - 1, zone)

    def _update_banner(self, report) -> None:
        if report.ok:
            self._banner.setVisible(False)
            return
        details: list[str] = []
        for name, dependency in report.missing[:8]:
            details.append(f"{name} 需要 {dependency.label}")
        for mod_id, items in report.duplicates.items():
            details.append(f"{mod_id} 存在重复版本：{', '.join(items)}")
        details.extend(report.mc_mismatch)
        for name, dependency in report.breaks[:5]:
            details.append(f"{name} 与 {dependency.label} 互斥")
        self._banner_label.setText(f"⚠ {report.summary()}\n" + "\n".join(details[:6]))
        self._banner_label.setStyleSheet("color: #B45309;")
        self._banner.setVisible(True)

    def _refresh_choices(self) -> None:
        instances = self.context.instances.list_instances()
        current = self.context.current_instance()
        self._instance_combo.blockSignals(True)
        self._instance_combo.clear()
        for instance in instances:
            self._instance_combo.addItem(f"{instance.name} · {instance.label}", instance.id)
        if current is not None:
            index = self._instance_combo.findData(current.id)
            if index >= 0:
                self._instance_combo.setCurrentIndex(index)
        self._instance_combo.blockSignals(False)

    def _on_instance_changed(self, index: int) -> None:
        instance_id = self._instance_combo.itemData(index)
        if instance_id:
            self.context.select_instance(str(instance_id))
            self.refresh()

    # ---------- 操作 ----------
    def _open_directory(self) -> None:
        manager = self._manager()
        if manager is None:
            show_toast(self, "请先创建实例", "warning")
            return
        path = manager.directory(self._current_kind())
        path.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform.startswith("win"):
                subprocess.Popen(["explorer", str(path)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except OSError as error:
            show_toast(self, f"无法打开目录：{error}", "error")

    def _import_files(self) -> None:
        manager = self._manager()
        if manager is None:
            show_toast(self, "请先创建实例", "warning")
            return
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择要导入的文件",
            str(Path.home()),
            "支持的文件 (*.jar *.zip *.disabled);;所有文件 (*)",
        )
        if files:
            self._install([Path(item) for item in files])

    def _install(self, sources: list[Path]) -> None:
        manager = self._manager()
        if manager is None:
            show_toast(self, "请先创建实例", "warning")
            return

        def done(installed) -> None:
            show_toast(self, f"已导入 {len(installed)} 个文件", "success")
            self.refresh()

        self.tasks.submit(
            manager.install_files,
            sources,
            on_done=done,
            on_error=lambda message: show_toast(self, message, "error"),
        )

    # ---------- 拖放 ----------
    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            kind = {"mods": "mods", "resourcepacks": "resourcepacks", "shaderpacks": "shaderpacks"}[self._current_kind()]
            manager = self._manager()
            if manager is not None:
                def done(installed) -> None:
                    show_toast(self, f"已导入 {len(installed)} 个文件", "success")
                    self.refresh()

                self.tasks.submit(
                    manager.install_files,
                    paths,
                    kind,
                    on_done=done,
                    on_error=lambda message: show_toast(self, message, "error"),
                )
        event.acceptProposedAction()
