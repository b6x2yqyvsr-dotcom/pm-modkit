#!/usr/bin/env python3
"""口蘑 Mod 工坊 —— 网页版服务端。

**为什么要有网页版**

桌面图形界面用的是 Dear ImGui，而 ``imgui-bundle`` 只有这些平台的预编译轮子：
Windows(x64/arm64)、Linux(manylinux/musllinux, x64/aarch64)、macOS(arm64)。
**Android 和 iOS 装不上**（Android 是 bionic libc，iOS 根本不给装 CPython 扩展）。

所以手机端走**浏览器**这条路：核心库 ``modkit`` 只依赖标准库 + Pillow + UnityPy，
把它跑在一个自带的小 HTTP 服务上，手机浏览器连过来就是完整界面。

三种用法：

1. **电脑跑服务，手机当界面** —— 电脑上执行本脚本，手机浏览器打开
   ``http://<电脑局域网IP>:8765``
2. **手机自己跑（Android / Termux）** —— 见 ``android/termux-install.sh``，
   然后手机浏览器开 ``http://127.0.0.1:8765``
3. **当无图形界面的替代品** —— 服务器、树莓派上都能跑

只用标准库，不需要装 imgui-bundle。
"""

from __future__ import annotations

import argparse
import io
import json
import mimetypes
import os
import socket
import sys
import threading
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modkit import apkbuild, assetops, entries as entries_mod, fields as fields_mod
from modkit import paths as pm_paths, session as session_mod, sysenv
from modkit.bundle import Bundle

WEB_DIR = Path(__file__).resolve().parent

#: 上传/导出文件放这里
WORK_DIR = Path(os.environ.get("PM_MODKIT_WORK", Path.home() / ".pm-modkit"))
UPLOAD_DIR = WORK_DIR / "uploads"
OUTPUT_DIR = WORK_DIR / "output"

MAX_UPLOAD = 4 * 1024 * 1024 * 1024  # 4GB，够放 1GB 的 APK


# ---------------------------------------------------------------- 应用状态


class App:
    """一个进程一份会话。所有操作加锁，避免浏览器并发点崩。"""

    def __init__(self) -> None:
        self.sess = session_mod.Session()
        self.lock = threading.RLock()

    # ---------------- 序列化小工具
    def state(self) -> dict:
        s = self.sess
        if s.source is None:
            return {"opened": False, "bundles": [], "modified": [], "log": s.log[-200:]}
        bundles = []
        for name in s.source.bundle_names():
            ref = s.ref_for(name)
            bundles.append({
                "name": name,
                "layout": ref.layout if ref else "",
                "layout_label": ref.layout_label if ref else "",
                "platform": ref.platform if ref else None,
                "version": s.bundle_version(name),
                "size": ref.size if ref else 0,
                "modified": name in s.modified,
                "doc": fields_mod.bundle_doc(name),
                "copies": [
                    {"layout": r.layout, "layout_label": r.layout_label,
                     "platform": r.platform, "version": r.version, "size": r.size}
                    for r in s.refs_for(name)
                ],
            })
        return {
            "opened": True,
            "label": s.source.label,
            "self_contained": s.source.is_self_contained,
            "pmseed": len(s.source.pmseed),
            "apk_bundles": sum(1 for lst in s.source.refs.values()
                               for r in lst if r.layout == "apk"),
            "containers": [
                {"label": c.label, "kind": c.kind, "root": str(c.root)}
                for c in s.source.containers
            ],
            "bundles": bundles,
            "apk_diagnosis": s.apk_diagnosis(),
            "check": _check_brief(s),
            "modified": [
                {"bundle": n, "prebuilt": b.prebuilt,
                 "assets": [
                     {"pid": pid, "name": next((a.name for a in b.assets if a.path_id == pid), ""),
                      "type": next((a.type for a in b.assets if a.path_id == pid), "")}
                     for pid in sorted(b.dirty)
                 ]}
                for n, b in s.modified.items()
            ],
            "log": s.log[-200:],
        }

    def bundle_assets(self, name: str) -> dict:
        b = self.sess.bundle(name, eager=True)
        if b is None:
            raise ValueError(f"没有这个包：{name}")
        if not b.ok:
            raise ValueError(f"这个包解析失败：{b.error}")
        return {
            "name": name,
            "counts": b.type_counts(),
            "types": [
                {"type": t, "short": fields_mod.type_short(t), "doc": fields_mod.type_doc(t)}
                for t in b.type_counts()
            ],
            "assets": [
                {
                    "pid": a.path_id,
                    "type": a.type,
                    "type_short": fields_mod.type_short(a.type),
                    "name": a.name,
                    "size": a.size,
                    "kind": assetops.asset_kind(a),
                    "replaceable": assetops.can_replace(a),
                    "note": assetops.replace_note(a),
                    "modified": a.path_id in b.dirty,
                }
                for a in b.assets
            ],
        }

    def asset_info(self, name: str, pid: int) -> dict:
        b = self.sess.bundle(name, eager=True)
        if b is None or not b.ok:
            raise ValueError("包打不开")
        e = next((a for a in b.assets if a.path_id == pid), None)
        if e is None:
            raise ValueError("找不到这个资源")
        kind = assetops.asset_kind(e)
        out = {
            "bundle": name, "pid": pid, "type": e.type,
            "type_short": fields_mod.type_short(e.type),
            "type_doc": fields_mod.type_doc(e.type),
            "name": e.name, "size": e.size, "kind": kind,
            "replaceable": assetops.can_replace(e),
            "note": assetops.replace_note(e),
            "modified": pid in b.dirty,
        }
        if kind == assetops.KIND_TEXT:
            try:
                t = b.preview_text(e)
                out["text"] = t[:400_000]
                out["text_len"] = len(t)
                out["truncated"] = len(t) > 400_000
            except Exception as exc:  # noqa: BLE001
                out["error"] = str(exc)
        elif kind == assetops.KIND_AUDIO:
            try:
                out["audio"] = b.audio_summary(e)
            except Exception:  # noqa: BLE001
                pass
        elif kind == assetops.KIND_TREE:
            try:
                tree = b.read_typetree(e)
                out["tree"] = json.dumps(tree, indent=2, ensure_ascii=False)[:400_000]
            except Exception as exc:  # noqa: BLE001
                out["error"] = str(exc)
        elif kind == assetops.KIND_IMAGE:
            try:
                img = b.preview_image(e)
                out["width"], out["height"] = (img.width, img.height) if img else (0, 0)
                if img is None and b.last_error:
                    out["error"] = b.last_error
            except Exception as exc:  # noqa: BLE001
                out["error"] = str(exc)
        return out

    def asset_image(self, name: str, pid: int, max_side: int = 1024) -> bytes:
        from PIL import Image

        b = self.sess.bundle(name, eager=True)
        e = next((a for a in b.assets if a.path_id == pid), None)
        if e is None:
            raise ValueError("找不到资源")
        img = b.preview_image(e)
        if img is None:
            raise ValueError("解不出图像" + (f"：{b.last_error}" if b.last_error else ""))
        if max(img.size) > max_side:
            r = max_side / max(img.size)
            img = img.resize((max(1, int(img.width * r)), max(1, int(img.height * r))),
                             Image.LANCZOS)
        buf = io.BytesIO()
        img.convert("RGBA").save(buf, format="PNG")
        return buf.getvalue()

    def export_asset_bytes(self, name: str, pid: int) -> tuple[str, bytes]:
        b = self.sess.bundle(name, eager=True)
        e = next((a for a in b.assets if a.path_id == pid), None)
        if e is None:
            raise ValueError("找不到资源")
        kind = assetops.asset_kind(e)
        if kind == assetops.KIND_IMAGE:
            from PIL import Image

            img = b.preview_image(e)
            if img is None:
                raise ValueError("解不出图像")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return f"{e.name or pid}.png", buf.getvalue()
        if kind == assetops.KIND_TEXT:
            return f"{e.name or pid}.json", b.preview_text(e).encode("utf-8")
        if kind == assetops.KIND_TREE:
            tree = b.read_typetree(e)
            return f"{e.name or pid}.json", json.dumps(tree, indent=2, ensure_ascii=False).encode()
        if kind == assetops.KIND_AUDIO:
            obj = b.get(e)
            samples = getattr(obj, "samples", None) or {}
            if samples:
                nm, data = next(iter(samples.items()))
                return f"{nm or e.name or pid}.wav", data
        # 兜底：原始字节
        r = b.object_reader(e)
        return f"{e.name or pid}.bin", bytes(r.get_raw_data())

    # ---------------- 动作
    def open(self, paths_in: list[str], append: bool = False) -> str:
        if append:
            added = self.sess.add_source(*paths_in)
            return f"追加 {len(added)} 个源"
        self.sess.open(*paths_in)
        return f"已打开 {self.sess.source.label}"

    def export(self, mode: str, out: str) -> str:
        out_p = Path(out) if out else OUTPUT_DIR
        lines = []
        if mode in ("cache", "all"):
            r = self.sess.output_cache(out_p / "UnityCache产物")
            lines.append(f"UnityCache → {r['dir']}（{len(r['written'])} 个包）")
        if mode in ("cdn", "all"):
            r = self.sess.output_cdn(out_p / "CDN产物")
            lines.append(f"CDN → {r['dir']}（{len(r['written'])} 个包）")
        if mode in ("bundles", "all"):
            r = self.sess.export_bundles(out_p / "bundle产物")
            lines.append(f"bundle → {r['dir']}（{len(r['files'])} 个）")
        if mode == "modpack":
            name = "我的模组"
            r = self.sess.output_modpack(out_p / f"{name}.pmmod", name=name)
            lines.append(f"模组包 → {r}")
        return "\n".join(lines) or "没有产出"


def _asset_ids(sess) -> list[str]:
    """BundleAssetAssignment 里的全部 assetid（给「借用美术」下拉用）。"""
    src = getattr(sess, "source", None)
    if src is None:
        return []
    try:
        return sorted({str(v.get("assetid") or v.get("id") or "")
                       for v in (src.manifest or {}).values()
                       if isinstance(v, dict)} - {""})
    except Exception:  # noqa: BLE001
        return []


def _check_brief(s) -> dict:
    """体检简报（给界面顶部显示用，别每次都把全文塞过去）。"""
    try:
        rep = s.check()
    except Exception as exc:  # noqa: BLE001
        return {"ok": True, "errors": 0, "warnings": 0, "summary": f"（体检失败：{exc}）"}
    return {
        "ok": rep.ok,
        "errors": len(rep.errors),
        "warnings": len(rep.warnings),
        "summary": rep.summary(),
    }


#: 15 个动作帧（和桌面端一致）
GAL_FRAMES = ["Front", "Back", "Icon",
              "Down_1", "Down_2", "Down_3", "Down_4",
              "Side_1", "Side_2", "Side_3", "Side_4",
              "Up_1", "Up_2", "Up_3", "Up_4"]


def _gallery_locate(sess, morty_id: str) -> dict:
    """这只莫蒂的数值 + 美术在哪个包。"""
    from modkit import entries as E

    row = E.entry_of_table(sess, "morty", E.MORTY_TABLES[0], morty_id)
    if not isinstance(row, dict):
        return {}
    assetid = row.get("assetid") or ""
    bundle = ""
    try:
        b = sess.bundle("appdata", eager=True)
        e = next((a for a in b.assets if a.name == "BundleAssetAssignment"), None)
        if e is not None:
            baa = json.loads(b.preview_text(e))
            bundle = str((baa.get(assetid) or {}).get("version") or "")
    except Exception:  # noqa: BLE001
        pass
    return {"row": row, "assetid": assetid, "bundle": bundle}


def gallery_info(sess, morty_id: str) -> dict:
    """图鉴要用的一切：数值、属性、技能（效果已翻成中文）、有哪些帧。"""
    from modkit import effects as FX
    from modkit import entries as E

    info = _gallery_locate(sess, morty_id)
    if not info:
        return {"ok": False, "message": f"找不到 {morty_id}"}
    row = info["row"]

    def num(f, d=0):
        try:
            return int(float(row.get(f, d) or 0))
        except (TypeError, ValueError):
            return d

    stats = {"hp": num("hpbase"), "atk": num("attackbase"),
             "def": num("defencebase"), "spd": num("speedbase")}
    stats["total"] = sum(stats.values())

    # 技能（带中文效果）
    amap = {}
    try:
        b = sess.bundle("spdata", eager=True)
        e = next((a for a in b.assets if a.name == "AttackInfo"), None)
        if e is not None:
            amap = json.loads(b.preview_text(e))
    except Exception:  # noqa: BLE001
        pass
    attacks = []
    for part in str(row.get("attacks", "")).split(","):
        part = part.strip()
        if not part:
            continue
        aid, _, lv = part.partition(":")
        arow = amap.get(aid.strip()) or {}
        attacks.append({
            "id": aid.strip(),
            "level": lv.strip() or "1",
            "effect": FX.describe(arow.get("effects", "")) if arow else "（表里没有）",
        })

    # 有哪些帧（用包里的对象名判断，避免逐张解码）
    frames = []
    if info["assetid"] and info["bundle"]:
        try:
            b2 = sess.bundle(info["bundle"], eager=True)
            names = {a.name for a in b2.assets if a.type == "Texture2D"}
            frames = [f for f in GAL_FRAMES if f"{info['assetid']}{f}" in names]
        except Exception:  # noqa: BLE001
            pass

    # 名字
    name = ""
    try:
        b3 = sess.bundle("text", eager=True)
        e3 = next((a for a in b3.assets if a.name == "ZH_CN"), None)
        if e3 is not None:
            name = ((json.loads(b3.preview_text(e3)).get("Morty") or {})
                    .get(morty_id) or {}).get("name", "")
    except Exception:  # noqa: BLE001
        pass

    el = (row.get("elementtype") or "").strip()
    return {
        "ok": True,
        "id": morty_id, "name": name,
        "assetid": info["assetid"], "bundle": info["bundle"],
        "stats": stats,
        "element": el,
        "element_zh": {"Rock": "石头", "Paper": "布", "Scissors": "剪刀"}.get(el, el or "—"),
        "number": row.get("number"), "division": row.get("division"),
        "badge": row.get("badgereq"),
        "gacha": str(row.get("includeingacha", "")).upper() == "TRUE",
        "evolution": (row.get("evolution") or "").strip(),
        "attacks": attacks,
        "frames": frames,
    }


def gallery_art(sess, morty_id: str, frame: str, max_size: int = 180):
    """把某一帧解成 PNG 字节。"""
    from io import BytesIO

    info = _gallery_locate(sess, morty_id)
    if not info or not info["assetid"] or not info["bundle"]:
        return None
    try:
        b = sess.bundle(info["bundle"], eager=True)
    except Exception:  # noqa: BLE001
        return None
    if b is None or not b.ok:
        return None
    want = f"{info['assetid']}{frame}"
    e = next((a for a in b.assets if a.type == "Texture2D" and a.name == want), None)
    if e is None:
        return None
    try:
        img = b.preview_image(e)
    except Exception:  # noqa: BLE001
        return None
    if img is None:
        return None
    if max_size and max(img.size) > max_size:
        r = max_size / max(img.size)
        img = img.resize((max(1, int(img.width * r)), max(1, int(img.height * r))),
                         Image.LANCZOS)
    buf = BytesIO()
    img.convert("RGBA").save(buf, "PNG")
    return buf.getvalue()


APP = App()


# ---------------------------------------------------------------- HTTP

INDEX = WEB_DIR / "index.html"


class Handler(BaseHTTPRequestHandler):
    server_version = "pm-modkit"
    protocol_version = "HTTP/1.1"

    # ---------------- 基础回应
    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8",
              extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _err(self, exc: Exception, code: int = 400) -> None:
        self._json({"ok": False, "error": f"{type(exc).__name__}: {exc}",
                    "trace": traceback.format_exc()[-2000:]}, code)

    def _body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return b""
        if n > MAX_UPLOAD:
            raise ValueError("上传太大了")
        buf = bytearray()
        remaining = n
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 1 << 20))
            if not chunk:
                break
            buf += chunk
            remaining -= len(chunk)
        return bytes(buf)

    def _json_body(self) -> dict:
        raw = self._body()
        return json.loads(raw.decode("utf-8")) if raw else {}

    def _q(self) -> dict:
        return {k: v[0] for k, v in urllib.parse.parse_qs(
            urllib.parse.urlparse(self.path).query).items()}

    def log_message(self, fmt, *args):  # 别把每条请求打到 stdout
        pass

    # ---------------- 路由
    def do_GET(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        q = self._q()
        try:
            # PWA：manifest、service worker、图标 —— 手机上「添加到主屏幕」要用
            if path == "/manifest.webmanifest":
                f = WEB_DIR / "manifest.webmanifest"
                if f.is_file():
                    return self._send(200, f.read_bytes(),
                                      "application/manifest+json; charset=utf-8")
            if path == "/sw.js":
                f = WEB_DIR / "sw.js"
                if f.is_file():
                    return self._send(200, f.read_bytes(),
                                      "application/javascript; charset=utf-8",
                                      {"Service-Worker-Allowed": "/"})
            if path.startswith("/icon") and path.endswith(".png"):
                f = (WEB_DIR / path.lstrip("/")).resolve()
                if str(f).startswith(str(WEB_DIR.resolve())) and f.is_file():
                    return self._send(200, f.read_bytes(), "image/png",
                                      {"Cache-Control": "public, max-age=86400"})

            if path in ("/", "/index.html"):
                body = INDEX.read_bytes() if INDEX.is_file() else b"index.html missing"
                return self._send(200, body, "text/html; charset=utf-8")

            if path == "/api/env":
                return self._json({
                    "ok": True,
                    "platform": sysenv.platform_name(),
                    "python": sys.platform,
                    "output": str(OUTPUT_DIR),
                    "work": str(WORK_DIR),
                    "device": pm_paths.DEVICE_FILES_DIR,
                    "package": pm_paths.DEFAULT_PACKAGE,
                    "toolchain": apkbuild.toolchain_status(),
                })

            if path == "/api/state":
                with APP.lock:
                    return self._json({"ok": True, **APP.state()})

            if path == "/api/bundle":
                with APP.lock:
                    return self._json({"ok": True, **APP.bundle_assets(q["name"])})

            if path == "/api/asset":
                with APP.lock:
                    return self._json({"ok": True, **APP.asset_info(q["bundle"], int(q["pid"]))})

            if path == "/api/asset/image":
                with APP.lock:
                    data = APP.asset_image(q["bundle"], int(q["pid"]),
                                           int(q.get("max", 1024)))
                return self._send(200, data, "image/png")

            if path == "/api/asset/download":
                with APP.lock:
                    fname, data = APP.export_asset_bytes(q["bundle"], int(q["pid"]))
                return self._send(200, data, "application/octet-stream",
                                  {"Content-Disposition": f'attachment; filename="{fname}"'})

            if path == "/api/gallery":
                with APP.lock:
                    return self._json(gallery_info(APP.sess, q["morty"]))

            if path == "/api/gallery/art":
                with APP.lock:
                    data = gallery_art(APP.sess, q["morty"], q.get("frame", "Icon"),
                                       int(q.get("max", 180)))
                if data is None:
                    return self._send(404, b"no art", "text/plain")
                return self._send(200, data, "image/png")

            if path == "/api/check":
                with APP.lock:
                    rep = APP.sess.check()
                    return self._json({
                        "ok": True,
                        "clean": rep.ok,
                        "summary": rep.summary(),
                        "issues": [
                            {"level": i.level, "table": i.table, "entry": i.entry,
                             "message": i.message, "line": i.line()}
                            for i in rep.issues
                        ],
                    })

            if path == "/api/entries":
                with APP.lock:
                    kind = q.get("kind", "morty")
                    kd = entries_mod.KINDS.get(kind)
                    if kd is None:
                        raise ValueError("不认识的类型")
                    ids = entries_mod.list_ids(APP.sess, kind)
                    # 每条模板的数值/属性 —— 网页版的筛选和「BEFORE/AFTER」要靠它，
                    # 只给 id 的话筛选器就是个摆设
                    stats = {}
                    try:
                        sp0 = kd.tables[0]
                        for i in ids:
                            row = entries_mod.entry_of_table(APP.sess, kind, sp0, i)
                            if not isinstance(row, dict):
                                continue
                            def _num(f):
                                try:
                                    return int(float(row.get(f, 0) or 0))
                                except (TypeError, ValueError):
                                    return 0
                            stats[i] = {
                                "hp": _num("hpbase"), "atk": _num("attackbase"),
                                "def": _num("defencebase"), "spd": _num("speedbase"),
                                "element": str(row.get("elementtype") or ""),
                                "evolution": str(row.get("evolution") or ""),
                            }
                    except Exception:  # noqa: BLE001
                        stats = {}
                    loc = {}
                    try:
                        b = APP.sess.bundle("text", eager=True)
                        e = next((x for x in b.assets if x.name == "ZH_CN"), None)
                        if e is not None:
                            loc = (json.loads(b.preview_text(e)).get(kd.loc_section) or {})
                    except Exception:  # noqa: BLE001
                        pass
                    tables = []
                    for sp in kd.tables:
                        row = None
                        try:
                            row = entries_mod.entry_of_table(
                                APP.sess, kind, sp, ids[0] if ids else "")
                        except Exception:  # noqa: BLE001
                            pass
                        tables.append({
                            "label": sp.label,
                            "desc": fields_mod.explain_table(sp.label),
                            "fields": [
                                {
                                    "name": f,
                                    "label": (fields_mod.doc_for(sp.label, f).label
                                              if fields_mod.doc_for(sp.label, f) else f),
                                    "help": (fields_mod.doc_for(sp.label, f).help
                                             if fields_mod.doc_for(sp.label, f) else ""),
                                    "values": (fields_mod.doc_for(sp.label, f).values
                                               if fields_mod.doc_for(sp.label, f) else {}),
                                    "auto": bool(fields_mod.doc_for(sp.label, f)
                                                 and fields_mod.doc_for(sp.label, f).auto),
                                    "common": bool(fields_mod.doc_for(sp.label, f)
                                                   and fields_mod.doc_for(sp.label, f).common),
                                    "value": ("" if v is None else (v if isinstance(v, str)
                                              else json.dumps(v, ensure_ascii=False)))
                                    if row else "",
                                }
                                for f, v in (row or {}).items()
                            ],
                        })
                    return self._json({
                        "ok": True, "kind": kind, "label": kd.label,
                        "kinds": [{"key": k, "label": v.label} for k, v in entries_mod.KINDS.items()],
                        "ids": ids,
                        "names": {i: (loc.get(i) or {}).get("name", "") if isinstance(loc.get(i), dict)
                                  else "" for i in ids},
                        "stats": stats,
                        "tables": tables,
                        "pools": entries_mod.gacha_pools(APP.sess),
                        "assets": _asset_ids(APP.sess),
                    })

            if path == "/api/download":
                # 把产物打包成 zip 下载（用 zip 流，避免额外依赖）
                import zipfile

                target = Path(q.get("path", ""))
                if not target.exists():
                    raise ValueError("路径不存在")
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                    if target.is_file():
                        z.write(target, target.name)
                    else:
                        for f in target.rglob("*"):
                            if f.is_file():
                                z.write(f, str(f.relative_to(target.parent)))
                return self._send(200, buf.getvalue(), "application/zip",
                                  {"Content-Disposition":
                                   f'attachment; filename="{target.name}.zip"'})

            return self._err(ValueError(f"没有这个接口：{path}"), 404)
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    def do_POST(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        q = self._q()
        try:
            if path == "/api/upload-many":
                # 拖拽上传：multipart 一次多个文件，存下来**直接当来源打开**
                import email
                from email import policy

                raw = self._body()
                ctype = self.headers.get("Content-Type", "")
                parts = []
                if "multipart/form-data" in ctype:
                    msg = email.message_from_bytes(
                        b"Content-Type: " + ctype.encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + raw,
                        policy=policy.default)
                    for part in msg.iter_parts():
                        fn = part.get_filename()
                        if not fn:
                            continue
                        payload = part.get_payload(decode=True) or b""
                        parts.append((Path(fn).name, payload))
                if not parts:
                    return self._json({"ok": False, "error": "没收到文件"})

                dest_dir = UPLOAD_DIR / "drop"
                dest_dir.mkdir(parents=True, exist_ok=True)
                paths = []
                for fname, data in parts:
                    dest = dest_dir / fname
                    i = 1
                    while dest.exists():
                        dest = dest_dir / f"{Path(fname).stem}-{i}{Path(fname).suffix}"
                        i += 1
                    dest.write_bytes(data)
                    paths.append(str(dest))

                append = APP.sess.source is not None
                with APP.lock:
                    ok, fail = [], []
                    for one in paths:
                        try:
                            if append:
                                APP.sess.add_source(one)
                            else:
                                APP.sess.open(one)
                                append = True
                            ok.append(one)
                        except Exception as exc:  # noqa: BLE001
                            fail.append(f"{Path(one).name}: {exc}")
                APP.sess.say(f"拖入 {len(ok)} 个文件"
                             + (f"，{len(fail)} 个失败：{fail[0]}" if fail else ""))
                return self._json({"ok": bool(ok), "added": len(ok),
                                   "paths": paths, "failed": fail,
                                   "error": fail[0] if fail and not ok else None})

            if path == "/api/upload":
                # 原始字节上传，文件名走 query
                name = Path(q.get("name", "upload.bin")).name
                dest_dir = UPLOAD_DIR / q.get("into", "")
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest = dest_dir / name
                dest.write_bytes(self._body())
                return self._json({"ok": True, "path": str(dest), "name": name,
                                   "size": dest.stat().st_size})

            body = self._json_body()

            if path == "/api/open":
                with APP.lock:
                    msg = APP.open(body.get("paths") or [], append=bool(body.get("append")))
                return self._json({"ok": True, "message": msg})

            if path == "/api/choose-copy":
                with APP.lock:
                    name = body["bundle"]
                    refs = APP.sess.refs_for(name)
                    i = int(body["index"])
                    if not (0 <= i < len(refs)):
                        raise ValueError("序号不对")
                    APP.sess.choose(name, refs[i])
                return self._json({"ok": True})

            if path == "/api/asset/text":
                with APP.lock:
                    b = APP.sess.bundle(body["bundle"], eager=True)
                    e = next((a for a in b.assets if a.path_id == int(body["pid"])), None)
                    if e is None:
                        raise ValueError("找不到资源")
                    b.modify_text(e, body["text"])
                return self._json({"ok": True, "message": "文本已更新"})

            if path == "/api/asset/typetree":
                with APP.lock:
                    b = APP.sess.bundle(body["bundle"], eager=True)
                    e = next((a for a in b.assets if a.path_id == int(body["pid"])), None)
                    if e is None:
                        raise ValueError("找不到资源")
                    b.modify_typetree(e, json.loads(body["text"]))
                return self._json({"ok": True, "message": "类型树已更新"})

            if path == "/api/asset/file":
                # 用已上传到服务器的文件替换资源
                with APP.lock:
                    b = APP.sess.bundle(body["bundle"], eager=True)
                    e = next((a for a in b.assets if a.path_id == int(body["pid"])), None)
                    if e is None:
                        raise ValueError("找不到资源")
                    r = assetops.import_asset(b, e, body["path"], resize=body.get("resize", True))
                return self._json({"ok": r.ok, "message": r.message})

            if path == "/api/asset/revert":
                with APP.lock:
                    b = APP.sess.bundle(body["bundle"], eager=True)
                    b.revert_asset(int(body["pid"]))
                return self._json({"ok": True, "message": "已撤销"})

            if path == "/api/bundle/revert":
                with APP.lock:
                    APP.sess.reset(body["bundle"])
                return self._json({"ok": True, "message": "已还原该包"})

            if path == "/api/reset-all":
                with APP.lock:
                    APP.sess.reset_all()
                return self._json({"ok": True, "message": "已清空全部改动"})

            if path == "/api/fix":
                from modkit import validate as V

                with APP.lock:
                    before = APP.sess.check()
                    if not before.issues:
                        return self._json({"ok": True, "message": "没有要修的",
                                           "done": []})
                    if body.get("dry_run"):
                        plan = V.repair(APP.sess, before, dry_run=True)
                        return self._json({"ok": True, "dry": True,
                                           "message": "打算这么修：\n" + "\n".join("· " + d for d in plan),
                                           "done": plan})
                    done = V.repair(APP.sess, before)
                    after = APP.sess.check()
                return self._json({
                    "ok": True, "done": done,
                    "message": "修了 " + str(len(done)) + " 处：\n"
                               + "\n".join("· " + d for d in done)
                               + "\n\n修之后：" + after.summary(),
                })

            if path == "/api/add-character":
                from modkit import character as CH

                images = body.get("images") or {}
                overrides = body.get("overrides") or {}
                with APP.lock:
                    res = CH.add_character(
                        APP.sess, body["id"], body["from"],
                        new_asset=body.get("assetid") or None,
                        new_bundle=body.get("bundle") or None,
                        images=images,
                        names=body.get("names") or {},
                        descriptions=body.get("descriptions") or {},
                        overrides=overrides,
                        add_to_gacha=bool(body.get("add_to_gacha")),
                        gacha_pool=int(body.get("gacha_pool") or 0),
                        dry_run=bool(body.get("dry_run")),
                    )
                return self._json({"ok": res["ok"], "message": res["message"]})

            if path == "/api/new-entry":
                with APP.lock:
                    overrides = body.get("overrides") or {}
                    rep = entries_mod.add_entry(
                        APP.sess,
                        body["kind"], body["id"], body["from"],
                        names=body.get("names") or {},
                        descriptions=body.get("descriptions") or {},
                        overrides=overrides,
                        assetid=body.get("assetid") or None,
                        add_to_gacha=bool(body.get("add_to_gacha")),
                        gacha_pool=int(body.get("gacha_pool") or 0),
                        dry_run=bool(body.get("dry_run")),
                    )
                return self._json({"ok": rep.ok, "message": rep.summary()})

            if path == "/api/modpack/apply":
                with APP.lock:
                    path_ = body["path"]
                    res = APP.sess.apply_modpack(path_)
                return self._json({"ok": not res.get("failed"),
                                   "message": f"应用了 {res.get('applied', 0)} 处",
                                   "failed": res.get("failed", [])})

            if path == "/api/export":
                with APP.lock:
                    msg = APP.export(body.get("mode", "cache"), body.get("out", ""))
                return self._json({"ok": True, "message": msg})

            if path == "/api/apk":
                with APP.lock:
                    rep = APP.sess.output_apk(
                        body["src"], body["out"],
                        keystore=body.get("keystore") or None,
                        do_sign=bool(body.get("sign", True)),
                    )
                return self._json({"ok": True, "message": "\n".join(rep.get("steps", []))})

            if path == "/api/log/clear":
                with APP.lock:
                    APP.sess.log.clear()
                return self._json({"ok": True})

            return self._err(ValueError(f"没有这个接口：{path}"), 404)
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)


# ---------------------------------------------------------------- 启动


def local_ip() -> str:
    """拿到本机在局域网里的地址（给手机填 URL 用）。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:  # noqa: BLE001
        return "127.0.0.1"


def main() -> int:
    ap = argparse.ArgumentParser(description="口蘑 Mod 工坊 网页版")
    ap.add_argument("--host", default="0.0.0.0", help="监听地址（默认所有网卡）")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("paths", nargs="*", help="启动时先打开这些文件")
    a = ap.parse_args()

    sysenv.ensure_dir(UPLOAD_DIR)
    sysenv.ensure_dir(OUTPUT_DIR)

    if a.paths:
        try:
            APP.sess.open(*a.paths)
        except Exception as exc:  # noqa: BLE001
            APP.sess.say(f"✗ 启动时打开失败：{exc}")

    st = apkbuild.toolchain_status()
    ip = local_ip()
    port = a.port
    print()
    print("  口蘑 Mod 工坊 · 网页版")
    print("  " + "─" * 46)
    print(f"  平台      : {sysenv.platform_name()}   Python {sys.version.split()[0]}")
    print(f"  本机访问  : http://127.0.0.1:{port}")
    print(f"  手机访问  : http://{ip}:{port}    ← 手机和电脑要在同一个 Wi-Fi")
    print(f"  工作目录  : {WORK_DIR}")
    print(f"  APK 工具  : {'✓ 齐了' if st['apksigner'] and st['zipalign'] else '✗ 缺 apksigner/zipalign（只有 APK 重打包要用）'}")
    if a.paths:
        print(f"  已打开    : {APP.sess.source.label if APP.sess.source else '无'}")
    print()
    print("  按 Ctrl+C 停止")
    print()

    srv = ThreadingHTTPServer((a.host, port), Handler)
    srv.daemon_threads = True
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  已停止")
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
