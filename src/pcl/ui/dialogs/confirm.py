"""通用对话框：确认、输入与消息。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from pcl.ui.widgets.animated_button import AnimatedButton


class BaseDialog(QDialog):
    """统一风格的对话框骨架。"""

    def __init__(self, parent: QWidget | None = None, *, title: str = "", width: int = 440) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(width)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(24, 22, 24, 18)
        self.root.setSpacing(14)

        heading = QLabel(title, self)
        heading.setObjectName("PageTitle")
        heading.setStyleSheet("font-size: 19px;")
        self.root.addWidget(heading)

        self.body = QVBoxLayout()
        self.body.setSpacing(10)
        self.root.addLayout(self.body)

        self.footer = QHBoxLayout()
        self.footer.setSpacing(10)
        self.footer.addStretch(1)
        self.root.addLayout(self.footer)

    def add_buttons(self, ok_text: str = "确定", cancel_text: str = "取消", *, danger: bool = False) -> AnimatedButton:
        cancel = AnimatedButton(cancel_text, self, variant="secondary")
        cancel.clicked.connect(self.reject)
        ok = AnimatedButton(ok_text, self, variant="danger" if danger else "primary")
        ok.clicked.connect(self.accept)
        self.footer.addWidget(cancel)
        self.footer.addWidget(ok)
        return ok


def confirm(
    parent: QWidget | None,
    title: str,
    message: str,
    *,
    ok_text: str = "确定",
    cancel_text: str = "取消",
    danger: bool = False,
) -> bool:
    """确认对话框。"""
    dialog = BaseDialog(parent, title=title)
    label = QLabel(message, dialog)
    label.setWordWrap(True)
    label.setObjectName("PageSubtitle")
    dialog.body.addWidget(label)
    dialog.add_buttons(ok_text, cancel_text, danger=danger)
    return dialog.exec() == QDialog.DialogCode.Accepted


def ask_text(
    parent: QWidget | None,
    title: str,
    label_text: str,
    default: str = "",
    *,
    placeholder: str = "",
) -> str | None:
    """文本输入对话框，取消返回 ``None``。"""
    dialog = BaseDialog(parent, title=title)
    label = QLabel(label_text, dialog)
    label.setObjectName("PageSubtitle")
    dialog.body.addWidget(label)
    editor = QLineEdit(dialog)
    editor.setText(default)
    editor.setPlaceholderText(placeholder)
    editor.selectAll()
    dialog.body.addWidget(editor)
    dialog.add_buttons("确定", "取消")
    editor.returnPressed.connect(dialog.accept)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        return editor.text().strip()
    return None


def show_message(parent: QWidget | None, title: str, message: str) -> None:
    """纯信息对话框。"""
    dialog = BaseDialog(parent, title=title)
    label = QLabel(message, dialog)
    label.setWordWrap(True)
    label.setObjectName("PageSubtitle")
    dialog.body.addWidget(label)
    button = AnimatedButton("知道了", dialog, variant="primary")
    button.clicked.connect(dialog.accept)
    dialog.footer.addWidget(button)
    dialog.exec()
