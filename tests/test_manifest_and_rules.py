"""版本清单解析与 rules / maven 坐标求值。"""

from __future__ import annotations

from pcl.version import meta
from pcl.version.manifest import VersionManifest, parse_manifest


def test_rules_os_and_default() -> None:
    windows_only = [{"action": "allow", "os": {"name": "windows"}}, {"action": "disallow"}]
    assert meta.evaluate_rules(windows_only, os_name="windows") is True
    assert meta.evaluate_rules(windows_only, os_name="linux") is False

    linux_only = [{"action": "allow", "os": {"name": "linux"}}]
    assert meta.evaluate_rules(linux_only, os_name="linux") is True
    assert meta.evaluate_rules(linux_only, os_name="osx") is False

    assert meta.evaluate_rules(None) is True
    assert meta.evaluate_rules([]) is True


def test_rules_arch_and_version_regex() -> None:
    rule = [{"action": "allow", "os": {"name": "linux", "arch": "x86"}}]
    assert meta.evaluate_rules(rule, os_name="linux", os_arch="x86") is True
    assert meta.evaluate_rules(rule, os_name="linux", os_arch="x64") is False

    version_rule = [{"action": "allow", "os": {"name": "osx", "version": "^10\\.9"}}]
    assert meta.evaluate_rules(version_rule, os_name="osx", os_version="10.9.5") is True
    assert meta.evaluate_rules(version_rule, os_name="osx", os_version="11.0") is False


def test_rules_features() -> None:
    rule = [{"action": "allow", "features": {"has_custom_resolution": True}}]
    assert meta.evaluate_rules(rule, features={"has_custom_resolution": True}) is True
    assert meta.evaluate_rules(rule, features={"has_custom_resolution": False}) is False

    negative = [{"action": "allow", "features": {"is_demo_user": False}}]
    assert meta.evaluate_rules(negative, features={"is_demo_user": False}) is True


def test_maven_path_variants() -> None:
    assert meta.maven_path("org.lwjgl:lwjgl:3.3.1") == "org/lwjgl/lwjgl/3.3.1/lwjgl-3.3.1.jar"
    assert (
        meta.maven_path("net.java:jinput-platform:2.0.5:natives-windows")
        == "net/java/jinput-platform/2.0.5/jinput-platform-2.0.5-natives-windows.jar"
    )
    assert meta.maven_path("com.example:demo:1.0@zip") == "com/example/demo/1.0/demo-1.0.zip"
    assert meta.maven_path("invalid") is None


def test_library_selection_helpers() -> None:
    library = {
        "name": "org.lwjgl:lwjgl:3.3.1",
        "downloads": {
            "artifact": {"path": "org/lwjgl/lwjgl/3.3.1/lwjgl-3.3.1.jar", "url": "https://example.invalid/a.jar"},
            "classifiers": {"natives-linux": {"path": "native.jar", "url": "https://example.invalid/n.jar"}},
        },
        "natives": {"linux": "natives-linux"},
        "extract": {"exclude": ["META-INF/"]},
    }
    assert meta.library_target_path(library) == "org/lwjgl/lwjgl/3.3.1/lwjgl-3.3.1.jar"
    assert meta.library_url(library) == "https://example.invalid/a.jar"
    assert meta.native_classifier(library, "linux") == "natives-linux"
    assert meta.native_download(library, "linux") is not None
    # ${arch} 会被替换为 32 / 64
    assert meta.native_classifier({"name": "g:a:1", "natives": {"linux": "natives-linux${arch}"}}, "linux") in (
        "natives-linux32",
        "natives-linux64",
    )
    assert meta.extract_excludes(library) == ["META-INF/"]
    assert meta.library_target_path({"name": "g:a:1", "url": "https://x"}).endswith("a-1.jar")


def test_parse_manifest_and_filter() -> None:
    raw = {
        "latest": {"release": "1.21.1", "snapshot": "25w01a"},
        "versions": [
            {"id": "1.21.1", "type": "release", "url": "https://x/1.json", "releaseTime": "2024-08-08T00:00:00+00:00"},
            {"id": "25w01a", "type": "snapshot", "url": "https://x/2.json"},
            {"id": "b1.7.3", "type": "old_beta", "url": "https://x/3.json"},
        ],
    }
    manifest = parse_manifest(raw)
    assert isinstance(manifest, VersionManifest)
    assert manifest.latest_release == "1.21.1"
    assert [entry.id for entry in manifest.filtered("release")] == ["1.21.1"]
    assert [entry.id for entry in manifest.filtered("snapshot")] == ["25w01a"]
    assert [entry.id for entry in manifest.filtered("old")] == ["b1.7.3"]
    assert manifest.find("1.21.1") is not None
    assert manifest.find("1.21.1").release_date == "2024-08-08"
    assert manifest.find("missing") is None


def test_required_java_major() -> None:
    assert meta.required_java_major({"javaVersion": {"majorVersion": 21}}) == 21
    assert meta.required_java_major({"javaVersion": {"majorVersion": "17"}}) == 17
    assert meta.required_java_major({}) == 8
    assert meta.required_java_major({}, default=16) == 16
    assert meta.version_type_label("snapshot") == "快照"
