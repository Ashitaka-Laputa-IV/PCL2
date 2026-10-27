"""主页：实例/账户选择、启动大按钮、快捷入口与下载任务摘要。"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPaintEvent
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from pcl.core.errors import friendly_message
from pcl.services import AppContext
from pcl.ui.pages.base import BasePage
from pcl.ui.widgets.animated_button import AnimatedButton
from pcl.ui.widgets.card import Card
from pcl.ui.widgets.progress import GradientProgressBar, RingProgress
from pcl.ui.widgets.toast import show_toast


class LaunchDial(QWidget):
    """环形进度 + 中心启动按钮。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(156, 156)
        self.ring = RingProgress(self, diameter=156, thickness=7)
        self.ring.set_track_color(QColor(255, 255, 255, 60))
        self.ring.move(0, 0)
        self.button = AnimatedButton("启动", self, variant="hero", icon_name="play", radius=46)
        self.button.setFixedSize(96, 96)
        self.button.move(30, 30)

    def set_progress(self, value: float) -> None:
        self.ring.set_value(value)


class HeroCard(QWidget):
    """渐变背景的主卡片。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(212)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        accent = self.palette().highlight().color()
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)

        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0.0, QColor(accent).lighter(108))
        gradient.setColorAt(0.5, QColor(accent).darker(108))
        gradient.setColorAt(1.0, QColor(accent).darker(140))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawRoundedRect(rect, 16, 16)

        # 装饰性光晕与细网格
        painter.setBrush(QColor(255, 255, 255, 18))
        painter.drawEllipse(rect.width() - 220, -90, 320, 320)
        painter.setBrush(QColor(255, 255, 255, 12))
        painter.drawEllipse(-70, rect.height() - 110, 240, 240)
        painter.end()


class HomePage(BasePage):
    """主页。"""

    title = "主页"
    subtitle = "选择实例，一键开始游戏"

    def __init__(self, context: AppContext, bridge, tasks, parent: QWidget | None = None) -> None:
        super().__init__(context, bridge, tasks, parent)
        self._dial: LaunchDial | None = None
        self._launching = False
        self._game_running = False
        self._build()
        self.bridge.game_state.connect(self.on_game_state)
        self.bridge.download_progress.connect(self._on_download_progress)

    # ---------- 构建 ----------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 22, 24, 20)
        outer.setSpacing(16)

        header = self.build_header()
        self._instance_combo = QComboBox(self)
        self._instance_combo.setMinimumWidth(190)
        self._instance_combo.currentIndexChanged.connect(self._on_instance_changed)
        self._account_combo = QComboBox(self)
        self._account_combo.setMinimumWidth(150)
        self._account_combo.currentIndexChanged.connect(self._on_account_changed)
        self._account_combo.setEditable(False)

        for widget in (self._instance_combo, self._account_combo):
            header.addWidget(widget)

        settings_button = AnimatedButton("", self, variant="ghost", icon_name="settings", radius=8)
        settings_button.setFixedSize(36, 34)
        settings_button.setToolTip("打开设置")
        settings_button.clicked.connect(lambda: self.window().navigate("settings") if hasattr(self.window(), "navigate") else None)
        header.addWidget(settings_button)
        outer.addLayout(header)

        # 主卡片
        self._hero = HeroCard(self)
        hero_layout = QHBoxLayout(self._hero)
        hero_layout.setContentsMargins(28, 22, 32, 22)
        hero_layout.setSpacing(28)

        self._dial = LaunchDial(self._hero)
        self._dial.button.clicked.connect(self._toggle_launch)
        hero_layout.addWidget(self._dial, 0, Qt.AlignmentFlag.AlignVCenter)

        info = QVBoxLayout()
        info.setSpacing(8)
        self._name_label = QLabel("还没有实例", self._hero)
        self._name_label.setStyleSheet("color: #FFFFFF; font-size: 24px; font-weight: 700;")
        self._version_label = QLabel("先在「版本管理」中新建实例，或从下载页安装游戏", self._hero)
        self._version_label.setStyleSheet("color: rgba(255,255,255,0.82); font-size: 13px;")
        info.addWidget(self._name_label)
        info.addWidget(self._version_label)
        info.addSpacing(6)

        tags = QHBoxLayout()
        tags.setSpacing(8)
        self._tag_loader = QLabel("原版", self._hero)
        self._tag_java = QLabel("Java 未知", self._hero)
        self._tag_memory = QLabel("内存 2048 MB", self._hero)
        for label in (self._tag_loader, self._tag_java, self._tag_memory):
            label.setStyleSheet(
                "background: rgba(255,255,255,0.18); color: #FFFFFF; border-radius: 9px;"
                " padding: 3px 12px; font-size: 12px;"
            )
            tags.addWidget(label)
        tags.addStretch(1)
        info.addLayout(tags)
        info.addStretch(1)

        self._hint_label = QLabel("", self._hero)
        self._hint_label.setStyleSheet("color: rgba(255,255,255,0.72); font-size: 12px;")
        info.addWidget(self._hint_label)
        hero_layout.addLayout(info, 1)
        outer.addWidget(self._hero)

        # 快捷入口 + 任务摘要
        bottom = QHBoxLayout()
        bottom.setSpacing(16)

        quick = Card(self, padding=18, spacing=12)
        quick_title = QLabel("快捷入口", quick)
        quick_title.setObjectName("SectionTitle")
        quick.add(quick_title)

        grid = QGridLayout()
        grid.setSpacing(10)
        entries = (
            ("下载游戏", "download", "安装原版与加载器"),
            ("版本管理", "instances", "管理实例与启动配置"),
            ("资源管理", "content", "Mod / 资源包 / 光影"),
            ("设置", "settings", "Java、主题与下载选项"),
        )
        for index, (label, page, hint) in enumerate(entries):
            button = AnimatedButton(f"{label}\n{hint}", quick, variant="secondary", icon_name=page, radius=10)
            button.setMinimumHeight(62)
            button.setToolTip(hint)
            button.clicked.connect(lambda _=False, target=page: self._navigate(target))
            grid.addWidget(button, index // 2, index % 2)
        quick.add_layout(grid, 1)
        quick.addStretch(1)
        bottom.addWidget(quick, 3)

        tasks_card = Card(self, padding=18, spacing=10)
        task_title = QLabel("最近任务", tasks_card)
        task_title.setObjectName("SectionTitle")
        tasks_card.add(task_title)
        self._task_label = QLabel("暂无下载任务", tasks_card)
        self._task_label.setObjectName("Muted")
        tasks_card.add(self._task_label)
        self._task_bar = GradientProgressBar(tasks_card, height=8)
        self._task_bar.set_value(0)
        self._task_bar.setVisible(False)
        tasks_card.add(self._task_bar)
        self._task_detail = QLabel("", tasks_card)
        self._task_detail.setObjectName("Muted")
        tasks_card.add(self._task_detail)
        tasks_card.addStretch(1)
        bottom.addWidget(tasks_card, 2)

        outer.addLayout(bottom, 1)
        self._refresh_choices()

    # ---------- 导航 ----------
    def _navigate(self, page: str) -> None:
        window = self.window()
        if hasattr(window, "navigate"):
            window.navigate(page)

    # ---------- 数据刷新 ----------
    def refresh(self) -> None:
        self._refresh_choices()
        self._refresh_java_tag()

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
        self._instance_combo.setEnabled(bool(instances))
        self._instance_combo.blockSignals(False)

        self._account_combo.blockSignals(True)
        self._account_combo.clear()
        for account in self.context.accounts.accounts:
            self._account_combo.addItem(account.display, account.name)
        selected = self.context.accounts.selected
        if selected:
            index = self._account_combo.findData(selected)
            if index >= 0:
                self._account_combo.setCurrentIndex(index)
        self._account_combo.blockSignals(False)

        self._update_hero(current)

    def _update_hero(self, instance) -> None:
        if instance is None:
            self._name_label.setText("还没有实例")
            self._version_label.setText("先在「版本管理」中新建实例，或从下载页安装游戏")
            self._tag_loader.setText("未选择")
            self._hint_label.setText("")
            if self._dial is not None:
                self._dial.button.setEnabled(False)
            return

        self._name_label.setText(instance.name)
        loader_label = instance.loader.capitalize() if instance.loader != "vanilla" else "原版"
        self._version_label.setText(f"Minecraft {instance.mc_version} · {loader_label}")
        self._tag_loader.setText(loader_label)
        memory = instance.memory_mb or self.context.config.launch.memory_mb
        self._tag_memory.setText(f"内存 {memory} MB")
        self._refresh_java_tag()
        if self._dial is not None:
            self._dial.button.setEnabled(not self._launching)
        if not self.context.version_installed(instance.version_id):
            self._hint_label.setText("⚠ 该实例对应的游戏版本尚未安装，请先在下载页完成安装")
        else:
            self._hint_label.setText(f"游戏目录：{instance.game_dir(self.context.paths)}")

    def _refresh_java_tag(self) -> None:
        runtimes = self.context.java_runtimes
        if runtimes:
            self._tag_java.setText(f"Java {runtimes[0].version}")
        else:
            self._tag_java.setText("未检测到 Java")

    # ---------- 交互 ----------
    def _on_instance_changed(self, index: int) -> None:
        instance_id = self._instance_combo.itemData(index)
        if not instance_id:
            return
        try:
            instance = self.context.select_instance(instance_id)
        except Exception as error:
            show_toast(self, friendly_message(error), "error")
            return
        self._update_hero(instance)

    def _on_account_changed(self, index: int) -> None:
        name = self._account_combo.itemData(index)
        if not name:
            return
        self.context.select_account(str(name))

    def _toggle_launch(self) -> None:
        if self._game_running:
            self.context.stop_game()
            return
        if self._launching:
            return

        instance = self.context.current_instance()
        if instance is None:
            show_toast(self, "请先新建一个实例", "warning")
            self._navigate("instances")
            return

        self._launching = True
        if self._dial is not None:
            self._dial.button.set_loading(True, "启动中")
            self._dial.button.setEnabled(False)

        def done(runner) -> None:
            self._launching = False
            self._game_running = True
            if self._dial is not None:
                self._dial.button.set_loading(False, "结束")
                self._dial.button.setEnabled(True)
            window = self.window()
            if hasattr(window, "show_log_window"):
                window.show_log_window(runner)
            show_toast(self, "游戏已启动", "success")

        def failed(message: str) -> None:
            self._launching = False
            if self._dial is not None:
                self._dial.button.set_loading(False, "启动")
                self._dial.button.setEnabled(True)
            show_toast(self, message, "error")

        self.tasks.submit(self.context.launch, instance, on_done=done, on_error=failed)

    # ---------- 事件 ----------
    def on_game_state(self, state: object) -> None:
        running = bool(getattr(state, "running", False))
        self._game_running = running
        if self._dial is None:
            return
        if not running:
            self._dial.set_progress(0)
            self._dial.button.set_loading(False, "启动")
            self._dial.button.setEnabled(True)
            code = getattr(state, "exit_code", None)
            if code not in (None, 0):
                self._hint_label.setText(f"游戏上次异常退出（退出码 {code}），可在日志窗口查看原因")
        else:
            self._dial.button.set_loading(False, "结束")

    def _on_download_progress(self, progress: object) -> None:
        percent = float(getattr(progress, "percent", 0.0))
        speed = float(getattr(progress, "speed", 0.0))
        current = str(getattr(progress, "current", ""))
        active = int(getattr(progress, "active", 0))
        self._task_bar.set_value(percent)
        if active <= 0 and percent <= 0:
            self._task_label.setText("暂无下载任务")
            self._task_detail.setText("")
            return
        self._task_label.setText(f"正在下载 · {percent:.1f}%")
        speed_text = f"{speed / 1024 / 1024:.1f} MB/s" if speed > 0 else "—"
        self._task_detail.setText(f"{current or '准备中'} · {speed_text}")
