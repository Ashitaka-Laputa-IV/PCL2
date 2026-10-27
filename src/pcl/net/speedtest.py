"""下载源测速：比较官方源与镜像源的延迟和下载速度。"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from pcl import USER_AGENT
from pcl.core.logging import get_logger
from pcl.net.mirror import DEFAULT_MIRROR_BASE, mirror_url

log = get_logger(__name__)

MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"


@dataclass
class SourceResult:
    """单个下载源的测速结果。"""

    name: str
    url: str
    ok: bool = False
    latency_ms: float = 0.0
    speed_kbps: float = 0.0
    bytes_read: int = 0
    error: str = ""


def test_sources(
    *,
    mirror_base: str = DEFAULT_MIRROR_BASE,
    timeout: float = 10.0,
    max_bytes: int = 512 * 1024,
) -> list[SourceResult]:
    """依次测试官方源与镜像源。"""
    targets = [("官方源", MANIFEST_URL)]
    mirrored = mirror_url(MANIFEST_URL, mirror_base)
    if mirrored and mirrored != MANIFEST_URL:
        targets.append(("镜像源（BMCLAPI）", mirrored))

    results: list[SourceResult] = []
    for name, url in targets:
        result = SourceResult(name=name, url=url)
        started = time.monotonic()
        try:
            with httpx.Client(
                headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"},
                timeout=httpx.Timeout(timeout),
                follow_redirects=True,
            ) as client:
                with client.stream("GET", url) as response:
                    response.raise_for_status()
                    first = time.monotonic()
                    total = 0
                    for chunk in response.iter_bytes(64 * 1024):
                        total += len(chunk)
                        if total >= max_bytes:
                            break
                    elapsed = max(1e-6, time.monotonic() - first)
                    result.ok = True
                    result.latency_ms = (first - started) * 1000
                    result.bytes_read = total
                    result.speed_kbps = total / 1024 / elapsed
        except (httpx.HTTPError, OSError) as error:
            result.error = str(error)
        results.append(result)
        log.info("%s 测速：ok=%s 延迟=%.0fms 速度=%.0fKB/s", name, result.ok, result.latency_ms, result.speed_kbps)
    return results
