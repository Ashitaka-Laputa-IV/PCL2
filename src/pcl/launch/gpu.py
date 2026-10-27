"""显卡选择：强制使用独立显卡 / 集成显卡。

Linux 通过 PRIME 相关环境变量在进程级别指定渲染设备（NVIDIA Optimus 与 Mesa 双显卡都支持）；
Windows 没有等价的环境变量，改为写入系统的「图形性能首选项」
（``HKCU\\Software\\Microsoft\\DirectX\\UserGpuPreferences``），对 javaw.exe 全局生效。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from pcl.core.logging import get_logger

log = get_logger(__name__)

GPU_MODES = ("auto", "dedicated", "integrated")

GPU_LABELS = {
    "auto": "自动（系统默认）",
    "dedicated": "独立显卡（高性能）",
    "integrated": "集成显卡（省电）",
}

#: Windows「图形性能首选项」里每个应用的取值
_WINDOWS_PREFERENCE = {"auto": "GpuPreference=0;", "dedicated": "GpuPreference=2;", "integrated": "GpuPreference=1;"}


def has_nvidia() -> bool:
    """本机是否存在 NVIDIA 显卡驱动。"""
    if Path("/proc/driver/nvidia/version").is_file():
        return True
    try:
        completed = subprocess.run(
            ["nvidia-smi", "-L"],
            capture_output=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def gpu_environment(mode: str, *, nvidia: bool | None = None) -> dict[str, str]:
    """返回强制指定显卡所需的环境变量（仅 Linux / 类 Unix 生效）。"""
    mode = (mode or "auto").lower()
    if mode not in ("dedicated", "integrated"):
        return {}
    if sys.platform.startswith("win") or sys.platform == "darwin":
        return {}

    if mode == "dedicated":
        env = {"DRI_PRIME": "1"}
        detected = nvidia if nvidia is not None else has_nvidia()
        if detected:
            # NVIDIA PRIME render offload：让 GLX/Vulkan 都走独显
            env.update(
                {
                    "__NV_PRIME_RENDER_OFFLOAD": "1",
                    "__GLX_VENDOR_LIBRARY_NAME": "nvidia",
                    "__VK_LAYER_NV_optimus": "NVIDIA_only",
                }
            )
        return env

    # 集成显卡：显式关闭 NVIDIA 卸载并优先使用 0 号设备
    return {"DRI_PRIME": "0", "__NV_PRIME_RENDER_OFFLOAD": "0"}


def apply_windows_preference(mode: str, java_path: Path | None) -> bool:
    """把显卡偏好写进 Windows 注册表，返回是否写入成功。"""
    if os.name != "nt" or java_path is None:
        return False
    mode = (mode or "auto").lower()
    if mode not in _WINDOWS_PREFERENCE or mode == "auto":
        return False
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover - 非 Windows 平台
        return False

    key_path = r"Software\Microsoft\DirectX\UserGpuPreferences"
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            winreg.SetValueEx(key, str(java_path), 0, winreg.REG_SZ, _WINDOWS_PREFERENCE[mode])
    except OSError as error:
        log.warning("写入显卡首选项失败：%s", error)
        return False
    log.info("已把 %s 的显卡首选项设为 %s", java_path, mode)
    return True


def apply_gpu(mode: str, java_path: Path | None = None) -> dict[str, str]:
    """应用显卡设置，返回需要注入游戏进程的环境变量。"""
    mode = (mode or "auto").lower()
    if mode == "auto":
        return {}
    apply_windows_preference(mode, java_path)
    env = gpu_environment(mode)
    if env:
        log.info("已指定显卡：%s（%s）", mode, ", ".join(sorted(env)))
    return env
