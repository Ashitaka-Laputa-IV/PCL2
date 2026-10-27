"""下载源镜像策略：官方地址 ↔ 国内镜像的映射与候选顺序。

设计要点：
- 镜像只做「URL 重写」，不改变请求语义；
- ``auto`` 模式优先镜像、失败后自动回退官方源；
- 任何校验失败都会触发下一个候选地址，避免镜像返回错误页被写入磁盘。
"""

from __future__ import annotations

from typing import Final, Iterable

DEFAULT_MIRROR_BASE: Final[str] = "https://bmclapi2.bangbang93.com"

#: 官方域名 -> 镜像上的路径前缀（空串表示直接拼接原路径）
MIRROR_PREFIXES: Final[dict[str, str]] = {
    "launchermeta.mojang.com": "",
    "piston-meta.mojang.com": "",
    "launcher.mojang.com": "",
    "piston-data.mojang.com": "",
    "resources.download.minecraft.net": "/assets",
    "libraries.minecraft.net": "/maven",
    "maven.minecraftforge.net": "/maven",
    "files.minecraftforge.net": "/maven",
    "meta.fabricmc.net": "/fabric-meta",
    "meta.quiltmc.org": "/quilt-meta",
}

#: 官方域名 -> 仅允许改写这些路径前缀（空集合表示全部允许）
_PATH_FILTERS: Final[dict[str, tuple[str, ...]]] = {}

VALID_SOURCES: Final[tuple[str, ...]] = ("auto", "official", "mirror")


def mirror_url(url: str, base: str = DEFAULT_MIRROR_BASE) -> str | None:
    """把官方地址改写为镜像地址，无法改写时返回 ``None``。"""
    if not url.startswith(("http://", "https://")):
        return None

    scheme, _, rest = url.partition("://")
    authority, slash, path = rest.partition("/")
    host = authority.split("@")[-1].split(":")[0].lower()
    prefix = MIRROR_PREFIXES.get(host)
    if prefix is None:
        return None

    allowed = _PATH_FILTERS.get(host)
    if allowed and not any(path.startswith(item) for item in allowed):
        return None

    base = base.rstrip("/")
    if not slash:
        path = ""
    return f"{base}{prefix}/{path}" if path else f"{base}{prefix}"


def candidates(
    url: str,
    *,
    source: str = "auto",
    mirror_base: str = DEFAULT_MIRROR_BASE,
    extra_mirrors: Iterable[str] = (),
) -> list[str]:
    """返回按优先级排列的候选地址列表（去重、保持顺序）。"""
    if source not in VALID_SOURCES:
        source = "auto"

    result: list[str] = []
    result.extend(extra_mirrors)

    mirrored = mirror_url(url, mirror_base)
    if source == "official":
        result.append(url)
    elif source == "mirror":
        if mirrored:
            result.append(mirrored)
        result.append(url)
    else:  # auto：镜像优先，官方兜底
        if mirrored:
            result.append(mirrored)
        result.append(url)

    if source == "mirror" and not mirrored:
        # 无镜像映射的地址只能走官方，但仍保留一次重试机会
        result.append(url)

    deduped: list[str] = []
    for item in result:
        if item and item not in deduped:
            deduped.append(item)
    return deduped


def describe_source(source: str) -> str:
    """返回下载源的中文名称。"""
    return {"auto": "自动选择", "official": "官方源", "mirror": "镜像源（BMCLAPI）"}.get(source, source)
