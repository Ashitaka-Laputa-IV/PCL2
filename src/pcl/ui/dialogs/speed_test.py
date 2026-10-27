"""下载源测速对话框。"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QListWidget, QListWidgetItem, QWidget

from pcl.net.speedtest import test_sources
from pcl.services import AppContext
from pcl.ui.dialogs.confirm import BaseDialog
from pcl.ui.widgets.animated_button import AnimatedButton
from pcl.ui.widgets.toast import show_toast


class SpeedTestDialog(BaseDialog):
    """比较官方源与镜像源的延迟和速度。"""

    def __init__(self, context: AppContext, tasks, parent: QWidget | None = None) -> None:
        super().__init__(parent, title="下载源测速", width=520)
        self.context = context
        self.tasks = tasks

        hint = QLabel("下载版本清单文件来估算延迟与速度，结果仅供参考。", self)
        hint.setObjectName("Muted")
        self.body.addWidget(hint)

        self._list = QListWidget(self)
        self._list.setMinimumHeight(140)
        self.body.addWidget(self._list)

        self._status = QLabel("", self)
        self._status.setObjectName("Muted")
        self.body.addWidget(self._status)

        self._test_button = AnimatedButton("开始测速", self, variant="primary")
        self._test_button.clicked.connect(self._run)
        self._close_button = AnimatedButton("关闭", self, variant="secondary")
        self._close_button.clicked.connect(self.accept)
        self.footer.addWidget(self._close_button)
        self.footer.addWidget(self._test_button)
        self._run()

    def _run(self) -> None:
        self._list.clear()
        self._status.setText("正在测速…")
        self._test_button.set_loading(True, "测速中")
        self._test_button.setEnabled(False)

        def done(results) -> None:
            self._test_button.set_loading(False, "重新测速")
            self._test_button.setEnabled(True)
            self._list.clear()
            for result in results:
                if result.ok:
                    text = (
                        f"{result.name}    延迟 {result.latency_ms:.0f} ms    "
                        f"速度 {result.speed_kbps:.0f} KB/s"
                    )
                else:
                    text = f"{result.name}    不可用：{result.error[:60]}"
                item = QListWidgetItem(text)
                self._list.addItem(item)
            self._status.setText("测速完成")

        def failed(message: str) -> None:
            self._test_button.set_loading(False, "重新测速")
            self._test_button.setEnabled(True)
            self._status.setText(f"测速失败：{message}")
            show_toast(self, message, "error")

        self.tasks.submit(
            test_sources,
            mirror_base=self.context.config.download.mirror_base,
            on_done=done,
            on_error=failed,
        )
