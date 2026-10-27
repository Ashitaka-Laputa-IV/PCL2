"""最小可用的 NBT（Named Binary Tag）读写。

只实现启动器需要的部分：读取任意 NBT 文件、写入以 compound 为根的 NBT（可选 gzip），
用于 ``servers.dat`` 等原版二进制配置，避免引入第三方 NBT 依赖。
"""

from __future__ import annotations

import gzip
import struct
from pathlib import Path
from typing import Any, BinaryIO

TAG_END = 0
TAG_BYTE = 1
TAG_SHORT = 2
TAG_INT = 3
TAG_LONG = 4
TAG_FLOAT = 5
TAG_DOUBLE = 6
TAG_BYTE_ARRAY = 7
TAG_STRING = 8
TAG_LIST = 9
TAG_COMPOUND = 10
TAG_INT_ARRAY = 11
TAG_LONG_ARRAY = 12


class NbtError(ValueError):
    """NBT 数据格式错误。"""


class NbtList(list):
    """带元素类型的 NBT 列表（写入时需要）。"""

    def __init__(self, items=(), tag_type: int | None = None) -> None:
        super().__init__(items)
        self.tag_type = tag_type


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    data = stream.read(size)
    if len(data) != size:
        raise NbtError("NBT 数据意外结束")
    return data


def _read_string(stream: BinaryIO) -> str:
    (length,) = struct.unpack(">H", _read_exact(stream, 2))
    return _read_exact(stream, length).decode("utf-8", errors="replace")


def _read_payload(stream: BinaryIO, tag: int) -> Any:
    if tag == TAG_BYTE:
        return struct.unpack(">b", _read_exact(stream, 1))[0]
    if tag == TAG_SHORT:
        return struct.unpack(">h", _read_exact(stream, 2))[0]
    if tag == TAG_INT:
        return struct.unpack(">i", _read_exact(stream, 4))[0]
    if tag == TAG_LONG:
        return struct.unpack(">q", _read_exact(stream, 8))[0]
    if tag == TAG_FLOAT:
        return struct.unpack(">f", _read_exact(stream, 4))[0]
    if tag == TAG_DOUBLE:
        return struct.unpack(">d", _read_exact(stream, 8))[0]
    if tag == TAG_BYTE_ARRAY:
        (length,) = struct.unpack(">i", _read_exact(stream, 4))
        return _read_exact(stream, length)
    if tag == TAG_STRING:
        return _read_string(stream)
    if tag == TAG_LIST:
        item_tag = struct.unpack(">b", _read_exact(stream, 1))[0]
        (length,) = struct.unpack(">i", _read_exact(stream, 4))
        items = [_read_payload(stream, item_tag) for _ in range(max(0, length))]
        return NbtList(items, item_tag)
    if tag == TAG_COMPOUND:
        result: dict[str, Any] = {}
        while True:
            child_tag = struct.unpack(">b", _read_exact(stream, 1))[0]
            if child_tag == TAG_END:
                break
            name = _read_string(stream)
            result[name] = _read_payload(stream, child_tag)
        return result
    if tag == TAG_INT_ARRAY:
        (length,) = struct.unpack(">i", _read_exact(stream, 4))
        return list(struct.unpack(f">{length}i", _read_exact(stream, 4 * length)))
    if tag == TAG_LONG_ARRAY:
        (length,) = struct.unpack(">i", _read_exact(stream, 4))
        return list(struct.unpack(f">{length}q", _read_exact(stream, 8 * length)))
    raise NbtError(f"未知的 NBT 标签类型：{tag}")


def read_nbt(data: bytes) -> tuple[str, Any]:
    """解析裸 NBT 数据，返回 ``(根名称, 根值)``。"""
    import io

    stream = io.BytesIO(data)
    tag = struct.unpack(">b", _read_exact(stream, 1))[0]
    if tag != TAG_COMPOUND:
        raise NbtError("NBT 根标签不是 compound")
    name = _read_string(stream)
    value = _read_payload(stream, TAG_COMPOUND)
    return name, value


def load_nbt_file(path: Path) -> Any:
    """读取（可能 gzip 压缩的）NBT 文件。"""
    data = path.read_bytes()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    _name, value = read_nbt(data)
    return value


def _infer_tag(value: Any) -> int:
    if isinstance(value, bool):
        return TAG_BYTE
    if isinstance(value, NbtList):
        return TAG_LIST
    if isinstance(value, dict):
        return TAG_COMPOUND
    if isinstance(value, (bytes, bytearray)):
        return TAG_BYTE_ARRAY
    if isinstance(value, str):
        return TAG_STRING
    if isinstance(value, float):
        return TAG_DOUBLE
    if isinstance(value, int):
        return TAG_INT
    if isinstance(value, list):
        return TAG_LIST
    raise NbtError(f"无法推断 NBT 类型：{type(value)!r}")


def _write_string(stream: BinaryIO, value: str) -> None:
    encoded = value.encode("utf-8")
    if len(encoded) > 65535:
        encoded = encoded[:65535]
    stream.write(struct.pack(">H", len(encoded)))
    stream.write(encoded)


def _write_payload(stream: BinaryIO, value: Any) -> None:
    if isinstance(value, bool):
        stream.write(struct.pack(">b", 1 if value else 0))
    elif isinstance(value, dict):
        for key, child in value.items():
            stream.write(struct.pack(">b", _infer_tag(child)))
            _write_string(stream, str(key))
            _write_payload(stream, child)
        stream.write(struct.pack(">b", TAG_END))
    elif isinstance(value, NbtList) or isinstance(value, list):
        items = list(value)
        tag_type = getattr(value, "tag_type", None)
        if tag_type is None:
            tag_type = _infer_tag(items[0]) if items else TAG_BYTE
        stream.write(struct.pack(">b", tag_type))
        stream.write(struct.pack(">i", len(items)))
        for item in items:
            _write_payload(stream, item)
    elif isinstance(value, str):
        _write_string(stream, value)
    elif isinstance(value, (bytes, bytearray)):
        stream.write(struct.pack(">i", len(value)))
        stream.write(bytes(value))
    elif isinstance(value, float):
        stream.write(struct.pack(">d", value))
    elif isinstance(value, int):
        if -(2**31) <= value < 2**31:
            stream.write(struct.pack(">i", value))
        else:
            stream.write(struct.pack(">q", value))
    else:
        raise NbtError(f"无法写入的 NBT 值：{type(value)!r}")


def dump_nbt(root: Any, name: str = "", *, compress: bool = True) -> bytes:
    """把 dict 序列化为 NBT（默认 gzip）。"""
    import io

    stream = io.BytesIO()
    stream.write(struct.pack(">b", TAG_COMPOUND))
    _write_string(stream, name)
    _write_payload(stream, root)
    data = stream.getvalue()
    return gzip.compress(data) if compress else data


def save_nbt_file(path: Path, root: Any, name: str = "", *, compress: bool = True) -> None:
    """写入 NBT 文件（原子替换）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_bytes(dump_nbt(root, name, compress=compress))
    temp.replace(path)
