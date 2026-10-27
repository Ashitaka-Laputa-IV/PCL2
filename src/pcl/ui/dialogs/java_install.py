"""一键安装 Java 对话框。"""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QLabel, QWidget

from pcl.launch.java_installer import SUPPORTED_MAJORS, install_java, installed_runtime
from pcl.services import AppContext
from pcl.ui.dialogs.confirm import BaseDialog
from pcl.ui.widgets.animated_button import AnimatedButton
from pcl.ui.widgets.progress import GradientProgressBar
from pcl.ui.widgets.toast import show_toast


class JavaInstallDialog(BaseDialog):
    """选择大版本并自动下载安装 JRE。"""

    def __init__(self, context: AppContext, tasks, bridge, parent: QWidget | None = None) -> None:
        super().__init__(parent, title="一键安装 Java", width=480)
        self.context = context
        self.tasks = tasks
        self.bridge = bridge
        self.runtime = None
        self._installing = False

        hint = QLabel(
            "将自动下载 Adoptium Temurin JRE 并解压到启动器目录，无需手动配置环境变量。\n"
            "1.17 及以上建议 Java 17 或 21，1.16 及更早版本建议 Java 8。",
            self,
        )
        hint.setObjectName("PageSubtitle")
        hint.setWordWrap(True)
        self.body.addWidget(hint)

        row = QWidget(self)
        from PySide6.QtWidgets import QHBoxLayout

        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        label = QLabel("Java 版本", row)
        self._major = QComboBox(row)
        for major in SUPPORTED_MAJORS:
            self._major.addItem(f"Java {major}", major)
        self._major.setCurrentIndex(2)
        layout.addWidget(label)
        layout.addWidget(self._major, 1)
        self.body.addWidget(row)

        self._status = QLabel("", self)
        self._status.setObjectName("Muted")
        self._status.setWordWrap(True)
        self.body.addWidget(self._status)

        self._progress = GradientProgressBar(self, height=8)
        self._progress.set_value(0)
        self.body.addWidget(self._progress)

        self._install_button = self.add_buttons("开始安装", "关闭")
        self._install_button.clicked.disconnect()
        self._install_button.clicked.connect(self._install)
        self.bridge.download_progress.connect(self._on_download)

    def _install(self) -> None:
        if self._installing:
            return
        major = int(self._major.currentData())
        existing = installed_runtime(self.context.paths, major)
        if existing is not None:
            self.runtime = existing
            self._status.setText(f"Java {major} 已安装：{existing.path}")
            self.accept()
            return

        self._installing = True
        self._install_button.set_loading(True, "安装中")
        self._install_button.setEnabled(False)
        self._status.setText("正在下载 Java…")
        self._progress.set_value(0)

        def done(runtime) -> None:
            self._installing = False
            self.runtime = runtime
            self._install_button.set_loading(False, "开始安装")
            self._install_button.setEnabled(True)
            self.context.refresh_java()
            self.context.config.launch.java_path = str(runtime.path)
            self.context.save_config()
            show_toast(self, f"Java {runtime.major} 安装完成", "success")
            self.accept()

        def failed(message: str) -> None:
            self._installing = False
            self._install_button.set_loading(False, "重试")
            self._install_button.setEnabled(True)
            self._status.setText(f"安装失败：{message}")

        self.tasks.submit(
            install_java,
            self.context.paths,
            major,
            self.context.config.download,
            on_done=done,
            on_error=failed,
        )

    def _on_download(self, progress) -> None:
        if not self._installing:
            return
        percent = float(getattr(progress, "percent", 0.0))
        self._progress.set_value(min(85.0, percent * 0.85))
        current = str(getattr(progress, "current", ""))
        if current:
            self._status.setText(f"正在下载 {percent:.0f}% · {current}")
