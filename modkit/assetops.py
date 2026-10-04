"""按资源类型导入 / 导出。

各类资源的处理方式：

==============  ==========================  ====================================
类型            导出                        导入
==============  ==========================  ====================================
Texture2D       PNG（原尺寸）               任意图片；尺寸不符可自动缩放
Sprite          PNG（裁切后）               不支持直接替换（改它引用的贴图）
TextAsset       .json（美化）/ .txt          文本文件，UTF-8
AudioClip       .wav                        仅导出（FSB 流式，需重编码，见下）
MonoBehaviour   .json（类型树）              .json（类型树）
==============  ==========================  ====================================

关于音频
--------
``AudioClip`` 的数据不在对象里，而是通过 ``m_Resource`` 指向包内的流式数据
（FMOD FSB）。把它换掉需要重新编码成 FSB 并重建偏移，属于另一个工程，
所以 v1 **只支持导出**，界面上会明确标注「不支持替换」。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from .bundle import (
    AUDIO_TYPES,
    IMAGE_READONLY,
    IMAGE_TYPES,
    OTHER_TYPES,
    TEXT_TYPES,
    AssetEntry,
    Bundle,
)

KIND_IMAGE = "image"
KIND_TEXT = "text"
KIND_AUDIO = "audio"
KIND_TREE = "tree"
KIND_OTHER = "other"

KIND_LABEL = {
    KIND_IMAGE: "贴图",
    KIND_TEXT: "文本",
    KIND_AUDIO: "音频",
    KIND_TREE: "类型树",
    KIND_OTHER: "其他",
}

#: 类型 → 界面上的单字角标
KIND_TAG = {KIND_IMAGE: "图", KIND_TEXT: "文", KIND_AUDIO: "音", KIND_TREE: "树", KIND_OTHER: "·"}

_SAFE = re.compile(r"[^A-Za-z0-9._\u4e00-\u9fff-]+")


def asset_kind(entry: AssetEntry) -> str:
    """把任意对象的类型归到四类里。

    **兜底是 ``KIND_TREE``** —— 也就是说资源包里**每一个对象**都能解出来看、
    导出成 JSON、改完再塞回去，不再局限于贴图和文本。
    实测 Material / AnimationClip / Shader / GameObject / Transform /
    SpriteRenderer / Animator / MonoBehaviour 全部支持。
    """
    if entry.type in IMAGE_TYPES:
        return KIND_IMAGE
    if entry.type in TEXT_TYPES:
        return KIND_TEXT
    if entry.type in AUDIO_TYPES:
        return KIND_AUDIO
    if entry.type in OTHER_TYPES:
        return KIND_OTHER
    return KIND_TREE


def can_replace(entry: AssetEntry) -> bool:
    # Sprite 只是个「引用 + 裁切框」，自身没有像素数据，换它没有意义 ——
    # 要改的是它引用的那张 Texture2D 图集。
    if entry.type in IMAGE_READONLY:
        return False
    return asset_kind(entry) in (KIND_IMAGE, KIND_TEXT, KIND_TREE)


def replace_note(entry: AssetEntry) -> str:
    if entry.type in IMAGE_READONLY:
        return "Sprite 请改它引用的 Texture2D"
    k = asset_kind(entry)
    if k == KIND_AUDIO:
        return "音频为 FSB 流式资源，本工具只支持导出"
    if k == KIND_OTHER:
        return f"{entry.type} 暂不支持替换"
    return ""


# ---------------------------------------------------------------- 文件名


def suggest_filename(bundle: Bundle, entry: AssetEntry) -> str:
    stem = _SAFE.sub("_", entry.name or f"pathid{entry.path_id}")[:60] or f"pathid{entry.path_id}"
    k = asset_kind(entry)
    ext = {KIND_IMAGE: ".png", KIND_TEXT: ".txt", KIND_AUDIO: ".wav", KIND_TREE: ".json"}.get(
        k, ".bin"
    )
    return f"{bundle.name}__{entry.type}__{stem}{ext}"


# ---------------------------------------------------------------- 导出


def export_asset(bundle: Bundle, entry: AssetEntry, out_dir: str | Path) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    k = asset_kind(entry)

    if k == KIND_IMAGE:
        img = bundle.preview_image(entry)
        if img is None:
            raise ValueError("解不出图像")
        # Texture2D 用原尺寸导出（Sprite 已经是裁切结果）
        path = out / suggest_filename(bundle, entry)
        img.save(path)
        return path

    if k == KIND_TEXT:
        text = bundle.preview_text(entry)
        if not text.strip():
            # MonoScript 之类 m_Script 是空的（Unity 自带脚本不带源码），
            # 退回类型树，让用户至少能看到东西
            tree = bundle.read_typetree(entry)
            if tree is not None:
                path = out / (suggest_filename(bundle, entry).rsplit(".", 1)[0] + ".json")
                path.write_text(json.dumps(tree, indent=2, ensure_ascii=False), encoding="utf-8")
                return path
        path = out / suggest_filename(bundle, entry)
        pretty = _maybe_pretty(text)
        path = path.with_suffix(".json" if pretty is not None else ".txt")
        path.write_text(pretty if pretty is not None else text, encoding="utf-8")
        return path

    if k == KIND_AUDIO:
        obj = bundle.get(entry)
        samples = getattr(obj, "samples", None) or {}
        if not samples:
            raise ValueError("这个音频没有可导出的采样")
        # 一个 AudioClip 可能带多个子音（samples 是多条），全部导出
        first = None
        for name, data in samples.items():
            safe = _SAFE.sub("_", str(name))[:60] or bundle.name
            path = out / f"{safe}.wav"
            path.write_bytes(data)
            first = first or path
        assert first is not None
        return first

    if k == KIND_TREE:
        tree = bundle.read_typetree(entry)
        if tree is None:
            # 有的对象没有可解析的类型树，退回原始字节
            return export_raw(bundle, entry, out)
        path = out / suggest_filename(bundle, entry)
        path.write_text(json.dumps(tree, indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    # 兜底：任何对象都能把原始序列化字节导出来。
    # 这样「导出」对包里每一个对象都成立，不会出现点了没反应的情况。
    return export_raw(bundle, entry, out)


def export_raw(bundle: Bundle, entry: AssetEntry, out_dir: str | Path) -> Path:
    """兜底：直接把对象的原始字节写出来。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    r = bundle.object_reader(entry)
    if r is None:
        raise ValueError("读不到对象")
    path = out / (suggest_filename(bundle, entry).rsplit(".", 1)[0] + ".bin")
    path.write_bytes(bytes(r.get_raw_data()))
    return path


def _maybe_pretty(text: str) -> str | None:
    s = text.strip()
    if not s or s[0] not in "{[":
        return None
    try:
        return json.dumps(json.loads(s), indent=2, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------- 导入


@dataclass
class ImportResult:
    ok: bool
    message: str
    resized: bool = False


def import_asset(
    bundle: Bundle,
    entry: AssetEntry,
    file_path: str | Path,
    *,
    resize: bool = True,
) -> ImportResult:
    path = Path(file_path)
    if not path.is_file():
        return ImportResult(False, f"文件不存在：{path}")
    k = asset_kind(entry)

    try:
        if k == KIND_IMAGE:
            return _import_image(bundle, entry, path, resize=resize)
        if k == KIND_TEXT:
            text = path.read_text(encoding="utf-8")
            bundle.modify_text(entry, text)
            return ImportResult(True, f"已替换文本（{len(text)} 字符）")
        if k == KIND_TREE:
            tree = json.loads(path.read_text(encoding="utf-8"))
            bundle.modify_typetree(entry, tree)
            return ImportResult(True, "已替换类型树")
        if k == KIND_AUDIO:
            return ImportResult(False, "音频为 FSB 流式资源，本工具只支持导出")
        return ImportResult(False, f"{entry.type} 暂不支持替换")
    except Exception as exc:  # noqa: BLE001
        return ImportResult(False, f"{type(exc).__name__}: {exc}")


def _import_image(bundle: Bundle, entry: AssetEntry, path: Path, *, resize: bool) -> ImportResult:
    obj = bundle.get(entry)
    if obj is None:
        return ImportResult(False, "读不到对象")
    w = getattr(obj, "m_Width", None)
    h = getattr(obj, "m_Height", None)

    img = Image.open(path)
    img.load()
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA")

    resized = False
    if w and h and img.size != (w, h):
        if not resize:
            return ImportResult(False, f"尺寸不符：需要 {w}×{h}，给的是 {img.size[0]}×{img.size[1]}")
        img = img.resize((w, h), Image.LANCZOS)
        resized = True

    bundle.modify_image(entry, img)
    msg = f"已替换贴图（{w}×{h}）"
    if resized:
        msg += "，已自动缩放"
    return ImportResult(True, msg, resized=resized)
