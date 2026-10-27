"""新建实例对话框：选择版本与加载器，可选立即安装。"""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QFormLayout, QLabel, QLineEdit, QWidget

from pcl.core.errors import friendly_message
from pcl.services import AppContext
from pcl.ui.dialogs.confirm import BaseDialog
from pcl.ui.widgets.progress import GradientProgressBar
from pcl.version import loaders
from pcl.version.manifest import VersionEntry, fetch_manifest

LOADER_CHOICES = (
    ("vanilla", "原版"),
    ("fabric", "Fabric"),
    ("quilt", "Quilt"),
    ("forge", "Forge"),
    ("neoforge", "NeoForge"),
)


class NewInstanceDialog(BaseDialog):
    """新建实例并（可选）安装游戏。"""

    def __init__(self, context: AppContext, tasks, bridge, parent: QWidget | None = None) -> None:
        super().__init__(parent, title="新建实例", width=520)
        self.context = context
        self.tasks = tasks
        self.bridge = bridge
        self.result_instance = None
        self._entries: dict[str, VersionEntry] = {}
        self._installing = False

        form = QFormLayout()
        form.setSpacing(12)
        form.setLabelAlignment(form.labelAlignment())
        self.body.addLayout(form)

        self._name = QLineEdit(self)
        self._name.setPlaceholderText("例如：生存 1.20")
        form.addRow("实例名称", self._name)

        self._version = QComboBox(self)
        self._version.setEditable(True)
        self._version.setMinimumWidth(220)
        form.addRow("游戏版本", self._version)

        self._loader = QComboBox(self)
        for key, label in LOADER_CHOICES:
            self._loader.addItem(label, key)
        form.addRow("加载器", self._loader)

        self._loader_version = QComboBox(self)
        self._loader_version.setEditable(True)
        self._loader_version.setEnabled(False)
        self._loader_version.setMinimumWidth(180)
        form.addRow("加载器版本", self._loader_version)

        self._install = QCheckBox("立即下载并安装（推荐）", self)
        self._install.setChecked(True)
        self.body.addWidget(self._install)

        self._status = QLabel("", self)
        self._status.setObjectName("Muted")
        self._status.setWordWrap(True)
        self.body.addWidget(self._status)

        self._progress = GradientProgressBar(self, height=8)
        self._progress.setVisible(False)
        self.body.addWidget(self._progress)

        self._ok = self.add_buttons("创建", "取消")

        self._loader.currentIndexChanged.connect(self._on_loader_changed)
        self._version.currentTextChanged.connect(self._on_version_changed)
        self.bridge.download_progress.connect(self._on_progress)

        self._populate_installed()
        self._load_manifest()

    # ---------- 数据 ----------
    def _populate_installed(self) -> None:
        installed = self.context.installed_versions()
        for version_id in sorted(installed, reverse=True):
            self._version.addItem(version_id)
        if not installed:
            self._version.setCurrentText("")

    def _load_manifest(self) -> None:
        self._status.setText("正在获取版本列表…")

        def done(manifest) -> None:
            self._status.setText("")
            releases = manifest.filtered("release")
            current = self._version.currentText()
            self._version.blockSignals(True)
            existing = {self._version.itemText(i) for i in range(self._version.count())}
            for entry in releases:
                self._entries[entry.id] = entry
                if entry.id not in existing:
                    self._version.addItem(entry.id)
            self._version.blockSignals(False)
            if current:
                self._version.setCurrentText(current)
            elif self._version.count():
                self._version.setCurrentIndex(0)
            self._on_version_changed(self._version.currentText())

        def failed(message: str) -> None:
            self._status.setText(f"获取版本列表失败：{message}（可手动输入已安装版本）")

        self.tasks.submit(
            fetch_manifest,
            self.context.client(),
            self.context.paths.cache / "version_manifest_v2.json",
            source=self.context.config.download.source,
            mirror_base=self.context.config.download.mirror_base,
            on_done=done,
            on_error=failed,
        )

    def _on_version_changed(self, text: str) -> None:
        if self._loader.currentData() != "vanilla" and text:
            self._load_loader_versions(text)

    def _on_loader_changed(self, index: int) -> None:
        kind = self._loader.currentData()
        vanilla = kind == "vanilla"
        self._loader_version.setEnabled(not vanilla)
        if vanilla:
            self._loader_version.clear()
        else:
            self._load_loader_versions(self._version.currentText())

    def _load_loader_versions(self, mc_version: str) -> None:
        kind = self._loader.currentData()
        if kind == "vanilla" or not mc_version:
            return
        self._loader_version.clear()
        self._loader_version.addItem("获取中…")
        self._loader_version.setEnabled(False)

        def done(versions) -> None:
            self._loader_version.clear()
            for item in versions:
                self._loader_version.addItem(item.label, item.version)
            self._loader_version.setEnabled(True)

        def failed(message: str) -> None:
            self._loader_version.clear()
            self._loader_version.setEnabled(True)
            self._status.setText(f"加载器版本获取失败：{message}")

        self.tasks.submit(
            loaders.list_versions,
            self.context.paths,
            self.context.config.download,
            kind=kind,
            mc_version=mc_version,
            on_done=done,
            on_error=failed,
        )

    # ---------- 创建 ----------
    def accept(self) -> None:
        if self._installing:
            return
        mc_version = self._version.currentText().strip()
        if not mc_version:
            self._status.setText("请选择或输入游戏版本。")
            return

        kind = self._loader.currentData() or "vanilla"
        loader_version = "" if kind == "vanilla" else self._loader_version.currentData() or self._loader_version.currentText()
        if kind != "vanilla" and not loader_version:
            self._status.setText("请选择加载器版本。")
            return

        name = self._name.text().strip() or f"{mc_version} 实例"
        instance = self.context.instances.create(
            name,
            mc_version,
            version_id=mc_version,
            loader=kind,
            loader_version=str(loader_version),
        )
        self.result_instance = instance

        installed = self.context.version_installed(mc_version)
        need_install = self._install.isChecked() and not installed
        if not need_install:
            if kind != "vanilla" and loader_version:
                self._finish_after_loader(instance)
            else:
                super().accept()
            return

        self._start_install(instance, kind, str(loader_version))

    def _start_install(self, instance, kind: str, loader_version: str) -> None:
        self._installing = True
        self._ok.setEnabled(False)
        self._progress.setVisible(True)
        self._progress.set_value(0)
        self._status.setText("正在准备下载…")

        def done(version_id) -> None:
            self._installing = False
            instance.version_id = str(version_id)
            self.context.instances.save(instance)
            self.context.select_instance(instance.id)
            self._status.setText("安装完成。")
            super(NewInstanceDialog, self).accept()

        def failed(message: str) -> None:
            self._installing = False
            self._ok.setEnabled(True)
            self._progress.setVisible(False)
            self._status.setText(f"安装失败：{message}")

        if kind == "vanilla":
            entry = self._entries.get(instance.mc_version)

            def install_vanilla():
                report = self.context.install_vanilla(instance.mc_version, entry)
                return report.version_id

            self.tasks.submit(install_vanilla, on_done=done, on_error=failed)
        else:
            self.tasks.submit(
                self.context.install_loader,
                kind,
                instance.mc_version,
                loader_version,
                on_done=done,
                on_error=failed,
            )

    def _finish_after_loader(self, instance) -> None:
        kind = instance.loader
        version = instance.loader_version
        if kind == "vanilla" or not version:
            super().accept()
            return
        self._installing = True
        self._ok.setEnabled(False)
        self._status.setText("正在安装加载器…")

        def done(version_id) -> None:
            self._installing = False
            instance.version_id = str(version_id)
            self.context.instances.save(instance)
            super(NewInstanceDialog, self).accept()

        def failed(message: str) -> None:
            self._installing = False
            self._ok.setEnabled(True)
            self._status.setText(f"加载器安装失败：{message}")

        self.tasks.submit(
            self.context.install_loader, kind, instance.mc_version, version, on_done=done, on_error=failed
        )

    def _on_progress(self, progress) -> None:
        if not self._installing:
            return
        percent = float(getattr(progress, "percent", 0.0))
        current = str(getattr(progress, "current", ""))
        self._progress.set_value(percent)
        self._status.setText(f"下载中 {percent:.1f}% · {current}")
