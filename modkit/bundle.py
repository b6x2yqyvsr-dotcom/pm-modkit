"""单个 AssetBundle 的读取、改动与回写。

底层是 UnityPy。已验证的闭环::

    读包 -> UnityPy.load -> 改对象（TextAsset.m_Script / Texture2D.image）
         -> obj.save() -> env.file.save(packer='original') -> 重新 load 数据正确

实测例：把 ``appdata`` 里的 ``GachaDefault.drop_rates`` 从 ``[80,12,6,2]``
改成 ``[100,0,0,0]``，回写后重新解包读出来就是 ``[100,0,0,0]``。
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any

import UnityPy

from .source import BundleRef

#: 回写时尝试的打包方式，第一个成功的就用
PACKERS = ("original", "lz4", "none")

# ---------------------------------------------------------------- 资源类型分类
#
# 分三类处理，覆盖 Unity 资源包里几乎所有对象：
#
#   图片   直接导出/导入 PNG（Cubemap 实测可换，回环正确）
#   文本   m_Script 是字符串的（TextAsset 及其全部子类）
#   类型树 其余一律走 typetree JSON —— Material / AnimationClip / Shader /
#          GameObject / Transform / SpriteRenderer / Animator / MonoBehaviour …
#          实测这些都能读出来、改完回写、重新解包内容一致

#: 按图片处理。Sprite 只是个「引用 + 裁切框」，自身没有像素，只能导出
IMAGE_TYPES = {"Texture2D", "Cubemap", "RenderTexture", "Texture2DArray", "Sprite"}
#: 只能看/导出、不能替换的图片类型
IMAGE_READONLY = {"Sprite"}

#: m_Script 是文本的类型（UnityPy 里 TextAsset 的子类，类型名各不相同）
TEXT_TYPES = {
    "TextAsset",
    "MonoScript",
    "ShaderInclude",
    "PackageManifest",
    "AssemblyDefinitionAsset",
    "AssemblyDefinitionReferenceAsset",
    "AssemblyJsonAsset",
    "CGProgram",
    "RuleSetFileAsset",
}

AUDIO_TYPES = {"AudioClip"}

#: 不该去动它的类型（每个包里都有一个 AssetBundle 对象，是包自身的元数据）
OTHER_TYPES = {"AssetBundle"}

#: 可以安全预览/替换的资源类型
REPLACEABLE = set(IMAGE_TYPES) | set(TEXT_TYPES) | set(AUDIO_TYPES) | {"MonoBehaviour"}


@dataclass
class AssetEntry:
    """包里一个资源对象的简介。"""

    path_id: int
    type: str
    name: str
    size: int = 0

    @property
    def display(self) -> str:
        return self.name or f"#{self.path_id}"

    @property
    def replaceable(self) -> bool:
        return self.type in REPLACEABLE

    def search_key(self) -> str:
        return f"{self.type} {self.name} {self.path_id}".lower()


@dataclass
class Bundle:
    """一个已加载（或待加载）的资源包。"""

    ref: BundleRef
    raw: bytes
    _env: Any = field(default=None, repr=False)
    _assets: list[AssetEntry] | None = field(default=None, repr=False)
    _readers: dict[int, Any] = field(default_factory=dict, repr=False)
    dirty: set[int] = field(default_factory=set, repr=False)
    error: str | None = None
    #: 来自 .pmmod 的成品包：字节已经是对的结果，不要再重新序列化
    prebuilt: bool = False
    #: 最近一次失败的原因（例如打包版缺原生扩展）
    last_error: str = ""
    _cache: dict[int, Any] = field(default_factory=dict, repr=False)
    #: 已改但「读回来还是旧值」的内容，读取接口优先用这些
    _pending_text: dict[int, str] = field(default_factory=dict, repr=False)
    _pending_image: dict[int, Any] = field(default_factory=dict, repr=False)
    _pending_tree: dict[int, dict] = field(default_factory=dict, repr=False)

    # ------------------------------------------------------------ 加载
    def _ensure(self) -> None:
        if self._env is not None or self.error:
            return
        try:
            self._env = UnityPy.load(io.BytesIO(self.raw))
        except Exception as exc:  # noqa: BLE001
            self.error = f"{type(exc).__name__}: {exc}"

    @property
    def name(self) -> str:
        return self.ref.name

    @property
    def loaded(self) -> bool:
        return self._env is not None

    @property
    def ok(self) -> bool:
        return self.error is None

    # ------------------------------------------------------------ 资源列表
    @property
    def assets(self) -> list[AssetEntry]:
        if self._assets is None:
            self._ensure()
            out: list[AssetEntry] = []
            if self._env is not None:
                for obj in self._env.objects:
                    # peek_name 只解名字字段，比整个 read() 快得多 ——
                    # 有些包有上万个对象，全解析要好几秒
                    nm = ""
                    try:
                        nm = obj.peek_name() or ""
                    except Exception:  # noqa: BLE001
                        try:
                            nm = getattr(obj.read(), "m_Name", "") or ""
                        except Exception:  # noqa: BLE001
                            nm = ""
                    out.append(
                        AssetEntry(
                            path_id=obj.path_id,
                            type=obj.type.name,
                            name=nm,
                            size=getattr(obj, "byte_size", 0) or 0,
                        )
                    )
                    self._readers[obj.path_id] = obj
            out.sort(key=lambda e: (e.type, e.name.lower(), e.path_id))
            self._assets = out
        return self._assets

    def type_counts(self) -> dict[str, int]:
        from collections import Counter

        return dict(Counter(a.type for a in self.assets).most_common())

    # ------------------------------------------------------------ 取对象
    def reader(self, entry: AssetEntry):
        self.assets  # 确保 readers 建好
        return self._readers.get(entry.path_id)

    def get(self, entry: AssetEntry):
        """读出结构化对象（只读用途时可缓存）。"""
        if entry.path_id in self._cache:
            return self._cache[entry.path_id]
        r = self.reader(entry)
        if r is None:
            return None
        obj = r.read()
        self._cache[entry.path_id] = obj
        return obj

    def object_reader(self, entry: AssetEntry):
        return self.reader(entry)

    # ------------------------------------------------------------ 改动
    #
    # 注意：UnityPy 的 ``ObjectReader`` 在 ``save_typetree`` 之后，
    # 再从 ``read()`` 读回来的仍是**旧值**（reader 与 set_raw_data 不同步），
    # 但 ``env.file.save()`` 写出的字节是对的（实测）。
    # 所以这里显式记一份「待生效值」，所有读取接口优先看它，
    # 不依赖 UnityPy 的内部缓存语义。

    def _mark(self, entry: AssetEntry) -> None:
        self.dirty.add(entry.path_id)

    def modify_text(self, entry: AssetEntry, text: str) -> None:
        """改 TextAsset（游戏里的数据表/本地化都是 JSON 文本）。"""
        obj = self.get(entry)
        if obj is None:
            raise ValueError("读不到对象")
        obj.m_Script = text
        obj.save()
        self._pending_text[entry.path_id] = text
        self._mark(entry)

    def modify_image(self, entry: AssetEntry, image) -> None:
        """改 Texture2D（PIL Image，尺寸需与原始一致）。"""
        obj = self.get(entry)
        if obj is None:
            raise ValueError("读不到对象")
        obj.image = image
        obj.save()
        self._pending_image[entry.path_id] = image
        self._mark(entry)

    def modify_typetree(self, entry: AssetEntry, tree: dict) -> None:
        """通用兜底：直接改 MonoBehaviour 等的类型树。"""
        r = self.reader(entry)
        if r is None:
            raise ValueError("读不到对象")
        r.save_typetree(tree)
        self._pending_tree[entry.path_id] = tree
        self._mark(entry)

    def read_typetree(self, entry: AssetEntry) -> dict | None:
        if entry.path_id in self._pending_tree:
            return self._pending_tree[entry.path_id]
        r = self.reader(entry)
        if r is None:
            return None
        try:
            return r.read_typetree()
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------ 还原
    def pending_snapshot(self) -> dict[int, tuple[str, Any]]:
        """当前所有待生效改动：path_id -> ('text'|'image'|'tree', 值)。"""
        out: dict[int, tuple[str, Any]] = {}
        for k, v in self._pending_text.items():
            out[k] = ("text", v)
        for k, v in self._pending_image.items():
            out[k] = ("image", v)
        for k, v in self._pending_tree.items():
            out[k] = ("tree", v)
        return out

    def _reset_to_raw(self) -> None:
        self._env = None
        self._assets = None
        self._readers = {}
        self._cache = {}
        self._pending_text = {}
        self._pending_image = {}
        self._pending_tree = {}
        self.dirty = set()
        self.error = None

    def _replay(self, snap: dict[int, tuple[str, Any]]) -> None:
        for pid, (kind, val) in snap.items():
            entry = next((a for a in self.assets if a.path_id == pid), None)
            if entry is None:
                continue
            if kind == "text":
                self.modify_text(entry, val)
            elif kind == "image":
                self.modify_image(entry, val)
            elif kind == "tree":
                self.modify_typetree(entry, val)

    def revert_asset(self, path_id: int) -> None:
        """还原单个资源：重载干净副本，再把其它改动重放回去。"""
        snap = self.pending_snapshot()
        snap.pop(path_id, None)
        self._reset_to_raw()
        self._replay(snap)

    def revert_all(self) -> None:
        self._reset_to_raw()

    # ------------------------------------------------------------ 回写
    def save(self, packer: str | None = None) -> bytes:
        """把当前状态序列化回 bundle 字节。未改动则原样返回。"""
        if self.prebuilt or not self.dirty:
            return self.raw
        self._ensure()
        if self._env is None:
            raise RuntimeError(self.error or "包无法解析")
        last: Exception | None = None
        cands = (packer,) if packer else PACKERS
        for p in cands:
            try:
                data = self._env.file.save(packer=p)
                if data:
                    return bytes(data)
            except Exception as exc:  # noqa: BLE001
                last = exc
        raise RuntimeError(f"回写失败：{last}")

    # ------------------------------------------------------------ 预览
    def preview_image(self, entry: AssetEntry):
        """返回 PIL Image 用于缩略图；Sprite 会取它引用的贴图。"""
        from PIL import Image

        pending = self._pending_image.get(entry.path_id)
        if pending is not None:
            return pending
        obj = self.get(entry)
        if obj is None:
            self.last_error = "读不到这个对象"
            return None
        try:
            img = obj.image
        except Exception as exc:  # noqa: BLE001
            # 别把原因吞掉 —— 打包版缺原生扩展时就靠这条信息定位
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None
        if img is None:
            return None
        if not isinstance(img, Image.Image):
            img = Image.fromarray(img)
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA")
        return img

    def preview_text(self, entry: AssetEntry, limit: int | None = None) -> str:
        """读出 TextAsset 的文本。

        **默认不截断**（``limit=None``）—— 这点很关键：游戏的数据表动辄
        几十万字符（``ZH_CN`` 30 万、``MortyAttacksInfo`` 59 万），导出和
        打模组包都必须拿全文，截断了就是把资源改坏。
        只有**界面显示**才传 ``limit``。
        """
        pending = self._pending_text.get(entry.path_id)
        if pending is not None:
            return pending if limit is None else pending[:limit]
        obj = self.get(entry)
        if obj is None:
            return ""
        raw = getattr(obj, "m_Script", b"")
        if isinstance(raw, (bytes, bytearray)):
            data = bytes(raw)
            return data.decode("utf-8", errors="replace") if limit is None else \
                data[:limit].decode("utf-8", errors="replace")
        return str(raw) if limit is None else str(raw)[:limit]

    def audio_summary(self, entry: AssetEntry) -> str:
        obj = self.get(entry)
        if obj is None:
            return ""
        ch = getattr(obj, "m_Channels", "?")
        freq = getattr(obj, "m_Frequency", "?")
        ln = getattr(obj, "m_Length", 0) or 0
        return f"{ch} 声道 · {freq} Hz · {ln:.2f} 秒"


# ---------------------------------------------------------------- 载入入口


def load(ref: BundleRef, raw: bytes, eager: bool = False) -> Bundle:
    b = Bundle(ref=ref, raw=raw)
    if eager:
        b._ensure()
        _ = b.assets  # noqa: SLF001
    return b


def probe(raw: bytes) -> tuple[bool, str]:
    """快速判断一个 bundle 能否被解析，返回 (成功, 类型统计或错误)。"""
    try:
        env = UnityPy.load(io.BytesIO(raw))
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    from collections import Counter

    c = Counter(o.type.name for o in env.objects)
    if not c:
        return False, "包里没有对象"
    return True, "，".join(f"{k}×{v}" for k, v in c.most_common())
