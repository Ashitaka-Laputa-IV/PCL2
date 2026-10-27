"""社区资源浏览（Modrinth）：搜索、版本选择与安装到游戏版本。"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pcl.core.config import DownloadOptions
from pcl.core.errors import friendly_message
from pcl.resource.installer import install_version
from pcl.resource.modrinth import ModrinthClient, Project, ProjectVersion
from pcl.services import AppContext
from pcl.ui.remote_image import ImageLoader
from pcl.ui.widgets.animated_button import AnimatedButton
from pcl.ui.widgets.card import Card
from pcl.ui.widgets.toast import show_toast

PROJECT_CHOICES = (
    ("mod", "Mod"),
    ("resourcepack", "资源包"),
    ("shader", "光影"),
    ("modpack", "整合包"),
)
LOADER_CHOICES = (
    ("", "全部加载器"),
    ("fabric", "Fabric"),
    ("quilt", "Quilt"),
    ("forge", "Forge"),
    ("neoforge", "NeoForge"),
)
SORT_CHOICES = (
    ("relevance", "相关度"),
    ("downloads", "下载量"),
    ("follows", "收藏数"),
    ("newest", "最新发布"),
    ("updated", "最近更新"),
)
PAGE_SIZE = 20


def _placeholder_icon(text: str, size: int = 40) -> QPixmap:
    dpr = 2
    pixmap = QPixmap(size * dpr, size * dpr)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    bounds = QRectF(0, 0, size * dpr, size * dpr)
    path = QPainterPath()
    path.addRoundedRect(bounds.adjusted(0, 0, -1, -1), 9 * dpr, 9 * dpr)
    painter.fillPath(path, QColor("#E8EEF8"))
    painter.setPen(QColor("#5F6B7A"))
    font = QFont()
    font.setWeight(QFont.Weight.DemiBold)
    font.setPixelSize(int(size * dpr * 0.5))
    painter.setFont(font)
    painter.drawText(bounds, Qt.AlignmentFlag.AlignCenter, (text or "?")[:1].upper())
    painter.end()
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


def _search_task(query: str, project_type: str, game_version: str, loader: str, offset: int, sort: str):
    client = ModrinthClient()
    try:
        return client.search(
            query,
            project_type=project_type,
            game_version=game_version,
            loader=loader,
            offset=offset,
            limit=PAGE_SIZE,
            sort=sort,
        )
    finally:
        client.close()


def _versions_task(project_id: str, game_version: str, loader: str):
    client = ModrinthClient()
    try:
        return client.versions(project_id, game_version=game_version, loader=loader)
    finally:
        client.close()


class CommunityBrowser(QWidget):
    """可嵌入下载页的社区资源浏览器。"""

    def __init__(self, context: AppContext, tasks, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.context = context
        self.tasks = tasks
        self.images = ImageLoader(self)
        self._projects: list[Project] = []
        self._versions: list[ProjectVersion] = []
        self._current: Project | None = None
        self._offset = 0
        self._total = 0
        self._busy = False
        self._build()

    # ---------- 构建 ----------
    def _build(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(14)

        left = QVBoxLayout()
        left.setSpacing(8)

        filters = QHBoxLayout()
        filters.setSpacing(8)
        self._search = QLineEdit(self)
        self._search.setPlaceholderText("搜索 Mod / 资源包 / 光影 / 整合包…")
        self._search.returnPressed.connect(lambda: self._search_community(reset=True))
        self._type = QComboBox(self)
        for key, label in PROJECT_CHOICES:
            self._type.addItem(label, key)
        self._type.currentIndexChanged.connect(lambda _index: self._search_community(reset=True))
        search_button = AnimatedButton("搜索", self, variant="primary", height=32)
        search_button.clicked.connect(lambda: self._search_community(reset=True))
        filters.addWidget(self._search, 1)
        filters.addWidget(self._type)
        filters.addWidget(search_button)
        left.addLayout(filters)

        filters2 = QHBoxLayout()
        filters2.setSpacing(8)
        self._game_version = QComboBox(self)
        self._game_version.setEditable(True)
        self._game_version.addItem("全部版本", "")
        self._loader = QComboBox(self)
        for key, label in LOADER_CHOICES:
            self._loader.addItem(label, key)
        self._sort = QComboBox(self)
        for key, label in SORT_CHOICES:
            self._sort.addItem(label, key)
        self._sort.currentIndexChanged.connect(lambda _index: self._search_community(reset=True))
        filters2.addWidget(self._game_version, 1)
        filters2.addWidget(self._loader)
        filters2.addWidget(self._sort)
        left.addLayout(filters2)

        self._results = QListWidget(self)
        self._results.setIconSize(QSize(40, 40))
        self._results.setSpacing(2)
        self._results.currentItemChanged.connect(self._on_project_selected)
        left.addWidget(self._results, 1)

        footer = QHBoxLayout()
        self._count_label = QLabel("输入关键词开始搜索", self)
        self._count_label.setObjectName("Muted")
        self._more_button = AnimatedButton("加载更多", self, variant="secondary", height=30)
        self._more_button.clicked.connect(lambda: self._search_community(reset=False))
        self._more_button.setEnabled(False)
        footer.addWidget(self._count_label, 1)
        footer.addWidget(self._more_button)
        left.addLayout(footer)
        layout.addLayout(left, 3)

        detail = Card(self, padding=20, spacing=10)
        detail.setMinimumWidth(280)
        top = QHBoxLayout()
        top.setSpacing(10)
        self._icon = QLabel(detail)
        self._icon.setFixedSize(56, 56)
        self._icon.setPixmap(_placeholder_icon("?", 56))
        top.addWidget(self._icon)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self._title = QLabel("选择一个项目", detail)
        self._title.setStyleSheet("font-size: 17px; font-weight: 700;")
        self._title.setWordWrap(True)
        self._meta = QLabel("", detail)
        self._meta.setObjectName("Muted")
        self._meta.setWordWrap(True)
        titles.addWidget(self._title)
        titles.addWidget(self._meta)
        top.addLayout(titles, 1)
        detail.add_layout(top)

        self._description = QLabel("", detail)
        self._description.setObjectName("PageSubtitle")
        self._description.setWordWrap(True)
        detail.add(self._description)

        version_label = QLabel("可用版本", detail)
        version_label.setObjectName("Muted")
        detail.add(version_label)
        self._version_combo = QComboBox(detail)
        self._version_combo.setEnabled(False)
        detail.add(self._version_combo)

        target_label = QLabel("安装到版本", detail)
        target_label.setObjectName("Muted")
        detail.add(target_label)
        self._target_combo = QComboBox(detail)
        detail.add(self._target_combo)

        self._install_button = AnimatedButton("安装到选中版本", detail, variant="primary", height=38)
        self._install_button.setEnabled(False)
        self._install_button.clicked.connect(self._install)
        detail.add(self._install_button)

        self._status = QLabel("", detail)
        self._status.setObjectName("Muted")
        self._status.setWordWrap(True)
        detail.add(self._status)
        detail.addStretch(1)
        layout.addWidget(detail, 2)

    # ---------- 数据 ----------
    def refresh(self) -> None:
        self._reload_targets()
        self._reload_game_versions()

    def _reload_targets(self) -> None:
        current = self._target_combo.currentData()
        self._target_combo.blockSignals(True)
        self._target_combo.clear()
        for target in self.context.list_versions():
            self._target_combo.addItem(target.name, target.id)
        if current:
            index = self._target_combo.findData(current)
            if index >= 0:
                self._target_combo.setCurrentIndex(index)
        self._target_combo.blockSignals(False)
        if self._target_combo.count() == 0:
            self._status.setText("还没有安装任何版本，请先在「下载」页安装游戏版本。")

    def _reload_game_versions(self) -> None:
        if self._game_version.count() > 1:
            return
        entries = self.context.installed_versions()
        for version_id in sorted(entries, reverse=True)[:30]:
            self._game_version.addItem(version_id, version_id)

    def _current_project_type(self) -> str:
        return str(self._type.currentData() or "mod")

    def _current_game_version(self) -> str:
        data = self._game_version.currentData()
        if data:
            return str(data)
        text = self._game_version.currentText().strip()
        return "" if text in ("", "全部版本") else text

    def _current_loader(self) -> str:
        return str(self._loader.currentData() or "")

    # ---------- 搜索 ----------
    def _search_community(self, reset: bool) -> None:
        if self._busy:
            return
        if reset:
            self._offset = 0
            self._projects.clear()
            self._results.clear()
            self._total = 0
        self._busy = True
        self._count_label.setText("正在搜索…")
        self._more_button.setEnabled(False)

        project_type = self._current_project_type()
        loader = self._current_loader() if project_type in ("mod", "modpack") else ""
        query = self._search.text().strip()

        def done(result) -> None:
            projects, total = result
            self._busy = False
            self._total = total
            self._offset += len(projects)
            for project in projects:
                self._projects.append(project)
                self._add_project_item(project)
            self._count_label.setText(
                f"共 {total} 个结果，已加载 {len(self._projects)} 个" if total else "没有找到匹配的项目"
            )
            self._more_button.setEnabled(self._offset < total)
            if self._results.count():
                self._results.blockSignals(True)
                if self._results.currentRow() < 0:
                    self._results.setCurrentRow(0)
                self._results.blockSignals(False)
                self._on_project_selected(self._results.currentItem())

        def failed(message: str) -> None:
            self._busy = False
            self._count_label.setText(f"搜索失败：{message}")
            self._more_button.setEnabled(False)

        self.tasks.submit(
            _search_task,
            query,
            project_type,
            self._current_game_version(),
            loader,
            self._offset,
            str(self._sort.currentData() or "relevance"),
            on_done=done,
            on_error=failed,
        )

    def _add_project_item(self, project: Project) -> None:
        item = QListWidgetItem()
        item.setText(f"{project.title}\n{project.description[:90] or '—'}")
        item.setData(Qt.ItemDataRole.UserRole, project)
        item.setSizeHint(QSize(0, 58))
        item.setIcon(QIcon(_placeholder_icon(project.title, 40)))
        self._results.addItem(item)

        def apply(pixmap: QPixmap, target=item) -> None:
            try:
                target.setIcon(QIcon(pixmap.scaled(40, 40, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)))
            except RuntimeError:
                return

        if project.icon_url:
            self.images.get(project.icon_url, apply)

    def _on_project_selected(self, current: QListWidgetItem | None, _previous=None) -> None:
        if current is None:
            return
        project = current.data(Qt.ItemDataRole.UserRole)
        if not isinstance(project, Project):
            return
        self._current = project
        self._title.setText(project.title)
        meta = [f"{project.downloads_label} 次下载"]
        if project.author:
            meta.append(f"作者 {project.author}")
        if project.categories:
            meta.append("、".join(project.categories[:3]))
        self._meta.setText(" · ".join(meta))
        self._description.setText(project.description or "暂无简介")
        self._icon.setPixmap(_placeholder_icon(project.title, 56))
        if project.icon_url:
            self.images.get(project.icon_url, self._set_detail_icon)
        self._load_versions(project)

    def _set_detail_icon(self, pixmap: QPixmap) -> None:
        self._icon.setPixmap(
            pixmap.scaled(56, 56, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        )

    def _load_versions(self, project: Project) -> None:
        self._version_combo.clear()
        self._version_combo.setEnabled(False)
        self._install_button.setEnabled(False)
        self._status.setText("正在获取可用版本…")
        project_type = project.project_type or self._current_project_type()
        loader = self._current_loader() if project_type in ("mod", "modpack") else ""
        game_version = self._current_game_version()

        def done(versions) -> None:
            self._versions = list(versions)
            self._version_combo.clear()
            for version in self._versions[:40]:
                self._version_combo.addItem(version.label, version.id)
            available = bool(self._versions)
            is_modpack = project_type == "modpack"
            self._version_combo.setEnabled(available)
            self._target_combo.setEnabled(not is_modpack)
            self._install_button.setText("下载并创建版本" if is_modpack else "安装到选中版本")
            self._install_button.setEnabled(available and (is_modpack or self._target_combo.count() > 0))
            if not available:
                self._status.setText("没有匹配当前筛选条件的版本，试试放宽版本或加载器。")
            elif is_modpack:
                self._status.setText(f"共 {len(self._versions)} 个版本可选，整合包会下载并自动创建版本。")
            else:
                self._status.setText(f"共 {len(self._versions)} 个版本可选。")

        def failed(message: str) -> None:
            self._status.setText(f"获取版本失败：{message}")

        self.tasks.submit(
            _versions_task,
            project.id,
            game_version,
            loader,
            on_done=done,
            on_error=failed,
        )

    # ---------- 安装 ----------
    def _install_modpack(self, project, version) -> None:
        file = version.primary_file()
        if file is None or not file.url:
            show_toast(self, "该整合包版本没有可下载文件", "error")
            return
        self._install_button.set_loading(True, "下载中…")
        self._install_button.setEnabled(False)
        self._status.setText("正在下载整合包…")
        filename = file.filename or f"{project.title}.mrpack"
        destination = self.context.paths.cache / "modpacks" / filename

        def work():
            from pcl.modpack import import_modpack
            from pcl.net.http import download_file

            download_file(
                self.context.client(),
                file.url,
                destination,
                sha1=file.sha1 or None,
                size=file.size or None,
                segments=6,
            )
            return import_modpack(
                destination,
                self.context.paths,
                self.context.config.download,
                store=self.context.versions,
                api_key=self.context.config.advanced.curseforge_api_key,
                java_path=self.context.config.launch.java_path or None,
                install_game=True,
                name_override=project.title,
            )

        def done(result) -> None:
            self._install_button.set_loading(False, "下载并创建版本")
            self._install_button.setEnabled(True)
            self.context.select_version(result.version.id)
            self.refresh()
            message = f"已创建版本 {result.version.name}"
            if result.downloaded:
                message += f"，下载 {result.downloaded} 个文件"
            if result.failed:
                message += f"，{len(result.failed)} 个失败"
            self._status.setText(message)
            show_toast(self, message, "success" if not result.failed else "warning")

        def failed(message: str) -> None:
            self._install_button.set_loading(False, "下载并创建版本")
            self._install_button.setEnabled(True)
            self._status.setText(f"导入失败：{message}")
            show_toast(self, message, "error")

        self.tasks.submit(work, on_done=done, on_error=failed)

    def _install(self) -> None:
        project = self._current
        if project is None:
            show_toast(self, "请先选择一个项目", "warning")
            return
        version_id = self._version_combo.currentData()
        version = next((item for item in self._versions if item.id == version_id), None)
        if version is None:
            show_toast(self, "请选择要安装的版本", "warning")
            return
        project_type = project.project_type or self._current_project_type()
        if project_type == "modpack":
            self._install_modpack(project, version)
            return
        target_id = self._target_combo.currentData()
        if not target_id:
            show_toast(self, "请先在「下载」页安装一个游戏版本", "warning")
            return
        try:
            target = self.context.versions.get(str(target_id))
        except Exception as error:
            show_toast(self, friendly_message(error), "error")
            return

        self._install_button.set_loading(True, "安装中")
        self._install_button.setEnabled(False)
        self._status.setText("正在下载…")
        game_dir = target.game_dir(self.context.paths)
        game_dir.mkdir(parents=True, exist_ok=True)
        options: DownloadOptions = self.context.config.download

        loader = target.loader if target.loader != "vanilla" else ""

        def done(result) -> None:
            self._install_button.set_loading(False, "安装到选中版本")
            self._install_button.setEnabled(True)
            if result.failures:
                self._status.setText("部分文件失败：" + "；".join(result.failures[:2]))
                show_toast(self, "安装未完全成功，请查看状态", "warning")
            else:
                self._status.setText(f"已安装 {len(result.installed)} 个文件" + (
                    f"，并安装前置 {len(result.dependencies)} 个" if result.dependencies else ""
                ))
                show_toast(self, f"{project.title} 安装完成", "success")

        def failed(message: str) -> None:
            self._install_button.set_loading(False, "安装到选中版本")
            self._install_button.setEnabled(True)
            self._status.setText(f"安装失败：{message}")
            show_toast(self, message, "error")

        self.tasks.submit(
            install_version,
            version,
            project.project_type or self._current_project_type(),
            game_dir,
            options=options,
            game_version=target.mc_version,
            loader=loader,
            on_done=done,
            on_error=failed,
        )
