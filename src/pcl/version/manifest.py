"""版本清单：拉取、缓存、筛选与查询。"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import httpx

from pcl.core.errors import NetworkError
from pcl.core.logging import get_logger
from pcl.net import http
from pcl.net.mirror import DEFAULT_MIRROR_BASE, candidates

log = get_logger(__name__)

MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
CACHE_TTL = 15 * 60  # 秒

#: 下载页使用的版本类型分组
VERSION_KINDS = {
    "release": ("release",),
    "snapshot": ("snapshot", "pending"),
    "old": ("old_beta", "old_alpha"),
    "all": ("release", "snapshot", "pending", "old_beta", "old_alpha"),
}


@dataclass(frozen=True)
class VersionEntry:
    """版本清单中的一个版本条目。"""

    id: str
    type: str
    url: str
    sha1: str = ""
    release_time: str = ""
    compliance_level: int = 0

    @property
    def release_date(self) -> str:
        return self.release_time.split("T")[0] if self.release_time else ""


@dataclass
class VersionManifest:
    """完整版本清单。"""

    latest_release: str = ""
    latest_snapshot: str = ""
    versions: list[VersionEntry] | None = None

    def __post_init__(self) -> None:
        if self.versions is None:
            self.versions = []

    def find(self, version_id: str) -> VersionEntry | None:
        for entry in self.versions or []:
            if entry.id == version_id:
                return entry
        return None

    def filtered(self, kind: str = "release") -> list[VersionEntry]:
        allowed = VERSION_KINDS.get(kind, VERSION_KINDS["release"])
        return [entry for entry in self.versions or [] if entry.type in allowed]

    def ids(self) -> list[str]:
        return [entry.id for entry in self.versions or []]


def parse_manifest(raw: Any) -> VersionManifest:
    """解析官方清单 JSON。"""
    if not isinstance(raw, dict):
        raise NetworkError("版本清单格式不正确。")
    latest = raw.get("latest") or {}
    versions: list[VersionEntry] = []
    for item in raw.get("versions") or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        versions.append(
            VersionEntry(
                id=str(item["id"]),
                type=str(item.get("type", "release")),
                url=str(item.get("url", "")),
                sha1=str(item.get("sha1", "")),
                release_time=str(item.get("releaseTime", "")),
                compliance_level=int(item.get("complianceLevel", 0) or 0),
            )
        )
    return VersionManifest(
        latest_release=str(latest.get("release", "")),
        latest_snapshot=str(latest.get("snapshot", "")),
        versions=versions,
    )


def load_cached(path: Path, *, ttl: int = CACHE_TTL) -> VersionManifest | None:
    """读取缓存清单，过期或损坏返回 ``None``。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    fetched_at = float(payload.get("fetched_at", 0))
    if ttl > 0 and time.time() - fetched_at > ttl:
        return None
    try:
        return parse_manifest(payload.get("data"))
    except NetworkError:
        return None


def save_cache(path: Path, raw: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"fetched_at": time.time(), "data": raw}
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def fetch_manifest(
    client: httpx.Client,
    cache_path: Path | None = None,
    *,
    source: str = "auto",
    mirror_base: str = DEFAULT_MIRROR_BASE,
    ttl: int = CACHE_TTL,
    force: bool = False,
) -> VersionManifest:
    """获取版本清单：优先缓存，失败时回退到旧缓存。"""
    cached = load_cached(cache_path, ttl=ttl) if cache_path and not force else None
    if cached is not None:
        return cached

    urls = candidates(MANIFEST_URL, source=source, mirror_base=mirror_base)
    last_error: Exception | None = None
    for url in urls:
        try:
            raw = http.get_json(client, url, retries=1)
            manifest = parse_manifest(raw)
            if cache_path is not None:
                try:
                    save_cache(cache_path, raw)
                except OSError as error:
                    log.debug("写入清单缓存失败：%s", error)
            return manifest
        except Exception as error:  # 换下一个候选地址
            last_error = error
            log.debug("获取版本清单失败 %s: %s", url, error)

    fallback = load_cached(cache_path, ttl=0) if cache_path else None
    if fallback is not None:
        log.warning("版本清单在线获取失败，使用离线缓存：%s", last_error)
        return fallback
    raise NetworkError("获取版本清单失败，请检查网络或下载源设置。", detail=str(last_error))


def filter_entries(entries: Iterable[VersionEntry], kind: str) -> list[VersionEntry]:
    """按类型过滤版本条目。"""
    allowed = VERSION_KINDS.get(kind, VERSION_KINDS["release"])
    return [entry for entry in entries if entry.type in allowed]
