"""新建实例对话框：直接从已安装版本列表中选择。"""

from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QFormLayout, QLabel, QLineEdit, QWidget

from pcl.services import AppContext
from pcl.ui.dialogs.confirm import BaseDialog


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
    """从 ``versions/`` 中已安装的版本创建实例。"""

    def __init__(self, context: AppContext, tasks, bridge, parent: QWidget | None = None) -> None:
        super().__init__(parent, title="新建实例", width=520)
        self.context = context
        self.tasks = tasks
        self.bridge = bridge
        self.result_instance = None
        self._meta: dict[str, dict[str, str]] = {}

        form = QFormLayout()
        form.setSpacing(12)
        self.body.addLayout(form)

        self._name = QLineEdit(self)
        form.addRow("实例名称", self._name)

        self._version = QComboBox(self)
        self._version.setMinimumWidth(280)
        form.addRow("游戏版本", self._version)

        self._detail = QLabel("", self)
        self._detail.setObjectName("Muted")
        self._detail.setWordWrap(True)
        self.body.addWidget(self._detail)

        self._status = QLabel("", self)
        self._status.setObjectName("Muted")
        self._status.setWordWrap(True)
        self.body.addWidget(self._status)

        self._ok = self.add_buttons("创建", "取消")

        self._version.currentIndexChanged.connect(self._on_version_changed)
        self._populate_installed()

    # ---------- 数据 ----------
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
            index = self._version.count() - 1
            self._version.setItemData(index, version_id, Qt.ItemDataRole.ToolTipRole)

        if not self._meta:
            self._ok.setEnabled(False)
            self._status.setText("还没有已安装的版本。请先在「下载」页安装游戏，完成后这里会自动列出。")
            return

        self._status.setText("只显示已安装的版本；需要安装新版本请前往「下载」页。")
        self._version.setCurrentIndex(0)
        self._on_version_changed(0)

    def _selected_meta(self) -> dict[str, str] | None:
        version_id = self._version.currentData()
        if not version_id:
            return None
        return self._meta.get(str(version_id))

    def _on_version_changed(self, _index: int) -> None:
        meta = self._selected_meta()
        if meta is None:
            self._detail.setText("")
            return
        self._name.setPlaceholderText(meta["label"].replace(" · ", " "))
        version_id = str(self._version.currentData())
        loader = meta["loader"]
        loader_text = "原版" if loader == "vanilla" else f"{loader.capitalize()} {meta['loader_version']}".strip()
        self._detail.setText(f"启动版本：{version_id}\n加载器：{loader_text}")

    # ---------- 创建 ----------
    def accept(self) -> None:
        meta = self._selected_meta()
        if meta is None:
            self._status.setText("请选择一个已安装的版本。")
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
