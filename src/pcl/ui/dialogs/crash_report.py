"""崩溃分析结果弹窗。"""

from __future__ import annotations

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPlainTextEdit, QVBoxLayout, QWidget

from pcl.launch.crash import CrashReport
from pcl.ui.dialogs.confirm import BaseDialog
from pcl.ui.widgets.animated_button import AnimatedButton
from pcl.ui.widgets.card import Card
from pcl.ui.widgets.toast import show_toast

SEVERITY_COLORS = {
    "error": "#EF4444",
    "warning": "#F59E0B",
    "info": "#2E7CF6",
}


class CrashDialog(BaseDialog):
    """展示崩溃原因与日志摘录。"""

    def __init__(self, report: CrashReport, parent: QWidget | None = None) -> None:
        super().__init__(parent, title="崩溃分析", width=680)
        self.report = report

        summary = QLabel(report.summary(), self)
        summary.setWordWrap(True)
        summary.setStyleSheet("font-size: 15px; font-weight: 600;")
        self.body.addWidget(summary)

        if report.files:
            files = QLabel("分析文件：" + "、".join(path.name for path in report.files[:3]), self)
            files.setObjectName("Muted")
            files.setWordWrap(True)
            self.body.addWidget(files)

        for reason in report.reasons[:6]:
            card = Card(self, padding=14, spacing=4, object_name="CardFlat")
            header = QHBoxLayout()
            header.setSpacing(8)
            dot = QLabel("●", card)
            dot.setStyleSheet(f"color: {SEVERITY_COLORS.get(reason.severity, '#2E7CF6')};")
            title = QLabel(reason.title, card)
            title.setStyleSheet("font-weight: 600;")
            header.addWidget(dot)
            header.addWidget(title, 1)
            card.add_layout(header)
            if reason.detail:
                detail = QLabel(reason.detail, card)
                detail.setObjectName("Muted")
                detail.setWordWrap(True)
                card.add(detail)
            if reason.suggestion:
                suggestion = QLabel(f"建议：{reason.suggestion}", card)
                suggestion.setWordWrap(True)
                card.add(suggestion)
            self.body.addWidget(card)

        if report.excerpt:
            excerpt_title = QLabel("日志摘录", self)
            excerpt_title.setObjectName("SectionTitle")
            self.body.addWidget(excerpt_title)
            self._excerpt = QPlainTextEdit(self)
            self._excerpt.setReadOnly(True)
            self._excerpt.setPlainText(report.excerpt)
            self._excerpt.setMaximumHeight(140)
            self._excerpt.setStyleSheet(
                "QPlainTextEdit { font-family: monospace; font-size: 12px;"
                " background: rgba(127, 127, 127, 0.08); border-radius: 8px; }"
            )
            self.body.addWidget(self._excerpt)

        copy_button = AnimatedButton("复制分析结果", self, variant="secondary")
        copy_button.clicked.connect(self._copy)
        close_button = AnimatedButton("关闭", self, variant="primary")
        close_button.clicked.connect(self.accept)
        self.footer.addWidget(copy_button)
        self.footer.addWidget(close_button)

    def _copy(self) -> None:
        lines = [f"崩溃分析（退出码 {self.report.exit_code}）", self.report.summary(), ""]
        for reason in self.report.reasons:
            lines.append(f"- {reason.title}")
            if reason.detail:
                lines.append(f"  {reason.detail}")
            if reason.suggestion:
                lines.append(f"  建议：{reason.suggestion}")
        if self.report.excerpt:
            lines += ["", self.report.excerpt]
        QGuiApplication.clipboard().setText("\n".join(lines))
        show_toast(self, "已复制分析结果", "success")


def show_crash_report(parent: QWidget | None, report: CrashReport) -> None:
    CrashDialog(report, parent).exec()
