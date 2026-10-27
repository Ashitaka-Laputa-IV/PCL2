"""PCL2 Python 版：使用 PySide6 实现的跨平台 Minecraft 启动器。"""

from __future__ import annotations

__version__ = "0.1.0"

APP_NAME = "PCL2"
LAUNCHER_NAME = "PCL2-Python"
LAUNCHER_VERSION = __version__
USER_AGENT = f"{LAUNCHER_NAME}/{__version__}"
CONFIG_SCHEMA_VERSION = 1
INSTANCE_SCHEMA_VERSION = 1

__all__ = [
    "APP_NAME",
    "CONFIG_SCHEMA_VERSION",
    "INSTANCE_SCHEMA_VERSION",
    "LAUNCHER_NAME",
    "LAUNCHER_VERSION",
    "USER_AGENT",
    "__version__",
]
