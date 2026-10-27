"""离线账户：UUID 规则与账户存储。"""

from __future__ import annotations

import pytest

from pcl.core.errors import ConfigError
from pcl.launch.auth import Account, AccountStore, compact_uuid, offline_uuid, validate_name


def test_offline_uuid_matches_java() -> None:
    # 与 Java UUID.nameUUIDFromBytes("OfflinePlayer:<name>") 的结果一致
    assert offline_uuid("Notch") == "b50ad385-829d-3141-a216-7e7d7539ba7f"
    assert offline_uuid("Player") == "a01e3843-e521-3998-958a-f459800e4d11"
    assert compact_uuid(offline_uuid("Player")) == "a01e3843e5213998958af459800e4d11"


def test_account_defaults_uuid() -> None:
    account = Account(name="Test_User")
    assert account.uuid == offline_uuid("Test_User")
    assert account.display.endswith("（离线）")


def test_validate_name() -> None:
    assert validate_name("  Steve_01  ") == "Steve_01"
    with pytest.raises(ConfigError):
        validate_name("")
    with pytest.raises(ConfigError):
        validate_name("名字里有中文")
    with pytest.raises(ConfigError):
        validate_name("a" * 17)


def test_account_store_roundtrip(tmp_path) -> None:
    path = tmp_path / "accounts.json"
    store = AccountStore(path)
    store.load()
    assert store.accounts == []

    account = store.add("Steve")
    assert account.name == "Steve"
    assert store.selected == "Steve"
    assert path.is_file()

    store.add("Alex")
    assert len(store.accounts) == 2
    assert store.selected == "Alex"

    reloaded = AccountStore(path)
    reloaded.load()
    assert [item.name for item in reloaded.accounts] == ["Steve", "Alex"]
    assert reloaded.selected == "Alex"
    assert reloaded.current().name == "Alex"

    assert reloaded.remove("Alex") is True
    assert reloaded.selected == "Steve"
    assert reloaded.remove("Nobody") is False


def test_ensure_default_account(tmp_path) -> None:
    store = AccountStore(tmp_path / "accounts.json")
    store.load()
    account = store.ensure_default()
    assert account.name == "Player"
    assert store.ensure_default() is account
