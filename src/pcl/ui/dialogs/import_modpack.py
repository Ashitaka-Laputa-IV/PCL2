"""整合包导入对话框：选择文件、识别格式、显示进度与结果。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QCheckBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QWidget

from pcl.core.errors import friendly_message
from pcl.modpack import FORMAT_LABELS, detect_format
from pcl.services import AppContext
from pcl.ui.dialogs.confirm import BaseDialog
from pcl.ui.widgets.animated_button import AnimatedButton
from pcl.ui.widgets.progress import GradientProgressBar


class ImportModpackDialog(BaseDialog):
    """导入整合包。"""

    def __init__(self, context: AppContext, tasks, bridge, parent: QWidget | None = None) -> None:
        super().__init__(parent, title="导入整合包", width=560)
        self.context = context
        self.tasks = tasks
        self.bridge = bridge
        self.result = None
        self._importing = False

        row = QHBoxLayout()
        self._path = QLineEdit(self)
        self._path.setPlaceholderText("选择 .mrpack / .zip 整合包")
        self._path.textChanged.connect(self._on_path_changed)
        browse = AnimatedButton("浏览…", self, variant="secondary")
        browse.clicked.connect(self._browse)
        row.addWidget(self._path, 1)
        row.addWidget(browse)
        self.body.addLayout(row)

        self._format = QLabel("支持 Modrinth / CurseForge / HMCL / PCL 四种格式", self)
        self._format.setObjectName("Muted")
        self.body.addWidget(self._format)

        self._name = QLineEdit(self)
        self._name.setPlaceholderText("实例名称（留空则使用整合包名称）")
        self.body.addWidget(self._name)

        self._install = QCheckBox("同时安装游戏本体与加载器", self)
        self._install.setChecked(True)
        self.body.addWidget(self._install)

        self._status = QLabel("", self)
        self._status.setObjectName("Muted")
        self._status.setWordWrap(True)
        self.body.addWidget(self._status)

        self._progress = GradientProgressBar(self, height=8)
        self._progress.setVisible(False)
        self.body.addWidget(self._progress)

        self._ok = self.add_buttons("开始导入", "取消")
        self.bridge.download_progress.connect(self._on_progress)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择整合包",
            str(Path.home()),
            "整合包 (*.mrpack *.zip);;所有文件 (*)",
        )
        if path:
            self._path.setText(path)

    def _on_path_changed(self, text: str) -> None:
        candidate = Path(text.strip())
        if not candidate.is_file():
            self._format.setText("支持 Modrinth / CurseForge / HMCL / PCL 四种格式")
            return
        try:
            kind = detect_format(candidate)
        except Exception as error:
            self._format.setText(f"无法识别：{friendly_message(error)}")
            return
        self._format.setText(f"识别为 {FORMAT_LABELS.get(kind, kind)} 整合包")

    def accept(self) -> None:
        if self._importing:
            return
        candidate = Path(self._path.text().strip())
        if not candidate.is_file():
            self._status.setText("请选择有效的整合包文件。")
            return

        self._importing = True
        self._ok.setEnabled(False)
        self._progress.setVisible(True)
        self._status.setText("正在解析整合包…")

        def done(result) -> None:
            self._importing = False
            self.result = result
            self.context.select_instance(result.instance.id)
            messages = [f"已导入实例：{result.instance.name}"]
            if result.downloaded:
                messages.append(f"下载 {result.downloaded} 个文件")
            if result.failed:
                messages.append(f"{len(result.failed)} 个文件失败")
            if result.warnings:
                messages.append(result.warnings[0])
            self._status.setText("；".join(messages))
            super(ImportModpackDialog, self).accept()

        def failed(message: str) -> None:
            self._importing = False
            self._ok.setEnabled(True)
            self._progress.setVisible(False)
            self._status.setText(f"导入失败：{message}")

        self.tasks.submit(
            self._do_import,
            candidate,
            on_done=done,
            on_error=failed,
        )

    def _do_import(self, path: Path):
        from pcl.modpack import import_modpack

        return import_modpack(
            path,
            self.context.paths,
            self.context.config.download,
            instance_manager=self.context.instances,
            api_key=self.context.config.advanced.curseforge_api_key,
            java_path=self.context.config.launch.java_path or None,
            install_game=self._install.isChecked(),
            name_override=self._name.text().strip(),
        )

    def _on_progress(self, progress) -> None:
        if not self._importing:
            return
        percent = float(getattr(progress, "percent", 0.0))
        current = str(getattr(progress, "current", ""))
        self._progress.set_value(percent)
        if current:
            self._status.setText(f"下载中 {percent:.1f}% · {current}")
