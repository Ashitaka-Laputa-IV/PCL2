"""更多：关于、帮助与目录快捷入口。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from pcl import APP_NAME, __version__
from pcl.services import AppContext
from pcl.ui.pages.base import BasePage
from pcl.ui.widgets.animated_button import AnimatedButton
from pcl.ui.widgets.card import Card
from pcl.ui.widgets.toast import show_toast

LINKS = (
    ("官方 PCL", "https://github.com/Meloong-Git/PCL"),
    ("参考仓库", "https://github.com/Ashitaka-Laputa-IV/PCL"),
    ("PCL 帮助文档", "https://github.com/LTCatt/PCL2Help"),
)

FAQ = (
    (
        "没有检测到 Java 怎么办？",
        "安装一个 Java 17（1.17+ 建议 17 或 21，1.16 及更早建议 8），然后在「设置 → 启动」中点击「重新检测」，"
        "或手动选择 java 可执行文件。",
    ),
    (
        "游戏启动后立刻退出？",
        "启动器会在「游戏日志」窗口显示实时输出；退出后可在日志窗口或版本详情页点击「崩溃分析」，"
        "它会读取 crash-reports 与 latest.log 并给出可能原因。",
    ),
    (
        "整合包导入后没有 Mod？",
        "Modrinth / HMCL / PCL 包会还原覆盖文件；CurseForge 包需要 API Key 才能自动下载，"
        "可在「设置 → 高级」填写，或按提示手动放入 mods 目录。",
    ),
    (
        "Mod 冲突怎么排查？",
        "打开版本详情的「Mod」页签，顶部横幅会列出缺失前置、重复安装与版本不匹配；"
        "也可以逐个禁用（开关）后启动测试。",
    ),
)


class MorePage(BasePage):
    """关于与帮助。"""

    title = "更多"
    subtitle = "关于本启动器、常见问题与目录入口"

    def __init__(self, context: AppContext, bridge, tasks, parent: QWidget | None = None) -> None:
        super().__init__(context, bridge, tasks, parent)
        self._build()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 22, 24, 18)
        outer.setSpacing(12)
        outer.addLayout(self.build_header())

        scroll, content = self.make_scroll()

        about = Card(self, padding=22, spacing=8)
        title = QLabel(f"{APP_NAME} · Python Edition  v{__version__}", about)
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        about.add(title)
        description = QLabel(
            "使用 Python 3.12 + PySide6 实现的 Minecraft 启动器，参考 PCL2 的页面结构与交互习惯独立开发。\n"
            "支持原版与 Forge / NeoForge / Fabric / Quilt 安装、离线启动、资源管理、"
            "四种整合包导入导出与崩溃分析。",
            about,
        )
        description.setObjectName("PageSubtitle")
        description.setWordWrap(True)
        about.add(description)

        links = QHBoxLayout()
        links.setSpacing(8)
        for label, url in LINKS:
            button = AnimatedButton(label, about, variant="secondary", height=32)
            button.clicked.connect(lambda _=False, target=url: QDesktopServices.openUrl(QUrl(target)))
            links.addWidget(button)
        links.addStretch(1)
        about.add_layout(links)
        content.addWidget(about)

        folders = Card(self, padding=22, spacing=10)
        folder_title = QLabel("目录", folders)
        folder_title.setObjectName("SectionTitle")
        folders.add(folder_title)
        row = QHBoxLayout()
        row.setSpacing(8)
        for label, path in (
            ("数据目录", self.context.paths.root),
            ("游戏目录", self.context.paths.game),
            ("日志目录", self.context.paths.logs),
        ):
            button = AnimatedButton(label, folders, variant="secondary", icon_name="folder", height=34)
            button.clicked.connect(lambda _=False, target=path: self._open(target))
            row.addWidget(button)
        row.addStretch(1)
        folders.add_layout(row)
        content.addWidget(folders)

        faq = Card(self, padding=22, spacing=12)
        faq_title = QLabel("常见问题", faq)
        faq_title.setObjectName("SectionTitle")
        faq.add(faq_title)
        for question, answer in FAQ:
            item = QWidget(faq)
            layout = QVBoxLayout(item)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(2)
            q = QLabel(question, item)
            q.setStyleSheet("font-weight: 600;")
            a = QLabel(answer, item)
            a.setObjectName("Muted")
            a.setWordWrap(True)
            layout.addWidget(q)
            layout.addWidget(a)
            faq.add(item)
        content.addWidget(faq)
        content.addStretch(1)

        outer.addWidget(scroll, 1)

    def _open(self, path: Path) -> None:
        try:
            path.mkdir(parents=True, exist_ok=True)
            if sys.platform.startswith("win"):
                subprocess.Popen(["explorer", str(path)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except OSError as error:
            show_toast(self, f"无法打开目录：{error}", "error")
