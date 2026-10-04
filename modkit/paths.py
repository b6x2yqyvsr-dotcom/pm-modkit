"""UnityCache 目录名与 ``__info`` 的编解码。

游戏把下载到的 AssetBundle 放在设备上::

    /sdcard/Android/data/com.conspiracyrick.pocketmortys/files/
        UnityCache/
            Shared/
                __info                        缓存级信息
                <包名>/                       例如 spdata
                    <目录名>/                 = 12 个 '0' + 小端 4 字节 version 的 hex
                        __data                AssetBundle 原文（UnityFS 开头）
                        __info                23 字节文本

``__info`` 的确切内容是（实测自客户端数据包）::

    Shared/__info      b'1792219309\n1\n1779259310\n'
    <包名>/__info      b'-1\n1779259343\n1\n__data\n'

目录名举例::

    version 1004 -> ec 03 00 00 -> '000000000000000000000000ec030000'
    version  219 -> db 00 00 00 -> '000000000000000000000000db000000'

替换资源时**必须保持目录名不变** —— 它是 Unity 认定「这份缓存还新鲜」的
依据，改名等于换了一个包，游戏会重新下载。
"""

from __future__ import annotations

import struct
import time

#: 目录名前缀长度：12 个零字节 = 24 个 '0' 字符
_ZERO_PREFIX = "0" * 24


def make_cache_dirname(version: int) -> str:
    """由 manifest 里的 version 生成 UnityCache 的一级目录名。"""
    return _ZERO_PREFIX + struct.pack("<I", version & 0xFFFFFFFF).hex()


def parse_cache_dirname(dirname: str) -> int | None:
    """从目录名反解 version；不是这个格式就返回 None。"""
    if len(dirname) != 32:
        return None
    try:
        raw = bytes.fromhex(dirname)
    except ValueError:
        return None
    if raw[:12] != b"\x00" * 12:
        return None
    return struct.unpack("<I", raw[12:])[0]


def make_bundle_info(timestamp: float | None = None) -> bytes:
    """生成单个包的 ``__info``（23 字节）。"""
    ts = int(timestamp if timestamp is not None else time.time())
    return f"-1\n{ts}\n1\n__data\n".encode()


def make_shared_info(timestamp: float | None = None) -> bytes:
    """生成 ``Shared/__info``。"""
    ts = int(timestamp if timestamp is not None else time.time())
    return f"1792219309\n1\n{ts}\n".encode()


#: 设备上缓存根目录的相对路径（相对 ``files/``）
CACHE_ROOT = "UnityCache/Shared"

#: 默认包名
DEFAULT_PACKAGE = "com.conspiracyrick.pocketmortys"

#: 设备上放置缓存的位置（adb shell 路径）
DEVICE_FILES_DIR = f"/sdcard/Android/data/{DEFAULT_PACKAGE}/files"

#: APK 内置包所在目录
APK_BUNDLE_DIR = "assets/AssetBundles"
