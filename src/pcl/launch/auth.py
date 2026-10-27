"""离线账户：UUID 生成规则与本地账户存储。

离线 UUID 与 Java 版 ``UUID.nameUUIDFromBytes`` 完全一致：
对 ``"OfflinePlayer:<name>"`` 的 UTF-8 字节做 MD5，再把版本位设为 3、变体位设为 IETF，
这样与 HMCL / PCL 等其他启动器以及服务端的离线 UUID 保持兼容。
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pcl.core.errors import ConfigError
from pcl.core.logging import get_logger

log = get_logger(__name__)

OFFLINE_PREFIX = "OfflinePlayer:"
ACCOUNT_SCHEMA_VERSION = 1


def offline_uuid(name: str) -> str:
    """按 Java 语义生成离线 UUID（带连字符）。"""
    digest = bytearray(hashlib.md5(f"{OFFLINE_PREFIX}{name}".encode("utf-8")).digest())
    digest[6] = (digest[6] & 0x0F) | 0x30  # version 3
    digest[8] = (digest[8] & 0x3F) | 0x80  # IETF variant
    return str(uuid.UUID(bytes=bytes(digest)))


def compact_uuid(value: str) -> str:
    """去掉连字符（启动参数要求无连字符形式）。"""
    return value.replace("-", "")


@dataclass
class Account:
    """一个离线账户。"""

    name: str
    uuid: str = ""
    kind: str = "offline"
    access_token: str = "0" * 32
    created_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if not self.uuid:
            self.uuid = offline_uuid(self.name)

    @property
    def display(self) -> str:
        return f"{self.name}（离线）" if self.kind == "offline" else self.name

    @property
    def uuid_compact(self) -> str:
        return compact_uuid(self.uuid)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "uuid": self.uuid,
            "kind": self.kind,
            "access_token": self.access_token,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Account:
        return cls(
            name=str(data.get("name", "")),
            uuid=str(data.get("uuid", "")),
            kind=str(data.get("kind", "offline")),
            access_token=str(data.get("access_token", "0" * 32)),
            created_at=float(data.get("created_at", time.time())),
        )


def validate_name(name: str) -> str:
    """校验离线用户名（仅允许字母数字与下划线，长度 1-16）。"""
    cleaned = (name or "").strip()
    if not cleaned:
        raise ConfigError("用户名不能为空。")
    if len(cleaned) > 16:
        raise ConfigError("用户名最长 16 个字符。")
    if not all(ch.isalnum() or ch == "_" for ch in cleaned):
        raise ConfigError("用户名只能包含字母、数字和下划线。")
    return cleaned


class AccountStore:
    """账户文件读写（``accounts.json``）。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.accounts: list[Account] = []
        self.selected: str = ""

    def load(self) -> list[Account]:
        if not self.path.is_file():
            self.accounts = []
            return self.accounts
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            log.warning("读取账户文件失败：%s", error)
            self.accounts = []
            return self.accounts

        items = raw.get("accounts") if isinstance(raw, dict) else raw
        self.accounts = [
            Account.from_dict(item) for item in (items or []) if isinstance(item, dict) and item.get("name")
        ]
        if isinstance(raw, dict):
            self.selected = str(raw.get("selected", "") or "")
        return self.accounts

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": ACCOUNT_SCHEMA_VERSION,
            "selected": self.selected,
            "accounts": [account.to_dict() for account in self.accounts],
        }
        temp = self.path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)

    def find(self, name: str) -> Account | None:
        for account in self.accounts:
            if account.name.lower() == name.lower():
                return account
        return None

    def add(self, name: str) -> Account:
        """添加离线账户；同名则复用已有条目。"""
        cleaned = validate_name(name)
        existing = self.find(cleaned)
        if existing is not None:
            self.selected = existing.name
            return existing
        account = Account(name=cleaned)
        self.accounts.append(account)
        self.selected = account.name
        self.save()
        return account

    def remove(self, name: str) -> bool:
        account = self.find(name)
        if account is None:
            return False
        self.accounts.remove(account)
        if self.selected.lower() == name.lower():
            self.selected = self.accounts[0].name if self.accounts else ""
        self.save()
        return True

    def current(self) -> Account | None:
        if self.selected:
            found = self.find(self.selected)
            if found is not None:
                return found
        return self.accounts[0] if self.accounts else None

    def ensure_default(self) -> Account:
        """保证至少存在一个账户（默认 ``Player``），便于首次启动。"""
        account = self.current()
        if account is not None:
            return account
        return self.add("Player")
