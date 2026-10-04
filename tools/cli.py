#!/usr/bin/env python3
"""口蘑 Mod 工坊 —— 命令行版。

GUI 能做的事这里都能做，方便批处理和复现。

    # 看有哪些包
    python3 tools/cli.py list 数据包.zip dp.apk

    # 看某个包里有什么
    python3 tools/cli.py list 数据包.zip --bundle spdata --type TextAsset

    # 把资源导出来当模板
    python3 tools/cli.py dump 数据包.zip spdata -o out/模板 --type TextAsset

    # 按配方做模组包
    python3 tools/cli.py build-mod 数据包.zip --recipe recipe.json -o 我的模组.pmmod

    # 把模组打到源上，出 UnityCache 产物
    python3 tools/cli.py apply 我的模组.pmmod 数据包.zip -o out/ --mode cache

    # 打进 APK（重打包 + 对齐 + 签名）
    python3 tools/cli.py pack-apk dp.apk 我的模组.pmmod -o out/mod.apk

    # 工具链自检
    python3 tools/cli.py doctor

配方（recipe.json）—— 路径相对配方文件所在目录：

    {
      "name": "抽卡全保底",
      "author": "某人",
      "edits": [
        {"bundle": "appdata", "asset": "GachaDefault",
         "type": "TextAsset", "file": "edits/gacha.json"},
        {"bundle": "anime101", "asset": "CharacterAnimeRickBack",
         "type": "Texture2D", "file": "edits/rick.png"}
      ]
    }
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
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modkit import apkbuild, assetops, modpack, paths, session as session_mod  # noqa: E402
from modkit.source import Source, summarize  # noqa: E402


def _open(paths_in: list[str], ref_specs: list[str] | None = None) -> session_mod.Session:
    s = session_mod.Session()
    s.open(*paths_in)
    for spec in ref_specs or []:
        _choose_ref(s, spec)
    return s


def _choose_ref(s: session_mod.Session, spec: str) -> None:
    """``--ref text=apk:Android`` —— 指定改哪一份副本。

    同一个逻辑包可能在多处存在（``text`` 就同时有 APK 的 Android / .iOS 两份
    和下载缓存一份）。不指定的话默认取下载缓存那份。
    """
    name, _, sel = spec.partition("=")
    layout, _, plat = sel.partition(":")
    plat = plat.lstrip(".").lower()
    for r in s.refs_for(name):
        got = (r.platform or "").lstrip(".").lower()
        if r.layout == layout and (not plat or got == plat):
            s.choose(name, r)
            return
    have = "，".join(f"{r.layout}:{r.platform or '-'}" for r in s.refs_for(name))
    raise SystemExit(f"找不到副本：{spec}（{name} 现有：{have}）")


# ---------------------------------------------------------------- list


def cmd_list(a) -> int:
    src = Source.open(*a.paths)
    if not a.bundle:
        print(f"{src.label}\n  {summarize(src.refs)}\n")
        print(f"  {'包名':<34}{'来源':<10}{'version':>8}{'大小':>12}")
        for name in src.bundle_names():
            refs = src.find(name)
            ref = refs[0]
            vers = src.version_of(name)
            extra = f"  (+{len(refs) - 1} 份副本)" if len(refs) > 1 else ""
            print(f"  {name:<34}{ref.layout_label:<10}"
                  f"{(vers if vers is not None else '-'):>8}"
                  f"{(ref.size or 0) / 1024:>10.0f} KB{extra}")
        print(f"\n共 {len(src)} 个包")
        return 0

    s = _open(a.paths, a.ref)
    b = s.bundle(a.bundle, eager=True)
    if b is None:
        print(f"没有这个包：{a.bundle}", file=sys.stderr)
        return 2
    if not b.ok:
        print(f"解析失败：{b.error}", file=sys.stderr)
        return 3
    ref = s.ref_for(a.bundle)
    print(f"{a.bundle}  ← {ref.layout_label}  {ref.container}\n")
    counts = b.type_counts()
    print("  类型分布：" + "，".join(f"{k}×{v}" for k, v in counts.items()) + "\n")
    assets = b.assets
    if a.type:
        assets = [x for x in assets if x.type == a.type]
    if a.filter:
        f = a.filter.lower()
        assets = [x for x in assets if f in x.search_key()]
    print(f"  {'类型':<16}{'名字':<44}{'path_id':>22}{'大小':>10}")
    for e in assets[: a.limit]:
        print(f"  {e.type:<16}{e.display[:42]:<44}{e.path_id:>22}{e.size:>10}")
    if len(assets) > a.limit:
        print(f"  … 还有 {len(assets) - a.limit} 个（用 --limit 调大）")
    print(f"\n共 {len(assets)} 个对象")
    return 0


# ---------------------------------------------------------------- dump


def cmd_dump(a) -> int:
    s = _open(a.paths, a.ref)
    out = Path(a.out)
    total = 0
    names = a.bundles or s.source.bundle_names()
    for name in names:
        b = s.bundle(name, eager=True)
        if b is None or not b.ok:
            continue
        assets = b.assets
        if a.type:
            assets = [x for x in assets if x.type == a.type]
        if a.name:
            assets = [x for x in assets if x.name == a.name]
        if not assets:
            continue
        target = out / name
        for e in assets:
            try:
                assetops.export_asset(b, e, target)
                total += 1
            except Exception as exc:  # noqa: BLE001
                if a.verbose:
                    print(f"  ! {name}/{e.display}: {exc}")
        print(f"  {name}: {len(assets)} 个")
    print(f"\n共导出 {total} 个 → {out}")
    return 0


# ---------------------------------------------------------------- build-mod


def cmd_build_mod(a) -> int:
    recipe_path = Path(a.recipe).resolve()
    recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    base = recipe_path.parent

    s = _open(a.paths, a.ref)
    pack = modpack.ModPack(
        name=recipe.get("name", "未命名模组"),
        author=recipe.get("author", ""),
        description=recipe.get("description", ""),
    )

    for i, ed in enumerate(recipe.get("edits", []), 1):
        fname = ed.get("file")
        if not fname:
            continue
        src_file = (base / fname).resolve()
        if not src_file.is_file():
            print(f"  ✗ [{i}] 找不到文件：{src_file}", file=sys.stderr)
            return 4
        b = s.bundle(ed["bundle"], eager=True)
        if b is None or not b.ok:
            print(f"  ✗ [{i}] 打不开包 {ed['bundle']}", file=sys.stderr)
            return 4
        # 按名字或 path_id 定位资源
        want = ed.get("asset")
        pid = ed.get("path_id")
        entry = None
        for x in b.assets:
            if pid is not None and x.path_id == pid:
                entry = x
                break
            if want is not None and x.name == want and (
                ed.get("type") is None or x.type == ed["type"]
            ):
                entry = x
                break
        if entry is None:
            print(f"  ✗ [{i}] {ed['bundle']} 里找不到 {want or pid}", file=sys.stderr)
            return 4
        res = assetops.import_asset(b, entry, src_file, resize=ed.get("resize", True))
        if not res.ok:
            print(f"  ✗ [{i}] {ed['bundle']}/{entry.display}: {res.message}", file=sys.stderr)
            return 4
        print(f"  ✓ [{i}] {ed['bundle']} / {entry.type} {entry.display} ← {src_file.name}")

    out = s.output_modpack(
        a.out,
        name=pack.name,
        author=pack.author,
        description=pack.description,
        prebuilt=a.prebuilt,
    )
    print(f"\n模组包：{out}  （{out.stat().st_size / 1024:.1f} KB）")
    return 0


# ---------------------------------------------------------------- apply


def cmd_apply(a) -> int:
    s = _open(a.paths, a.ref)
    res = s.apply_modpack(a.mod)
    if res.get("failed"):
        for f in res["failed"]:
            print(f"  ⚠ {f}", file=sys.stderr)
    print(f"应用了 {len(s.modified)} 个包，{res.get('applied', 0)} 处替换")

    out = Path(a.out)
    if a.mode in ("cache", "all"):
        rep = s.output_cache(out / "UnityCache产物", only_modified=not a.full)
        print(f"  UnityCache → {rep['dir']}（{len(rep['written'])} 个包）")
    if a.mode in ("cdn", "all"):
        rep = s.output_cdn(out / "CDN产物")
        print(f"  CDN       → {rep['dir']}（{len(rep['written'])} 个包）")
    if a.mode in ("bundles", "all"):
        rep = s.export_bundles(out / "bundle产物")
        print(f"  bundle    → {rep['dir']}（{len(rep['files'])} 个）")
    return 0


# ---------------------------------------------------------------- pack-apk


def cmd_pack_apk(a) -> int:
    s = _open([a.apk] + list(a.extra), a.ref)
    src_label = s.source.label if s.source else "?"
    print(f"  源：{src_label}（{len(s.source) if s.source else 0} 个包）")

    applied_failed: list[str] = []
    if a.mod:
        res = s.apply_modpack(a.mod)
        applied_failed = list(res.get("failed") or [])
        print(f"  应用模组：成功 {res.get('applied', 0)} 处，失败 {len(applied_failed)} 处")

    # ---- 先把「为什么不行」讲清楚，再谈打包
    if applied_failed:
        print()
        print("  ⚠ 这些改动没能应用：")
        for f in applied_failed[:10]:
            print("     " + f)
        print()
        print("  模组改的包必须在当前源里存在。解决办法二选一：")
        print("    · 把含这些包的文件也传进来：")
        print(f"        pack-apk {a.apk} 口蘑数据包.zip --mod {a.mod or 'x.pmmod'} -o {a.out}")
        print("    · 或者换用自带全部资源的加强版 APK：")
        print("        pack-apk PocketMortys-加强版.apk --mod x.pmmod -o out.apk")
        print()

    if not s.modified:
        if applied_failed:
            print("  ✗ 没有任何改动落到源上，无法打包")
            return 4
        print("  没有任何改动。先给 --mod 指定模组包，或走 build-mod / apply 流程")
        return 2

    n_log = len(s.log)
    reps = s.apk_replacements()
    d = s.apk_diagnosis()

    if d["where"]:
        print()
        print("  每个改动包在哪：")
        for name, locs in d["where"].items():
            mark = "✓" if name in d["in_apk"] else "✗"
            tail = "" if mark == "✓" else "   ← 不在 APK 里，打不进去"
            print(f"    {mark} {name:<14}{'/'.join(locs)}{tail}")

    if d["not_in_apk"] or not d["has_apk"]:
        print()
        for line in d["advice"]:
            print("  ⚠ " + line.replace("**", ""))
        print()

    if not reps:
        print("  ✗ 没有任何改动能打进这个 APK")
        if d["not_in_apk"]:
            print(f"    （{len(d['not_in_apk'])} 个改动包都不在 APK 里）")
        return 3

    print()
    print(f"  将替换 APK 内 {len(reps)} 个条目：")
    for k in reps:
        print(f"    {k}")
    print()

    rep = s.output_apk(a.apk, a.out, keystore=a.keystore, do_sign=not a.no_sign,
                       bump_versions=a.bump)
    # output_apk 会把每一步都写进日志，这里统一打一遍就够（别和 steps 重复打）
    for line in s.log[n_log:]:
        print("  " + line)
    if not s.log[n_log:] and rep.get("steps"):
        for line in rep["steps"]:
            print("  " + line)

    ok, msg = apkbuild.verify(a.out)
    print(f"  {'✓' if ok else '✗'} apksigner verify: {msg.splitlines()[0] if msg else ''}")
    print(f"\n输出：{a.out}  ({Path(a.out).stat().st_size / 1e6:.1f} MB)")
    return 0 if ok or a.no_sign else 5


# ---------------------------------------------------------------- doctor


def cmd_doctor(a) -> int:
    st = apkbuild.toolchain_status()
    print("工具链：")
    for k, v in st.items():
        print(f"  {'✓' if v else '✗'} {k:<10} {v or '未找到'}")
    from modkit import bundle as B

    print(f"\nUnityPy {'可用' if hasattr(B, 'UnityPy') else '缺失'}")
    print(f"设备缓存目录：{paths.DEVICE_FILES_DIR}")
    print(f"包名：{paths.DEFAULT_PACKAGE}")
    return 0 if st["apksigner"] and st["zipalign"] else 1


# ---------------------------------------------------------------- add-entry


def cmd_add_entry(a) -> int:
    """新增一条莫蒂 / 道具 / 技能。"""
    from modkit import entries as E

    s = _open(a.paths, a.ref)
    if a.kind not in E.KINDS:
        print(f"不认识的类型：{a.kind}（可选：{'/'.join(E.KINDS)}）", file=sys.stderr)
        return 2

    # --set 表.字段=值  解析
    overrides: dict[str, dict] = {}
    for spec in a.set or []:
        field, _, val = spec.partition("=")
        table, _, fname = field.rpartition(".")
        if not table or not fname:
            print(f"--set 格式应为 表.字段=值，收到：{spec}", file=sys.stderr)
            return 2
        overrides.setdefault(table, {})[fname] = _coerce(val)

    names = {}
    if a.name:
        names["ZH_CN"] = a.name
    if a.en_name:
        names["EN"] = a.en_name

    rep = E.add_entry(
        s, a.kind, a.id, a.clone_from,
        names=names,
        descriptions={"ZH_CN": a.desc, "EN": a.desc} if a.desc else {},
        langs=a.langs or None,
        overrides=overrides,
        assetid=a.assetid,
        add_to_gacha=a.gacha is not None,
        gacha_pool=a.gacha or 0,
        dry_run=a.dry_run,
    )
    print(rep.summary())
    if not rep.ok:
        return 3
    if a.dry_run:
        return 0

    # 产物
    out = Path(a.out)
    if a.mode in ("cache", "all"):
        r = s.output_cache(out / "UnityCache产物")
        print(f"  UnityCache → {r['dir']}（{len(r['written'])} 个包）")
    if a.mode in ("cdn", "all"):
        r = s.output_cdn(out / "CDN产物")
        print(f"  CDN       → {r['dir']}（{len(r['written'])} 个包）")
    if a.mode in ("bundles", "all"):
        r = s.export_bundles(out / "bundle产物")
        print(f"  bundle    → {r['dir']}（{len(r['files'])} 个）")
    if a.modpack:
        p = s.output_modpack(a.modpack, name=a.mod_name or f"新增{a.kind} {a.id}")
        print(f"  模组包    → {p}")
    return 0


def _coerce(v: str):
    """把命令行里的字符串转成合适的类型（数字/布尔留原样）。"""
    low = v.lower()
    if low in ("true", "false"):
        return low == "true"
    for cast in (int, float):
        try:
            return cast(v)
        except ValueError:
            pass
    return v


def cmd_list_entries(a) -> int:
    """列出可新增的类型和现有条目。"""
    from modkit import entries as E

    s = _open(a.paths, a.ref)
    for k, kind in E.KINDS.items():
        try:
            ids = E.list_ids(s, k)
        except Exception as exc:  # noqa: BLE001
            print(f"  {kind.label:<6} 读不到：{exc}")
            continue
        nums = E.existing_numbers(s, k)
        extra = "，".join(f"{n}={v}" for n, v in nums.items()) if nums else ""
        print(f"  {k:<7}{kind.label:<6}{len(ids):>5} 条   {extra}")
        if a.show:
            loc = {}
            try:
                b = s.bundle("text", eager=True)
                e = next((x for x in b.assets if x.name == "ZH_CN"), None)
                if e is not None:
                    loc = (json.loads(b.preview_text(e)).get(kind.loc_section) or {})
            except Exception:  # noqa: BLE001
                pass
            for i in ids[: a.limit]:
                nm = (loc.get(i) or {}).get("name", "") if isinstance(loc.get(i), dict) else ""
                print(f"          {i:<40}{nm}")
            if len(ids) > a.limit:
                print(f"          … 还有 {len(ids) - a.limit} 条")
    return 0


# ---------------------------------------------------------------- check / fix


def _print_report(rep, verbose=True) -> None:
    print()
    print("  " + rep.summary())
    if not rep.issues:
        return
    print()
    shown = 0
    limit = None if verbose else 25
    for i in rep.issues:
        if limit is not None and shown >= limit:
            print(f"  …还有 {len(rep.issues) - shown} 条（-v 看全部）")
            break
        print("  " + i.line())
        shown += 1
    print()


def cmd_check(a) -> int:
    """给数据表做体检。"""
    from modkit import validate as V

    s = _open(a.paths, a.ref)
    rep = s.check()
    _print_report(rep, verbose=a.verbose)
    if rep.errors:
        print("  这些都是**会让游戏起不来**的问题（主键冲突之类）。")
        print("  自动修： tools/cli.py fix ...      或加 --fix 直接修")
        return 1
    return 0


def cmd_fix(a) -> int:
    """体检 + 自动修，可选直接出产物。"""
    from modkit import validate as V

    s = _open(a.paths, a.ref)
    before = s.check()
    print()
    print("  修之前：" + before.summary())
    if not before.issues:
        print("  没有要修的。")
        return 0
    if a.dry_run:
        print("\n  打算这么修：")
        for d in V.repair(s, before, dry_run=True):
            print("    · " + d)
        return 0

    done = V.repair(s, before)
    print("\n  修了这些：")
    for d in done:
        print("    · " + d)
    after = s.check()
    print()
    print("  修之后：" + after.summary())
    for i in after.issues[:10]:
        print("    " + i.line())

    if a.out and s.modified:
        out = Path(a.out)
        if a.mode in ("cache", "all"):
            r = s.output_cache(out / "UnityCache产物")
            print(f"\n  UnityCache → {r['dir']}（{len(r['written'])} 个包）")
        if a.mode in ("bundles", "all"):
            r = s.export_bundles(out / "bundle产物")
            print(f"  bundle → {r['dir']}（{len(r['files'])} 个）")
        if a.apk:
            rep2 = s.output_apk(a.apk, a.modpack or str(out / "fixed.apk"))
            for line in rep2.get("steps", []):
                print("  " + line)
            print(f"  修好的 APK → {a.modpack or out / 'fixed.apk'}")
    return 0 if after.ok else 1


# ---------------------------------------------------------------- add-character


def cmd_add_character(a) -> int:
    """完全新增一个角色：连它自己的 15 帧美术一起造。"""
    from modkit import character as CH

    s = _open(a.paths, a.ref)

    images: dict[str, str] = {}
    for spec in a.image or []:
        frame, _, path = spec.partition("=")
        if not path:
            print(f"--image 格式应为 帧=文件，收到：{spec}", file=sys.stderr)
            return 2
        if frame not in CH.FRAMES:
            print(f"不认识的帧 {frame!r}（可选：{'、'.join(CH.FRAMES)}）", file=sys.stderr)
            return 2
        images[frame] = path

    overrides: dict[str, dict] = {}
    for spec in a.set or []:
        field, _, val = spec.partition("=")
        table, _, fname = field.rpartition(".")
        if not table or not fname:
            print(f"--set 格式应为 表.字段=值，收到：{spec}", file=sys.stderr)
            return 2
        overrides.setdefault(table, {})[fname] = _coerce(val)

    names = {}
    if a.name:
        names["ZH_CN"] = a.name
    if a.en_name:
        names["EN"] = a.en_name

    res = CH.add_character(
        s, a.id, a.clone_from,
        new_asset=a.assetid, new_bundle=a.bundle,
        images=images, names=names,
        descriptions={"ZH_CN": a.desc, "EN": a.desc} if a.desc else {},
        overrides=overrides,
        add_to_gacha=a.gacha is not None, gacha_pool=a.gacha or 0,
        dry_run=a.dry_run,
    )
    print(res["message"])
    if not res["ok"]:
        return 3
    if a.dry_run:
        return 0

    out = Path(a.out)
    if a.mode in ("cache", "all"):
        r = s.output_cache(out / "UnityCache产物")
        print(f"  UnityCache → {r['dir']}（{len(r['written'])} 个包）")
    if a.mode in ("bundles", "all"):
        r = s.export_bundles(out / "bundle产物")
        print(f"  bundle → {r['dir']}（{len(r['files'])} 个）")
    if a.modpack:
        p = s.output_modpack(a.modpack, name=a.mod_name or f"新角色 {a.id}")
        print(f"  模组包 → {p}")
    if a.apk:
        r = s.output_apk(a.apk, a.apache_out or str(out / "newchar.apk"),
                         bump_versions=True)
        for line in r.get("steps", []):
            print("  " + line)
        print(f"  APK → {a.apache_out or out / 'newchar.apk'}")
    return 0


def _add_ref_arg(p) -> None:
    p.add_argument(
        "--ref", action="append", metavar="包=来源[:平台]",
        help="指定改哪一份副本，例如 --ref text=apk:Android（默认取下载缓存）",
    )


# ---------------------------------------------------------------- main


def main() -> int:
    ap = argparse.ArgumentParser(
        description="口蘑 Mod 工坊 命令行",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="列出资源包 / 包内对象")
    p.add_argument("paths", nargs="+")
    p.add_argument("--bundle", help="看这个包里的对象")
    p.add_argument("--type", help="只看这种类型")
    p.add_argument("--filter", help="名字筛选")
    p.add_argument("--limit", type=int, default=60)
    _add_ref_arg(p)
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("dump", help="导出资源（当模板用）")
    p.add_argument("paths", nargs="+")
    p.add_argument("-o", "--out", required=True)
    p.add_argument("--bundles", nargs="*", help="只导这些包（默认全部）")
    p.add_argument("--type", help="只导这种类型")
    p.add_argument("--name", help="只导这个名字的资源")
    p.add_argument("-v", "--verbose", action="store_true")
    _add_ref_arg(p)
    p.set_defaults(fn=cmd_dump)

    p = sub.add_parser("build-mod", help="按配方生成 .pmmod")
    p.add_argument("paths", nargs="+")
    p.add_argument("--recipe", required=True)
    p.add_argument("-o", "--out", required=True)
    p.add_argument("--prebuilt", action="store_true", help="连成品 bundle 一起打包")
    _add_ref_arg(p)
    p.set_defaults(fn=cmd_build_mod)

    p = sub.add_parser("apply", help="把 .pmmod 打到源上并出产物")
    p.add_argument("mod")
    p.add_argument("paths", nargs="+")
    p.add_argument("-o", "--out", required=True)
    p.add_argument("--mode", default="cache", choices=["cache", "cdn", "bundles", "all"])
    p.add_argument("--full", action="store_true", help="连未改动的包一起导出")
    _add_ref_arg(p)
    p.set_defaults(fn=cmd_apply)

    p = sub.add_parser("pack-apk", help="改 APK 并重打包 + 签名")
    p.add_argument("apk", help="原 APK；接口里还可以再跟数据包，例如 "
                               "pack-apk dp.apk 口蘑数据包.zip --mod x.pmmod")
    p.add_argument("--mod", help="要应用的 .pmmod")
    p.add_argument("extra", nargs="*", help="额外的源（可选）")
    p.add_argument("-o", "--out", required=True)
    p.add_argument("--keystore")
    p.add_argument("--no-sign", action="store_true")
    p.add_argument("--bump", action="store_true",
                   help="把改动的包 version +1（让设备丢掉旧缓存、重新从 APK 播种）")
    _add_ref_arg(p)
    p.set_defaults(fn=cmd_pack_apk)

    p = sub.add_parser("add-entry", help="新增莫蒂 / 道具 / 技能")
    p.add_argument("paths", nargs="+")
    p.add_argument("--kind", required=True, choices=["morty", "item", "attack"])
    p.add_argument("--id", required=True, help="新条目的 id")
    p.add_argument("--from", dest="clone_from", required=True, help="克隆哪一条做模板")
    p.add_argument("--name", help="中文名")
    p.add_argument("--en-name", help="英文名")
    p.add_argument("--desc", help="描述")
    p.add_argument("--langs", nargs="*", help="新名字写进哪些语言（默认中文+英文）")
    p.add_argument("--set", action="append", metavar="表.字段=值",
                   help="覆盖字段，可多次，例如 --set spdata/MortyInfo.hpbase=99")
    p.add_argument("--assetid", help="借用哪个已存在的美术资源（默认沿用模板）")
    p.add_argument("--gacha", type=int, metavar="N", help="顺便加进第 N 个卡池（从 0 数）")
    p.add_argument("--dry-run", action="store_true", help="只预演，不写入")
    p.add_argument("--modpack", help="顺便导出成 .pmmod")
    p.add_argument("--mod-name", help="模组包名字")
    p.add_argument("-o", "--out", default="新增产物")
    p.add_argument("--mode", default="cache", choices=["cache", "cdn", "bundles", "all", "none"])
    _add_ref_arg(p)
    p.set_defaults(fn=cmd_add_entry)

    p = sub.add_parser("entries", help="列出可新增的类型与现有条目")
    p.add_argument("paths", nargs="+")
    p.add_argument("--show", action="store_true", help="连条目一起列出来")
    p.add_argument("--limit", type=int, default=30)
    _add_ref_arg(p)
    p.set_defaults(fn=cmd_list_entries)

    p = sub.add_parser("check", help="数据表体检（找「会让游戏崩」的问题）")
    p.add_argument("paths", nargs="+")
    p.add_argument("-v", "--verbose", action="store_true", help="列出全部问题")
    _add_ref_arg(p)
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("fix", help="体检 + 自动修（主键重复、编号冲突等）")
    p.add_argument("paths", nargs="+")
    p.add_argument("-o", "--out", help="修完顺手出产物到这个目录")
    p.add_argument("--mode", default="cache", choices=["cache", "bundles", "all", "none"])
    p.add_argument("--apk", help="顺手重打包这个 APK")
    p.add_argument("--apk-out", dest="modpack", help="重打包输出路径")
    p.add_argument("--dry-run", action="store_true", help="只说打算怎么修")
    _add_ref_arg(p)
    p.set_defaults(fn=cmd_fix)

    p = sub.add_parser("add-character", help="完全新增一个角色（连自己的美术一起造）")
    p.add_argument("paths", nargs="+")
    p.add_argument("--id", required=True, help="新莫蒂的 id，例如 MortyMyGuy")
    p.add_argument("--from", dest="clone_from", required=True, help="拿哪只当模板")
    p.add_argument("--assetid", help="新的美术 id（默认跟 --id 一样）")
    p.add_argument("--bundle", help="新包名（默认 pm_<assetid小写>）")
    p.add_argument("--name", help="中文名")
    p.add_argument("--en-name", help="英文名")
    p.add_argument("--desc", help="描述")
    p.add_argument("--image", action="append", metavar="帧=图片",
                   help=f"替换某一帧，可多次。帧可选：{'、'.join(__import__('modkit.character', fromlist=['FRAMES']).FRAMES)}")
    p.add_argument("--set", action="append", metavar="表.字段=值", help="覆盖字段")
    p.add_argument("--gacha", type=int, metavar="N", help="顺便加进第 N 个卡池")
    p.add_argument("--apk", help="顺便重打包这个 APK（会自动 --bump）")
    p.add_argument("--apk-out", dest="apache_out", help="重打包输出路径")
    p.add_argument("--modpack", help="顺便导出成 .pmmod")
    p.add_argument("--mod-name", help="模组包名字")
    p.add_argument("--dry-run", action="store_true", help="只预演")
    p.add_argument("-o", "--out", default="新角色产物")
    p.add_argument("--mode", default="cache", choices=["cache", "bundles", "all", "none"])
    _add_ref_arg(p)
    p.set_defaults(fn=cmd_add_character)

    p = sub.add_parser("doctor", help="工具链自检")
    p.set_defaults(fn=cmd_doctor)

    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
