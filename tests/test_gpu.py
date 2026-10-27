"""显卡开关：PRIME 环境变量与配置收敛。"""

from __future__ import annotations

import sys

import pytest

from pcl.core.config import Config
from pcl.launch.gpu import GPU_LABELS, GPU_MODES, gpu_environment


def test_auto_mode_sets_nothing() -> None:
    assert gpu_environment("auto") == {}
    assert gpu_environment("") == {}


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="PRIME 环境变量仅在 Linux 生效")
def test_dedicated_mode_uses_prime() -> None:
    env = gpu_environment("dedicated", nvidia=True)
    assert env["DRI_PRIME"] == "1"
    assert env["__NV_PRIME_RENDER_OFFLOAD"] == "1"
    assert env["__GLX_VENDOR_LIBRARY_NAME"] == "nvidia"

    # 非 NVIDIA（例如 AMD / Intel 双显卡）只设置 DRI_PRIME
    amd = gpu_environment("dedicated", nvidia=False)
    assert amd == {"DRI_PRIME": "1"}


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="PRIME 环境变量仅在 Linux 生效")
def test_integrated_mode_disables_offload() -> None:
    env = gpu_environment("integrated", nvidia=True)
    assert env["DRI_PRIME"] == "0"
    assert env["__NV_PRIME_RENDER_OFFLOAD"] == "0"


def test_labels_cover_modes() -> None:
    assert set(GPU_LABELS) == set(GPU_MODES)


def test_config_normalizes_gpu_mode() -> None:
    config = Config()
    config.launch.gpu = "something-else"
    assert config.normalized().launch.gpu == "auto"

    config.launch.gpu = "dedicated"
    assert config.normalized().launch.gpu == "dedicated"
