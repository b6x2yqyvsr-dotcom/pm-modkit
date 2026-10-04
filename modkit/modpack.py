"""``.pmmod`` 模组包格式。

本质是个 zip，两种形态可以共存：

**A. 源码级模组（推荐，体积小）** —— 只存「替换文件 + 它该盖到哪个资源上」::

    mod.json
    assets/appdata/TextAsset__1234__GachaDefault.json
    assets/spdata/Texture2D__5678__MortyX.png

应用时现场打开目标包、把文件盖进去、回写。同一个模组可以打到 APK 上，
也可以打到 UnityCache 上。

**B. 成品级模组（免依赖）** —— 直接存回写好的 bundle::

    mod.json
    bundles/appdata.assetbundle

推到设备 UnityCache 即可生效，不依赖用户的原始数据。

``mod.json`` 结构::

    {
      "format": "pmmod/1",
      "name": "抽卡全保底",
      "author": "...",
      "description": "...",
      "created": "2026-05-20T14:41:00",
      "game": "com.conspiracyrick.pocketmortys",
      "edits": [
        {"bundle": "appdata", "type": "TextAsset", "path_id": 1234,
         "name": "GachaDefault", "file": "assets/appdata/....json",
         "sha256": "..."}
      ]
    }
"""

from __future__ import annotations

import hashlib
import io
import json
import time
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import assetops, bundle as bundle_mod
from .bundle import AssetEntry, Bundle
from .source import Source

FORMAT = "pmmod/1"
GAME = "com.conspiracyrick.pocketmortys"
MOD_JSON = "mod.json"


@dataclass
class Edit:
    """一条待应用的替换。"""

    bundle: str
    type: str
    path_id: int
    name: str
    file: str  # mod 包内路径
    sha256: str = ""

    def key(self) -> tuple[str, int]:
        return (self.bundle, self.path_id)


@dataclass
class ModPack:
    name: str = "未命名模组"
    author: str = ""
    description: str = ""
    created: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))
    game: str = GAME
    edits: list[Edit] = field(default_factory=list)
    #: 成品级模组：包名 -> 回写好的 bundle 字节
    bundles: dict[str, bytes] = field(default_factory=dict)

    # ------------------------------------------------------------ 写
    def save(self, path: str | Path, asset_bytes: dict[tuple[str, int], bytes] | None = None) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            for e in self.edits:
                data = (asset_bytes or {}).get(e.key())
                if data is None:
                    continue
                z.writestr(e.file, data)
            for bname, data in self.bundles.items():
                z.writestr(f"bundles/{bname}.assetbundle", data)
            meta = {
                "format": FORMAT,
                "name": self.name,
                "author": self.author,
                "description": self.description,
                "created": self.created,
                "game": self.game,
                "edits": [asdict(e) for e in self.edits],
            }
            z.writestr(MOD_JSON, json.dumps(meta, indent=2, ensure_ascii=False))
        return path

    # ------------------------------------------------------------ 读
    @classmethod
    def load(cls, path: str | Path) -> "ModPack":
        with zipfile.ZipFile(path) as z:
            meta = json.loads(z.read(MOD_JSON).decode("utf-8"))
            pack = cls(
                name=meta.get("name", "未命名模组"),
                author=meta.get("author", ""),
                description=meta.get("description", ""),
                created=meta.get("created", ""),
                game=meta.get("game", GAME),
            )
            pack.edits = [Edit(**e) for e in meta.get("edits", [])]
            for n in z.namelist():
                if n.startswith("bundles/") and n.endswith(".assetbundle"):
                    pack.bundles[Path(n).stem] = z.read(n)
        return pack

    def asset_bytes(self, path: str | Path) -> dict[tuple[str, int], bytes]:
        out: dict[tuple[str, int], bytes] = {}
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            for e in self.edits:
                if e.file in names:
                    out[e.key()] = z.read(e.file)
        return out

    # ------------------------------------------------------------ 信息
    def summary(self) -> str:
        bits = []
        if self.edits:
            bundles = sorted({e.bundle for e in self.edits})
            bits.append(f"{len(self.edits)} 处替换（{len(bundles)} 个包：{', '.join(bundles[:6])}"
                        + ("…" if len(bundles) > 6 else "") + "）")
        if self.bundles:
            bits.append(f"{len(self.bundles)} 个成品包")
        return "；".join(bits) or "空模组"


# ---------------------------------------------------------------- 打包


def make_edit(bundle: Bundle, entry: AssetEntry, payload: bytes) -> Edit:
    """由「包 + 资源 + 导出文件内容」造一条 Edit。"""
    ext = Path(assetops.suggest_filename(bundle, entry)).suffix or ".bin"
    stem = (entry.name or f"pathid{entry.path_id}").replace("/", "_")[:60]
    rel = f"assets/{bundle.name}/{entry.type}__{entry.path_id}__{stem}{ext}"
    return Edit(
        bundle=bundle.name,
        type=entry.type,
        path_id=entry.path_id,
        name=entry.name,
        file=rel,
        sha256=hashlib.sha256(payload).hexdigest(),
    )


def build_from_bundles(
    name: str,
    bundles: dict[str, bytes],
    *,
    author: str = "",
    description: str = "",
) -> ModPack:
    pack = ModPack(name=name, author=author, description=description)
    pack.bundles = dict(bundles)
    return pack


# ---------------------------------------------------------------- 应用


@dataclass
class ApplyReport:
    applied: int = 0
    failed: list[str] = field(default_factory=list)
    bundles: dict[str, bytes] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.failed


def apply_to_source(pack: ModPack, src: Source, asset_payloads: dict[tuple[str, int], bytes]) -> ApplyReport:
    """把源码级模组打到某个源上，返回「包名 -> 新 bundle 字节」。"""
    report = ApplyReport()
    by_bundle: dict[str, list[Edit]] = {}
    for e in pack.edits:
        by_bundle.setdefault(e.bundle, []).append(e)

    for bname, edits in by_bundle.items():
        ref = src.primary(bname)
        if ref is None:
            report.failed.append(f"{bname}：源里没有这个包")
            continue
        try:
            b = bundle_mod.load(ref, src.read(ref))
            b.assets  # 建索引
        except Exception as exc:  # noqa: BLE001
            report.failed.append(f"{bname}：打不开（{exc}）")
            continue

        index = {a.path_id: a for a in b.assets}
        for e in edits:
            entry = index.get(e.path_id)
            if entry is None:
                report.failed.append(f"{bname}#{e.path_id}：包里找不到该资源")
                continue
            payload = asset_payloads.get(e.key())
            if payload is None:
                report.failed.append(f"{bname}#{e.path_id}：模组里缺这个文件")
                continue
            try:
                _apply_payload(b, entry, payload)
                report.applied += 1
            except Exception as exc:  # noqa: BLE001
                report.failed.append(f"{bname}#{e.path_id}：{exc}")

        if b.dirty:
            try:
                report.bundles[bname] = b.save()
            except Exception as exc:  # noqa: BLE001
                report.failed.append(f"{bname}：回写失败（{exc}）")
    return report


def apply_payload(b: Bundle, entry: AssetEntry, payload: bytes) -> None:
    """把一份「文件内容」盖到某个资源上（按资源类型分派）。"""
    kind = assetops.asset_kind(entry)
    if kind == assetops.KIND_TEXT:
        b.modify_text(entry, payload.decode("utf-8"))
    elif kind == assetops.KIND_IMAGE:
        from PIL import Image

        img = Image.open(io.BytesIO(payload))
        img.load()
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA")
        obj = b.get(entry)
        w, h = getattr(obj, "m_Width", None), getattr(obj, "m_Height", None)
        if w and h and img.size != (w, h):
            img = img.resize((w, h), Image.LANCZOS)
        b.modify_image(entry, img)
    elif kind == assetops.KIND_TREE:
        b.modify_typetree(entry, json.loads(payload.decode("utf-8")))
    else:
        raise ValueError(f"{entry.type} 不支持替换")


#: 旧名，保留兼容
_apply_payload = apply_payload
