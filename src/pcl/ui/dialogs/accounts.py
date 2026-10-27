"""离线账户管理：添加、删除与切换。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pcl.core.errors import friendly_message
from pcl.launch.auth import offline_uuid
from pcl.services import AppContext
from pcl.ui.dialogs.confirm import BaseDialog, ask_text, confirm
from pcl.ui.widgets.animated_button import AnimatedButton
from pcl.ui.widgets.toast import show_toast


class AccountsDialog(BaseDialog):
    """账户列表与操作。"""

    def __init__(self, context: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent, title="账户管理", width=520)
        self.context = context

        hint = QLabel("当前版本仅支持离线账户；UUID 由用户名按 Java 规则生成，与其他启动器一致。", self)
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        self.body.addWidget(hint)

        self._list = QListWidget(self)
        self._list.setMinimumHeight(220)
        self._list.itemDoubleClicked.connect(lambda _item: self._activate_current())
        self.body.addWidget(self._list)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        add_button = AnimatedButton("添加账户", self, variant="primary")
        add_button.clicked.connect(self._add)
        remove_button = AnimatedButton("删除", self, variant="ghost")
        remove_button.clicked.connect(self._remove)
        activate_button = AnimatedButton("设为当前", self, variant="secondary")
        activate_button.clicked.connect(self._activate_current)
        actions.addWidget(add_button)
        actions.addWidget(activate_button)
        actions.addStretch(1)
        actions.addWidget(remove_button)
        self.body.addLayout(actions)

        self._preview = QLabel("", self)
        self._preview.setObjectName("Muted")
        self._preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._preview.setWordWrap(True)
        self.body.addWidget(self._preview)

        self._close_button = AnimatedButton("关闭", self, variant="secondary")
        self._close_button.clicked.connect(self.accept)
        self.footer.addWidget(self._close_button)

        self._list.currentItemChanged.connect(self._update_preview)
        self._reload()

    # ---------- 数据 ----------
    def _reload(self) -> None:
        self._list.clear()
        current = self.context.accounts.selected
        for account in self.context.accounts.accounts:
            marker = "● " if account.name == current else "○ "
            item = QListWidgetItem(f"{marker}{account.name}    {account.uuid}")
            item.setData(Qt.ItemDataRole.UserRole, account.name)
            self._list.addItem(item)
        if self._list.count():
            self._list.setCurrentRow(0)
        else:
            self._preview.setText("还没有账户，点击「添加账户」创建一个。")

    def _selected_name(self) -> str:
        item = self._list.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole)) if item is not None else ""

    def _update_preview(self, *_args) -> None:
        name = self._selected_name()
        account = self.context.accounts.find(name) if name else None
        if account is None:
            self._preview.setText("")
            return
        self._preview.setText(f"用户名：{account.name}\nUUID：{account.uuid}\n点击“设为当前”后启动游戏将使用该账户。")

    # ---------- 操作 ----------
    def _add(self) -> None:
        name = ask_text(
            self,
            "添加离线账户",
            "用户名（1-16 位字母、数字或下划线）",
            placeholder="例如 Steve",
        )
        if not name:
            return
        try:
            account = self.context.accounts.add(name)
        except Exception as error:
            show_toast(self, friendly_message(error), "error")
            return
        self.context.select_account(account.name)
        show_toast(self, f"已添加账户 {account.name}", "success")
        self._reload()

    def _remove(self) -> None:
        name = self._selected_name()
        if not name:
            return
        if not confirm(self, "删除账户", f"确定删除账户「{name}」吗？", ok_text="删除", danger=True):
            return
        self.context.accounts.remove(name)
        if not self.context.accounts.accounts:
            self.context.accounts.ensure_default()
        self.context.config.selected_account = self.context.accounts.selected
        self.context.save_config()
        self._reload()

    def _activate_current(self) -> None:
        name = self._selected_name()
        if not name:
            return
        self.context.select_account(name)
        show_toast(self, f"已切换到 {name}", "success")
        self._reload()

    def _copy_uuid(self) -> None:
        name = self._selected_name()
        if name:
            QGuiApplication.clipboard().setText(offline_uuid(name))
            show_toast(self, "UUID 已复制", "success")
