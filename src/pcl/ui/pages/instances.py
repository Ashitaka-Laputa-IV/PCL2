"""版本管理页：实例卡片网格、搜索、新建、导入与维护操作。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QVBoxLayout,
    QWidget,
)

from pcl.core.errors import friendly_message
from pcl.instance.model import Instance
from pcl.services import AppContext
from pcl.ui.dialogs.confirm import ask_text, confirm
from pcl.ui.dialogs.import_modpack import ImportModpackDialog
from pcl.ui.dialogs.new_instance import NewInstanceDialog
from pcl.ui.pages.base import BasePage
from pcl.ui.widgets.animated_button import AnimatedButton
from pcl.ui.widgets.card import Card
from pcl.ui.widgets.toast import show_toast


def _initial_pixmap(text: str, color: QColor, size: int = 48) -> QPixmap:
    """生成带首字母的圆角图标（按设备像素绘制，保证高分屏清晰）。"""
    dpr = 2
    pixmap = QPixmap(size * dpr, size * dpr)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    rect = QRectF(0, 0, size * dpr, size * dpr)
    path = QPainterPath()
    path.addRoundedRect(rect.adjusted(0, 0, -1, -1), 12 * dpr, 12 * dpr)
    painter.fillPath(path, QColor(color))
    painter.setPen(QColor("#FFFFFF"))
    font = QFont()
    font.setWeight(QFont.Weight.Bold)
    font.setPixelSize(int(size * dpr * 0.56))
    painter.setFont(font)
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, (text or "?").strip()[:1].upper())
    painter.end()
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


class InstanceCard(Card):
    """单个实例卡片。"""

    def __init__(self, instance: Instance, page: "InstancesPage", parent: QWidget | None = None) -> None:
        super().__init__(parent, hover_lift=True, padding=16, spacing=8)
        self.instance = instance
        self.page = page

        top = QHBoxLayout()
        top.setSpacing(12)

        icon = QLabel(self)
        accent = self.palette().highlight().color()
        icon.setPixmap(_initial_pixmap(instance.name, accent, 48))
        icon.setFixedSize(48, 48)
        top.addWidget(icon)

        texts = QVBoxLayout()
        texts.setSpacing(3)
        name = QLabel(instance.name, self)
        name.setStyleSheet("font-size: 15px; font-weight: 600;")
        name.setToolTip(instance.name)
        texts.addWidget(name)

        tags = QHBoxLayout()
        tags.setSpacing(6)
        version_tag = QLabel(instance.mc_version, self)
        version_tag.setObjectName("TagNeutral")
        tags.addWidget(version_tag)
        loader_text = instance.loader.capitalize() if instance.loader != "vanilla" else "原版"
        loader_tag = QLabel(loader_text, self)
        loader_tag.setObjectName("Tag")
        tags.addWidget(loader_tag)
        tags.addStretch(1)
        texts.addLayout(tags)
        top.addLayout(texts, 1)

        self.body.addLayout(top)

        hint = QLabel(
            "已安装" if page.context.version_installed(instance.version_id) else "游戏文件缺失，请先在下载页安装",
            self,
        )
        hint.setObjectName("Muted")
        self.body.addWidget(hint)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        launch = AnimatedButton("启动", self, variant="primary", icon_name="play", height=32)
        launch.clicked.connect(lambda: page.launch_instance(instance))
        more = AnimatedButton("更多", self, variant="secondary", height=32)
        more.clicked.connect(lambda: self._show_menu(more))
        actions.addWidget(launch)
        actions.addWidget(more)
        actions.addStretch(1)
        self.body.addLayout(actions)

    def _show_menu(self, anchor: QWidget) -> None:
        menu = QMenu(self)
        menu.addAction("打开游戏目录", lambda: self.page.open_game_dir(self.instance))
        menu.addAction("重命名", lambda: self.page.rename_instance(self.instance))
        menu.addAction("复制实例", lambda: self.page.duplicate_instance(self.instance))
        menu.addAction("导出整合包", lambda: self.page.export_instance(self.instance))
        menu.addSeparator()
        menu.addAction("删除实例", lambda: self.page.delete_instance(self.instance))
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))


class InstancesPage(BasePage):
    """实例列表页。"""

    title = "版本管理"
    subtitle = "管理已安装的实例、启动配置与整合包导出"

    def __init__(self, context: AppContext, bridge, tasks, parent: QWidget | None = None) -> None:
        super().__init__(context, bridge, tasks, parent)
        self._cards: list[InstanceCard] = []
        self._filter = ""
        self._build()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 22, 24, 20)
        outer.setSpacing(14)

        header = self.build_header()
        self._search = QLineEdit(self)
        self._search.setPlaceholderText("搜索实例…")
        self._search.setFixedWidth(200)
        self._search.textChanged.connect(self._on_search)
        header.addWidget(self._search)

        new_button = AnimatedButton("新建实例", self, variant="primary")
        new_button.clicked.connect(self.create_instance)
        import_button = AnimatedButton("导入整合包", self, variant="secondary")
        import_button.clicked.connect(self.import_modpack)
        header.addWidget(import_button)
        header.addWidget(new_button)
        outer.addLayout(header)

        self._summary = QLabel("", self)
        self._summary.setObjectName("Muted")
        outer.addWidget(self._summary)

        self._scroll, self._grid_root = self.make_scroll()
        self._grid = QGridLayout()
        self._grid.setSpacing(14)
        self._grid_root.addLayout(self._grid)
        self._grid_root.addStretch(1)
        outer.addWidget(self._scroll, 1)

        self._empty = QWidget(self)
        empty_layout = QVBoxLayout(self._empty)
        empty_layout.setContentsMargins(0, 40, 0, 40)
        empty_title = QLabel("还没有任何实例", self._empty)
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_title.setStyleSheet("font-size: 17px; font-weight: 600;")
        empty_hint = QLabel("新建一个实例来开始游戏，或导入现成的整合包", self._empty)
        empty_hint.setObjectName("PageSubtitle")
        empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_button = AnimatedButton("新建实例", self._empty, variant="primary")
        empty_button.clicked.connect(self.create_instance)
        empty_layout.addWidget(empty_title)
        empty_layout.addWidget(empty_hint)
        empty_layout.addWidget(empty_button, 0, Qt.AlignmentFlag.AlignHCenter)
        self._grid_root.addWidget(self._empty)

    # ---------- 刷新 ----------
    def refresh(self) -> None:
        self._rebuild()

    def _on_search(self, text: str) -> None:
        self._filter = text.strip().lower()
        self._rebuild()

    def _rebuild(self) -> None:
        for card in self._cards:
            card.deleteLater()
        self._cards.clear()

        instances = self.context.instances.list_instances()
        if self._filter:
            instances = [
                item
                for item in instances
                if self._filter in item.name.lower() or self._filter in item.mc_version.lower()
            ]

        self._summary.setText(f"共 {len(instances)} 个实例")
        self._empty.setVisible(not instances)

        columns = max(1, min(3, (self._scroll.width() - 24) // 300 or 1))
        for index, instance in enumerate(instances):
            card = InstanceCard(instance, self, self)
            self._grid.addWidget(card, index // columns, index % columns)
            self._cards.append(card)

    # ---------- 操作 ----------
    def launch_instance(self, instance: Instance) -> None:
        try:
            self.context.select_instance(instance.id)
        except Exception as error:
            show_toast(self, friendly_message(error), "error")
            return
        self.tasks.submit(
            self.context.launch,
            instance,
            on_done=lambda runner: self._on_launched(runner),
            on_error=lambda message: show_toast(self, message, "error"),
        )

    def _on_launched(self, runner) -> None:
        window = self.window()
        if hasattr(window, "show_log_window"):
            window.show_log_window(runner)
        show_toast(self, "游戏已启动", "success")

    def open_game_dir(self, instance: Instance) -> None:
        path = instance.game_dir(self.context.paths)
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

    def rename_instance(self, instance: Instance) -> None:
        name = ask_text(self, "重命名实例", "新的实例名称", instance.name)
        if not name:
            return
        try:
            self.context.instances.rename(instance.id, name)
        except Exception as error:
            show_toast(self, friendly_message(error), "error")
            return
        self._rebuild()

    def duplicate_instance(self, instance: Instance) -> None:
        try:
            clone = self.context.instances.duplicate(instance.id)
        except Exception as error:
            show_toast(self, friendly_message(error), "error")
            return
        show_toast(self, f"已复制为 {clone.name}", "success")
        self._rebuild()

    def delete_instance(self, instance: Instance) -> None:
        if not confirm(
            self,
            "删除实例",
            f"确定要删除「{instance.name}」吗？实例目录与其中的 Mod / 存档将一并删除。",
            ok_text="删除",
            danger=True,
        ):
            return
        try:
            self.context.instances.delete(instance.id)
        except Exception as error:
            show_toast(self, friendly_message(error), "error")
            return
        if self.context.config.selected_instance == instance.id:
            self.context.config.selected_instance = ""
            self.context.save_config()
        show_toast(self, "实例已删除", "success")
        self._rebuild()

    def export_instance(self, instance: Instance) -> None:
        path, selected = QFileDialog.getSaveFileName(
            self, "导出整合包", f"{instance.name}.zip", "整合包 (*.zip *.mrpack)"
        )
        if not path:
            return
        kind = "modrinth" if path.endswith(".mrpack") else "pcl"

        def done(result) -> None:
            show_toast(self, f"已导出：{result.path.name}", "success")

        self.tasks.submit(
            self._export,
            instance,
            Path(path),
            kind,
            on_done=done,
            on_error=lambda message: show_toast(self, message, "error"),
        )

    def _export(self, instance: Instance, path: Path, kind: str):
        from pcl.modpack import exporter

        return exporter.export(instance, self.context.paths, path, kind)

    def create_instance(self) -> None:
        dialog = NewInstanceDialog(self.context, self.tasks, self.bridge, self)
        if dialog.exec() == dialog.DialogCode.Accepted and dialog.result_instance is not None:
            self.context.select_instance(dialog.result_instance.id)
            self._rebuild()
            show_toast(self, f"已创建实例 {dialog.result_instance.name}", "success")

    def import_modpack(self) -> None:
        dialog = ImportModpackDialog(self.context, self.tasks, self.bridge, self)
        if dialog.exec() == dialog.DialogCode.Accepted and dialog.result is not None:
            self._rebuild()
            show_toast(self, f"已导入 {dialog.result.instance.name}", "success")

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._cards:
            self._rebuild()
