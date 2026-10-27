"""崩溃分析：从 crash-reports / latest.log / JVM 崩溃日志中定位常见原因。

规则参考原版 PCL 的 ``ModCrash.vb``，只保留可稳定判断、且能给出可执行建议的项，
避免误报干扰用户。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from pcl.core.logging import get_logger

log = get_logger(__name__)

MAX_READ_BYTES = 4 * 1024 * 1024


@dataclass
class CrashReason:
    """一条崩溃原因。"""

    title: str
    detail: str = ""
    suggestion: str = ""
    severity: str = "warning"  # info / warning / error

    @property
    def text(self) -> str:
        if self.suggestion:
            return f"{self.title}：{self.suggestion}"
        return self.title


@dataclass
class CrashReport:
    """分析结果。"""

    reasons: list[CrashReason] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)
    excerpt: str = ""
    exit_code: int | None = None

    @property
    def ok(self) -> bool:
        return not self.reasons

    def summary(self) -> str:
        if not self.reasons:
            return "没有从日志中定位到明确原因。"
        return self.reasons[0].text


# (关键字, 标题, 建议, 级别)；关键字全部命中才触发
_RULES: tuple[tuple[tuple[str, ...], str, str, str], ...] = (
    (
        ("java.lang.outofmemoryerror", "java heap space"),
        "内存不足",
        "在版本设置中调大最大内存，或减少同时加载的 Mod / 材质",
        "error",
    ),
    (
        ("java.lang.outofmemoryerror", "metaspace"),
        "元空间不足（通常是 Mod 过多）",
        "调大最大内存并减少 Mod 数量，或添加 -XX:MaxMetaspaceSize=512m",
        "error",
    ),
    (
        ("unsupportedclassversionerror",),
        "Java 版本过低",
        "该版本需要更高版本的 Java，请在设置中更换 Java 运行时",
        "error",
    ),
    (
        ("unable to make protected final java.lang.class",),
        "Java 版本过高",
        "请改用与该版本匹配的 Java（常见为 Java 8 / 17）",
        "error",
    ),
    (
        ("nashornscriptenginefactory",),
        "Java 版本过高",
        "请改用 Java 8 启动该版本",
        "error",
    ),
    (
        ("classcastexception: java.base/jdk",),
        "正在使用 JDK 而不是 JRE",
        "换用普通的 JRE 运行时通常可以解决",
        "warning",
    ),
    (
        ("classcastexception: class jdk.",),
        "正在使用 JDK 而不是 JRE",
        "换用普通的 JRE 运行时通常可以解决",
        "warning",
    ),
    (
        ("has been decompressed",),
        "Mod 文件被解压",
        "请删除被解压出的目录，只保留原始的 .jar 文件",
        "error",
    ),
    (
        ("not a valid mod file",),
        "Mod 文件损坏或格式不正确",
        "重新下载该 Mod，并确认它适用于当前游戏版本",
        "error",
    ),
    (
        ("duplicatemodsfoundexception",),
        "Mod 重复安装",
        "删除 mods 目录中重复的同一个 Mod",
        "error",
    ),
    (
        ("missing or unsupported mandatory dependencies",),
        "缺少 Mod 前置",
        "根据日志中的 Mod ID 安装对应前置（如 Fabric API / Architectury）",
        "error",
    ),
    (
        ("org.spongepowered.asm.launch.mixintweaker", "classnotfoundexception"),
        "缺少 MixinBootstrap",
        "该 Forge 版本需要 MixinBootstrap，请安装对应前置 Mod",
        "error",
    ),
    (
        ("pixel format not accelerated",),
        "显卡不支持 OpenGL",
        "更新显卡驱动；核显用户可尝试专用的驱动版本",
        "error",
    ),
    (
        ("opengl", "1282"),
        "光影或资源包导致 OpenGL 1282 错误",
        "移除光影 / 提高材质分辨率，或更新显卡驱动",
        "warning",
    ),
    (
        ("exception_access_violation",),
        "显卡驱动崩溃（EXCEPTION_ACCESS_VIOLATION）",
        "更新显卡驱动，关闭 OptiFine / 光影后重试",
        "error",
    ),
    (
        ("mod loading has failed",),
        "Mod 加载失败",
        "查看日志中紧随其后的 FATAL 行，通常会列出具体 Mod",
        "error",
    ),
    (
        ("failed to load mods",),
        "Mod 加载失败",
        "查看日志中紧随其后的 FATAL 行，通常会列出具体 Mod",
        "error",
    ),
    (
        ("java.lang.nosuchmethoderror",),
        "Mod 与游戏版本不匹配",
        "检查该 Mod 是否适用于当前 Minecraft 与加载器版本",
        "warning",
    ),
    (
        ("outofmemory",),
        "内存不足",
        "调大最大内存后重试",
        "error",
    ),
)


def _read_tail(path: Path, limit: int = MAX_READ_BYTES) -> str:
    try:
        size = path.stat().st_size
        with path.open("rb") as stream:
            if size > limit:
                stream.seek(size - limit)
            return stream.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _latest_crash_report(game_dir: Path) -> Path | None:
    directory = game_dir / "crash-reports"
    if not directory.is_dir():
        return None
    reports = sorted(directory.glob("crash-*.txt"), key=lambda item: item.stat().st_mtime, reverse=True)
    if reports:
        return reports[0]
    return None


def _jvm_crash_log(game_dir: Path) -> Path | None:
    logs = sorted(game_dir.glob("hs_err_pid*.log"), key=lambda item: item.stat().st_mtime, reverse=True)
    return logs[0] if logs else None


def _excerpt(text: str) -> str:
    """截取 crash report 的描述段，便于展示。"""
    match = re.search(r"Description:\s*(.+?)(?:\n\n|\Z)", text, re.S)
    if match:
        return match.group(1).strip()[:600]
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("----"):
            return stripped[:200]
    return ""


def _mod_mentions(text: str, mods_dir: Path) -> list[str]:
    """找出日志中出现的 Mod 文件名。"""
    if not mods_dir.is_dir():
        return []
    lowered = text.lower()
    hits: list[str] = []
    for path in mods_dir.iterdir():
        if not path.is_file() or not path.name.endswith((".jar", ".jar.disabled")):
            continue
        stem = path.name.replace(".disabled", "").rsplit(".jar", 1)[0]
        if len(stem) < 4:
            continue
        if stem.lower() in lowered:
            hits.append(path.name.replace(".disabled", ""))
    return hits[:5]


def analyze_crash(
    game_dir: Path,
    *,
    logs_dir: Path | None = None,
    exit_code: int | None = None,
) -> CrashReport:
    """分析最近一次崩溃，返回原因列表。"""
    files: list[Path] = []
    chunks: list[str] = []

    report = _latest_crash_report(game_dir)
    if report is not None:
        files.append(report)
        chunks.append(_read_tail(report))

    latest_log = (logs_dir / "latest.log") if logs_dir else (game_dir / "logs" / "latest.log")
    if latest_log.is_file():
        files.append(latest_log)
        chunks.append(_read_tail(latest_log))

    jvm_log = _jvm_crash_log(game_dir)
    if jvm_log is not None:
        files.append(jvm_log)
        chunks.append(_read_tail(jvm_log))

    text = "\n".join(chunks)
    lowered = text.lower()
    crash_text = _read_tail(report).lower() if report else ""

    result = CrashReport(files=files, exit_code=exit_code)

    seen: set[str] = set()
    for keywords, title, suggestion, severity in _RULES:
        if title in seen:
            continue
        if all(keyword in lowered for keyword in keywords):
            seen.add(title)
            result.reasons.append(
                CrashReason(title=title, suggestion=suggestion, severity=severity)
            )

    # 退出码兜底（Windows 的 0xC0000005 与 Linux 的 139 均指向原生崩溃）
    if exit_code is not None and not result.reasons:
        if exit_code in (-1073741819, 139, -11):
            result.reasons.append(
                CrashReason(
                    title="游戏进程异常终止",
                    detail=f"退出码 {exit_code}",
                    suggestion="通常是显卡驱动或原生库问题，尝试更新驱动并关闭光影",
                    severity="error",
                )
            )
        elif exit_code == 1:
            result.reasons.append(
                CrashReason(
                    title="游戏启动失败",
                    detail="退出码 1",
                    suggestion="打开日志查看第一条报错，常见原因是 Java 版本或 Mod 前置缺失",
                    severity="warning",
                )
            )

    # Mod 归因：日志里出现的 Mod 文件名
    mod_hits = _mod_mentions(crash_text or text, game_dir / "mods")
    if mod_hits:
        result.reasons.append(
            CrashReason(
                title="日志中出现了疑似相关 Mod",
                detail="、".join(mod_hits),
                suggestion="可先禁用这些 Mod 再测试，以确认冲突来源",
                severity="info",
            )
        )

    if report is not None:
        result.excerpt = _excerpt(_read_tail(report))
    elif text:
        result.excerpt = "\n".join(text.splitlines()[-8:])[:600]

    if not files:
        result.reasons.append(
            CrashReason(
                title="没有找到可分析的日志",
                detail=str(game_dir / "crash-reports"),
                suggestion="确认游戏确实启动过，或手动选择日志文件进行分析",
                severity="info",
            )
        )

    log.info("崩溃分析完成：%d 条原因，读取 %d 个文件", len(result.reasons), len(files))
    return result
