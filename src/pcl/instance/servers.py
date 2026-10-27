"""服务器列表（``servers.dat``）读写。

格式与原版一致：gzip 压缩的 NBT，根 compound 下 ``servers`` 为 compound 列表，
每项包含 ``name`` / ``ip``（可带端口），可选 ``icon``（base64 PNG）与 ``hidden``。
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from pcl.core.errors import InstanceError
from pcl.core.logging import get_logger
from pcl.core.nbt import NbtList, load_nbt_file, save_nbt_file

log = get_logger(__name__)

MAX_ICON_BYTES = 512 * 1024


@dataclass
class ServerEntry:
    """服务器条目。"""

    name: str
    ip: str
    icon: str = ""  # base64 编码的 PNG
    hidden: bool = False

    @property
    def host(self) -> str:
        return self.ip.rsplit(":", 1)[0] if ":" in self.ip else self.ip

    @property
    def port(self) -> int:
        if ":" in self.ip:
            try:
                return int(self.ip.rsplit(":", 1)[1])
            except ValueError:
                return 25565
        return 25565

    def icon_bytes(self) -> bytes | None:
        if not self.icon:
            return None
        try:
            raw = base64.b64decode(self.icon, validate=False)
        except (ValueError, TypeError):
            return None
        return raw if 0 < len(raw) <= MAX_ICON_BYTES else None

    def set_icon_bytes(self, data: bytes | None) -> None:
        self.icon = base64.b64encode(data).decode("ascii") if data else ""

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"name": self.name, "ip": self.ip}
        if self.icon:
            result["icon"] = self.icon
        if self.hidden:
            result["hidden"] = True
        return result


class ServerList:
    """某个实例的服务器列表。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.entries: list[ServerEntry] = []

    def load(self) -> list[ServerEntry]:
        self.entries = []
        if not self.path.is_file():
            return self.entries
        try:
            root = load_nbt_file(self.path)
        except (OSError, ValueError) as error:
            log.warning("读取 servers.dat 失败：%s", error)
            return self.entries

        if not isinstance(root, Mapping):
            return self.entries
        raw_servers = root.get("servers")
        if not isinstance(raw_servers, (list, NbtList)):
            return self.entries

        for item in raw_servers:
            if not isinstance(item, Mapping):
                continue
            name = str(item.get("name", "") or "")
            ip = str(item.get("ip", "") or "")
            if not ip:
                continue
            self.entries.append(
                ServerEntry(
                    name=name or ip,
                    ip=ip,
                    icon=str(item.get("icon", "") or ""),
                    hidden=bool(item.get("hidden", False)),
                )
            )
        return self.entries

    def save(self) -> None:
        root = {"servers": NbtList([entry.to_dict() for entry in self.entries])}
        try:
            save_nbt_file(self.path, root)
        except OSError as error:
            raise InstanceError("保存服务器列表失败。", detail=str(error)) from error

    def add(self, name: str, ip: str) -> ServerEntry:
        address = (ip or "").strip()
        if not address:
            raise InstanceError("服务器地址不能为空。")
        if ":" in address:
            host, _, port = address.rpartition(":")
            if host and port.isdigit() and 0 < int(port) < 65536:
                address = f"{host}:{int(port)}"
        entry = ServerEntry(name=(name or address).strip() or address, ip=address)
        self.entries.append(entry)
        self.save()
        log.info("已添加服务器 %s（%s）", entry.name, entry.ip)
        return entry

    def remove(self, entry: ServerEntry) -> None:
        if entry in self.entries:
            self.entries.remove(entry)
            self.save()

    def update(self, entry: ServerEntry) -> None:
        index = self.entries.index(entry)
        self.save()
        log.debug("已更新服务器 %s（索引 %d）", entry.name, index)
