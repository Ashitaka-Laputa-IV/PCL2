"""设置页：启动、界面、下载与高级选项，修改即时保存。"""

from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from pcl.core.config import ACCENT_COLORS, load_config, save_config
from pcl.services import AppContext
from pcl.ui.dialogs.confirm import confirm
from pcl.ui.pages.base import BasePage
from pcl.ui.widgets.animated_button import AnimatedButton
from pcl.ui.widgets.card import Card
from pcl.ui.widgets.section import SectionCard
from pcl.ui.widgets.switch import ToggleSwitch
from pcl.ui.widgets.toast import show_toast


class AccentDot(QWidget):
    """主题色圆点。"""

    def __init__(self, color: str, page: "SettingsPage", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.color = color
        self.page = page
        self._selected = False
        self.setFixedSize(24, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(color)

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.page.set_accent(self.color)

    def paintEvent(self, event) -> None:  # noqa: N802
        from PySide6.QtGui import QColor, QPainter, QPen

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self._selected:
            pen = QPen(QColor(self.color))
            pen.setWidthF(2.0)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(1, 1, self.width() - 2, self.height() - 2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self.color))
        painter.drawEllipse(5, 5, self.width() - 10, self.height() - 10)
        painter.end()


class SettingsPage(BasePage):
    """设置页面。"""

    title = "设置"
    subtitle = "所有修改会自动保存"

    def __init__(self, context: AppContext, bridge, tasks, parent: QWidget | None = None) -> None:
        super().__init__(context, bridge, tasks, parent)
        self._loading = True
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(400)
        self._save_timer.timeout.connect(self._save)
        self._dots: list[AccentDot] = []
        self._build()
        self._load()
        self._loading = False

    # ---------- 构建 ----------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 22, 24, 18)
        outer.setSpacing(12)
        outer.addLayout(self.build_header())

        scroll, content = self.make_scroll()
        content.addWidget(self._build_launch_card())
        content.addWidget(self._build_ui_card())
        content.addWidget(self._build_download_card())
        content.addWidget(self._build_advanced_card())
        content.addStretch(1)
        outer.addWidget(scroll, 1)

        self._status = QLabel("", self)
        self._status.setObjectName("Muted")
        outer.addWidget(self._status)

        # 统一控件宽度，保持右侧控件纵向对齐
        for widget, width in (
            (self._memory, 130),
            (self._width, 108),
            (self._height, 108),
            (self._radius, 108),
            (self._concurrency, 108),
            (self._speed, 140),
            (self._retry, 108),
            (self._log_lines, 140),
        ):
            widget.setFixedWidth(width)
        self._java_combo.setFixedWidth(300)
        self._jvm_args.setFixedWidth(360)
        self._game_args.setFixedWidth(360)
        self._mirror.setFixedWidth(360)
        self._curseforge.setFixedWidth(360)
        self._game_root.setFixedWidth(280)

    def _row(self, title: str, description: str, control: QWidget) -> QWidget:
        container = QWidget(self)
        control.setParent(container)
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 4, 0, 4)
        row.setSpacing(16)

        texts = QVBoxLayout()
        texts.setSpacing(2)
        label = QLabel(title, container)
        texts.addWidget(label)
        if description:
            hint = QLabel(description, container)
            hint.setObjectName("Muted")
            hint.setWordWrap(True)
            texts.addWidget(hint)
        row.addLayout(texts, 1)
        wrapper = QWidget(container)
        wrapper_layout = QHBoxLayout(wrapper)
        wrapper_layout.setContentsMargins(0, 0, 0, 0)
        wrapper_layout.setSpacing(0)
        wrapper_layout.addWidget(control, 0, Qt.AlignmentFlag.AlignLeft)
        wrapper_layout.addStretch(1)
        row.addWidget(wrapper, 2)
        return container

    def _card(self, title: str, icon_name: str = "settings") -> SectionCard:
        return SectionCard(title, icon_name, self)

    def _build_launch_card(self) -> Card:
        card = self._card("启动", "play")
        self._java_combo = QComboBox(card)
        self._java_combo.setMinimumWidth(260)
        refresh = AnimatedButton("重新检测", card, variant="secondary", height=30)
        refresh.clicked.connect(self._refresh_java)
        installer = AnimatedButton("一键安装", card, variant="primary", height=30)
        installer.clicked.connect(self._open_java_install)
        java_row = QWidget(card)
        java_layout = QHBoxLayout(java_row)
        java_layout.setContentsMargins(0, 0, 0, 0)
        java_layout.setSpacing(8)
        java_layout.addWidget(self._java_combo)
        java_layout.addWidget(refresh)
        java_layout.addWidget(installer)
        card.add(self._row("Java 运行时", "留空或选择「自动检测」由启动器按版本要求挑选", java_row))

        self._memory = QSpinBox(card)
        self._memory.setRange(512, 65536)
        self._memory.setSingleStep(512)
        self._memory.setSuffix(" MB")
        card.add(self._row("最大内存", "建议为 2048–8192 MB，加载大量 Mod 时需要更高", self._memory))

        self._resolution_row = QWidget(card)
        resolution_layout = QHBoxLayout(self._resolution_row)
        resolution_layout.setContentsMargins(0, 0, 0, 0)
        resolution_layout.setSpacing(8)
        self._width = QSpinBox(card)
        self._width.setRange(320, 15360)
        self._width.setSuffix(" px")
        self._height = QSpinBox(card)
        self._height.setRange(240, 8640)
        self._height.setSuffix(" px")
        resolution_layout.addWidget(self._width)
        resolution_layout.addWidget(QLabel("×", card))
        resolution_layout.addWidget(self._height)
        card.add(self._row("窗口尺寸", "游戏默认窗口大小", self._resolution_row))

        self._fullscreen = ToggleSwitch(card)
        card.add(self._row("全屏启动", "启动后直接进入全屏模式", self._fullscreen))

        self._jvm_args = QLineEdit(card)
        self._jvm_args.setPlaceholderText("例如：-XX:+UseG1GC -Dfile.encoding=UTF-8")
        self._jvm_args.setMinimumWidth(320)
        card.add(self._row("额外 JVM 参数", "以空格分隔，追加到 JVM 参数末尾", self._jvm_args))

        self._game_args = QLineEdit(card)
        self._game_args.setPlaceholderText("例如：--fullscreen")
        self._game_args.setMinimumWidth(320)
        card.add(self._row("额外游戏参数", "以空格分隔，追加到游戏参数末尾", self._game_args))
        return card

    def _build_ui_card(self) -> Card:
        card = self._card("界面", "settings")
        self._dark = ToggleSwitch(card)
        card.add(self._row("深色模式", "切换深色配色，立即生效", self._dark))

        dots_row = QWidget(card)
        dots_layout = QHBoxLayout(dots_row)
        dots_layout.setContentsMargins(0, 0, 0, 0)
        dots_layout.setSpacing(6)
        for color in ACCENT_COLORS:
            dot = AccentDot(color, self, dots_row)
            self._dots.append(dot)
            dots_layout.addWidget(dot)
        card.add(self._row("主题色", "选择启动器强调色", dots_row))

        self._animations = ToggleSwitch(card)
        card.add(self._row("界面动效", "关闭可降低低配设备的绘制开销", self._animations))

        self._radius = QSpinBox(card)
        self._radius.setRange(0, 24)
        self._radius.setSuffix(" px")
        card.add(self._row("圆角大小", "卡片与按钮的圆角半径", self._radius))
        return card

    def _build_download_card(self) -> Card:
        card = self._card("下载", "download")
        self._source = QComboBox(card)
        self._source.addItem("自动选择（镜像优先）", "auto")
        self._source.addItem("官方源", "official")
        self._source.addItem("镜像源（BMCLAPI）", "mirror")
        card.add(self._row("下载源", "自动模式在镜像失败时回退官方源", self._source))

        self._concurrency = QSpinBox(card)
        self._concurrency.setRange(1, 64)
        card.add(self._row("并发线程", "IO 密集，通常 8–32 较合适", self._concurrency))

        self._speed = QSpinBox(card)
        self._speed.setRange(0, 102400)
        self._speed.setSuffix(" KB/s")
        card.add(self._row("限速", "0 表示不限速", self._speed))

        self._mirror = QLineEdit(card)
        self._mirror.setMinimumWidth(320)
        card.add(self._row("镜像地址", "BMCLAPI 或其他兼容镜像的根地址", self._mirror))

        speed_test = AnimatedButton("测速", card, variant="secondary", height=30)
        speed_test.clicked.connect(self._open_speed_test)
        card.add(self._row("下载源测速", "比较官方源与镜像源的延迟和速度", speed_test))

        self._retry = QSpinBox(card)
        self._retry.setRange(0, 10)
        card.add(self._row("重试次数", "单个文件在每个候选地址上的重试次数", self._retry))
        return card

    def _build_advanced_card(self) -> Card:
        card = self._card("高级", "more")
        self._game_root = QLineEdit(card)
        self._game_root.setPlaceholderText("留空使用数据目录下的 .minecraft")
        self._game_root.setMinimumWidth(300)
        browse = AnimatedButton("浏览…", card, variant="secondary", height=30)
        browse.clicked.connect(self._pick_game_root)
        root_row = QWidget(card)
        root_layout = QHBoxLayout(root_row)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(8)
        root_layout.addWidget(self._game_root)
        root_layout.addWidget(browse)
        card.add(self._row("共享游戏目录", "versions / libraries / assets 的存放位置", root_row))

        self._curseforge = QLineEdit(card)
        self._curseforge.setEchoMode(QLineEdit.EchoMode.Password)
        self._curseforge.setPlaceholderText("导入 CurseForge 整合包时用于获取下载地址")
        self._curseforge.setMinimumWidth(300)
        card.add(self._row("CurseForge API Key", "留空则导入时跳过自动下载并提示手动安装", self._curseforge))

        self._auto_crash = ToggleSwitch(card)
        card.add(self._row("自动分析崩溃", "游戏异常退出后自动读取 crash-reports 并给出原因", self._auto_crash))

        self._log_lines = QSpinBox(card)
        self._log_lines.setRange(500, 50000)
        self._log_lines.setSingleStep(500)
        card.add(self._row("日志缓冲行数", "日志窗口保留的最大行数", self._log_lines))

        backup = QWidget(card)
        backup_layout = QHBoxLayout(backup)
        backup_layout.setContentsMargins(0, 0, 0, 0)
        backup_layout.setSpacing(8)
        export = AnimatedButton("导出配置…", backup, variant="secondary", height=30)
        export.clicked.connect(self._export_config)
        import_button = AnimatedButton("导入配置…", backup, variant="secondary", height=30)
        import_button.clicked.connect(self._import_config)
        backup_layout.addWidget(export)
        backup_layout.addWidget(import_button)
        card.add(self._row("配置备份", "把 config.json 复制到任意位置，或从备份恢复全部设置", backup))

        clear = AnimatedButton("清理下载缓存", card, variant="danger", height=30)
        clear.clicked.connect(self._clear_cache)
        card.add(self._row("缓存", "删除未完成的下载与版本清单缓存", clear))
        return card

    # ---------- 载入 / 保存 ----------
    def _load(self) -> None:
        config = self.context.config
        self._refresh_java_combo(config.launch.java_path)
        self._memory.setValue(config.launch.memory_mb)
        self._width.setValue(config.launch.window_width)
        self._height.setValue(config.launch.window_height)
        self._fullscreen.setChecked(config.launch.fullscreen)
        self._jvm_args.setText(" ".join(config.launch.jvm_args))
        self._game_args.setText(" ".join(config.launch.game_args))

        self._dark.setChecked(config.ui.theme == "dark")
        self._animations.setChecked(config.ui.animations)
        self._radius.setValue(config.ui.corner_radius)
        self._sync_dots(config.ui.accent)

        self._source.setCurrentIndex(max(0, self._source.findData(config.download.source)))
        self._concurrency.setValue(config.download.concurrency)
        self._speed.setValue(config.download.speed_limit_kbps)
        self._mirror.setText(config.download.mirror_base)
        self._retry.setValue(config.download.retry)

        self._game_root.setText(config.advanced.game_root)
        self._curseforge.setText(config.advanced.curseforge_api_key)
        self._log_lines.setValue(config.advanced.max_log_lines)
        self._auto_crash.setChecked(config.advanced.auto_analyze_crash)

        for widget in (
            self._memory,
            self._width,
            self._height,
            self._jvm_args,
            self._game_args,
            self._radius,
            self._concurrency,
            self._speed,
            self._mirror,
            self._retry,
            self._game_root,
            self._curseforge,
            self._log_lines,
        ):
            signal = getattr(widget, "valueChanged", None) or getattr(widget, "textChanged", None)
            if signal is not None:
                signal.connect(self._schedule_save)
        for widget in (self._fullscreen, self._animations, self._dark, self._auto_crash):
            widget.toggled.connect(self._schedule_save)
        for widget in (self._source, self._java_combo):
            widget.currentIndexChanged.connect(self._schedule_save)

    def _refresh_java_combo(self, selected: str = "") -> None:
        self._java_combo.blockSignals(True)
        self._java_combo.clear()
        self._java_combo.addItem("自动检测", "")
        for runtime in self.context.java_runtimes:
            self._java_combo.addItem(f"{runtime.label} · {runtime.path}", str(runtime.path))
        index = self._java_combo.findData(selected) if selected else 0
        self._java_combo.setCurrentIndex(max(0, index))
        self._java_combo.blockSignals(False)

    def _open_speed_test(self) -> None:
        from pcl.ui.dialogs.speed_test import SpeedTestDialog

        SpeedTestDialog(self.context, self.tasks, self).exec()

    def _open_java_install(self) -> None:
        from pcl.ui.dialogs.java_install import JavaInstallDialog

        dialog = JavaInstallDialog(self.context, self.tasks, self.bridge, self)
        if dialog.exec() == dialog.DialogCode.Accepted and dialog.runtime is not None:
            self._refresh_java_combo(str(dialog.runtime.path))
            self._schedule_save()

    def _refresh_java(self) -> None:
        runtimes = self.context.refresh_java()
        self._refresh_java_combo(self._java_combo.currentData() or "")
        show_toast(self, f"检测到 {len(runtimes)} 个 Java 运行时", "success" if runtimes else "warning")

    def _sync_dots(self, accent: str) -> None:
        for dot in self._dots:
            dot.set_selected(dot.color.lower() == accent.lower())
            dot.update()

    def set_accent(self, color: str) -> None:
        self.context.config.ui.accent = color
        self._sync_dots(color)
        self._apply_theme()
        self._schedule_save()

    def _schedule_save(self, *_args) -> None:
        if self._loading:
            return
        self._save_timer.start()

    def _save(self) -> None:
        config = self.context.config
        config.launch.java_path = str(self._java_combo.currentData() or "")
        config.launch.memory_mb = self._memory.value()
        config.launch.window_width = self._width.value()
        config.launch.window_height = self._height.value()
        config.launch.fullscreen = self._fullscreen.isChecked()
        config.launch.jvm_args = self._jvm_args.text().split()
        config.launch.game_args = self._game_args.text().split()

        config.ui.theme = "dark" if self._dark.isChecked() else "light"
        config.ui.animations = self._animations.isChecked()
        config.ui.corner_radius = self._radius.value()

        config.download.source = str(self._source.currentData() or "auto")
        config.download.concurrency = self._concurrency.value()
        config.download.speed_limit_kbps = self._speed.value()
        config.download.mirror_base = self._mirror.text().strip() or config.download.mirror_base
        config.download.retry = self._retry.value()

        config.advanced.game_root = self._game_root.text().strip()
        config.advanced.curseforge_api_key = self._curseforge.text().strip()
        config.advanced.max_log_lines = self._log_lines.value()
        config.advanced.auto_analyze_crash = self._auto_crash.isChecked()

        self.context.save_config()
        self._apply_theme()
        self._status.setText("已保存")

    def _apply_theme(self) -> None:
        window = self.window()
        if hasattr(window, "apply_theme"):
            window.apply_theme()

    def _pick_game_root(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择共享游戏目录", str(Path.home()))
        if path:
            self._game_root.setText(path)

    def _export_config(self) -> None:
        target, _filter = QFileDialog.getSaveFileName(
            self, "导出配置", str(Path.home() / "pcl2-config.json"), "JSON (*.json)"
        )
        if not target:
            return
        try:
            save_config(self.context.config, Path(target))
        except OSError as error:
            show_toast(self, f"导出失败：{error}", "error")
            return
        show_toast(self, "配置已导出", "success")

    def _import_config(self) -> None:
        source, _filter = QFileDialog.getOpenFileName(self, "导入配置", str(Path.home()), "JSON (*.json)")
        if not source:
            return
        try:
            config = load_config(Path(source))
        except Exception as error:  # noqa: BLE001 - 损坏的备份直接提示
            show_toast(self, f"配置文件无法读取：{error}", "error")
            return
        if not confirm(self, "导入配置", "导入后将覆盖当前全部设置并立即生效，是否继续？", ok_text="导入"):
            return
        self.context.config = config
        self.context.save_config()
        self._load()
        self._apply_theme()
        self._status.setText("已导入配置")
        show_toast(self, "配置已导入", "success")

    def _clear_cache(self) -> None:
        cache = self.context.paths.cache
        removed = 0
        if cache.is_dir():
            for child in cache.iterdir():
                try:
                    if child.is_dir():
                        shutil.rmtree(child)
                    else:
                        child.unlink()
                    removed += 1
                except OSError:
                    continue
        show_toast(self, f"已清理 {removed} 项缓存", "success")

    def refresh(self) -> None:
        self._refresh_java_combo(self.context.config.launch.java_path)
