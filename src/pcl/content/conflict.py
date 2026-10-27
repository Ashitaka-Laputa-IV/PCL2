"""Mod 依赖与冲突的静态检查。

只做「基于元数据的静态推断」，不解析 Java 字节码、不读取注册表：
- 必需依赖缺失；
- 同一 modId 出现多个版本；
- 声明的 Minecraft 版本不匹配；
- 显式 ``breaks`` 但受限版本无法判断时给出提示。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pcl.content.mods import ModDependency, ModInfo

_VERSION_SPLIT = re.compile(r"[.\-+]")


def version_tuple(text: str) -> tuple[int, ...]:
    """把版本号转换为可比较元组（忽略非数字后缀）。"""
    parts: list[int] = []
    for piece in _VERSION_SPLIT.split(text.strip()):
        digits = re.match(r"^(\d+)", piece)
        if not digits:
            break
        parts.append(int(digits.group(1)))
    return tuple(parts) or (0,)


def compare_versions(left: str, right: str) -> int:
    """比较两个版本号，返回 -1 / 0 / 1。"""
    a = list(version_tuple(left))
    b = list(version_tuple(right))
    length = max(len(a), len(b))
    a.extend([0] * (length - len(a)))
    b.extend([0] * (length - len(b)))
    if a < b:
        return -1
    if a > b:
        return 1
    return 0


def _satisfies_maven(version: str, spec: str) -> bool:
    body = spec.strip()
    if body[0] in "[(" and body[-1] in "])":
        lower_inclusive = body[0] == "["
        upper_inclusive = body[-1] == "]"
        inner = body[1:-1]
        low, _, high = inner.partition(",")
        low = low.strip()
        high = high.strip()
        if low:
            cmp = compare_versions(version, low)
            if cmp < 0 or (cmp == 0 and not lower_inclusive):
                return False
        if high:
            cmp = compare_versions(version, high)
            if cmp > 0 or (cmp == 0 and not upper_inclusive):
                return False
        return True
    return version == spec


def satisfies(version: str, spec: str) -> bool:
    """判断版本是否满足依赖声明。

    支持的形式：空 / ``*`` / 精确值 / ``1.20`` 前缀 / ``>=1.2 <2`` /
    Maven 区间 ``[1.20,1.21)`` / ``^1.2.3`` / ``~1.2``。
    """
    spec = (spec or "").strip()
    if not spec or spec in ("*", "x", "X"):
        return True
    if not version:
        return True  # 版本未知时不做否定判断，避免误报

    for token in spec.split():
        if not _satisfies_token(version, token):
            return False
    return True


def _satisfies_token(version: str, token: str) -> bool:
    token = token.strip()
    if not token or token in ("*", "x", "X"):
        return True
    if token[0] in "[(":
        return _satisfies_maven(version, token)
    if token.startswith(">="):
        return compare_versions(version, token[2:]) >= 0
    if token.startswith("<="):
        return compare_versions(version, token[2:]) <= 0
    if token.startswith(">"):
        return compare_versions(version, token[1:]) > 0
    if token.startswith("<"):
        return compare_versions(version, token[1:]) < 0
    if token.startswith("="):
        return compare_versions(version, token[1:]) == 0
    if token.startswith("^"):
        base = version_tuple(token[1:])
        if not base:
            return True
        low = ".".join(str(part) for part in base)
        high = (base[0] + 1, 0, 0)
        return compare_versions(version, low) >= 0 and compare_versions(
            version, ".".join(str(part) for part in high)
        ) < 0
    if token.startswith("~"):
        base = version_tuple(token[1:])
        if not base:
            return True
        low = ".".join(str(part) for part in base)
        major = base[0]
        minor = base[1] if len(base) > 1 else 0
        high = (major, minor + 1, 0)
        return compare_versions(version, low) >= 0 and compare_versions(
            version, ".".join(str(part) for part in high)
        ) < 0
    # 精确或前缀匹配
    return compare_versions(version, token) == 0 or version.startswith(token)


@dataclass
class ConflictReport:
    """冲突检查结果。"""

    missing: list[tuple[str, ModDependency]] = field(default_factory=list)
    duplicates: dict[str, list[str]] = field(default_factory=dict)
    mc_mismatch: list[str] = field(default_factory=list)
    breaks: list[tuple[str, ModDependency]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.missing or self.duplicates or self.mc_mismatch or self.breaks)

    @property
    def problem_count(self) -> int:
        return (
            len(self.missing)
            + len(self.mc_mismatch)
            + len(self.breaks)
            + sum(len(items) for items in self.duplicates.values())
        )

    def summary(self) -> str:
        if self.ok:
            return "未发现依赖问题。"
        parts: list[str] = []
        if self.missing:
            parts.append(f"{len(self.missing)} 个依赖缺失")
        if self.duplicates:
            parts.append(f"{len(self.duplicates)} 个模组存在重复版本")
        if self.mc_mismatch:
            parts.append(f"{len(self.mc_mismatch)} 个模组与游戏版本不匹配")
        if self.breaks:
            parts.append(f"{len(self.breaks)} 个互斥冲突")
        return "；".join(parts)


def check_conflicts(mods: list[ModInfo], mc_version: str = "") -> ConflictReport:
    """检查已启用 Mod 之间的依赖问题。"""
    report = ConflictReport()
    enabled = [mod for mod in mods if mod.enabled]

    by_id: dict[str, list[ModInfo]] = {}
    for mod in enabled:
        if mod.mod_id:
            by_id.setdefault(mod.mod_id.lower(), []).append(mod)

    for mod_id, items in by_id.items():
        if len(items) > 1:
            report.duplicates[mod_id] = [f"{item.display_name} {item.version_label}" for item in items]

    known: dict[str, list[str]] = {
        mod_id: [item.version for item in items] for mod_id, items in by_id.items()
    }
    builtin = {"minecraft", "java", "forge", "neoforge", "fabricloader", "fabric-api", "quilt_loader", "fml"}

    for mod in enabled:
        for dependency in mod.dependencies:
            dep_id = dependency.mod_id.lower()
            if dependency.ordering == "breaks":
                versions = known.get(dep_id)
                if versions and any(satisfies(version, dependency.version_range) for version in versions):
                    report.breaks.append((mod.display_name, dependency))
                continue
            if not dependency.mandatory:
                continue
            if dep_id in builtin:
                continue
            versions = known.get(dep_id)
            if not versions:
                report.missing.append((mod.display_name, dependency))
                continue
            if not any(satisfies(version, dependency.version_range) for version in versions):
                report.missing.append((mod.display_name, dependency))

        if mc_version and mod.mc_versions:
            if not any(satisfies(mc_version, spec) for spec in mod.mc_versions):
                report.mc_mismatch.append(f"{mod.display_name}（声明 {', '.join(mod.mc_versions)}）")

    return report
