"""下载页：原版、加载器、整合包与资源四类入口。"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from pcl.core.errors import friendly_message
from pcl.services import AppContext
from pcl.ui.dialogs.import_modpack import ImportModpackDialog
from pcl.ui.pages.base import BasePage
from pcl.ui.pages.community import CommunityBrowser
from pcl.ui.widgets.animated_button import AnimatedButton
from pcl.ui.widgets.card import Card
from pcl.ui.widgets.progress import GradientProgressBar
from pcl.ui.widgets.toast import show_toast
from pcl.version import loaders, meta
from pcl.version.manifest import VersionEntry, fetch_manifest

KIND_FILTERS = (("release", "正式版"), ("snapshot", "快照"), ("old", "远古版本"), ("all", "全部"))
LOADER_CHOICES = (
    ("fabric", "Fabric"),
    ("quilt", "Quilt"),
    ("forge", "Forge"),
    ("neoforge", "NeoForge"),
)


class VersionRow(QWidget):
    """版本列表富行：版本号 + 类型徽章 + 发布日期。"""

    def __init__(
        self,
        version_id: str,
        type_label: str,
        date: str,
        parent: QWidget | None = None,
        *,
        installed: bool = False,
    ) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 5, 12, 5)
        row.setSpacing(8)
        name = QLabel(version_id, self)
        name.setStyleSheet("font-weight: 600; font-size: 13px;")
        tag = QLabel(type_label, self)
        tag.setObjectName("TagNeutral")
        when = QLabel(date, self)
        when.setObjectName("Muted")
        row.addWidget(name)
        row.addWidget(tag)
        if installed:
            badge = QLabel("已安装", self)
            badge.setStyleSheet(
                "background: rgba(34, 197, 94, 0.14); color: #16A34A;"
                " border-radius: 9px; padding: 2px 9px; font-size: 11px; font-weight: 600;"
            )
            row.addWidget(badge)
        row.addStretch(1)
        row.addWidget(when)


class DownloadPage(BasePage):
    """下载中心。"""

    title = "下载"
    subtitle = "安装原版游戏、模组加载器与整合包"

    def __init__(self, context: AppContext, bridge, tasks, parent: QWidget | None = None) -> None:
        super().__init__(context, bridge, tasks, parent)
        self._manifest = None
        self._entries: dict[str, VersionEntry] = {}
        self._loader_versions: list = []
        self._busy = False
        self._build()
        self.bridge.download_progress.connect(self._on_progress)

    # ---------- 构建 ----------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 22, 24, 18)
        outer.setSpacing(12)

        header = self.build_header()
        refresh = AnimatedButton("刷新版本列表", self, variant="secondary")
        refresh.clicked.connect(lambda: self._load_manifest(force=True))
        header.addWidget(refresh)
        outer.addLayout(header)

        self._tabs = QTabWidget(self)
        self._tabs.addTab(self._build_vanilla_tab(), "原版游戏")
        self._tabs.addTab(self._build_loader_tab(), "模组加载器")
        self._tabs.addTab(self._build_modpack_tab(), "整合包")
        self._tabs.addTab(self._build_resource_tab(), "资源")
        self._community = CommunityBrowser(self.context, self.tasks, self)
        self._tabs.addTab(self._community, "社区资源")
        self._tabs.currentChanged.connect(self._on_tab_changed)
        outer.addWidget(self._tabs, 1)

        status = QHBoxLayout()
        status.setSpacing(12)
        self._status = QLabel("就绪", self)
        self._status.setObjectName("Muted")
        self._progress = GradientProgressBar(self, height=6)
        self._progress.setFixedWidth(240)
        self._progress.setVisible(False)
        status.addWidget(self._progress)
        status.addWidget(self._status, 1)
        outer.addLayout(status)

    def _build_vanilla_tab(self) -> QWidget:
        page = QWidget(self)
        layout = QHBoxLayout(page)
        layout.setContentsMargins(0, 14, 0, 0)
        layout.setSpacing(16)

        left = QVBoxLayout()
        left.setSpacing(8)
        self._search = QLineEdit(page)
        self._search.setPlaceholderText("搜索版本号…")
        self._search.textChanged.connect(self._refresh_version_list)
        left.addWidget(self._search)

        self._kind = QComboBox(page)
        for key, label in KIND_FILTERS:
            self._kind.addItem(label, key)
        self._kind.currentIndexChanged.connect(self._refresh_version_list)
        left.addWidget(self._kind)

        self._version_list = QListWidget(page)
        self._version_list.setMinimumWidth(280)
        self._version_list.currentItemChanged.connect(self._on_version_selected)
        left.addWidget(self._version_list, 1)
        layout.addLayout(left, 2)

        detail = Card(page, padding=22, spacing=10)
        self._detail_title = QLabel("选择一个版本", detail)
        self._detail_title.setStyleSheet("font-size: 20px; font-weight: 700;")
        self._detail_meta = QLabel("从左侧列表选择需要安装的游戏版本", detail)
        self._detail_meta.setObjectName("PageSubtitle")
        self._detail_meta.setWordWrap(True)
        detail.add(self._detail_title)
        detail.add(self._detail_meta)
        self._detail_info = QLabel("", detail)
        self._detail_info.setObjectName("Muted")
        self._detail_info.setWordWrap(True)
        detail.add(self._detail_info)
        detail.addStretch(1)

        self._create_instance_check = QCheckBox("安装完成后自动创建实例", detail)
        self._create_instance_check.setChecked(True)
        detail.add(self._create_instance_check)

        self._install_button = AnimatedButton("安装该版本", detail, variant="primary", height=40)
        self._install_button.setEnabled(False)
        self._install_button.clicked.connect(self._install_selected_version)
        detail.add(self._install_button)

        self._detail_hint = QLabel("", detail)
        self._detail_hint.setObjectName("Muted")
        self._detail_hint.setWordWrap(True)
        detail.add(self._detail_hint)
        layout.addWidget(detail, 3)
        return page

    def _build_loader_tab(self) -> QWidget:
        page = QWidget(self)
        layout = QHBoxLayout(page)
        layout.setContentsMargins(0, 14, 0, 0)
        layout.setSpacing(16)

        left = Card(page, padding=18, spacing=10)
        left.setMinimumWidth(300)
        title = QLabel("选择加载器", left)
        title.setObjectName("SectionTitle")
        left.add(title)

        self._loader_mc = QComboBox(left)
        self._loader_mc.setEditable(True)
        left.add(self._loader_mc)

        self._loader_kind = QComboBox(left)
        for key, label in LOADER_CHOICES:
            self._loader_kind.addItem(label, key)
        left.add(self._loader_kind)

        fetch_button = AnimatedButton("获取可用版本", left, variant="secondary")
        fetch_button.clicked.connect(self._load_loader_versions)
        left.add(fetch_button)
        left.addStretch(1)
        self._loader_hint = QLabel("先选择游戏版本，再获取加载器版本。", left)
        self._loader_hint.setObjectName("Muted")
        self._loader_hint.setWordWrap(True)
        left.addWidget(self._loader_hint)
        layout.addWidget(left, 2)

        right = Card(page, padding=20, spacing=10)
        right_title = QLabel("可用加载器版本", right)
        right_title.setObjectName("SectionTitle")
        right.add(right_title)
        self._loader_list = QListWidget(right)
        right.add(self._loader_list, 1)
        self._install_loader_button = AnimatedButton("安装并创建实例", right, variant="primary", height=40)
        self._install_loader_button.clicked.connect(self._install_loader)
        right.add(self._install_loader_button)
        layout.addWidget(right, 3)
        return page

    def _build_modpack_tab(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 14, 0, 0)

        card = Card(page, padding=24, spacing=12)
        title = QLabel("导入整合包", card)
        title.setStyleSheet("font-size: 19px; font-weight: 700;")
        card.add(title)
        description = QLabel(
            "支持 Modrinth（.mrpack）、CurseForge（zip）、HMCL 与 PCL 四种格式。\n"
            "导入时会自动还原实例目录、下载声明的文件，并按需安装对应游戏版本与加载器。",
            card,
        )
        description.setObjectName("PageSubtitle")
        description.setWordWrap(True)
        card.add(description)

        button = AnimatedButton("选择整合包文件…", card, variant="primary")
        button.clicked.connect(self._import_modpack)
        card.add(button, 0, Qt.AlignmentFlag.AlignLeft)
        card.addStretch(1)
        layout.addWidget(card, 1)
        return page

    def _build_resource_tab(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 14, 0, 0)
        card = Card(page, padding=24, spacing=12)
        title = QLabel("Mod / 资源包 / 光影", card)
        title.setStyleSheet("font-size: 19px; font-weight: 700;")
        card.add(title)
        text = QLabel(
            "资源文件请放入对应实例的 mods / resourcepacks / shaderpacks 目录。\n"
            "在「资源管理」页可以直接拖入文件、启用或禁用、查看依赖冲突。",
            card,
        )
        text.setObjectName("PageSubtitle")
        text.setWordWrap(True)
        card.add(text)
        button = AnimatedButton("打开资源管理", card, variant="primary")
        button.clicked.connect(lambda: self.window().navigate("content") if hasattr(self.window(), "navigate") else None)
        card.add(button, 0, Qt.AlignmentFlag.AlignLeft)
        card.addStretch(1)
        layout.addWidget(card, 1)
        return page

    # ---------- 版本清单 ----------
    def refresh(self) -> None:
        self._load_manifest()
        if self._tabs.currentIndex() == 4:
            self._community.refresh()

    def _on_tab_changed(self, index: int) -> None:
        if index == 4:
            self._community.refresh()

    def _load_manifest(self, force: bool = False) -> None:
        if self._manifest is not None and not force:
            return
        self._status.setText("正在获取版本列表…")
        self._show_list_hint("正在获取版本列表…")

        def done(manifest) -> None:
            self._manifest = manifest
            self._entries = {entry.id: entry for entry in manifest.versions or []}
            self._status.setText(f"共 {len(self._entries)} 个版本")
            self._fill_loader_mc()
            self._refresh_version_list()

        def failed(message: str) -> None:
            self._status.setText(f"获取失败：{message}")
            self._show_list_hint(f"获取版本列表失败：{message}")

        self.tasks.submit(
            fetch_manifest,
            self.context.client(),
            self.context.paths.cache / "version_manifest_v2.json",
            source=self.context.config.download.source,
            mirror_base=self.context.config.download.mirror_base,
            force=force,
            on_done=done,
            on_error=failed,
        )

    def _show_list_hint(self, text: str) -> None:
        self._version_list.clear()
        item = QListWidgetItem(text)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        self._version_list.addItem(item)

    def _refresh_version_list(self) -> None:
        if self._manifest is None:
            return
        kind = self._kind.currentData() or "release"
        keyword = self._search.text().strip().lower()
        entries = self._manifest.filtered(kind)
        if keyword:
            entries = [entry for entry in entries if keyword in entry.id.lower()]

        self._version_list.clear()
        for entry in entries[:400]:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, entry.id)
            item.setSizeHint(QSize(0, 46))
            self._version_list.addItem(item)
            self._version_list.setItemWidget(
                item,
                VersionRow(
                    entry.id,
                    meta.version_type_label(entry.type),
                    entry.release_date,
                    self._version_list,
                    installed=self.context.version_installed(entry.id),
                ),
            )
        if entries:
            self._version_list.setCurrentRow(0)

    def _fill_loader_mc(self) -> None:
        if self._manifest is None:
            return
        current = self._loader_mc.currentText()
        self._loader_mc.blockSignals(True)
        self._loader_mc.clear()
        for entry in self._manifest.filtered("release")[:60]:
            self._loader_mc.addItem(entry.id)
        self._loader_mc.blockSignals(False)
        if current:
            self._loader_mc.setCurrentText(current)

    def _on_version_selected(self, current: QListWidgetItem | None, _previous=None) -> None:
        if current is None:
            self._install_button.setEnabled(False)
            return
        version_id = str(current.data(Qt.ItemDataRole.UserRole))
        entry = self._entries.get(version_id)
        self._detail_title.setText(version_id)
        if entry is not None:
            self._detail_meta.setText(
                f"{meta.version_type_label(entry.type)}　·　发布于 {entry.release_date or '未知'}"
            )
        installed = self.context.version_installed(version_id)
        source_label = {"auto": "自动选择", "official": "官方源", "mirror": "BMCLAPI 镜像"}.get(
            self.context.config.download.source, self.context.config.download.source
        )
        self._detail_info.setText(
            f"安装位置：{self.context.paths.version_dir(version_id)}\n下载源：{source_label}"
        )
        self._detail_hint.setText("该版本已安装，可重复安装以修复文件。" if installed else "")
        self._install_button.setText("重新安装" if installed else "安装该版本")
        self._install_button.setEnabled(True)

    # ---------- 安装 ----------
    def _install_selected_version(self) -> None:
        item = self._version_list.currentItem()
        if item is None or self._busy:
            return
        version_id = str(item.data(Qt.ItemDataRole.UserRole))
        entry = self._entries.get(version_id)
        self._busy = True
        self._install_button.set_loading(True, "安装中")
        self._install_button.setEnabled(False)
        self._status.setText(f"正在安装 {version_id}…")

        def done(report) -> None:
            self._busy = False
            self._install_button.set_loading(False, "重新安装")
            self._install_button.setEnabled(True)
            self._status.setText(f"{version_id} 安装完成（{report.task_count} 个文件）")
            if self._create_instance_check.isChecked():
                self._create_instance_for(version_id, version_id, "vanilla", "")
            show_toast(self, f"{version_id} 安装完成", "success")
            self._refresh_version_list()

        def failed(message: str) -> None:
            self._busy = False
            self._install_button.set_loading(False, "安装该版本")
            self._install_button.setEnabled(True)
            self._status.setText(f"安装失败：{message}")
            show_toast(self, message, "error")

        self.tasks.submit(
            self.context.install_vanilla,
            version_id,
            entry,
            on_done=done,
            on_error=failed,
        )

    def _create_instance_for(self, name: str, mc_version: str, loader: str, loader_version: str, version_id: str = "") -> None:
        try:
            instance = self.context.instances.create(
                f"{name}",
                mc_version,
                version_id=version_id or mc_version,
                loader=loader,
                loader_version=loader_version,
            )
            self.context.select_instance(instance.id)
        except Exception as error:
            show_toast(self, friendly_message(error), "warning")

    def _load_loader_versions(self) -> None:
        mc_version = self._loader_mc.currentText().strip()
        kind = self._loader_kind.currentData()
        if not mc_version:
            self._loader_hint.setText("请先选择游戏版本。")
            return
        self._loader_hint.setText("正在获取加载器版本…")
        self._loader_list.clear()

        def done(versions) -> None:
            self._loader_versions = list(versions)
            self._loader_list.clear()
            for version in versions:
                item = QListWidgetItem(version.label)
                item.setData(Qt.ItemDataRole.UserRole, version.version)
                self._loader_list.addItem(item)
            if versions:
                self._loader_list.setCurrentRow(0)
                self._loader_hint.setText(f"共 {len(versions)} 个可用版本")
            else:
                self._loader_hint.setText("没有获取到可用版本，请检查网络或下载源。")

        def failed(message: str) -> None:
            self._loader_hint.setText(f"获取失败：{message}")

        self.tasks.submit(
            loaders.list_versions,
            self.context.paths,
            self.context.config.download,
            kind=kind,
            mc_version=mc_version,
            on_done=done,
            on_error=failed,
        )

    def _install_loader(self) -> None:
        item = self._loader_list.currentItem()
        if item is None or self._busy:
            return
        kind = self._loader_kind.currentData()
        loader_version = str(item.data(Qt.ItemDataRole.UserRole))
        mc_version = self._loader_mc.currentText().strip()
        self._busy = True
        self._install_loader_button.set_loading(True, "安装中")
        self._install_loader_button.setEnabled(False)
        self._status.setText(f"正在安装 {kind} {loader_version}…")

        def done(version_id) -> None:
            self._busy = False
            self._install_loader_button.set_loading(False, "安装并创建实例")
            self._install_loader_button.setEnabled(True)
            self._status.setText(f"已安装 {version_id}")
            self._create_instance_for(
                f"{mc_version} {kind.capitalize()}",
                mc_version,
                kind,
                loader_version,
                version_id,
            )
            show_toast(self, f"{kind} 安装完成", "success")

        def failed(message: str) -> None:
            self._busy = False
            self._install_loader_button.set_loading(False, "安装并创建实例")
            self._install_loader_button.setEnabled(True)
            self._status.setText(f"安装失败：{message}")
            show_toast(self, message, "error")

        self.tasks.submit(
            self.context.install_loader,
            kind,
            mc_version,
            loader_version,
            on_done=done,
            on_error=failed,
        )

    def _import_modpack(self) -> None:
        dialog = ImportModpackDialog(self.context, self.tasks, self.bridge, self)
        if dialog.exec() == dialog.DialogCode.Accepted and dialog.result is not None:
            show_toast(self, f"已导入 {dialog.result.instance.name}", "success")

    # ---------- 进度 ----------
    def _on_progress(self, progress) -> None:
        percent = float(getattr(progress, "percent", 0.0))
        speed = float(getattr(progress, "speed", 0.0))
        current = str(getattr(progress, "current", ""))
        active = int(getattr(progress, "active", 0))
        self._progress.setVisible(active > 0)
        self._progress.set_value(percent)
        if current and self._busy:
            speed_text = f" · {speed / 1024 / 1024:.1f} MB/s" if speed > 0 else ""
            self._status.setText(f"{percent:.1f}% · {current}{speed_text}")
