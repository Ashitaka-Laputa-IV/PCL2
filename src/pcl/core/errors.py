"""领域异常与中文可读消息映射。"""

from __future__ import annotations


class PclError(Exception):
    """所有启动器异常的基类。"""

    default_message = "出现了未知错误。"

    def __init__(self, message: str | None = None, *, detail: str | None = None) -> None:
        self.message = message or self.default_message
        self.detail = detail
        super().__init__(self.message)

    def __str__(self) -> str:  # pragma: no cover - 简单转发
        if self.detail:
            return f"{self.message}（{self.detail}）"
        return self.message


class ConfigError(PclError):
    default_message = "配置文件读取或写入失败。"


class NetworkError(PclError):
    default_message = "网络请求失败，请检查网络连接。"


class DownloadError(PclError):
    default_message = "文件下载失败。"


class ChecksumError(PclError):
    default_message = "文件校验失败，已阻止使用该缓存。"

    def __init__(self, message: str | None = None, *, path: str | None = None, **kwargs: object) -> None:
        super().__init__(message or self.default_message, **kwargs)  # type: ignore[arg-type]
        self.path = path


class VersionError(PclError):
    default_message = "版本信息处理失败。"


class VersionNotFoundError(VersionError):
    default_message = "找不到该版本，请先下载或检查版本号。"


class LoaderError(PclError):
    default_message = "模组加载器安装失败。"


class JavaNotFoundError(PclError):
    default_message = "没有找到可用的 Java，请在设置中手动指定。"


class LaunchError(PclError):
    default_message = "游戏启动失败。"


class ContentError(PclError):
    default_message = "资源文件处理失败。"


class ModpackError(PclError):
    default_message = "整合包解析失败。"


class CancelledError(PclError):
    default_message = "操作已被取消。"


def friendly_message(error: BaseException) -> str:
    """把异常转换为可直接展示给用户的中文说明。"""
    if isinstance(error, PclError):
        return str(error)
    return f"{type(error).__name__}: {error}"
