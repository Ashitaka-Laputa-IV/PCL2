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
from pcl.instance.manager import InstanceManager
from pcl.instance.model import Instance
from pcl.launch.args import build_plan
from pcl.launch.auth import Account, AccountStore
from pcl.launch.java import JavaRuntime, scan_java, select_java
from pcl.launch.java_installer import managed_candidates
from pcl.launch.runner import GameRunner
from pcl.net import http
from pcl.version import loaders, meta
from pcl.version.installer import InstallReport, VanillaInstaller
from pcl.version.manifest import VersionEntry
from pcl.version.merge import resolve_version

log = get_logger(__name__)


@dataclass
class AppContext:
    """全局运行上下文（进程内单例）。"""

    paths: AppPaths
    config: Config
    accounts: AccountStore
    instances: InstanceManager
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
            instances=InstanceManager(paths),
        )
        context.java_runtimes = scan_java(context.java_candidates())
        log.info("数据目录：%s，检测到 %d 个 Java", paths.root, len(context.java_runtimes))
        return context

    # ---------- 配置 ----------
    def save_config(self) -> None:
        save_config(self.config, self.paths.config_file)

    def sync_installed_versions(self) -> list:
        """把共享目录中新安装、尚未登记的版本补成实例。"""
        try:
            return self.instances.sync_installed(self.config.advanced.ignored_versions)
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

    # ---------- 实例 ----------
    def current_instance(self) -> Instance | None:
        instances = self.instances.list_instances()
        if not instances:
            return None
        if self.config.selected_instance:
            for instance in instances:
                if instance.id == self.config.selected_instance:
                    return instance
        return instances[0]

    def select_instance(self, instance_id: str) -> Instance:
        instance = self.instances.get(instance_id)
        self.config.selected_instance = instance.id
        self.save_config()
        return instance

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
    def resolve_java(self, version_doc: dict, instance: Instance) -> JavaRuntime:
        required = meta.required_java_major(version_doc)
        preferred = instance.java_path or self.config.launch.java_path
        runtime = select_java(self.java_runtimes or self.refresh_java(), required_major=required, preferred=preferred or None)
        if runtime is None:
            raise JavaNotFoundError(
                f"该版本需要 Java {required} 或更高版本，但没有找到满足要求的运行时。可点击「一键安装 Java {required}」自动安装。"
            )
        return runtime

    def launch_options(self, instance: Instance) -> LaunchOptions:
        options = dataclasses.replace(self.config.launch)
        if instance.memory_mb > 0:
            options.memory_mb = instance.memory_mb
        if instance.window_width > 0:
            options.window_width = instance.window_width
        if instance.window_height > 0:
            options.window_height = instance.window_height
        if instance.jvm_args:
            options.jvm_args = [*options.jvm_args, *instance.jvm_args]
        if instance.game_args:
            options.game_args = [*options.game_args, *instance.game_args]
        return options

    def launch(self, instance: Instance | None = None, account: Account | None = None) -> GameRunner:
        """构建启动计划并启动游戏，返回运行器。"""
        with self._lock:
            if self.runner is not None and self.runner.is_running:
                raise LaunchError("游戏已经在运行中。")

        instance = instance or self.current_instance()
        if instance is None:
            raise LaunchError("还没有任何实例，请先新建或导入。")
        account = account or self.current_account()
        if account is None:
            account = self.accounts.ensure_default()

        version_id = instance.version_id or instance.mc_version
        if not self.paths.version_json(version_id).is_file():
            raise VersionNotFoundError(f"实例 {instance.name} 对应的版本 {version_id} 未安装。")

        loader = str(instance.loader or "vanilla").lower()
        if loader not in ("", "vanilla") and loader not in version_id.lower():
            raise VersionNotFoundError(
                f"实例 {instance.name} 声明的 {loader} 加载器尚未安装（当前版本 {version_id}），"
                "请在下载页重新安装该加载器或重新导入整合包。"
            )

        version_doc = resolve_version(self.paths, version_id)
        java = self.resolve_java(version_doc, instance)
        options = self.launch_options(instance)

        extra_args: list[str] = []
        if instance.server:
            host, _, port = instance.server.rpartition(":")
            if host and port.isdigit():
                extra_args = ["--server", host, "--port", str(int(port))]
            else:
                extra_args = ["--server", instance.server, "--port", "25565"]

        plan = build_plan(
            self.paths,
            version_id,
            account,
            options,
            java,
            game_dir=instance.game_dir(self.paths),
            version_doc=version_doc,
            extra_game_args=extra_args,
        )
        runner = GameRunner(self.paths, plan, version_doc=version_doc)
        runner.start()

        with self._lock:
            self.runner = runner
        self.instances.touch(instance.id)
        return runner

    def stop_game(self) -> None:
        runner = self.runner
        if runner is not None and runner.is_running:
            runner.stop()

    def is_game_running(self) -> bool:
        return self.runner is not None and self.runner.is_running

    # ---------- 崩溃分析 ----------
    def crash_report(self, instance: Instance | None = None):
        """分析最近一次崩溃，返回 :class:`~pcl.launch.crash.CrashReport`。"""
        from pcl.launch.crash import analyze_crash

        instance = instance or self.current_instance()
        if instance is None:
            raise LaunchError("还没有任何实例。")
        state = self.runner.state if self.runner is not None else None
        return analyze_crash(
            instance.game_dir(self.paths),
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
        """删除某个版本的目录（仅文件，不影响实例）。"""
        import shutil

        directory = self.paths.version_dir(version_id)
        if directory.is_dir():
            shutil.rmtree(directory, ignore_errors=True)
