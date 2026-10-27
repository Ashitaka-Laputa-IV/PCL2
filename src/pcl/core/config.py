"""全局配置：数据类定义、JSON 原子读写与向前兼容的默认值合并。"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import MISSING, asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Union, get_args, get_origin, get_type_hints

from pcl import CONFIG_SCHEMA_VERSION
from pcl.core.errors import ConfigError
from pcl.core.logging import get_logger

log = get_logger(__name__)

DEFAULT_MEMORY_MB = 2048
MIN_MEMORY_MB = 512
MAX_MEMORY_MB = 65536

#: 可选主题色（界面设置中以色点展示）
ACCENT_COLORS = (
    "#2E7CF6",
    "#7C5CFF",
    "#00B4A6",
    "#22C55E",
    "#F59E0B",
    "#EF4444",
    "#EC4899",
    "#0EA5E9",
)


@dataclass
class LaunchOptions:
    """启动相关设置。"""

    java_path: str = ""  # 为空表示自动检测
    memory_mb: int = DEFAULT_MEMORY_MB
    window_width: int = 854
    window_height: int = 480
    fullscreen: bool = False
    auto_java: bool = True
    jvm_args: list[str] = field(default_factory=list)
    game_args: list[str] = field(default_factory=list)
    show_log_window: bool = True


@dataclass
class UiOptions:
    """界面设置。"""

    theme: str = "light"  # light / dark
    accent: str = "#2E7CF6"
    animations: bool = True
    corner_radius: int = 8
    font_scale: float = 1.0
    sidebar_collapsed: bool = False


@dataclass
class DownloadOptions:
    """下载设置。"""

    source: str = "auto"  # auto / official / mirror
    concurrency: int = 16
    speed_limit_kbps: int = 0  # 0 表示不限速
    mirror_base: str = "https://bmclapi2.bangbang93.com"
    retry: int = 3


@dataclass
class AdvancedOptions:
    """高级设置。"""

    game_root: str = ""  # 为空表示使用数据根目录下的 .minecraft
    max_log_lines: int = 5000
    curseforge_api_key: str = ""
    keep_old_logs: bool = True


@dataclass
class Config:
    """启动器配置根对象。"""

    schema_version: int = CONFIG_SCHEMA_VERSION
    selected_instance: str = ""
    selected_account: str = ""
    close_to_tray: bool = False
    launch: LaunchOptions = field(default_factory=LaunchOptions)
    ui: UiOptions = field(default_factory=UiOptions)
    download: DownloadOptions = field(default_factory=DownloadOptions)
    advanced: AdvancedOptions = field(default_factory=AdvancedOptions)

    # ---------- 序列化 ----------
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def normalized(self) -> Config:
        """把越界值收敛到合法区间，避免脏配置导致启动失败。"""
        launch = self.launch
        launch.memory_mb = max(MIN_MEMORY_MB, min(MAX_MEMORY_MB, int(launch.memory_mb)))
        launch.window_width = max(320, min(15360, int(launch.window_width)))
        launch.window_height = max(240, min(8640, int(launch.window_height)))

        download = self.download
        download.concurrency = max(1, min(64, int(download.concurrency)))
        download.speed_limit_kbps = max(0, int(download.speed_limit_kbps))
        download.retry = max(0, min(10, int(download.retry)))
        if download.source not in ("auto", "official", "mirror"):
            download.source = "auto"

        ui = self.ui
        if ui.theme not in ("light", "dark"):
            ui.theme = "light"
        if ui.accent not in ACCENT_COLORS:
            ui.accent = ACCENT_COLORS[0]
        ui.corner_radius = max(0, min(24, int(ui.corner_radius)))
        ui.font_scale = max(0.8, min(1.5, float(ui.font_scale)))

        self.advanced.max_log_lines = max(500, min(50000, int(self.advanced.max_log_lines)))
        return self


def _coerce(annotation: Any, value: Any, default: Any) -> Any:
    """按类型注解做宽松转换，失败时回退默认值。"""
    if annotation is Any or annotation is None:
        return value

    if is_dataclass(annotation) and isinstance(value, dict):
        return _from_mapping(annotation, value)

    origin = get_origin(annotation)
    if origin is Union:  # Optional[X]
        inner = [arg for arg in get_args(annotation) if arg is not type(None)]
        if value is None:
            return None
        return _coerce(inner[0], value, default) if inner else value

    try:
        if annotation is bool:
            if isinstance(value, bool):
                return value
            return str(value).strip().lower() in ("1", "true", "yes", "on")
        if annotation is int:
            return int(value)
        if annotation is float:
            return float(value)
        if annotation is str:
            return str(value)
        if origin in (list, tuple):
            if not isinstance(value, (list, tuple)):
                return default
            (item_type,) = get_args(annotation) or (Any,)
            return [_coerce(item_type, item, item) for item in value]
        if origin is dict:
            return dict(value) if isinstance(value, dict) else default
    except (TypeError, ValueError):
        return default

    return value if isinstance(value, annotation) else default


def _from_mapping(cls: Any, data: dict[str, Any]) -> Any:
    """从 dict 构造数据类，忽略未知字段并保留缺省值。"""
    hints = get_type_hints(cls)
    kwargs: dict[str, Any] = {}
    for f in fields(cls):
        if f.name not in data:
            continue
        if f.default is not MISSING:
            default = f.default
        elif f.default_factory is not MISSING:
            default = f.default_factory()
        else:
            default = None
        kwargs[f.name] = _coerce(hints.get(f.name, Any), data[f.name], default)
    return cls(**kwargs)


def config_from_dict(data: dict[str, Any]) -> Config:
    """把外部 JSON 数据转换为 :class:`Config`。"""
    if not isinstance(data, dict):
        raise ConfigError("配置文件格式不正确，应为 JSON 对象。")
    config = _from_mapping(Config, data)
    if not isinstance(config, Config):
        config = Config()
    return config.normalized()


def load_config(path: Path) -> Config:
    """读取配置；文件不存在或损坏时返回默认配置。"""
    if not path.exists():
        return Config()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return config_from_dict(raw)
    except (OSError, ValueError) as error:
        log.warning("读取配置失败，使用默认配置：%s", error)
        return Config()


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".config-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def save_config(config: Config, path: Path) -> None:
    """原子写入配置文件，避免中途崩溃损坏。"""
    config.schema_version = CONFIG_SCHEMA_VERSION
    try:
        _atomic_write(path, json.dumps(config.normalized().to_dict(), ensure_ascii=False, indent=2))
    except OSError as error:
        raise ConfigError("配置保存失败。", detail=str(error)) from error
