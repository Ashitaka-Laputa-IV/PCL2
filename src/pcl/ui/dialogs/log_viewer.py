"""游戏日志窗口：实时追加、自动滚动与结束进程。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QVBoxLayout, QWidget

from pcl.services import AppContext
from pcl.ui.widgets.animated_button import AnimatedButton

MAX_BLOCKS = 5000


class LogWindow(QDialog):
    """独立的日志查看窗口。"""

    def __init__(self, context: AppContext, bridge, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.context = context
        self.setWindowTitle("游戏日志 · PCL2")
        self.resize(880, 560)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        header = QHBoxLayout()
        self._title = QLabel("游戏日志", self)
        self._title.setObjectName("SectionTitle")
        header.addWidget(self._title)
        header.addStretch(1)

        self._autoscroll = QCheckBox("自动滚动", self)
        self._autoscroll.setChecked(True)
        header.addWidget(self._autoscroll)

        clear_button = AnimatedButton("清空", self, variant="ghost")
        clear_button.clicked.connect(lambda: self._output.clear())
        header.addWidget(clear_button)

        crash_button = AnimatedButton("崩溃分析", self, variant="secondary")
        crash_button.clicked.connect(self._analyze_crash)
        header.addWidget(crash_button)

        self._stop_button = AnimatedButton("结束游戏", self, variant="danger")
        self._stop_button.clicked.connect(self._stop_game)
        header.addWidget(self._stop_button)
        layout.addLayout(header)

        self._output = QPlainTextEdit(self)
        self._output.setReadOnly(True)
        self._output.setMaximumBlockCount(MAX_BLOCKS)
        self._output.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        mono = QFont("JetBrains Mono")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setPixelSize(12)
        self._output.setFont(mono)
        self._output.setStyleSheet(
            "QPlainTextEdit { background: #12161F; color: #D5DEEA; border: none; border-radius: 10px;"
            " padding: 10px; selection-background-color: #2E7CF6; }"
        )
        layout.addWidget(self._output, 1)

        self._bridge = bridge
        self._unsubscribe = bridge.game_log.connect(self._append)
        bridge.game_state.connect(self._on_state)
        self._load_existing()

    # ---------- 日志 ----------
    def _load_existing(self) -> None:
        path = self.context.paths.game_log
        if not path.is_file():
            return
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        lines = text.splitlines()[-MAX_BLOCKS:]
        self._output.setPlainText("\n".join(lines))
        self._scroll_to_bottom()

    def _append(self, line: str) -> None:
        self._output.appendPlainText(line)
        if self._autoscroll.isChecked():
            self._scroll_to_bottom()

    def _scroll_to_bottom(self) -> None:
        bar = self._output.verticalScrollBar()
        bar.setValue(bar.maximum())

    # ---------- 状态 ----------
    def update_state(self, state: object) -> None:
        """公开的状态刷新入口（避免外部访问私有方法）。"""
        self._on_state(state)

    def _on_state(self, state: object) -> None:
        running = bool(getattr(state, "running", False))
        self._stop_button.setEnabled(running)
        if not running:
            code = getattr(state, "exit_code", None)
            self._title.setText(f"游戏日志 · 已退出（退出码 {code}）" if code is not None else "游戏日志")

    def _stop_game(self) -> None:
        self.context.stop_game()
        self._title.setText("游戏日志 · 正在结束进程…")

    def _analyze_crash(self) -> None:
        from pcl.ui.dialogs.crash_report import show_crash_report

        try:
            report = self.context.crash_report()
        except Exception as error:
            from pcl.ui.widgets.toast import show_toast

            show_toast(self, str(error), "error")
            return
        show_crash_report(self, report)

    def closeEvent(self, event) -> None:  # noqa: N802
        try:
            self._unsubscribe()
        except Exception:
            pass
        super().closeEvent(event)
