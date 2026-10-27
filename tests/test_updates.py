"""Modrinth 更新检测：指纹匹配、版本筛选与请求失败处理。"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

from pcl.content.updates import _pick_latest, check_mod_updates

MC = "1.20.1"


class FakeResponse:
    def __init__(self, data, status: int = 200) -> None:
        self._data = data
        self.status_code = status

    def json(self):
        return self._data


class FakeClient:
    """只实现更新检测用到的 POST /version_files 与版本列表 GET。"""

    def __init__(self, versions: list[dict], matched: bool = True, fail: bool = False) -> None:
        self.versions = versions
        self.matched = matched
        self.fail = fail

    def post(self, url: str, json):
        if self.fail:
            return FakeResponse({}, status=503)
        payload = {}
        if self.matched:
            for digest in json["hashes"]:
                payload[digest] = {
                    "id": "v-old",
                    "project_id": "proj",
                    "version_number": "1.0",
                    "version_type": "release",
                    "date_published": "2026-01-01T00:00:00Z",
                }
        return FakeResponse(payload)

    def get(self, url: str):
        return FakeResponse(self.versions)


def _version(version_id: str, number: str, date: str, *, type_: str = "release", loader: str = "fabric") -> dict:
    return {
        "id": version_id,
        "project_id": "proj",
        "version_number": number,
        "version_type": type_,
        "date_published": date,
        "loaders": [loader],
        "game_versions": [MC],
        "files": [
            {
                "primary": True,
                "filename": f"mod-{number}.jar",
                "url": f"https://cdn.example/mod-{number}.jar",
                "hashes": {"sha1": "b" * 40},
            }
        ],
    }


def _mod(tmp_path, name: str = "demo.jar", enabled: bool = True):
    path = tmp_path / name
    path.write_bytes(b"mod-bytes")
    return SimpleNamespace(path=path, file_name=name, enabled=enabled), hashlib.sha1(b"mod-bytes").hexdigest()


def test_check_finds_newer_release(tmp_path) -> None:
    mod, _digest = _mod(tmp_path)
    client = FakeClient([_version("v-old", "1.0", "2026-01-01T00:00:00Z"), _version("v-new", "1.1", "2026-02-01T00:00:00Z")])
    report = check_mod_updates([mod], client, mc_version=MC, loader="fabric")
    assert report.checked == 1
    assert report.matched == 1
    assert len(report.updates) == 1
    update = report.updates[0]
    assert (update.current_version, update.latest_version) == ("1.0", "1.1")
    assert update.latest_filename == "mod-1.1.jar"
    assert update.page_url == "https://modrinth.com/project/proj"


def test_skip_when_game_version_mismatch(tmp_path) -> None:
    mod, _digest = _mod(tmp_path)
    other = _version("v-new", "1.1", "2026-02-01T00:00:00Z")
    other["game_versions"] = ["1.21.1"]
    report = check_mod_updates([mod], FakeClient([_version("v-old", "1.0", "2026-01-01T00:00:00Z"), other]), mc_version=MC, loader="fabric")
    assert report.updates == []


def test_prefers_release_over_newer_beta() -> None:
    versions = [
        _version("beta", "2.0", "2026-03-01T00:00:00Z", type_="beta"),
        _version("release", "1.5", "2026-02-01T00:00:00Z"),
    ]
    assert _pick_latest(versions, MC, "fabric")["id"] == "release"


def test_skips_disabled_mods(tmp_path) -> None:
    mod, _digest = _mod(tmp_path, enabled=False)
    report = check_mod_updates([mod], FakeClient([]), mc_version=MC, loader="fabric")
    assert report.checked == 0
    assert report.updates == []


def test_handles_unmatched_and_http_error(tmp_path) -> None:
    mod, _digest = _mod(tmp_path)
    report = check_mod_updates([mod], FakeClient([], matched=False), mc_version=MC)
    assert report.matched == 0 and report.updates == []

    report = check_mod_updates([mod], FakeClient([], fail=True), mc_version=MC)
    assert report.error
    assert report.updates == []
