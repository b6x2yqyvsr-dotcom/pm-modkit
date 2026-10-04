"""「源」抽象：把 APK / 数据包 zip / 解包目录统一成一张资源包索引。

游戏里同一个逻辑包（例如 ``text``）可能同时存在于多个地方：

===========================  ====================================================
位置                          路径
===========================  ====================================================
APK 内置（StreamingAssets）   ``assets/AssetBundles/Android/text.assetbundle``
运行时下载缓存（设备数据）    ``files/UnityCache/Shared/text/<ver>/__data``
自建 CDN                      ``AssetBundles/<group>/Android/text.assetbundle``
散装                          ``<任意目录>/*.assetbundle``
===========================  ====================================================

本模块把这些全都扫成一个 ``BundleRef`` 列表，并保留「它原本长在哪」，
这样产物可以按原样写回去。

一个 ``Source`` 可以由**多个**路径组成 —— 实际用法就是
「同时打开 dp.apk 和 口蘑数据包.zip」。
"""

from __future__ import annotations

import json
import os
import re
import struct
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from . import paths

# ---------------------------------------------------------------- 布局识别

_RE_APK = re.compile(r"^assets/AssetBundles/(?P<plat>[^/]+)/(?P<name>[^/]+)\.assetbundle$")
_RE_CACHE = re.compile(
    r"^(?P<pre>.*?)UnityCache/Shared/(?P<name>[^/]+)/(?P<dir>[0-9a-fA-F]{32})/__data$"
)
_RE_CDN = re.compile(
    r"^(?P<pre>.*?)AssetBundles/(?P<group>[^/]+)/(?P<plat>[^/]+)/(?P<name>[^/]+)\.assetbundle$"
)
_RE_LOOSE = re.compile(r"^(?P<pre>.*?)(?P<name>[^/]+)\.assetbundle$")

#: 主选顺序：修改运行时生效的那份
LAYOUT_PRIORITY = {"cache": 0, "cdn": 1, "apk": 2, "loose": 3}


def platform_rank(ref: "BundleRef") -> int:
    """同一个包的多个平台副本里，哪一份该优先。

    **点开头的平台目录要排后面**：``assets/AssetBundles/.iOS/`` 是构建遗留，
    实测那份 ``text`` 是 **Unity 2019.4.5f1** 时代的老桩，``ZH_CN`` 里只有
    **430** 条莫蒂，而 ``Android`` 那份有 **564** 条。

    原来只按 layout 排序，``.iOS`` 因为字母序在前就成了默认选中项 ——
    结果用户在界面上看到的、改的都是那份**少了 134 只莫蒂的旧文件**，
    而真正会用到的 ``Android`` 那份反而排后面。这个坑踩过一次。
    """
    return 1 if "/." in ref.container else 0

LAYOUT_LABEL = {
    "cache": "下载缓存",
    "cdn": "CDN",
    "apk": "APK 内置",
    "loose": "散装",
}


@dataclass
class BundleRef:
    """指向某个源里的一个资源包。"""

    name: str
    layout: str  # cache | cdn | apk | loose
    container: str  # zip 内路径 / 目录相对路径
    member: int  # Source 里的容器序号（会随增删移动，别拿它当身份）
    version: int | None = None
    platform: str | None = None
    size: int | None = None
    #: 容器根路径 —— 稳定的身份标识。增删源之后 ``member`` 会错位，
    #: 但 ``root`` 不会，所以读取一律以它为准。
    root: str = ""

    @property
    def layout_label(self) -> str:
        return LAYOUT_LABEL.get(self.layout, self.layout)

    def same_as(self, other: "BundleRef | None") -> bool:
        if other is None:
            return False
        if self.root and other.root:
            return self.root == other.root and self.container == other.container
        return self.member == other.member and self.container == other.container

    def describe(self) -> str:
        bits = [self.layout_label]
        if self.platform:
            bits.append(self.platform)
        if self.version is not None:
            bits.append(f"v{self.version}")
        return " · ".join(bits)


@dataclass
class Container:
    """一个可读的容器。

    支持四种：

    ==========  ==========================================================
    ``zip``     zip 家族：``.zip`` ``.apk`` ``.apks`` ``.obb`` ``.jar``
                ``.unity3d`` ``.bundle`` ``.pak``
    ``tar``     tar 家族：``.tar`` ``.tar.gz`` ``.tgz`` ``.tar.bz2`` ``.tar.xz``
    ``dir``     已解包的目录
    ``file``    单个散装文件（一个 ``.assetbundle`` 或一个 ``__data``）
    ==========  ==========================================================
    """

    root: Path
    kind: str  # 'zip' | 'tar' | 'dir' | 'file'
    label: str
    _zip: zipfile.ZipFile | None = field(default=None, repr=False)
    _tar: tarfile.TarFile | None = field(default=None, repr=False)

    # -------------------------------------------------------------- 访问
    def zip(self) -> zipfile.ZipFile:
        if self._zip is None:
            self._zip = zipfile.ZipFile(self.root)
        return self._zip

    def tar(self) -> tarfile.TarFile:
        if self._tar is None:
            self._tar = tarfile.open(self.root)
        return self._tar

    def _tar_names(self) -> list[str]:
        out = []
        for m in self.tar().getmembers():
            if not m.isfile():
                continue
            n = m.name
            while n.startswith("./"):
                n = n[2:]
            out.append(n)
        return out

    @property
    def single_name(self) -> str:
        """``file`` 类容器的条目名（就是文件名本身）。"""
        return self.root.name

    def names(self) -> list[str]:
        if self.kind == "zip":
            return self.zip().namelist()
        if self.kind == "tar":
            return self._tar_names()
        if self.kind == "file":
            return [self.single_name]
        out: list[str] = []
        base = self.root
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__")]
            rel = os.path.relpath(dirpath, base)
            prefix = "" if rel == "." else rel.replace(os.sep, "/") + "/"
            for fn in filenames:
                out.append(prefix + fn)
        return out

    def infos(self) -> dict[str, int]:
        """路径 -> 大小。"""
        if self.kind == "zip":
            return {i.filename: i.file_size for i in self.zip().infolist()}
        if self.kind == "tar":
            return {m.name.lstrip("./"): m.size for m in self.tar().getmembers() if m.isfile()}
        if self.kind == "file":
            try:
                return {self.single_name: self.root.stat().st_size}
            except OSError:
                return {self.single_name: 0}
        out = {}
        for name in self.names():
            try:
                out[name] = (self.root / name).stat().st_size
            except OSError:
                out[name] = 0
        return out

    def read(self, name: str) -> bytes:
        if self.kind == "zip":
            return self.zip().read(name)
        if self.kind == "tar":
            f = self.tar().extractfile(name)
            if f is None:
                raise KeyError(name)
            return f.read()
        if self.kind == "file":
            return self.root.read_bytes()
        return (self.root / name).read_bytes()

    def exists(self, name: str) -> bool:
        if self.kind == "zip":
            try:
                self.zip().getinfo(name)
                return True
            except KeyError:
                return False
        if self.kind == "tar":
            try:
                return self.tar().getmember(name) is not None
            except KeyError:
                return False
        if self.kind == "file":
            return name == self.single_name
        return (self.root / name).is_file()

    def close(self) -> None:
        if self._zip is not None:
            self._zip.close()
            self._zip = None
        if self._tar is not None:
            self._tar.close()
            self._tar = None


# ---------------------------------------------------------------- Source


class Source:
    """一个或多个容器组成的资源来源。"""

    def __init__(self, containers: list[Container]):
        self.containers = containers
        self._refs: dict[str, list[BundleRef]] | None = None
        self._manifest: dict[str, dict] | None = None
        self._pmseed: dict[str, str] | None = None

    # ------------------------------------------------------------ 打开
    @classmethod
    def _make_container(cls, p: str | os.PathLike) -> Container:
        path = Path(p).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"路径不存在：{path}")
        if path.is_dir():
            return Container(path, "dir", path.name)
        if zipfile.is_zipfile(path):
            return Container(path, "zip", path.name)
        if tarfile.is_tarfile(path):
            return Container(path, "tar", path.name)
        # 散装：一个单独的 .assetbundle / .bundle / __data / 任何 UnityFS 文件
        if _looks_like_bundle(path):
            return Container(path, "file", path.name)
        raise ValueError(
            f"认不出这是什么文件：{path.name}\n"
            "支持：目录、zip（含 .apk/.apks/.obb/.jar/.unity3d/.bundle）、"
            "tar（含 .tar.gz/.tgz/.tar.bz2/.tar.xz）、单个 UnityFS 资源包"
        )

    @classmethod
    def open(cls, *paths_in: str | os.PathLike, label: str | None = None) -> "Source":
        containers = [cls._make_container(p) for p in paths_in]
        if not containers:
            raise ValueError("没有可用的路径")
        return cls(containers)

    # ------------------------------------------------------------ 增删
    def add(self, *paths_in: str | os.PathLike) -> list[str]:
        """往已有源里追加容器。

        ``Container`` 一旦建立就不改位置，``BundleRef.member`` 是下标，
        所以**只能往后加**，已加载的包和改动都不会失效。
        """
        added: list[str] = []
        have = {c.root for c in self.containers}
        for p in paths_in:
            try:
                c = self._make_container(p)
            except (FileNotFoundError, ValueError) as exc:
                raise ValueError(str(exc)) from exc
            if c.root in have:
                continue
            self.containers.append(c)
            have.add(c.root)
            added.append(c.label)
        if added:
            self._refs = None
            self._manifest = None
            self._pmseed = None
        return added

    def remove_member(self, index: int) -> str:
        """移除一个容器。

        ``member`` 下标会因此移动，所以**读取一律走 ``ref.root``**（见 ``read``），
        已加载的包和改动不会因为下标变化而失效。
        """
        c = self.containers[index]
        label = c.label
        c.close()
        del self.containers[index]
        self._refs = None
        self._manifest = None
        self._pmseed = None
        return label

    def container_by_root(self, root: str) -> Container | None:
        for c in self.containers:
            if str(c.root) == root:
                return c
        return None

    def index_of_root(self, root: str) -> int:
        for i, c in enumerate(self.containers):
            if str(c.root) == root:
                return i
        return -1

    def member_of(self, ref: BundleRef) -> Container:
        c = self.container_by_root(ref.root) if ref.root else None
        return c if c is not None else self.containers[ref.member]

    @property
    def label(self) -> str:
        return " + ".join(c.label for c in self.containers)

    def close(self) -> None:
        for c in self.containers:
            c.close()

    # ------------------------------------------------------------ 索引
    def _build(self) -> None:
        if self._refs is not None:
            return
        refs: dict[str, list[BundleRef]] = {}
        for mi, c in enumerate(self.containers):
            if c.kind == "file":
                # 散装的单个资源包：文件名推不出包名（可能叫 __data），
                # 按「__data 的上一级目录」或文件名来定
                ref = BundleRef(
                    name=_loose_bundle_name(c.root),
                    layout="loose",
                    container=c.single_name,
                    member=mi,
                    size=c.infos().get(c.single_name, 0),
                    root=str(c.root),
                )
                refs.setdefault(ref.name, []).append(ref)
                continue
            sizes = c.infos()
            for name in sizes:
                ref = self._classify(name, mi, sizes[name], str(c.root))
                if ref is not None:
                    refs.setdefault(ref.name, []).append(ref)
        for lst in refs.values():
            # 顺序：布局优先级 → 非点开头的平台优先 → 容器序号
            lst.sort(key=lambda r: (
                LAYOUT_PRIORITY.get(r.layout, 9),
                platform_rank(r),
                r.member,
            ))
        self._refs = refs

    def _classify(self, name: str, member: int, size: int, root: str) -> BundleRef | None:
        m = _RE_CACHE.match(name)
        if m:
            return BundleRef(
                name=m.group("name"),
                layout="cache",
                container=name,
                member=member,
                root=root,
                version=paths.parse_cache_dirname(m.group("dir")),
                size=size,
            )
        m = _RE_APK.match(name)
        if m:
            return BundleRef(
                name=m.group("name"),
                layout="apk",
                container=name,
                member=member,
                root=root,
                platform=m.group("plat").lstrip("."),
                size=size,
            )
        m = _RE_CDN.match(name)
        if m:
            return BundleRef(
                name=m.group("name"),
                layout="cdn",
                container=name,
                member=member,
                root=root,
                platform=m.group("plat").lstrip("."),
                size=size,
            )
        m = _RE_LOOSE.match(name)
        if m:
            return BundleRef(
                name=m.group("name"),
                layout="loose",
                container=name,
                member=member,
                root=root,
                size=size,
            )
        return None

    @property
    def refs(self) -> dict[str, list[BundleRef]]:
        self._build()
        assert self._refs is not None
        return self._refs

    def bundle_names(self) -> list[str]:
        return sorted(self.refs)

    def find(self, name: str) -> list[BundleRef]:
        return list(self.refs.get(name, []))

    def primary(self, name: str) -> BundleRef | None:
        lst = self.refs.get(name)
        return lst[0] if lst else None

    def __len__(self) -> int:
        return len(self.refs)

    # ------------------------------------------------------------ 读取
    def read(self, ref: BundleRef) -> bytes:
        """按 ``ref.root`` 定位容器，而不是用会漂移的 ``member`` 下标。"""
        c = self.container_by_root(ref.root) if ref.root else None
        if c is None:
            c = self.containers[ref.member]
        return c.read(ref.container)

    def read_path(self, path: str) -> bytes | None:
        for c in self.containers:
            if c.exists(path):
                return c.read(path)
        return None

    # ------------------------------------------------------------ 清单
    @property
    def manifest(self) -> dict[str, dict]:
        """合并所有能找到的 manifest（含 ``AssetBundle.dat`` 里的 JSON）。

        **按条目数从多到少合并** —— 这点很重要：加强版 APK 里同时存在

        * ``assets/AssetBundles/Android/manifest.json`` —— 156 条，真清单
        * ``assets/AssetBundles/.iOS/manifest.json``    —— 1 条，种子桩

        如果按「先读到的赢」，那个只有 1 条的桩会把真清单挤掉，
        于是 ``text`` 的 version 读出来是 1 而不是 1004，缓存目录名就全错了。
        """
        if self._manifest is None:
            found: list[dict] = []
            for c in self.containers:
                for name in c.names():
                    data = None
                    if name.endswith("manifest.json"):
                        try:
                            data = json.loads(c.read(name))
                        except Exception:  # noqa: BLE001
                            data = None
                    elif name.endswith("AssetBundle.dat"):
                        try:
                            data = _manifest_from_dat(c.read(name))
                        except Exception:  # noqa: BLE001
                            data = None
                    if isinstance(data, dict):
                        found.append(data)
            # 条目多的优先
            found.sort(
                key=lambda d: len([k for k, v in d.items() if isinstance(v, dict)]),
                reverse=True,
            )
            self._manifest = {}
            for data in found:
                self._merge_manifest(data)
        return self._manifest

    @property
    def pmseed(self) -> dict[str, str]:
        """读 ``assets/pmseed/index.txt``：包名 → UnityCache 目录名。

        「加强版」那类**自包含 APK** 会把全部资源包塞进
        ``assets/AssetBundles/<平台>/``，再放一份 ``assets/pmseed/index.txt``
        告诉游戏首次运行时怎么把它们播种到
        ``files/UnityCache/Shared/<包名>/<目录名>/``。

        这张表比从 manifest 推算 version 更可靠 —— 它就是游戏真正认的那个
        目录名，直接抄来用即可。
        """
        if self._pmseed is None:
            self._pmseed = {}
            for c in self.containers:
                for name in c.names():
                    if not name.endswith("pmseed/index.txt"):
                        continue
                    try:
                        text = c.read(name).decode("utf-8", errors="replace")
                    except Exception:  # noqa: BLE001
                        continue
                    for line in text.splitlines():
                        parts = line.split()
                        if len(parts) >= 2 and len(parts[1]) == 32:
                            self._pmseed[parts[0]] = parts[1]
        return self._pmseed

    def cache_dirname(self, name: str) -> str | None:
        """这个包在 UnityCache 里应该用哪个目录名。"""
        got = self.pmseed.get(name)
        if got:
            return got
        v = self.version_of(name)
        return paths.make_cache_dirname(v) if v is not None else None

    @property
    def is_self_contained(self) -> bool:
        """是不是「自带全部资源」的 APK（加强版那种）。

        判据：APK 内置的包数量占绝大多数。普通 APK 里只有 ``text`` 一个，
        这种则有上百个。
        """
        apk = sum(1 for lst in self.refs.values() for r in lst if r.layout == "apk")
        return apk >= 20

    def _merge_manifest(self, data) -> None:
        assert self._manifest is not None
        if not isinstance(data, dict):
            return
        for k, v in data.items():
            if isinstance(v, dict) and ("id" in v or "crc" in v):
                self._manifest.setdefault(k, v)

    def version_of(self, name: str) -> int | None:
        """先看 ref 自带，再看 manifest。"""
        for ref in self.find(name):
            if ref.version is not None:
                return ref.version
        meta = self.manifest.get(name)
        if meta and meta.get("version") is not None:
            try:
                return int(meta["version"])
            except (TypeError, ValueError):
                return None
        return None

    def manifest_version(self) -> int | None:
        meta = self.manifest
        v = meta.get("version")
        try:
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None


# ---------------------------------------------------------------- helpers


#: 当作「单个资源包」的扩展名（真实判断还要看 magic）
_BUNDLE_EXTS = {".assetbundle", ".bundle", ".ab", ".unity3d", ".unityfs"}


def _looks_like_bundle(path: Path) -> bool:
    """判断一个散装文件是不是 Unity 资源包。

    优先看 magic（``UnityFS`` / ``UnityWeb``），其次看扩展名，
    另外 ``__data`` 是 Unity 下载缓存里的包正文，直接认。
    """
    if path.name == "__data":
        return True
    if path.suffix.lower() in _BUNDLE_EXTS:
        return True
    try:
        with open(path, "rb") as f:
            head = f.read(8)
    except OSError:
        return False
    return head.startswith(b"UnityFS") or head.startswith(b"UnityWeb")


def _loose_bundle_name(path: Path) -> str:
    """从一个散装文件推出它的逻辑包名。

    常见两种情况::

        spdata.assetbundle                      -> spdata
        .../UnityCache/Shared/text/<ver>/__data  -> text
    """
    name = path.name
    if name == "__data":
        parent = path.parent  # <12 个 0 + version 的 hex>
        if paths.parse_cache_dirname(parent.name) is not None and parent.parent.name:
            return parent.parent.name
        return parent.name or name
    for suf in sorted(_BUNDLE_EXTS, key=len, reverse=True):
        if name.lower().endswith(suf):
            return name[: -len(suf)] or name
    return path.stem or name


def _manifest_from_dat(raw: bytes) -> dict | None:
    """``AssetBundle.dat`` 是 .NET BinaryFormatter 流，里面嵌了一整段 manifest JSON。

    不解析 BinaryFormatter —— 直接定位第一个 ``{"``，再从后往前找一个能
    ``json.loads`` 成功的前缀即可。
    """
    i = raw.find(b'{"')
    if i < 0:
        return None
    seg = raw[i:]
    for end in range(len(seg), 2, -1):
        if seg[end - 1 : end] != b"}":
            continue
        try:
            obj = json.loads(seg[:end])
        except Exception:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def summarize(refs: dict[str, list[BundleRef]]) -> str:
    from collections import Counter

    c = Counter(r.layout for lst in refs.values() for r in lst)
    parts = [f"{LAYOUT_LABEL.get(k, k)} {v}" for k, v in c.most_common()]
    return f"{len(refs)} 个包（" + "，".join(parts) + "）"
