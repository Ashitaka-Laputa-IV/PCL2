"""应用服务门面：把各领域模块装配成 UI 可直接调用的操作。"""

from __future__ import annotations

import dataclasses
import threading
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from pcl.core.config import Config, LaunchOptions, load_config, save_config
from pcl.core.errors import JavaNotFoundError, LaunchError, VersionNotFoundError
from pcl.core.logging import get_logger
from pcl.core.paths import AppPaths, default_data_root
from pcl.launch.args import build_plan
from pcl.launch.auth import Account, AccountStore
from pcl.launch.gpu import apply_gpu
from pcl.launch.java import JavaRuntime, scan_java, select_java
from pcl.launch.java_installer import managed_candidates
from pcl.launch.runner import GameRunner
from pcl.net import http
from pcl.version import loaders, meta
from pcl.version.installer import InstallReport, VanillaInstaller
from pcl.version.local import LocalVersion
from pcl.version.manifest import VersionEntry
from pcl.version.merge import resolve_version
from pcl.version.store import VersionStore

log = get_logger(__name__)


@dataclass
class AppContext:
    """全局运行上下文（进程内单例）。"""

    paths: AppPaths
    config: Config
    accounts: AccountStore
    versions: VersionStore
    java_runtimes: list[JavaRuntime] = field(default_factory=list)
    runner: GameRunner | None = None
    _client: httpx.Client | None = None
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    # ---------- 装配 ----------
    @classmethod
    def bootstrap(cls) -> AppContext:
        config = load_config(AppPaths(default_data_root()).config_file)
        root = default_data_root()
        game_root = Path(config.advanced.game_root).expanduser() if config.advanced.game_root else None
        paths = AppPaths(root, game_root).ensure()

        accounts = AccountStore(paths.accounts_file)
        accounts.load()
        accounts.selected = config.selected_account or accounts.selected
        accounts.ensure_default()

        context = cls(
            paths=paths,
            config=config,
            accounts=accounts,
            versions=VersionStore(paths),
        )
        context._migrate_legacy_versions()
        context.java_runtimes = scan_java(context.java_candidates())
        log.info("数据目录：%s，检测到 %d 个 Java", paths.root, len(context.java_runtimes))
        return context

    def _migrate_legacy_versions(self) -> None:
        """把旧版实例条目迁移成版本条目，并接续上次选择的版本。"""
        try:
            self.versions.migrate_legacy()
            self.versions.normalize_layout()
            self.versions.upgrade_isolation()
        except Exception as error:  # noqa: BLE001 - 迁移失败不应阻塞启动
            log.warning("迁移旧实例数据失败：%s", error)
        if self.config.selected_version:
            return
        legacy = self.config.selected_instance
        if not legacy:
            return
        for version in self.versions.list_versions():
            if legacy in (version.id, version.name):
                self.config.selected_version = version.id
                self.save_config()
                return

    # ---------- 配置 ----------
    def save_config(self) -> None:
        save_config(self.config, self.paths.config_file)

    def list_versions(self) -> list[LocalVersion]:
        """扫描已安装版本（新装的版本会自动登记）。"""
        try:
            return self.versions.list_versions()
        except Exception as error:  # noqa: BLE001 - 扫描失败不应阻塞界面
            log.warning("扫描已安装版本失败：%s", error)
            return []

    def client(self) -> httpx.Client:
        with self._lock:
            if self._client is None:
                self._client = http.create_client(concurrency=self.config.download.concurrency)
            return self._client

    def close(self) -> None:
        self.stop_game()
        with self._lock:
            if self._client is not None:
                self._client.close()
                self._client = None

    # ---------- 账户 ----------
    def select_account(self, name: str) -> Account:
        account = self.accounts.find(name)
        if account is None:
            account = self.accounts.add(name)
        self.accounts.selected = account.name
        self.accounts.save()
        self.config.selected_account = account.name
        self.save_config()
        return account

    def current_account(self) -> Account | None:
        return self.accounts.current()

    def java_candidates(self) -> list[str]:
        candidates: list[str] = []
        if self.config.launch.java_path:
            candidates.append(self.config.launch.java_path)
        candidates.extend(str(path) for path in managed_candidates(self.paths))
        return candidates

    def refresh_java(self) -> list[JavaRuntime]:
        self.java_runtimes = scan_java(self.java_candidates())
        return self.java_runtimes

    # ---------- 版本 ----------
    def current_version(self) -> LocalVersion | None:
        versions = self.list_versions()
        if not versions:
            return None
        if self.config.selected_version:
            for version in versions:
                if version.id == self.config.selected_version:
                    return version
        return versions[0]

    def select_version(self, version_id: str) -> LocalVersion:
        version = self.versions.get(version_id)
        self.config.selected_version = version.id
        self.save_config()
        return version

    def adopt_version(self, version_id: str) -> LocalVersion:
        """下载安装完成后调用：登记该版本，重名时自动派生 ``-1`` / ``-2`` 副本。"""
        return self.versions.adopt(version_id)

    # ---------- 安装 ----------
    def install_vanilla(self, version_id: str, entry: VersionEntry | None = None, *, force: bool = False) -> InstallReport:
        installer = VanillaInstaller(self.paths, self.config.download)
        return installer.install(version_id, entry, force=force)

    def install_loader(
        self,
        kind: str,
        mc_version: str,
        loader_version: str,
        *,
        java_path: str | None = None,
    ) -> str:
        return loaders.install_loader(
            self.paths,
            self.config.download,
            kind=kind,
            mc_version=mc_version,
            loader_version=loader_version,
            java_path=java_path or self.config.launch.java_path or None,
        )

    # ---------- 启动 ----------
    def resolve_java(self, version_doc: dict, version: LocalVersion) -> JavaRuntime:
        required = meta.required_java_major(version_doc)
        preferred = version.java_path or self.config.launch.java_path
        runtime = select_java(self.java_runtimes or self.refresh_java(), required_major=required, preferred=preferred or None)
        if runtime is None:
            raise JavaNotFoundError(
                f"该版本需要 Java {required} 或更高版本，但没有找到满足要求的运行时。可点击「一键安装 Java {required}」自动安装。"
            )
        return runtime

    def launch_options(self, version: LocalVersion) -> LaunchOptions:
        options = dataclasses.replace(self.config.launch)
        if version.memory_mb > 0:
            options.memory_mb = version.memory_mb
        if version.window_width > 0:
            options.window_width = version.window_width
        if version.window_height > 0:
            options.window_height = version.window_height
        if version.jvm_args:
            options.jvm_args = [*options.jvm_args, *version.jvm_args]
        if version.game_args:
            options.game_args = [*options.game_args, *version.game_args]
        return options

    def launch(self, version: LocalVersion | None = None, account: Account | None = None) -> GameRunner:
        """构建启动计划并启动游戏，返回运行器。"""
        with self._lock:
            if self.runner is not None and self.runner.is_running:
                raise LaunchError("游戏已经在运行中。")

        version = version or self.current_version()
        if version is None:
            raise LaunchError("还没有任何版本，请先去下载页安装。")
        account = account or self.current_account()
        if account is None:
            account = self.accounts.ensure_default()

        version_id = version.id
        if not self.paths.version_json(version_id).is_file():
            raise VersionNotFoundError(f"版本 {version.name} 的文件已缺失（{version_id}），请重新安装。")

        loader = str(version.loader or "vanilla").lower()
        if loader not in ("", "vanilla") and loader not in version_id.lower():
            raise VersionNotFoundError(
                f"版本 {version.name} 声明的 {loader} 加载器尚未安装（当前版本 {version_id}），"
                "请在下载页重新安装该加载器或重新导入整合包。"
            )

        version_doc = resolve_version(self.paths, version_id)
        java = self.resolve_java(version_doc, version)
        options = self.launch_options(version)

        extra_args: list[str] = []
        if version.server:
            host, _, port = version.server.rpartition(":")
            if host and port.isdigit():
                extra_args = ["--server", host, "--port", str(int(port))]
            else:
                extra_args = ["--server", version.server, "--port", "25565"]

        plan = build_plan(
            self.paths,
            version_id,
            account,
            options,
            java,
            game_dir=version.game_dir(self.paths),
            version_doc=version_doc,
            extra_game_args=extra_args,
        )
        plan.env.update(apply_gpu(self.config.launch.gpu, Path(str(java.path))))
        runner = GameRunner(self.paths, plan, version_doc=version_doc)
        runner.start()

        with self._lock:
            self.runner = runner
        self.versions.touch(version.id)
        return runner

    def stop_game(self) -> None:
        runner = self.runner
        if runner is not None and runner.is_running:
            runner.stop()

    def is_game_running(self) -> bool:
        return self.runner is not None and self.runner.is_running

    # ---------- 崩溃分析 ----------
    def crash_report(self, version: LocalVersion | None = None):
        """分析最近一次崩溃，返回 :class:`~pcl.launch.crash.CrashReport`。"""
        from pcl.launch.crash import analyze_crash

        version = version or self.current_version()
        if version is None:
            raise LaunchError("还没有任何版本。")
        state = self.runner.state if self.runner is not None else None
        return analyze_crash(
            version.game_dir(self.paths),
            logs_dir=self.paths.logs,
            exit_code=state.exit_code if state is not None else None,
        )

    # ---------- 其他 ----------
    def version_installed(self, version_id: str) -> bool:
        return self.paths.version_json(version_id).is_file()

    def installed_versions(self) -> list[str]:
        from pcl.version.merge import installed_versions

        return installed_versions(self.paths)

    def delete_version_files(self, version_id: str) -> None:
        """删除某个版本目录及其全部文件。"""
        import shutil

        directory = self.paths.version_dir(version_id)
        if directory.is_dir():
            shutil.rmtree(directory, ignore_errors=True)
