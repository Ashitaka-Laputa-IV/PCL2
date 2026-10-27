"""新建实例对话框：从已安装版本或已下载的整合包创建。"""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QStandardPaths, Qt
from PySide6.QtWidgets import QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QWidget

from pcl.core.errors import friendly_message
from pcl.modpack import FORMAT_LABELS, detect_format
from pcl.services import AppContext
from pcl.ui.dialogs.confirm import BaseDialog
from pcl.ui.widgets.animated_button import AnimatedButton
from pcl.ui.widgets.progress import GradientProgressBar


def _version_sort_key(version_id: str) -> list:
    """把版本号拆成可比较的片段，避免 1.9 排在 1.20 之后。"""
    parts: list = []
    for piece in re.split(r"[._+-]", version_id):
        parts.append((1, int(piece)) if piece.isdigit() else (0, piece.lower()))
    return parts


def _friendly_label(mc_version: str, loader: str, loader_version: str) -> str:
    if not loader or loader == "vanilla":
        return f"{mc_version} · 原版"
    suffix = f"{loader.capitalize()} {loader_version}".strip()
    return f"{mc_version} · {suffix}"


class NewInstanceDialog(BaseDialog):
    """从已安装版本或整合包文件创建实例。"""

    def __init__(self, context: AppContext, tasks, bridge, parent: QWidget | None = None) -> None:
        super().__init__(parent, title="新建实例", width=560)
        self.context = context
        self.tasks = tasks
        self.bridge = bridge
        self.result_instance = None
        self._meta: dict[str, dict[str, str]] = {}
        self._packs: dict[str, str] = {}
        self._installing = False

        form = QFormLayout()
        form.setSpacing(12)
        self.body.addLayout(form)

        self._name = QLineEdit(self)
        form.addRow("实例名称", self._name)

        self._version = QComboBox(self)
        self._version.setMinimumWidth(300)
        form.addRow("游戏版本", self._version)

        pack_row = QWidget(self)
        pack_layout = QHBoxLayout(pack_row)
        pack_layout.setContentsMargins(0, 0, 0, 0)
        pack_layout.setSpacing(8)
        self._pack = QComboBox(pack_row)
        self._pack.setMinimumWidth(230)
        browse = AnimatedButton("浏览…", pack_row, variant="secondary", height=30)
        browse.clicked.connect(self._browse_pack)
        pack_layout.addWidget(self._pack, 1)
        pack_layout.addWidget(browse)
        form.addRow("整合包", pack_row)

        self._detail = QLabel("", self)
        self._detail.setObjectName("Muted")
        self._detail.setWordWrap(True)
        self.body.addWidget(self._detail)

        self._status = QLabel("", self)
        self._status.setObjectName("Muted")
        self._status.setWordWrap(True)
        self.body.addWidget(self._status)

        self._progress = GradientProgressBar(self, height=8)
        self._progress.setVisible(False)
        self.body.addWidget(self._progress)

        self._ok = self.add_buttons("创建", "取消")

        self._version.currentIndexChanged.connect(self._on_version_changed)
        self._pack.currentIndexChanged.connect(self._on_pack_changed)
        self.bridge.download_progress.connect(self._on_progress)

        self._populate_installed()
        self._populate_packs()

    # ---------- 已安装版本 ----------
    def _populate_installed(self) -> None:
        game_root = self.context.paths.game
        manager = self.context.instances
        installed = sorted(self.context.installed_versions(), key=_version_sort_key, reverse=True)
        for version_id in installed:
            try:
                mc_version, loader, loader_version = manager.describe_version(game_root, version_id)
            except Exception:  # noqa: BLE001 - 单个损坏版本不影响列表
                continue
            label = _friendly_label(mc_version, loader, loader_version)
            self._meta[version_id] = {
                "mc_version": mc_version,
                "loader": loader,
                "loader_version": loader_version,
                "label": label,
            }
            self._version.addItem(label, version_id)
            self._version.setItemData(
                self._version.count() - 1,
                version_id,
                Qt.ItemDataRole.ToolTipRole,
            )

        if not self._meta:
            self._version.addItem("还没有已安装的版本，请先在「下载」页安装", "")
            self._version.setEnabled(False)
            self._status.setText("新建实例需要已安装版本，或从下方选择一个整合包文件。")
        else:
            self._version.setCurrentIndex(0)
            self._on_version_changed(0)

    # ---------- 整合包文件 ----------
    def _pack_candidates(self) -> list[Path]:
        roots = [self.context.paths.game / "modpacks", self.context.paths.cache / "modpacks"]
        downloads = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
        if downloads:
            roots.append(Path(downloads))
        found: dict[str, Path] = {}
        for root in roots:
            if not root.is_dir():
                continue
            try:
                children = sorted(root.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True)
            except OSError:
                continue
            for child in children[:60]:
                if not child.is_file():
                    continue
                suffix = child.suffix.lower()
                if suffix not in (".mrpack", ".zip"):
                    continue
                if root == Path(downloads) and suffix != ".mrpack":
                    continue  # Downloads 里的普通 zip 太多，只收 .mrpack
                found.setdefault(str(child.resolve()), child)
        return list(found.values())

    def _populate_packs(self) -> None:
        self._pack.addItem("不使用整合包", "")
        for path in self._pack_candidates():
            try:
                kind = detect_format(path)
            except Exception:  # noqa: BLE001 - 损坏或不支持的 zip 直接跳过
                continue
            label = f"{path.name}（{FORMAT_LABELS.get(kind, kind)}）"
            self._packs[str(path)] = label
            self._pack.addItem(label, str(path))
        if len(self._packs) == 0:
            self._pack.setToolTip("没有在 modpacks 目录或下载目录找到整合包，可点击「浏览…」选择")
        else:
            self._pack.setToolTip("从 .mrpack / 整合包 zip 创建独立实例")
            self._status.setText(
                f"检测到 {len(self._packs)} 个已下载整合包，可在「整合包」一行选择后直接创建。"
            )

    def _browse_pack(self) -> None:
        selected, _filter = QFileDialog.getOpenFileName(
            self,
            "选择整合包",
            QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation) or str(Path.home()),
            "整合包 (*.mrpack *.zip)",
        )
        if not selected:
            return
        path = Path(selected)
        try:
            kind = detect_format(path)
        except Exception as error:  # noqa: BLE001
            self._status.setText(f"无法识别该整合包：{friendly_message(error)}")
            return
        key = str(path.resolve())
        label = f"{path.name}（{FORMAT_LABELS.get(kind, kind)}）"
        self._packs[key] = label
        index = self._pack.findData(key)
        if index < 0:
            self._pack.addItem(label, key)
            index = self._pack.count() - 1
        self._pack.setCurrentIndex(index)

    def _selected_pack(self) -> Path | None:
        data = self._pack.currentData()
        return Path(str(data)) if data else None

    def _sync_ok_state(self) -> None:
        has_source = self._selected_pack() is not None or bool(self._meta)
        self._ok.setEnabled(has_source and not self._installing)

    # ---------- 选择联动 ----------
    def _on_pack_changed(self, _index: int) -> None:
        pack = self._selected_pack()
        self._version.setEnabled(pack is None and bool(self._meta))
        if pack is not None:
            self._name.setPlaceholderText(pack.stem)
            self._detail.setText(
                f"将从整合包创建：{pack.name}\n"
                "导入时会释放配置/Mod，并按包内声明安装对应的游戏版本与加载器。"
            )
        else:
            self._on_version_changed(self._version.currentIndex())
        self._sync_ok_state()

    def _on_version_changed(self, _index: int) -> None:
        meta = self._selected_meta()
        if meta is None:
            self._detail.setText("")
            self._sync_ok_state()
            return
        version_id = str(self._version.currentData())
        loader = meta["loader"]
        loader_text = "原版" if loader == "vanilla" else f"{loader.capitalize()} {meta['loader_version']}".strip()
        self._name.setPlaceholderText(meta["label"].replace(" · ", " "))
        self._detail.setText(f"启动版本：{version_id}\n加载器：{loader_text}")
        self._sync_ok_state()

    def _selected_meta(self) -> dict[str, str] | None:
        version_id = self._version.currentData()
        if not version_id:
            return None
        return self._meta.get(str(version_id))

    # ---------- 创建 ----------
    def accept(self) -> None:
        if self._installing:
            return
        pack = self._selected_pack()
        if pack is not None:
            self._start_import(pack)
            return
        self._create_from_version()

    def _create_from_version(self) -> None:
        meta = self._selected_meta()
        if meta is None:
            self._status.setText("请选择一个已安装的版本，或选择一个整合包。")
            return
        version_id = str(self._version.currentData())
        name = self._name.text().strip() or meta["label"].replace(" · ", " ")
        try:
            instance = self.context.instances.create(
                name,
                meta["mc_version"],
                version_id=version_id,
                loader=meta["loader"],
                loader_version=meta["loader_version"],
            )
        except Exception as error:  # noqa: BLE001 - 对话框内直接提示
            self._status.setText(f"创建失败：{error}")
            return
        self.result_instance = instance
        super().accept()

    def _start_import(self, pack: Path) -> None:
        self._installing = True
        self._ok.setEnabled(False)
        self._progress.setVisible(True)
        self._progress.set_value(0)
        self._status.setText("正在导入整合包…")
        name = self._name.text().strip()

        def work():
            from pcl.modpack import import_modpack

            return import_modpack(
                pack,
                self.context.paths,
                self.context.config.download,
                instance_manager=self.context.instances,
                api_key=self.context.config.advanced.curseforge_api_key,
                java_path=self.context.config.launch.java_path or None,
                install_game=True,
                name_override=name,
            )

        def done(result) -> None:
            self._installing = False
            self.result_instance = result.instance
            self.context.select_instance(result.instance.id)
            super(NewInstanceDialog, self).accept()

        def failed(message: str) -> None:
            self._installing = False
            self._ok.setEnabled(True)
            self._progress.setVisible(False)
            self._status.setText(f"导入失败：{message}")

        self.tasks.submit(work, on_done=done, on_error=failed)

    def _on_progress(self, progress) -> None:
        if not self._installing:
            return
        percent = float(getattr(progress, "percent", 0.0))
        current = str(getattr(progress, "current", ""))
        self._progress.set_value(percent)
        if current:
            self._status.setText(f"导入中 {percent:.1f}% · {current}")
