#!/usr/bin/env python3
"""网页版接口的端到端自测（只用标准库）。

用法::

    # 先起服务
    python3 web/server.py --port 8791 &
    # 再跑测试
    python3 tools/webtest.py --base http://127.0.0.1:8791 --data 口蘑数据包.zip

覆盖：打开源 → 列包 → 列资源 → 读文本 → 改文本 → 校验生效 → 撤销 →
新增条目 → 导出产物 → 上传替换贴图 → 读日志。
"""

from __future__ import annotations

def _force_utf8() -> None:
    """Windows 控制台默认 cp1252/cp936，print 中文会 UnicodeEncodeError。"""
    import sys
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass


_force_utf8()


import argparse
from pathlib import Path
import io
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

FAIL = 0


def check(label: str, cond: bool, extra: str = "") -> None:
    global FAIL
    if not cond:
        FAIL += 1
    print(f"  {'✓' if cond else '✗'} {label}" + (f"  {extra}" if extra else ""))


def api(base: str, path: str, body=None, method: str | None = None, raw: bytes | None = None):
    url = base + path
    if raw is not None:
        req = urllib.request.Request(url, data=raw, method="POST")
    elif body is not None:
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(), method=method or "POST",
            headers={"Content-Type": "application/json"},
        )
    else:
        req = urllib.request.Request(url, method=method or "GET")
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            data = r.read()
            ctype = r.headers.get("Content-Type", "")
            if "json" in ctype:
                return json.loads(data)
            return {"ok": True, "_raw": data, "_ctype": ctype}
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read())
        except Exception:  # noqa: BLE001
            return {"ok": False, "error": f"HTTP {e.code}"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8791")
    ap.add_argument("--data", required=True, help="要打开的数据包 zip / apk")
    ap.add_argument("--img", help="用来测替换的图片（可选）")
    a = ap.parse_args()
    B = a.base.rstrip("/")

    print("\n[1] 静态页与环境")
    r = api(B, "/")
    check("首页返回 HTML", b"<!DOCTYPE html" in r.get("_raw", b""), r.get("_ctype", ""))
    env = api(B, "/api/env")
    check("拿到平台信息", env.get("ok") and env.get("platform"), env.get("platform", ""))
    check("输出目录可用", bool(env.get("output")), env.get("output", ""))

    print("\n[2] 打开源")
    r = api(B, "/api/open", {"paths": [str(Path(a.data).resolve())]})
    check("打开成功", r.get("ok"), r.get("message") or r.get("error", ""))
    st = api(B, "/api/state")
    check("state 报告已打开", st.get("opened"))
    check("包数 > 100", len(st.get("bundles", [])) > 100, str(len(st.get("bundles", []))))
    check("包带中文说明",
          any(b.get("doc") for b in st.get("bundles", [])),
          next((b["doc"] for b in st.get("bundles", []) if b.get("doc")), ""))

    print("\n[3] 列资源")
    bd = api(B, "/api/bundle?name=appdata")
    check("拿到 appdata 资源列表", bd.get("ok"), str(bd.get("counts")))
    check("类型带中文短名",
          all(t.get("short") for t in bd.get("types", [])),
          "，".join(f"{t['type']}={t['short']}" for t in bd.get("types", [])))
    gacha = next((x for x in bd.get("assets", []) if x.get("name") == "GachaDefault"), None)
    check("找到 GachaDefault", gacha is not None)
    if gacha is None:
        return 1
    pid = gacha["pid"]

    print("\n[4] 读文本 / 改文本")
    ai = api(B, f"/api/asset?bundle=appdata&pid={pid}")
    check("读出文本", ai.get("ok") and "drop_rates" in ai.get("text", ""),
          f"{ai.get('text_len')} 字符")
    doc = json.loads(ai["text"])
    before = doc["drop_rates"]
    doc["drop_rates"] = [100, 0, 0, 0]
    r = api(B, "/api/asset/text",
            {"bundle": "appdata", "pid": pid, "text": json.dumps(doc, ensure_ascii=False)})
    check("写入成功", r.get("ok"), r.get("message") or r.get("error", ""))
    ai2 = api(B, f"/api/asset?bundle=appdata&pid={pid}")
    check("读回来是改后的值",
          json.loads(ai2["text"])["drop_rates"] == [100, 0, 0, 0],
          f"{before} → {json.loads(ai2['text'])['drop_rates']}")
    check("被标记为已修改", ai2.get("modified"))
    st2 = api(B, "/api/state")
    check("state 里出现改动", len(st2.get("modified", [])) >= 1,
          "，".join(m["bundle"] for m in st2.get("modified", [])))

    print("\n[5] 撤销")
    r = api(B, "/api/asset/revert", {"bundle": "appdata", "pid": pid})
    check("撤销成功", r.get("ok"), r.get("message") or r.get("error", ""))
    ai3 = api(B, f"/api/asset?bundle=appdata&pid={pid}")
    check("恢复成原值", json.loads(ai3["text"])["drop_rates"] == before,
          str(json.loads(ai3["text"])["drop_rates"]))

    print("\n[6] 图片接口")
    bd2 = api(B, "/api/bundle?name=anime101")
    tex = next((x for x in bd2.get("assets", []) if x.get("type") == "Texture2D"), None)
    if tex:
        img = api(B, f"/api/asset/image?bundle=anime101&pid={tex['pid']}&max=256")
        raw = img.get("_raw", b"")
        check("能取到 PNG 缩略图", raw[:8] == b"\x89PNG\r\n\x1a\n", f"{len(raw)} 字节")
        info = api(B, f"/api/asset?bundle=anime101&pid={tex['pid']}")
        check("报告了原尺寸", info.get("width") and info.get("height"),
              f"{info.get('width')}×{info.get('height')}")
    else:
        check("找到贴图", False)

    print("\n[7] 新增条目接口")
    ent = api(B, "/api/entries?kind=morty")
    check("拿到莫蒂模板列表", ent.get("ok") and len(ent.get("ids", [])) > 100,
          f"{len(ent.get('ids', []))} 条")
    check("字段带中文名",
          all(f.get("label") for t in ent.get("tables", []) for f in t.get("fields", [])),
          "，".join(f.get("label", "") for f in ent.get("tables", [{}])[0].get("fields", [])[:4]))
    r = api(B, "/api/new-entry", {
        "kind": "morty", "id": "MortyWebTest", "from": "MortyDefault",
        "names": {"ZH_CN": "网页版测试莫蒂"},
        "overrides": {"spdata/MortyInfo": {"hpbase": "55", "attackbase": "44",
                                           "defencebase": "33", "speedbase": "22"}},
        "dry_run": True,
    })
    check("预演通过", r.get("ok"), (r.get("message") or r.get("error", ""))[:80])
    r2 = api(B, "/api/new-entry", {
        "kind": "morty", "id": "MortyWebTest", "from": "MortyDefault",
        "names": {"ZH_CN": "网页版测试莫蒂"},
        "overrides": {"spdata/MortyInfo": {"hpbase": "55", "attackbase": "44",
                                           "defencebase": "33", "speedbase": "22"}},
    })
    check("真正创建成功", r2.get("ok"), (r2.get("message") or r2.get("error", ""))[:120])
    st3 = api(B, "/api/state")
    check("state 里多个包被改", len(st3.get("modified", [])) >= 3,
          "，".join(m["bundle"] for m in st3.get("modified", [])))

    print("\n[8] 上传替换（贴图）")
    if a.img and Path(a.img).is_file():
        payload = Path(a.img).read_bytes()
        up = api(B, "/api/upload?name=test.png&into=webtest", raw=payload)
        check("上传成功", up.get("ok"), f"{up.get('size')} 字节")
        if tex and up.get("ok"):
            r = api(B, "/api/asset/file",
                    {"bundle": "anime101", "pid": tex["pid"], "path": up["path"]})
            check("替换成功", r.get("ok"), (r.get("message") or r.get("error", ""))[:80])
    else:
        print("  （没给 --img，跳过）")

    print("\n[9] 导出产物")
    r = api(B, "/api/export", {"mode": "cache"})
    check("导出成功", r.get("ok"), (r.get("message") or r.get("error", ""))[:200])
    if r.get("ok"):
        out = env.get("work", "/tmp") + "/output"
        check("产物目录存在", Path(out).is_dir(), out)

    print("\n[10] 日志与清空")
    st4 = api(B, "/api/state")
    check("日志有内容", len(st4.get("log", [])) > 0, f"{len(st4.get('log', []))} 行")
    r = api(B, "/api/log/clear", {})
    check("清空日志", r.get("ok"))
    st5 = api(B, "/api/state")
    check("日志已空", len(st5.get("log", [])) == 0)

    # --- 新增条目页面要用到的数据（分类筛选 / BEFORE-AFTER 对比）
    ent = api(B, "/api/entries?kind=morty")
    st = ent.get("stats") or {}
    check("entries 带每条模板的数值", len(st) >= 100, f"{len(st)} 条")
    sample = next(iter(st.values()), {})
    check("数值字段齐全（hp/atk/def/spd/element/evolution）",
          {"hp", "atk", "def", "spd", "element", "evolution"} <= set(sample),
          str(sorted(sample)))
    check("属性值可用于筛选",
          any(v.get("element") == "Rock" for v in st.values()))
    check("能算出「高体力」子集",
          sum(1 for v in st.values() if v.get("hp", 0) >= 110) > 0)

    chk = api(B, "/api/check")
    check("体检接口可用", chk.get("ok") is True, str(chk.get("summary"))[:40])
    check("体检返回 issues 列表", isinstance(chk.get("issues"), list))

    # --- 拖拽上传（网页版把文件拖进来就用这个接口）
    import urllib.request
    boundary = "----pmmodkit"
    src = a.data
    with open(src, "rb") as f:
        blob = f.read()
    body = (f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="files"; filename="{Path(src).name}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n").encode() + blob + \
           f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        B + "/api/upload-many", data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            up = json.loads(r.read().decode())
        check("拖拽上传接口可用", up.get("ok") is True, str(up.get("error") or "")[:60])
        check("上传后直接当成来源打开",
              (api(B, "/api/state").get("bundles") or []) != [],
              f"{len(api(B, '/api/state').get('bundles') or [])} 个包")
    except Exception as exc:  # noqa: BLE001
        check("拖拽上传接口可用", False, f"{type(exc).__name__}: {exc}")

    print()
    if FAIL:
        print(f"❌ {FAIL} 项失败")
    else:
        print("✅ 网页版接口全部通过")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
