"""Forge / NeoForge 安装：解包安装器并执行 processors 链。

官方安装器是一个可执行的 jar，内部包含 ``install_profile.json``（安装清单）、
``version.json``（待生成的版本描述）与 ``maven/``（内嵌工件）。本模块不启动
安装器 GUI，而是复现其核心流程：

1. 下载安装器并解包到临时目录；
2. 把内嵌 maven 工件释放到 ``libraries/``；
3. 下载清单声明的库与处理器依赖；
4. 按 ``processors`` 顺序执行 Java 处理器（仅 client 侧）；
5. 落盘版本 JSON。
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import httpx

from pcl.core.config import DownloadOptions
from pcl.core.errors import JavaNotFoundError, LoaderError, NetworkError
from pcl.core.logging import get_logger
from pcl.core.paths import AppPaths, classpath_separator, java_executable
from pcl.launch.java import find_java, probe_java
from pcl.net import http
from pcl.net.downloader import DownloadManager, DownloadTask
from pcl.net.mirror import DEFAULT_MIRROR_BASE, candidates
from pcl.version import meta, merge
from pcl.version.installer import VanillaInstaller, is_installed
from pcl.version.loaders.util import library_tasks, maven_coord_path
from pcl.version.manifest import VersionEntry

log = get_logger(__name__)

_TOKEN_PATTERN = re.compile(r"\{([A-Za-z0-9_]+)\}")
#: 处理器参数里直接写死的 maven 坐标，例如 ``[de.oceanlabs.mcp:mcp_config:1.20.1-...@zip]``
_COORD_PATTERN = re.compile(r"^\[([^\[\]]+)\]$")


def _bracket_coord(value: str) -> str:
    """把 ``[group:artifact:version@ext]`` 解析成 maven 坐标，否则返回空串。"""
    match = _COORD_PATTERN.match(str(value).strip())
    return match.group(1) if match else ""


def _version_at_least(version: str, target: str) -> bool:
    """比较版本号，忽略后缀（如 1.20.5-pre1）。"""
    def parts(text: str) -> tuple:
        numbers: list[int] = []
        for piece in str(text).split(".")[:3]:
            digits = "".join(ch for ch in piece if ch.isdigit())
            numbers.append(int(digits) if digits else 0)
        return tuple(numbers)

    return parts(version) >= parts(target)

FORGE_MAVEN = "https://maven.minecraftforge.net"
NEOFORGE_MAVEN = "https://maven.neoforged.net/releases"
FORGE_PROMOTIONS = "https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json"


@dataclass(frozen=True)
class LoaderVersion:
    """加载器版本（version 为安装入口使用的字符串）。"""

    version: str
    stable: bool = True
    recommended: bool = False

    @property
    def label(self) -> str:
        suffix = " · 推荐" if self.recommended else ("" if self.stable else "（测试版）")
        return f"{self.version}{suffix}"


@dataclass
class InstallProfile:
    """解析后的安装清单。"""

    raw: dict[str, Any]
    version_json: dict[str, Any]
    version_id: str
    mc_version: str
    libraries: list[dict[str, Any]] = field(default_factory=list)
    processors: list[dict[str, Any]] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)


class ForgeInstaller:
    """Forge / NeoForge 安装器。"""

    def __init__(
        self,
        paths: AppPaths,
        options: DownloadOptions | None = None,
        *,
        kind: str = "forge",
        client: httpx.Client | None = None,
        java_path: str | None = None,
    ) -> None:
        if kind not in ("forge", "neoforge"):
            raise ValueError(f"不支持的加载器: {kind}")
        self.paths = paths
        self.options = options or DownloadOptions()
        self.kind = kind
        self._client = client
        self.java_path = java_path

    # ---------- 地址与版本 ----------
    @property
    def maven_base(self) -> str:
        return FORGE_MAVEN if self.kind == "forge" else NEOFORGE_MAVEN

    def _artifact_version(self, mc_version: str, loader_version: str) -> str:
        if loader_version.startswith(f"{mc_version}-"):
            return loader_version
        if self.kind in ("forge", "neoforge") and mc_version == "1.20.1" and self.kind == "neoforge":
            return f"{mc_version}-{loader_version}"
        if self.kind == "forge":
            return f"{mc_version}-{loader_version}"
        return loader_version

    def installer_url(self, mc_version: str, loader_version: str) -> str:
        """返回安装器 jar 的下载地址。"""
        artifact = self._artifact_version(mc_version, loader_version)
        if self.kind == "forge":
            path = f"net/minecraftforge/forge/{artifact}/forge-{artifact}-installer.jar"
        elif artifact.startswith(f"{mc_version}-"):
            path = f"net/neoforged/forge/{artifact}/forge-{artifact}-installer.jar"
        else:
            path = f"net/neoforged/neoforge/{artifact}/neoforge-{artifact}-installer.jar"
        return f"{self.maven_base}/{path}"

    def _client(self) -> httpx.Client:
        return self._client or http.create_client(concurrency=8)

    def _fetch_text(self, url: str) -> str:
        owns = self._client is None
        client = self._client or http.create_client(concurrency=8)
        try:
            last: Exception | None = None
            for candidate in candidates(
                url,
                source=self.options.source,
                mirror_base=self.options.mirror_base or DEFAULT_MIRROR_BASE,
            ):
                try:
                    return http.get_text(client, candidate)
                except Exception as error:
                    last = error
            raise NetworkError("获取加载器信息失败。", detail=str(last))
        finally:
            if owns:
                client.close()

    def _fetch_json(self, url: str) -> Any:
        return json.loads(self._fetch_text(url))

    def list_versions(self, mc_version: str) -> list[LoaderVersion]:
        """列出某 Minecraft 版本可用的加载器版本。"""
        if self.kind == "forge":
            return self._list_forge_versions(mc_version)
        return self._list_neoforge_versions(mc_version)

    def _list_forge_versions(self, mc_version: str) -> list[LoaderVersion]:
        try:
            data = self._fetch_json(FORGE_PROMOTIONS)
            promos = data.get("promos", {}) if isinstance(data, dict) else {}
            recommended = str(promos.get(f"{mc_version}-recommended", "") or "")
            latest = str(promos.get(f"{mc_version}-latest", "") or "")
            versions: list[LoaderVersion] = []
            if latest:
                versions.append(LoaderVersion(version=latest, stable=True, recommended=latest == recommended))
            if recommended and recommended != latest:
                versions.append(LoaderVersion(version=recommended, stable=True, recommended=True))
            if versions:
                return versions
        except (NetworkError, ValueError) as error:
            log.debug("读取 Forge 推荐版本失败：%s", error)

        # 回退：解析 maven-metadata.xml
        xml = self._fetch_text(f"{FORGE_MAVEN}/net/minecraftforge/forge/maven-metadata.xml")
        return self._versions_from_metadata(xml, mc_version)

    def _list_neoforge_versions(self, mc_version: str) -> list[LoaderVersion]:
        if mc_version == "1.20.1":
            xml = self._fetch_text(f"{NEOFORGE_MAVEN}/net/neoforged/forge/maven-metadata.xml")
            return self._versions_from_metadata(xml, mc_version)
        xml = self._fetch_text(f"{NEOFORGE_MAVEN}/net/neoforged/neoforge/maven-metadata.xml")
        parts = mc_version.split(".")
        try:
            minor = int(parts[1])
            patch = int(parts[2]) if len(parts) > 2 else 0
        except (IndexError, ValueError):
            raise LoaderError(f"无法确定 NeoForge 对应的 Minecraft 版本：{mc_version}")
        prefix = f"{minor}.{patch}."
        versions = self._versions_from_metadata(xml, None)
        filtered = [item for item in versions if item.version.startswith(prefix)]
        if not filtered:
            raise LoaderError(f"Minecraft {mc_version} 没有可用的 NeoForge 版本。")
        return filtered

    @staticmethod
    def _versions_from_metadata(xml: str, mc_prefix: str | None) -> list[LoaderVersion]:
        try:
            root = ET.fromstring(xml)
        except ET.ParseError as error:
            raise LoaderError("解析加载器版本列表失败。", detail=str(error)) from error

        result: list[LoaderVersion] = []
        seen: set[str] = set()
        for element in root.iter("version"):
            value = (element.text or "").strip()
            if not value or value in seen:
                continue
            if mc_prefix:
                if not value.startswith(f"{mc_prefix}-"):
                    continue
                value = value[len(mc_prefix) + 1 :]
            seen.add(value)
            result.append(LoaderVersion(version=value))
        result.reverse()  # maven-metadata 通常按升序排列
        return result

    # ---------- 安装 ----------
    def _download_installer(self, mc_version: str, loader_version: str) -> Path:
        url = self.installer_url(mc_version, loader_version)
        filename = url.rsplit("/", 1)[-1]
        dest = self.paths.cache / "installers" / filename
        if http.file_matches(dest, None, None):
            return dest

        client = self._client or http.create_client(concurrency=4)
        owns = self._client is None
        last_error: Exception | None = None
        try:
            for candidate in candidates(
                url,
                source=self.options.source,
                mirror_base=self.options.mirror_base or DEFAULT_MIRROR_BASE,
            ):
                try:
                    http.download_file(client, candidate, dest)
                    return dest
                except Exception as error:
                    last_error = error
        finally:
            if owns:
                client.close()
        raise LoaderError("下载安装器失败。", detail=f"{last_error}")

    def read_profile(self, installer: Path, mc_version: str) -> InstallProfile:
        """解析安装器内的 install_profile.json 与 version.json。"""
        try:
            with zipfile.ZipFile(installer) as archive:
                names = set(archive.namelist())
                if "install_profile.json" not in names:
                    raise LoaderError("安装器缺少 install_profile.json。")
                raw = json.loads(archive.read("install_profile.json").decode("utf-8-sig"))

                version_json: dict[str, Any] = {}
                candidates_in_zip = [
                    str(raw.get("json") or "").lstrip("/"),
                    "version.json",
                ]
                for name in candidates_in_zip:
                    if name and name in names:
                        version_json = json.loads(archive.read(name).decode("utf-8-sig"))
                        break
                if not version_json:
                    info = raw.get("versionInfo")
                    if isinstance(info, Mapping):
                        version_json = dict(info)
                if not version_json:
                    raise LoaderError("安装器内没有版本描述文件。")
        except (OSError, zipfile.BadZipFile, ValueError) as error:
            raise LoaderError("安装器文件损坏或格式异常。", detail=str(error)) from error

        version_id = str(version_json.get("id") or raw.get("version") or "")
        if not version_id:
            raise LoaderError("无法确定加载器版本 id。")
        version_json.setdefault("inheritsFrom", mc_version)
        version_json.setdefault("id", version_id)

        return InstallProfile(
            raw=raw,
            version_json=version_json,
            version_id=version_id,
            mc_version=mc_version,
            libraries=list(raw.get("libraries") or []),
            processors=list(raw.get("processors") or []),
            data=dict(raw.get("data") or {}),
        )

    def _extract_embedded(self, installer: Path, work_dir: Path) -> None:
        """释放安装器内的 maven 工件与数据文件。"""
        try:
            with zipfile.ZipFile(installer) as archive:
                for info in archive.infolist():
                    if info.is_dir():
                        continue
                    if info.filename.startswith("maven/"):
                        relative = info.filename[len("maven/") :]
                        if not relative:
                            continue
                        target = self.paths.libraries / relative
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(info) as source, target.open("wb") as output:
                            output.write(source.read())
                    elif info.filename.startswith("data/"):
                        target = work_dir / info.filename
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(info) as source, target.open("wb") as output:
                            output.write(source.read())
        except (OSError, zipfile.BadZipFile) as error:
            raise LoaderError("释放安装器内容失败。", detail=str(error)) from error

    def _resolve_data_value(self, value: str, work_dir: Path, side: str) -> str:
        """解析 install_profile.data 中的变量取值。"""
        text = str(value)
        if text.startswith("[") and text.endswith("]"):
            coord = text[1:-1]
            path = maven_coord_path(coord)
            if not path:
                raise LoaderError(f"无法解析 maven 坐标：{coord}")
            return str(self.paths.libraries / path)
        if len(text) >= 2 and text.startswith("'") and text.endswith("'"):
            return text[1:-1]
        if text.startswith("/"):
            return str(work_dir / text.lstrip("/"))
        return text

    def _build_data(self, profile: InstallProfile, work_dir: Path, side: str = "client") -> dict[str, str]:
        resolved: dict[str, str] = {}
        for key, item in profile.data.items():
            if isinstance(item, Mapping):
                value = item.get(side) or item.get("client") or item.get("server") or ""
            else:
                value = item
            if value:
                resolved[str(key)] = self._resolve_data_value(str(value), work_dir, side)
        return resolved

    def _auto_java(self, mc_version: str):
        """系统里没有可用 Java 时，按 MC 版本自动补装一个。"""
        major = 21 if _version_at_least(mc_version, "1.20.5") else (17 if _version_at_least(mc_version, "1.18") else 8)
        try:
            from pcl.launch.java_installer import install_java

            runtime = install_java(self.paths, major, self.options)
        except Exception as error:  # noqa: BLE001 - 自动补装失败仍要给出明确报错
            log.warning("自动安装 Java %s 失败：%s", major, error)
            return None
        log.info("已自动安装 Java %s：%s", major, runtime.path)
        return runtime

    @staticmethod
    def _processor_artifacts(profile: InstallProfile) -> list[str]:
        """收集处理器参数中直接写死的 maven 坐标（需要预先下载）。"""
        coords: list[str] = []
        for processor in profile.processors:
            if not isinstance(processor, Mapping):
                continue
            for arg in processor.get("args") or []:
                coord = _bracket_coord(str(arg))
                if coord and coord not in coords:
                    coords.append(coord)
        return coords

    @staticmethod
    def _processor_main_class(jar_path: Path) -> str | None:
        try:
            with zipfile.ZipFile(jar_path) as archive:
                with archive.open("META-INF/MANIFEST.MF") as stream:
                    raw = stream.read().decode("utf-8", errors="replace")
        except (OSError, KeyError, zipfile.BadZipFile):
            return None
        for line in raw.splitlines():
            if line.lower().startswith("main-class:"):
                return line.split(":", 1)[1].strip()
        return None

    def _run_processors(self, profile: InstallProfile, work_dir: Path, installer: Path) -> None:
        if not profile.processors:
            return

        java_path = self.java_path
        runtime = probe_java(Path(java_path)) if java_path else None
        runtime = runtime or find_java()
        if runtime is None:
            # PCL 的做法：缺少可用 Java 时自动补装一个对应版本，而不是直接失败
            runtime = self._auto_java(profile.mc_version)
        if runtime is None:
            raise JavaNotFoundError("安装 Forge 需要可用的 Java，请先在设置中安装或指定。")

        data = self._build_data(profile, work_dir)
        minecraft_jar = self.paths.version_jar(profile.mc_version)
        if not minecraft_jar.is_file():
            raise LoaderError(f"缺少原版客户端 jar：{minecraft_jar}")

        variables = {
            "MINECRAFT_JAR": str(minecraft_jar),
            "MINECRAFT_VERSION": profile.mc_version,
            "SIDE": "client",
            "ROOT": str(self.paths.game),
            "INSTALLER": str(installer),
            "LIBRARY_DIR": str(self.paths.libraries),
        }
        for key, value in data.items():
            # Forge 的处理器参数同时存在 {BINPATCH} 与 {DATA_BINPATCH} 两种写法
            variables[f"DATA_{key}"] = value
            variables.setdefault(str(key), value)

        for index, processor in enumerate(profile.processors, start=1):
            if not isinstance(processor, Mapping):
                continue
            sides = processor.get("sides")
            if isinstance(sides, list) and sides and "client" not in sides:
                continue

            jar_coord = str(processor.get("jar") or "")
            jar_path = self.paths.libraries / (maven_coord_path(jar_coord) or "")
            if not jar_coord or not jar_path.is_file():
                raise LoaderError(f"处理器依赖缺失：{jar_coord}")

            classpath: list[str] = [str(jar_path)]
            for coord in processor.get("classpath") or []:
                path = maven_coord_path(str(coord))
                if not path:
                    continue
                candidate = self.paths.libraries / path
                if candidate.is_file():
                    classpath.append(str(candidate))

            main_class = self._processor_main_class(jar_path)
            if not main_class:
                raise LoaderError(f"处理器 jar 缺少 Main-Class：{jar_coord}")

            args: list[str] = []
            for raw in processor.get("args") or []:
                text = str(raw)
                coord = _bracket_coord(text)
                if coord:
                    # 官方安装器会先把坐标下载到 libraries 再传本地路径
                    relative = maven_coord_path(coord)
                    target = self.paths.libraries / relative if relative else None
                    if target is None or not target.is_file():
                        raise LoaderError(f"缺少处理器输入文件：{coord}", detail=f"{jar_coord}")
                    text = str(target)
                args.append(self._substitute(text, variables))
            unresolved = [token for token in _TOKEN_PATTERN.findall(" ".join(args)) if token not in variables]
            if unresolved:
                raise LoaderError(
                    f"处理器参数缺少变量：{unresolved[0]}（install_profile.data 未声明）",
                    detail=f"{jar_coord}",
                )
            command = [
                str(runtime.path),
                "-cp",
                classpath_separator().join(classpath),
                main_class,
                *args,
            ]
            log.info("[%s] 执行处理器 %d/%d", self.kind, index, len(profile.processors))
            log.debug("处理器命令：%s", " ".join(command))
            try:
                completed = subprocess.run(
                    command,
                    cwd=str(work_dir),
                    capture_output=True,
                    text=True,
                    errors="replace",
                    timeout=900,
                )
            except (OSError, subprocess.SubprocessError) as error:
                raise LoaderError(f"处理器执行失败：{main_class}", detail=str(error)) from error
            if completed.returncode != 0:
                tail = (completed.stderr or completed.stdout or "").strip()[-800:]
                raise LoaderError(f"处理器执行失败：{main_class}", detail=tail)

            self._verify_outputs(processor.get("outputs"), variables)

    @staticmethod
    def _substitute(value: Any, variables: Mapping[str, str]) -> str:
        text = str(value)
        for key, replacement in variables.items():
            text = text.replace("{" + key + "}", replacement)
        return text

    def _verify_outputs(self, outputs: Any, variables: Mapping[str, str]) -> None:
        if not isinstance(outputs, Mapping):
            return
        for raw_path, expected in outputs.items():
            path = Path(self._substitute(raw_path, variables))
            if not path.is_file():
                raise LoaderError(f"处理器输出缺失：{path}")
            expected_text = str(expected)
            if expected_text.startswith("["):
                continue
            if len(expected_text) == 40 and http.sha1_file(path).lower() != expected_text.lower():
                raise LoaderError(f"处理器输出校验失败：{path}")

    def install(
        self,
        mc_version: str,
        loader_version: str,
        *,
        vanilla: VanillaInstaller | None = None,
        vanilla_entry: VersionEntry | None = None,
        java_path: str | None = None,
    ) -> str:
        """安装加载器并返回生成的版本 id。"""
        if java_path:
            self.java_path = java_path
        if not is_installed(self.paths, mc_version):
            if vanilla is None:
                raise LoaderError(f"请先安装原版 {mc_version} 再安装 {self.kind}。")
            vanilla.install(mc_version, vanilla_entry)

        installer = self._download_installer(mc_version, loader_version)
        profile = self.read_profile(installer, mc_version)
        version_id = profile.version_id

        with tempfile.TemporaryDirectory(prefix="pcl-forge-") as temp:
            work_dir = Path(temp)
            self._extract_embedded(installer, work_dir)

            tasks = library_tasks(self.paths, profile.libraries, priority=10)
            tasks += library_tasks(self.paths, profile.version_json.get("libraries") or [], priority=10)

            # 处理器所需的 jar 与 classpath 也必须先下载
            processor_tasks: list[DownloadTask] = []
            for processor in profile.processors:
                if not isinstance(processor, Mapping):
                    continue
                coords = [str(processor.get("jar") or ""), *(str(c) for c in processor.get("classpath") or [])]
                for coord in coords:
                    path = maven_coord_path(coord)
                    if not path:
                        continue
                    processor_tasks.append(
                        DownloadTask(
                            url=f"{self.maven_base}/{path}",
                            dest=self.paths.libraries / path,
                            priority=20,
                            name=coord,
                        )
                    )

            # 处理器参数里直接引用的 maven 工件（如 mcp_config）也要先下载
            for coord in self._processor_artifacts(profile):
                path = maven_coord_path(coord)
                if not path:
                    continue
                dest = self.paths.libraries / path
                if dest.is_file():
                    continue
                processor_tasks.append(
                    DownloadTask(url=f"{self.maven_base}/{path}", dest=dest, priority=20, name=coord)
                )

            manager = DownloadManager(self.options)
            summary = manager.run([*tasks, *processor_tasks])
            if summary.failures:
                detail = "; ".join(f"{item.task.display_name}: {item.error}" for item in summary.failures[:3])
                raise LoaderError(f"{self.kind} 依赖下载失败。", detail=detail)

            self._run_processors(profile, work_dir, installer)

        version_json = dict(profile.version_json)
        version_json.setdefault("id", version_id)
        version_json.setdefault("inheritsFrom", mc_version)
        version_json.setdefault("type", "release")
        merge.save_version_json(self.paths, version_id, version_json)
        log.info("已安装 %s %s（Minecraft %s）：%s", self.kind, loader_version, mc_version, version_id)
        return version_id


def install(
    paths: AppPaths,
    options: DownloadOptions,
    mc_version: str,
    loader_version: str,
    *,
    kind: str = "forge",
    java_path: str | None = None,
    vanilla_entry: VersionEntry | None = None,
) -> str:
    """模块级安装入口。"""
    installer = ForgeInstaller(paths, options, kind=kind, java_path=java_path)
    return installer.install(
        mc_version,
        loader_version,
        vanilla=VanillaInstaller(paths, options),
        vanilla_entry=vanilla_entry,
        java_path=java_path,
    )
