"""实例详情页：概览、设置、内容、存档与服务器的统一入口。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from pcl.content.conflict import check_conflicts
from pcl.content.manager import ContentManager
from pcl.content.updates import ModUpdate, check_mod_updates, download_update
from pcl.core.errors import friendly_message
from pcl.instance.model import Instance
from pcl.instance.saves import WorldInfo, backup_world, delete_world, scan_worlds
from pcl.instance.servers import ServerEntry, ServerList
from pcl.services import AppContext
from pcl.ui.dialogs.confirm import ask_text, confirm
from pcl.ui import icons
from pcl.ui.dialogs.crash_report import show_crash_report
from pcl.ui.pages.base import BasePage
from pcl.ui.widgets.animated_button import AnimatedButton
from pcl.ui.widgets.cover import Cover
from pcl.ui.widgets.card import Card
from pcl.ui.widgets.switch import ToggleSwitch
from pcl.ui.widgets.toast import show_toast

TAB_KINDS = (("mods", "Mod"), ("resourcepacks", "资源包"), ("shaderpacks", "光影"))


def _rounded_icon(text: str, size: int = 56, color: QColor | None = None) -> QPixmap:
    dpr = 2
    pixmap = QPixmap(size * dpr, size * dpr)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    rect = QRectF(0, 0, size * dpr, size * dpr)
    path = QPainterPath()
    path.addRoundedRect(rect.adjusted(0, 0, -1, -1), 16 * dpr, 16 * dpr)
    painter.fillPath(path, color or QColor("#2E7CF6"))
    painter.setPen(QColor("#FFFFFF"))
    font = QFont()
    font.setWeight(QFont.Weight.Bold)
    font.setPixelSize(int(size * dpr * 0.5))
    painter.setFont(font)
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, (text or "?")[:1].upper())
    painter.end()
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


class InstanceDetailPage(BasePage):
    """实例详情。"""

    title = "实例详情"
    subtitle = ""

    def __init__(self, context: AppContext, bridge, tasks, parent: QWidget | None = None) -> None:
        super().__init__(context, bridge, tasks, parent)
        self.instance: Instance | None = None
        self._build()

    # ---------- 构建 ----------
    def _build(self) -> None:
        self._content_ui: dict[str, tuple[QLabel, QVBoxLayout]] = {}
        self._content_tools: dict[str, QWidget] = {}
        self._updates: dict[str, ModUpdate] = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 20, 24, 16)
        outer.setSpacing(12)

        back_row = QHBoxLayout()
        back = AnimatedButton("← 返回", self, variant="ghost", height=28)
        back.clicked.connect(lambda: self.window().navigate("instances") if hasattr(self.window(), "navigate") else None)
        back_row.addWidget(back)
        back_row.addStretch(1)
        outer.addLayout(back_row)

        self._cover = Cover(self)
        header = QHBoxLayout(self._cover)
        header.setContentsMargins(22, 18, 22, 18)
        header.setSpacing(16)

        self._icon = QLabel(self._cover)
        self._icon.setFixedSize(64, 64)
        header.addWidget(self._icon)

        titles = QVBoxLayout()
        titles.setSpacing(4)
        self._name = QLabel("选择一个实例", self._cover)
        self._name.setStyleSheet("color: #FFFFFF; font-size: 22px; font-weight: 700;")
        self._tags = QLabel("", self._cover)
        self._tags.setStyleSheet("color: rgba(255,255,255,0.82); font-size: 12px;")
        titles.addWidget(self._name)
        titles.addWidget(self._tags)
        header.addLayout(titles, 1)

        self._crash_button = AnimatedButton("崩溃分析", self._cover, variant="secondary", height=34)
        self._crash_button.clicked.connect(self._analyze_crash)
        self._launch_button = AnimatedButton("启动", self._cover, variant="white", icon_name="play", height=38)
        self._launch_button.clicked.connect(self._launch)
        header.addWidget(self._crash_button)
        header.addWidget(self._launch_button)
        outer.addWidget(self._cover)

        self._tabs = QTabWidget(self)
        self._tabs.setDocumentMode(True)
        self._overview = self._build_overview()
        self._settings = self._build_settings()
        self._content_tabs: dict[str, QWidget] = {}
        self._tabs.addTab(self._overview, "概览")
        self._tabs.addTab(self._settings, "设置")
        for kind, label in TAB_KINDS:
            widget = self._build_content(kind)
            self._content_tabs[kind] = widget
            self._tabs.addTab(widget, label)
        self._worlds = self._build_worlds()
        self._tabs.addTab(self._worlds, "存档")
        self._servers = self._build_servers()
        self._tabs.addTab(self._servers, "服务器")
        self._tabs.currentChanged.connect(self._on_tab_changed)
        outer.addWidget(self._tabs, 1)

    # ---------- 概览 ----------
    def _build_overview(self) -> QWidget:
        page = QWidget(self)
        layout = QHBoxLayout(page)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(14)

        left = QVBoxLayout()
        left.setSpacing(14)

        info_card = Card(page, padding=20, spacing=8)
        title = QLabel("版本信息", info_card)
        title.setObjectName("SectionTitle")
        info_card.add(title)
        self._info_grid = QGridLayout()
        self._info_grid.setHorizontalSpacing(18)
        self._info_grid.setVerticalSpacing(8)
        info_card.add_layout(self._info_grid)
        left.addWidget(info_card)

        action_card = Card(page, padding=20, spacing=10)
        action_title = QLabel("快捷操作", action_card)
        action_title.setObjectName("SectionTitle")
        action_card.add(action_title)
        actions = QGridLayout()
        actions.setSpacing(8)
        action_defs = (
            ("打开游戏目录", "folder", self._open_game_dir),
            ("重命名实例", "edit", self._rename),
            ("复制实例", "copy", self._duplicate),
            ("选择图标", "content", self._pick_icon),
            ("导出整合包", "download", self._export),
            ("删除实例", "trash", self._delete),
        )
        for index, (label, icon, handler) in enumerate(action_defs):
            button = AnimatedButton(label, action_card, variant="secondary", icon_name=icon, height=36)
            button.clicked.connect(handler)
            actions.addWidget(button, index // 3, index % 3)
        action_card.add_layout(actions)
        left.addWidget(action_card)
        left.addStretch(1)
        layout.addLayout(left, 3)

        log_card = Card(page, padding=20, spacing=8)
        log_title = QLabel("最近日志", log_card)
        log_title.setObjectName("SectionTitle")
        log_card.add(log_title)
        self._log_preview = QPlainTextEdit(log_card)
        self._log_preview.setReadOnly(True)
        self._log_preview.setMaximumBlockCount(200)
        self._log_preview.setStyleSheet(
            "QPlainTextEdit { font-family: monospace; font-size: 12px;"
            " background: rgba(127,127,127,0.06); border: none; border-radius: 8px; }"
        )
        log_card.add(self._log_preview, 1)
        refresh_log = AnimatedButton("刷新日志", log_card, variant="secondary", height=30)
        refresh_log.clicked.connect(self._load_log_preview)
        log_card.add(refresh_log, 0, Qt.AlignmentFlag.AlignRight)
        layout.addWidget(log_card, 2)
        return page

    # ---------- 设置 ----------
    def _build_settings(self) -> QWidget:
        page = QWidget(self)
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 12, 8, 0)
        outer.setSpacing(10)

        scroll = QScrollArea(page)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content = QWidget(scroll)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 8, 8)
        layout.setSpacing(14)

        base = Card(content, padding=20, spacing=10)
        base_title = QLabel("启动设置（留空或 0 表示跟随全局）", base)
        base_title.setObjectName("SectionTitle")
        base.add(base_title)

        self._java = QComboBox(base)
        self._java.setMinimumWidth(320)
        base.add(self._row("Java 运行时", self._java))

        self._memory = QSpinBox(base)
        self._memory.setRange(0, 65536)
        self._memory.setSingleStep(512)
        self._memory.setSuffix(" MB")
        self._memory.setSpecialValueText("跟随全局")
        self._memory.setFixedWidth(150)
        self._memory.setMaximumWidth(150)
        base.add(self._row("最大内存", self._memory))

        resolution = QWidget(base)
        resolution_layout = QHBoxLayout(resolution)
        resolution_layout.setContentsMargins(0, 0, 0, 0)
        resolution_layout.setSpacing(6)
        self._width = QSpinBox(base)
        self._width.setRange(0, 15360)
        self._width.setSpecialValueText("默认")
        self._width.setSuffix(" px")
        self._width.setFixedWidth(110)
        self._height = QSpinBox(base)
        self._height.setRange(0, 8640)
        self._height.setSpecialValueText("默认")
        self._height.setSuffix(" px")
        self._height.setFixedWidth(110)
        resolution_layout.addWidget(self._width)
        resolution_layout.addWidget(QLabel("×", base))
        resolution_layout.addWidget(self._height)
        resolution_layout.addStretch(1)
        base.add(self._row("窗口尺寸", resolution))

        self._server_edit = QLineEdit(base)
        self._server_edit.setPlaceholderText("例如 play.example.com:25565")
        self._server_edit.setMinimumWidth(320)
        base.add(self._row("启动后进入服务器", self._server_edit))
        layout.addWidget(base)

        advance = Card(content, padding=20, spacing=10)
        advance_title = QLabel("高级", advance)
        advance_title.setObjectName("SectionTitle")
        advance.add(advance_title)
        self._jvm_args = QLineEdit(advance)
        self._jvm_args.setPlaceholderText("追加的 JVM 参数，空格分隔")
        self._jvm_args.setMinimumWidth(320)
        advance.add(self._row("JVM 参数", self._jvm_args))
        self._game_args = QLineEdit(advance)
        self._game_args.setPlaceholderText("追加的游戏参数，空格分隔")
        self._game_args.setMinimumWidth(320)
        advance.add(self._row("游戏参数", self._game_args))
        self._notes = QLineEdit(advance)
        self._notes.setPlaceholderText("备注")
        advance.add(self._row("备注", self._notes))
        layout.addWidget(advance)

        save_row = QHBoxLayout()
        save_row.addStretch(1)
        save = AnimatedButton("保存设置", content, variant="primary")
        save.clicked.connect(self._save_settings)
        save_row.addWidget(save)
        layout.addLayout(save_row)
        layout.addStretch(1)

        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        return page

    def _row(self, label: str, control: QWidget) -> QWidget:
        row = QWidget(self)
        control.setParent(row)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(12)
        text = QLabel(label, row)
        text.setMinimumWidth(150)
        layout.addWidget(text)
        layout.addWidget(control, 1)
        return row

    # ---------- 内容 ----------
    def _build_content(self, kind: str) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(8)

        banner = QLabel("", page)
        banner.setWordWrap(True)
        banner.setVisible(False)
        banner.setStyleSheet("color: #B45309;")
        layout.addWidget(banner)

        tools = QWidget(page)
        tool_row = QHBoxLayout(tools)
        tool_row.setContentsMargins(0, 0, 0, 0)
        tool_row.setSpacing(8)
        if kind == "mods":
            check = AnimatedButton("检查更新", tools, variant="secondary", height=28)
            check.clicked.connect(lambda _=False, button=check: self._check_updates(button))
            tool_row.addWidget(check)
        enable_all = AnimatedButton("全部启用", tools, variant="ghost", height=28)
        enable_all.clicked.connect(lambda: self._set_all_enabled(kind, True))
        tool_row.addWidget(enable_all)
        disable_all = AnimatedButton("全部禁用", tools, variant="ghost", height=28)
        disable_all.clicked.connect(lambda: self._set_all_enabled(kind, False))
        tool_row.addWidget(disable_all)
        tool_row.addStretch(1)
        tools.setVisible(False)
        layout.addWidget(tools)
        self._content_tools[kind] = tools

        scroll = QScrollArea(page)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content = QWidget(scroll)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 8, 8)
        content_layout.setSpacing(8)
        content_layout.addStretch(1)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        footer = QHBoxLayout()
        hint = QLabel("可直接把文件拖入本页安装。", page)
        hint.setObjectName("Muted")
        footer.addWidget(hint, 1)
        open_dir = AnimatedButton("打开目录", page, variant="secondary", icon_name="folder", height=32)
        open_dir.clicked.connect(lambda: self._open_content_dir(kind))
        import_file = AnimatedButton("导入文件", page, variant="primary", height=32)
        import_file.clicked.connect(lambda: self._import_content(kind))
        footer.addWidget(open_dir)
        footer.addWidget(import_file)
        layout.addLayout(footer)

        self._content_ui[kind] = (banner, content_layout)
        return page

    # ---------- 存档 ----------
    def _build_worlds(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(8)
        self._worlds_list = QVBoxLayout()
        self._worlds_list.setSpacing(8)
        layout.addLayout(self._worlds_list)
        layout.addStretch(1)
        return page

    # ---------- 服务器 ----------
    def _build_servers(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(8)
        self._server_list_layout = QVBoxLayout()
        self._server_list_layout.setSpacing(8)
        layout.addLayout(self._server_list_layout)
        layout.addStretch(1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        add = AnimatedButton("添加服务器", page, variant="primary", icon_name="server", height=34)
        add.clicked.connect(self._add_server)
        footer.addWidget(add)
        layout.addLayout(footer)
        return page

    # ---------- 打开实例 ----------
    def open_instance(self, instance: Instance) -> None:
        self.instance = instance
        self._refresh_header()
        self._tabs.setCurrentIndex(0)
        self._load_overview()
        self._load_settings()
        self._reload_content_all()

    def _refresh_header(self) -> None:
        instance = self.instance
        if instance is None:
            return
        self._name.setText(instance.name)
        loader = "原版" if instance.loader == "vanilla" else instance.loader.capitalize()
        parts = [f"Minecraft {instance.mc_version}", loader]
        if instance.loader_version:
            parts.append(instance.loader_version)
        played = f"启动 {instance.play_count} 次"
        parts.append(played)
        self._tags.setText(" · ".join(parts))

        icon_path = Path(instance.icon_path) if instance.icon_path else None
        if icon_path and icon_path.is_file():
            pixmap = QPixmap(str(icon_path))
            if not pixmap.isNull():
                self._icon.setPixmap(
                    pixmap.scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
                )
                return
        self._icon.setPixmap(_rounded_icon(instance.name, 64, self.palette().highlight().color().lighter(118)))

    def _load_overview(self) -> None:
        instance = self.instance
        if instance is None:
            return
        while self._info_grid.count():
            item = self._info_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        game_dir = instance.game_dir(self.context.paths)
        rows = [
            ("游戏版本", instance.mc_version),
            ("加载器", "原版" if instance.loader == "vanilla" else instance.loader.capitalize()),
            ("加载器版本", instance.loader_version or "—"),
            ("启动版本", instance.version_id),
            ("游戏目录", str(game_dir)),
            ("Java 路径", instance.java_path or "跟随全局设置"),
            ("最大内存", f"{instance.memory_mb} MB" if instance.memory_mb else "跟随全局设置"),
            ("自动进入", instance.server or "—"),
        ]
        for index, (key, value) in enumerate(rows):
            label = QLabel(f"{key}", self)
            label.setObjectName("Muted")
            label.setMinimumWidth(80)
            value_label = QLabel(str(value), self)
            value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value_label.setWordWrap(True)
            self._info_grid.addWidget(label, index, 0)
            self._info_grid.addWidget(value_label, index, 1)
        self._load_log_preview()

    def _load_log_preview(self) -> None:
        path = self.context.paths.game_log
        if not path.is_file():
            self._log_preview.setPlainText("暂无日志，启动一次游戏后会显示最近输出。")
            return
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            self._log_preview.setPlainText("日志读取失败。")
            return
        lines = text.splitlines()[-40:]
        self._log_preview.setPlainText("\n".join(lines))
        bar = self._log_preview.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _load_settings(self) -> None:
        instance = self.instance
        if instance is None:
            return
        self._java.blockSignals(True)
        self._java.clear()
        self._java.addItem("跟随全局设置", "")
        for runtime in self.context.java_runtimes:
            self._java.addItem(f"{runtime.label}", str(runtime.path))
        if instance.java_path:
            self._java.addItem(f"自定义：{instance.java_path}", instance.java_path)
        index = self._java.findData(instance.java_path or "")
        self._java.setCurrentIndex(max(0, index))
        self._java.blockSignals(False)

        self._memory.setValue(instance.memory_mb)
        self._width.setValue(instance.window_width)
        self._height.setValue(instance.window_height)
        self._server_edit.setText(instance.server)
        self._jvm_args.setText(" ".join(instance.jvm_args))
        self._game_args.setText(" ".join(instance.game_args))
        self._notes.setText(instance.notes)

    def _save_settings(self) -> None:
        instance = self.instance
        if instance is None:
            return
        instance.java_path = str(self._java.currentData() or "")
        instance.memory_mb = self._memory.value()
        instance.window_width = self._width.value()
        instance.window_height = self._height.value()
        instance.server = self._server_edit.text().strip()
        instance.jvm_args = self._jvm_args.text().split()
        instance.game_args = self._game_args.text().split()
        instance.notes = self._notes.text().strip()
        try:
            self.context.instances.save(instance)
        except Exception as error:
            show_toast(self, friendly_message(error), "error")
            return
        self._load_overview()
        self._refresh_header()
        show_toast(self, "实例设置已保存", "success")

    # ---------- 内容刷新 ----------
    def _on_tab_changed(self, index: int) -> None:
        if index == 0:
            self._load_overview()
        elif index == 1:
            self._load_settings()
        elif 2 <= index <= 4:
            self._reload_content(TAB_KINDS[index - 2][0])
        elif index == 5:
            self._load_worlds()
        elif index == 6:
            self._load_servers()

    def _reload_content_all(self) -> None:
        for kind, _label in TAB_KINDS:
            self._reload_content(kind)

    def _reload_content(self, kind: str) -> None:
        instance = self.instance
        widget = self._content_tabs.get(kind)
        if instance is None or widget is None:
            return
        banner, layout = self._content_ui[kind]
        while layout.count() > 1:
            item = layout.takeAt(0)
            child = item.widget()
            if child is not None:
                child.deleteLater()

        manager = ContentManager(self.context.paths, instance)
        if kind == "mods":
            items = manager.mods()
            report = check_conflicts(items, instance.mc_version)
            if report.ok:
                banner.setVisible(False)
            else:
                banner.setText(f"⚠ {report.summary()}　点击「Mod」页签可逐个禁用排查。")
                banner.setVisible(True)
        elif kind == "resourcepacks":
            items = manager.resourcepacks()
            banner.setVisible(False)
        else:
            items = manager.shaderpacks()
            banner.setVisible(False)

        tools = self._content_tools.get(kind)
        if tools is not None:
            tools.setVisible(bool(items))
        if not items:
            layout.insertWidget(
                layout.count() - 1,
                self._empty_hint("这里还是空的，点击右下角「导入文件」或直接拖入。", 120),
            )
            return

        for item in items:
            layout.insertWidget(layout.count() - 1, self._content_row(manager, kind, item))

    def _content_row(self, manager: ContentManager, kind: str, item) -> QWidget:
        row = Card(self, padding=12, spacing=0)
        line = QHBoxLayout()
        line.setSpacing(11)

        icon = QLabel(row)
        icon.setFixedSize(36, 36)
        pixmap = QPixmap()
        data = getattr(item, "icon", None)
        if data and pixmap.loadFromData(data):
            icon.setPixmap(
                pixmap.scaled(36, 36, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            )
        else:
            icon.setPixmap(_rounded_icon(item.display_name, 36, self.palette().highlight().color()))
        line.addWidget(icon)

        texts = QVBoxLayout()
        texts.setSpacing(2)
        name = QLabel(item.display_name, row)
        name.setStyleSheet("font-size: 13px; font-weight: 600;")
        name.setToolTip(getattr(item, "file_name", item.display_name))
        texts.addWidget(name)
        meta_parts = [getattr(item, "file_name", "")]
        version = getattr(item, "version_label", "") or getattr(item, "format_label", "")
        if version:
            meta_parts.append(version)
        loader = getattr(item, "loader", "")
        if loader and loader != "unknown":
            meta_parts.append(loader.capitalize())
        meta = QLabel(" · ".join(part for part in meta_parts if part), row)
        meta.setObjectName("Muted")
        texts.addWidget(meta)
        line.addLayout(texts, 1)

        update = self._updates.get(str(item.path))
        if update is not None:
            bump = AnimatedButton(f"↑ 更新 {update.latest_version}", row, variant="secondary", height=28)
            bump.setToolTip(f"Modrinth 最新版本 {update.latest_version}，点击下载并替换")
            bump.clicked.connect(lambda _=False, target=update: self._apply_update(target))
            line.addWidget(bump)

        switch = ToggleSwitch(row)
        switch.setChecked(bool(item.enabled))
        switch.toggled.connect(lambda checked, target=item: self._toggle_content(target))
        line.addWidget(switch)
        remove = AnimatedButton("", row, variant="ghost", icon_name="trash", height=28)
        remove.setFixedWidth(34)
        remove.setToolTip("删除")
        remove.clicked.connect(lambda _=False, target=item: self._delete_content(target))
        line.addWidget(remove)

        container = QWidget(row)
        container.setLayout(line)
        row.add(container)
        return row

    def _toggle_content(self, item) -> None:
        try:
            ContentManager.toggle(item.path)
        except Exception as error:
            show_toast(self, friendly_message(error), "error")
        self._reload_content_all()

    def _delete_content(self, item) -> None:
        if not confirm(self, "删除文件", f"确定删除「{item.display_name}」吗？", ok_text="删除", danger=True):
            return
        try:
            ContentManager.delete(item.path)
        except Exception as error:
            show_toast(self, friendly_message(error), "error")
            return
        self._reload_content_all()

    def _set_all_enabled(self, kind: str, enabled: bool) -> None:
        instance = self.instance
        if instance is None:
            return
        manager = ContentManager(self.context.paths, instance)
        if kind == "mods":
            items = manager.mods()
        elif kind == "resourcepacks":
            items = manager.resourcepacks()
        else:
            items = manager.shaderpacks()
        changed = 0
        failed = 0
        for item in items:
            try:
                ContentManager.set_enabled(item.path, enabled)
                changed += 1
            except Exception:
                failed += 1
        self._updates.clear()
        self._reload_content_all()
        text = f"已{'启用' if enabled else '禁用'} {changed} 项"
        if failed:
            text += f"，{failed} 项失败"
        show_toast(self, text, "success" if not failed else "error")

    def _check_updates(self, button) -> None:
        instance = self.instance
        if instance is None:
            return
        manager = ContentManager(self.context.paths, instance)
        mods = [item for item in manager.mods() if item.enabled]
        if not mods:
            show_toast(self, "当前实例没有启用的 Mod", "error")
            return
        button.set_loading(True, "检查中…")

        def done(report) -> None:
            button.set_loading(False, "检查更新")
            self._updates = {str(update.path): update for update in report.updates}
            self._reload_content_all()
            if report.error:
                show_toast(self, f"检查失败：{report.error}", "error")
            elif report.updates:
                show_toast(self, f"发现 {len(report.updates)} 个可更新 Mod", "success")
            else:
                show_toast(self, f"已检查 {report.checked} 个 Mod，均为最新", "success")

        def failed(message: str) -> None:
            button.set_loading(False, "检查更新")
            show_toast(self, message, "error")

        self.tasks.submit(
            check_mod_updates,
            mods,
            self.context.client(),
            mc_version=instance.mc_version,
            loader=instance.loader,
            on_done=done,
            on_error=failed,
        )

    def _apply_update(self, update: ModUpdate) -> None:
        dest = update.path.parent / update.latest_filename

        def done(_path) -> None:
            if str(dest) != str(update.path) and update.path.exists():
                try:
                    ContentManager.set_enabled(update.path, False)
                except Exception:
                    pass
            self._updates.pop(str(update.path), None)
            show_toast(self, f"{update.file_name} 已更新到 {update.latest_version}", "success")
            self._reload_content_all()

        def failed(message: str) -> None:
            show_toast(self, message, "error")

        self.tasks.submit(
            download_update,
            self.context.client(),
            update,
            dest,
            on_done=done,
            on_error=failed,
        )

    def _open_content_dir(self, kind: str) -> None:
        instance = self.instance
        if instance is None:
            return
        manager = ContentManager(self.context.paths, instance)
        self._open_path(manager.directory(kind))

    def _import_content(self, kind: str) -> None:
        instance = self.instance
        if instance is None:
            return
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择要导入的文件", str(Path.home()), "支持的文件 (*.jar *.zip *.disabled);;所有文件 (*)"
        )
        if not files:
            return
        manager = ContentManager(self.context.paths, instance)

        def done(installed) -> None:
            show_toast(self, f"已导入 {len(installed)} 个文件", "success")
            self._reload_content_all()

        self.tasks.submit(
            manager.install_files,
            [Path(item) for item in files],
            kind,
            on_done=done,
            on_error=lambda message: show_toast(self, message, "error"),
        )

    # ---------- 存档 ----------
    def _empty_hint(self, text: str, height: int = 150) -> QWidget:
        box = QWidget(self)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 18, 0, 18)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon = QLabel(box)
        icon.setPixmap(icons.pixmap("inbox", "#94A3B8", 44))
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label = QLabel(text, box)
        label.setObjectName("Muted")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon)
        layout.addWidget(label)
        box.setMinimumHeight(height)
        return box

    def _load_worlds(self) -> None:
        while self._worlds_list.count():
            item = self._worlds_list.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        instance = self.instance
        if instance is None:
            return
        worlds = scan_worlds(instance.game_dir(self.context.paths))
        if not worlds:
            self._worlds_list.addWidget(self._empty_hint("还没有存档，进入游戏创建世界后会显示在这里。"))
            return

        for world in worlds:
            self._worlds_list.addWidget(self._world_card(world))

    def _world_card(self, world: WorldInfo) -> QWidget:
        card = Card(self, padding=14, spacing=0)
        line = QHBoxLayout()
        line.setSpacing(12)

        icon = QLabel(card)
        icon.setFixedSize(44, 44)
        if world.icon:
            pixmap = QPixmap()
            if pixmap.loadFromData(world.icon):
                icon.setPixmap(pixmap.scaled(44, 44, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                line.addWidget(icon)
        if icon.pixmap().isNull():
            icon.setPixmap(_rounded_icon(world.name, 44, QColor("#22C55E")))
            line.addWidget(icon)

        texts = QVBoxLayout()
        texts.setSpacing(2)
        name = QLabel(world.name, card)
        name.setStyleSheet("font-weight: 600;")
        texts.addWidget(name)
        meta_parts = [world.folder]
        if world.game_version:
            meta_parts.append(world.game_version)
        meta_parts.append(f"最近游玩 {world.last_played_label}")
        meta_parts.append(world.size_label)
        info = QLabel(" · ".join(meta_parts), card)
        info.setObjectName("Muted")
        texts.addWidget(info)
        line.addLayout(texts, 1)

        open_dir = AnimatedButton("打开", card, variant="secondary", height=30)
        open_dir.clicked.connect(lambda _=False, target=world: self._open_path(target.path))
        backup = AnimatedButton("备份", card, variant="secondary", height=30)
        backup.clicked.connect(lambda _=False, target=world: self._backup_world(target))
        remove = AnimatedButton("删除", card, variant="ghost", height=30)
        remove.clicked.connect(lambda _=False, target=world: self._delete_world(target))
        line.addWidget(open_dir)
        line.addWidget(backup)
        line.addWidget(remove)

        container = QWidget(card)
        container.setLayout(line)
        card.add(container)
        return card

    def _backup_world(self, world: WorldInfo) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "备份存档", f"{world.folder}-backup.zip", "压缩包 (*.zip)")
        if not path:
            return
        try:
            backup_world(world, Path(path))
        except Exception as error:
            show_toast(self, friendly_message(error), "error")
            return
        show_toast(self, "存档已备份", "success")

    def _delete_world(self, world: WorldInfo) -> None:
        if not confirm(
            self, "删除存档", f"确定删除存档「{world.name}」吗？此操作不可恢复。", ok_text="删除", danger=True
        ):
            return
        try:
            delete_world(world)
        except Exception as error:
            show_toast(self, friendly_message(error), "error")
            return
        self._load_worlds()

    # ---------- 服务器 ----------
    def _server_list(self) -> ServerList | None:
        instance = self.instance
        if instance is None:
            return None
        return ServerList(instance.game_dir(self.context.paths) / "servers.dat")

    def _load_servers(self) -> None:
        while self._server_list_layout.count():
            item = self._server_list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        server_list = self._server_list()
        if server_list is None:
            return
        entries = server_list.load()
        if not entries:
            self._server_list_layout.addWidget(
                self._empty_hint("还没有服务器，添加后可以一键启动并连接。")
            )
            return
        for entry in entries:
            self._server_list_layout.addWidget(self._server_card(entry))

    def _server_card(self, entry: ServerEntry) -> QWidget:
        card = Card(self, padding=14, spacing=0)
        line = QHBoxLayout()
        line.setSpacing(12)
        icon = QLabel(card)
        icon.setFixedSize(40, 40)
        data = entry.icon_bytes()
        pixmap = QPixmap()
        if data and pixmap.loadFromData(data):
            icon.setPixmap(pixmap.scaled(40, 40, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        else:
            icon.setPixmap(_rounded_icon(entry.name, 40, QColor("#0EA5E9")))
        line.addWidget(icon)

        texts = QVBoxLayout()
        texts.setSpacing(2)
        name = QLabel(entry.name, card)
        name.setStyleSheet("font-weight: 600;")
        texts.addWidget(name)
        info = QLabel(entry.ip, card)
        info.setObjectName("Muted")
        texts.addWidget(info)
        line.addLayout(texts, 1)

        connect = AnimatedButton("启动并连接", card, variant="primary", height=30)
        connect.clicked.connect(lambda _=False, target=entry: self._connect_server(target))
        remove = AnimatedButton("删除", card, variant="ghost", height=30)
        remove.clicked.connect(lambda _=False, target=entry: self._remove_server(target))
        line.addWidget(connect)
        line.addWidget(remove)
        container = QWidget(card)
        container.setLayout(line)
        card.add(container)
        return card

    def _add_server(self) -> None:
        name = ask_text(self, "添加服务器", "服务器名称", placeholder="我的服务器")
        if not name:
            return
        address = ask_text(self, "添加服务器", "服务器地址（可带端口，如 play.example.com:25565）")
        if not address:
            return
        server_list = self._server_list()
        if server_list is None:
            return
        try:
            server_list.add(name, address)
        except Exception as error:
            show_toast(self, friendly_message(error), "error")
            return
        show_toast(self, "已添加服务器", "success")
        self._load_servers()

    def _remove_server(self, entry: ServerEntry) -> None:
        if not confirm(self, "删除服务器", f"确定删除「{entry.name}」吗？", ok_text="删除", danger=True):
            return
        server_list = self._server_list()
        if server_list is not None:
            server_list.load()
            for item in server_list.entries:
                if item.ip == entry.ip and item.name == entry.name:
                    server_list.remove(item)
                    break
        self._load_servers()

    def _connect_server(self, entry: ServerEntry) -> None:
        instance = self.instance
        if instance is None:
            return
        instance.server = entry.ip
        self.context.instances.save(instance)
        self._server_edit.setText(entry.ip)
        self._launch()

    # ---------- 操作 ----------
    def _launch(self) -> None:
        instance = self.instance
        if instance is None:
            return
        self._launch_button.set_loading(True, "启动中")
        self._launch_button.setEnabled(False)

        def done(runner) -> None:
            self._launch_button.set_loading(False, "启动")
            self._launch_button.setEnabled(True)
            window = self.window()
            if hasattr(window, "show_log_window"):
                window.show_log_window(runner)
            show_toast(self, "游戏已启动", "success")

        def failed(message: str) -> None:
            self._launch_button.set_loading(False, "启动")
            self._launch_button.setEnabled(True)
            show_toast(self, message, "error")

        self.tasks.submit(self.context.launch, instance, on_done=done, on_error=failed)

    def _analyze_crash(self) -> None:
        instance = self.instance
        if instance is None:
            return

        def done(report) -> None:
            show_crash_report(self, report)

        self.tasks.submit(
            self.context.crash_report,
            instance,
            on_done=done,
            on_error=lambda message: show_toast(self, message, "error"),
        )

    def _open_game_dir(self) -> None:
        instance = self.instance
        if instance is not None:
            self._open_path(instance.game_dir(self.context.paths))

    def _open_path(self, path: Path) -> None:
        try:
            path.mkdir(parents=True, exist_ok=True)
            if sys.platform.startswith("win"):
                subprocess.Popen(["explorer", str(path)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except OSError as error:
            show_toast(self, f"无法打开目录：{error}", "error")

    def _rename(self) -> None:
        instance = self.instance
        if instance is None:
            return
        name = ask_text(self, "重命名实例", "新的实例名称", instance.name)
        if not name:
            return
        try:
            self.instance = self.context.instances.rename(instance.id, name)
        except Exception as error:
            show_toast(self, friendly_message(error), "error")
            return
        self._refresh_header()

    def _duplicate(self) -> None:
        instance = self.instance
        if instance is None:
            return
        try:
            clone = self.context.instances.duplicate(instance.id)
        except Exception as error:
            show_toast(self, friendly_message(error), "error")
            return
        show_toast(self, f"已复制为 {clone.name}", "success")

    def _delete(self) -> None:
        instance = self.instance
        if instance is None:
            return
        if not confirm(
            self,
            "删除实例",
            f"确定删除「{instance.name}」吗？实例目录与其中的 Mod / 存档将一并删除。",
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
        self.instance = None
        window = self.window()
        if hasattr(window, "navigate"):
            window.navigate("instances")

    def _pick_icon(self) -> None:
        instance = self.instance
        if instance is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, "选择实例图标", str(Path.home()), "图片 (*.png *.jpg *.jpeg)")
        if not path:
            return
        target = self.context.paths.instance_dir(instance.id) / "icon.png"
        try:
            from PySide6.QtGui import QImage

            image = QImage(path)
            if image.isNull():
                show_toast(self, "无法读取该图片", "error")
                return
            image.scaled(256, 256, Qt.AspectRatioMode.KeepAspectRatioByExpanding).save(str(target), "PNG")
        except Exception as error:
            show_toast(self, f"保存图标失败：{error}", "error")
            return
        instance.icon_path = str(target)
        self.context.instances.save(instance)
        self._refresh_header()
        show_toast(self, "图标已更新", "success")

    def _export(self) -> None:
        instance = self.instance
        if instance is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出整合包", f"{instance.name}.zip", "整合包 (*.zip *.mrpack)")
        if not path:
            return
        kind = "modrinth" if path.endswith(".mrpack") else "pcl"

        def done(result) -> None:
            show_toast(self, f"已导出：{result.path.name}", "success")

        from pcl.modpack import exporter

        self.tasks.submit(
            exporter.export,
            instance,
            self.context.paths,
            Path(path),
            kind,
            on_done=done,
            on_error=lambda message: show_toast(self, message, "error"),
        )

    # ---------- 拖放 ----------
    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        instance = self.instance
        if instance is None:
            return
        index = self._tabs.currentIndex()
        if not 2 <= index <= 4:
            return
        kind = TAB_KINDS[index - 2][0]
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        if not paths:
            return
        manager = ContentManager(self.context.paths, instance)

        def done(installed) -> None:
            show_toast(self, f"已导入 {len(installed)} 个文件", "success")
            self._reload_content(kind)

        self.tasks.submit(
            manager.install_files,
            paths,
            kind,
            on_done=done,
            on_error=lambda message: show_toast(self, message, "error"),
        )
        event.acceptProposedAction()

    def refresh(self) -> None:
        if self.instance is not None:
            try:
                self.instance = self.context.instances.get(self.instance.id)
            except Exception:
                self.instance = None
            if self.instance is not None:
                self._refresh_header()
                self._load_overview()
