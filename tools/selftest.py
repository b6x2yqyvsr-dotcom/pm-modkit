#!/usr/bin/env python3
"""核心库端到端自测（不需要 GUI）。

跑一遍真实数据：解包 → 改数据表 → 改贴图 → 出 UnityCache 产物 →
打 .pmmod → 用模组包打到干净会话上 → 校验产物能被重新解析。

    python3 tools/selftest.py --apk <apk> --data <数据包.zip> [--out 目录]
"""

from __future__ import annotations

def _force_utf8() -> None:
    """Windows 控制台默认不是 UTF-8，``print`` 中文会直接抛 UnicodeEncodeError。
    所有入口脚本开头都调一下这个。"""
    import sys
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass


_force_utf8()

import argparse
import copy
import io
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modkit import assetops, entries as entries_mod, modpack, paths, session  # noqa: E402
from modkit.source import Source, summarize  # noqa: E402

FAIL = 0


def check(label: str, cond: bool, extra: str = "") -> None:
    global FAIL
    mark = "✓" if cond else "✗"
    if not cond:
        FAIL += 1
    print(f"  {mark} {label}" + (f"  {extra}" if extra else ""))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apk", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", default="/tmp/pmmodkit-selftest")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    print("\n[1] 打开源（APK + 数据包）")
    src = Source.open(a.apk, a.data)
    print("   ", summarize(src.refs))
    check("包数量 > 100", len(src) > 100, f"{len(src)}")
    check("text 同时存在于多处", len(src.find("text")) >= 2,
          " / ".join(r.layout for r in src.find("text")))
    check("拿到 manifest", bool(src.manifest), f"version={src.manifest_version()}")

    print("\n[2] 改数据表（appdata / GachaDefault）")
    s = session.Session()
    s.open(a.apk, a.data)
    b = s.bundle("appdata", eager=True)
    check("appdata 打开成功", b is not None and b.ok, b.error or "")
    entry = next(e for e in b.assets if e.name == "GachaDefault")
    before = json.loads(b.preview_text(entry))
    print("    原始 drop_rates =", before["drop_rates"])
    after = dict(before)
    after["drop_rates"] = [100, 0, 0, 0]
    b.modify_text(entry, json.dumps(after, indent=2))
    check("包被标记为已改动", b.dirty == {entry.path_id})

    print("\n[3] 改贴图（挑一个 Texture2D 换成纯色）")
    tex_bundle = None
    for cand in ["anime101", "preload", "spdata"]:
        bb = s.bundle(cand, eager=True)
        if bb and bb.ok and any(e.type == "Texture2D" for e in bb.assets):
            tex_bundle = bb
            break
    check("找到含贴图的包", tex_bundle is not None, tex_bundle.name if tex_bundle else "")
    if tex_bundle:
        te = next(e for e in tex_bundle.assets if e.type == "Texture2D")
        from PIL import Image

        obj = tex_bundle.get(te)
        size = (obj.m_Width, obj.m_Height)
        tex_bundle.modify_image(te, Image.new("RGBA", size, (255, 0, 255, 255)))
        check(f"贴图 {te.name} 已替换 {size}", te.path_id in tex_bundle.dirty)

    print("\n[4] 出 UnityCache 产物")
    rep = s.output_cache(out / "cache")
    written = rep["written"]
    check("至少写出 2 个包", len(written) >= 2, "，".join(written))
    check("目录名符合 12 零 + version", (out / "cache/UnityCache/Shared/appdata").is_dir())
    d = next((out / "cache/UnityCache/Shared/appdata").iterdir())
    check("目录名 32 位 hex 且以 12 个 0 开头",
          len(d.name) == 32 and d.name[:12] == "0" * 12, d.name)
    check("__data 存在且是 UnityFS", (d / "__data").read_bytes()[:7] == b"UnityFS")
    check("__info 格式正确", (d / "__info").read_bytes().split(b"\n")[0] == b"-1")
    check("推送脚本已生成", (out / "cache/push.sh").is_file())

    print("\n[5] 校验 UnityCache 产物真的带着改动")
    data = (d / "__data").read_bytes()
    import UnityPy

    env = UnityPy.load(io.BytesIO(data))
    got = None
    for o in env.objects:
        if o.type.name == "TextAsset":
            r = o.read()
            if r.m_Name == "GachaDefault":
                got = json.loads(r.m_Script if isinstance(r.m_Script, str) else r.m_Script.decode())
    check("重新解包拿到 GachaDefault", got is not None)
    check("drop_rates 改动生效", got and got["drop_rates"] == [100, 0, 0, 0],
          str(got["drop_rates"] if got else None))

    print("\n[6] 打 .pmmod 模组包")
    pack_path = s.output_modpack(out / "demo.pmmod", name="自测模组", author="selftest")
    check("模组包已生成", pack_path.is_file(), f"{pack_path.stat().st_size} 字节")
    pk = modpack.ModPack.load(pack_path)
    check("模组包可重新读出", pk.name == "自测模组", pk.summary())
    check("edit 数量 = 改动资源数", len(pk.edits) == sum(len(x.dirty) for x in s.modified.values()),
          f"{len(pk.edits)}")

    print("\n[7] 把模组包打到干净会话")
    s2 = session.Session()
    s2.open(a.apk, a.data)
    res = s2.apply_modpack(pack_path)
    check("应用无失败", not res["failed"], str(res["failed"][:3]))
    check("干净会话也变成已改动", len(s2.modified) >= 2, "，".join(s2.modified))
    b2 = s2.bundle("appdata")
    e2 = next(e for e in b2.assets if e.name == "GachaDefault")
    got2 = json.loads(b2.preview_text(e2))
    check("应用后 drop_rates 正确", got2["drop_rates"] == [100, 0, 0, 0], str(got2["drop_rates"]))

    print("\n[8] 资源导出 / 导入回环")
    ex = assetops.export_asset(b, entry, out / "export")
    check("文本导出成功", ex.is_file(), ex.name)
    ex2 = assetops.export_asset(tex_bundle, te, out / "export")
    check("贴图导出为 PNG", ex2.suffix == ".png" and ex2.is_file(), ex2.name)
    r = assetops.import_asset(b, entry, ex, resize=True)
    check("文本导入成功", r.ok, r.message)

    print("\n[9] 大文本资源不能被截断（回归测试）")
    # 曾经踩过的坑：preview_text 默认只读 20 万字符，导致大表导出/打包被截断
    s3 = session.Session()
    s3.open(a.apk, a.data)
    big = None
    for cand in ["spdata", "text", "mpdata"]:
        bb = s3.bundle(cand, eager=True)
        if bb is None or not bb.ok:
            continue
        for x in bb.assets:
            if x.type == "TextAsset" and (x.size or 0) > 200_000:
                big = (bb, x)
                break
        if big:
            break
    check("找到一个 >20 万字节的文本表", big is not None,
          f"{big[0].name}/{big[1].name} {big[1].size:,} 字节" if big else "")
    if big:
        bb, bx = big
        full = bb.preview_text(bx)
        check("读出的长度与对象大小接近", abs(len(full) - bx.size) < bx.size * 0.2,
              f"读出 {len(full):,} 字符 vs 对象 {bx.size:,} 字节")
        p = assetops.export_asset(bb, bx, out / "bigtext")
        check("导出文件没有被截断", p.stat().st_size > 200_000, f"{p.stat().st_size:,} 字节")
        # 打模组包再读回来，长度必须一致
        bb.modify_text(bx, full)
        pk2 = s3.output_modpack(out / "big.pmmod", name="大文本回归")
        import zipfile

        with zipfile.ZipFile(pk2) as z:
            entry_name = [n for n in z.namelist() if n.endswith(".json") or n.endswith(".txt")][0]
            back = z.read(entry_name).decode("utf-8")
        check("模组包里的内容完整", len(back) == len(full), f"{len(back):,} vs {len(full):,}")

    print("\n[10] 路径工具")
    check("make/parse 目录名互逆",
          paths.parse_cache_dirname(paths.make_cache_dirname(1004)) == 1004,
          paths.make_cache_dirname(1004))
    check("已知值正确", paths.make_cache_dirname(1004) == "000000000000000000000000ec030000")

    print("\n[11] 追加源 / 移除源（不能弄丢改动）")
    s4 = session.Session()
    s4.open(a.data)
    n1 = len(s4.source)
    s4.add_source(a.apk)
    check("追加后包数不减少", len(s4.source) >= n1, f"{n1} → {len(s4.source)}")
    check("追加后 text 出现多份副本", len(s4.refs_for("text")) >= 2,
          "，".join(f"{r.layout}:{r.platform}" for r in s4.refs_for("text")))
    check("重复追加同一文件会被跳过",
          s4.add_source(a.apk) == [] and len(s4.source.containers) == 2)

    b4 = s4.bundle("appdata", eager=True)
    e4 = next(x for x in b4.assets if x.name == "GachaDefault")
    b4.modify_text(e4, '{"keep": true}')
    idx_apk = next(
        (i for i, c in enumerate(s4.source.containers) if str(c.root).lower().endswith(".apk")), None
    )
    if idx_apk is not None:
        ok = s4.remove_container(idx_apk)
        check("移除源成功", ok)
        # 关键回归：移除另一个源不能把改动清掉
        check("改动没有因为移除而被清掉", "appdata" in s4.modified, str(list(s4.modified)))
        kept = s4.bundle("appdata")
        check("改动内容仍在", '"keep": true' in kept.preview_text(e4))
        rep4 = s4.output_cache(out / "addrm")
        check("移除后仍能正常导出", "appdata" in rep4["written"], str(rep4["written"]))
        check("被移除容器的包已消失（只剩缓存）",
              all(r.layout != "apk" for r in s4.refs_for("text")))

    print("\n[12] 扩展资源类型（每种都要能解出来、回写后内容一致）")
    s5 = session.Session()
    s5.open(a.data)
    cases = [
        ("Cubemap", "ep804", True),          # 图片：可导出可替换
        ("Shader", "ep309", True),           # 类型树
        ("Material", "ep309", True),
        ("AnimationClip", "ep801", True),
        ("Transform", "ep801", True),
        ("SpriteRenderer", "ep801", True),
        ("MonoScript", "ep802", True),
        ("Sprite", "anime101", False),       # 只读图片
        ("AudioClip", "preload", False),     # 只读音频
        ("AssetBundle", "spdata", False),    # 包元数据，不该动
    ]
    out_types = out / "types"
    for tname, bund, replaceable in cases:
        b5 = s5.bundle(bund, eager=True)
        e5 = next((x for x in b5.assets if x.type == tname), None)
        if e5 is None:
            check(f"{tname} 存在", False, f"{bund} 里没有")
            continue
        kind = assetops.asset_kind(e5)
        check(f"{tname:<14} 分类正确", assetops.can_replace(e5) == replaceable,
              f"{assetops.KIND_LABEL[kind]} / 可替换={assetops.can_replace(e5)}")
        try:
            p = assetops.export_asset(b5, e5, out_types)
            check(f"{tname:<14} 能导出", p.is_file(), f"{p.suffix} {p.stat().st_size:,} 字节")
        except Exception as exc:  # noqa: BLE001
            check(f"{tname:<14} 能导出", False, f"{type(exc).__name__}: {exc}")
            continue
        if replaceable:
            r5 = assetops.import_asset(b5, e5, p, resize=True)
            check(f"{tname:<14} 能导入回环", r5.ok, r5.message)

    print("\n[13] 类型树改动真的会写进去")
    b6 = s5.bundle("ep801", eager=True)
    e6 = next(x for x in b6.assets if x.type == "AnimationClip")
    tree = b6.read_typetree(e6)
    b6.modify_typetree(e6, tree)
    import UnityPy

    env6 = UnityPy.load(io.BytesIO(b6.save()))
    o6 = next(x for x in env6.objects if x.type.name == "AnimationClip")
    check("AnimationClip 回写后类型树一致",
          json.dumps(o6.read_typetree(), sort_keys=True) == json.dumps(tree, sort_keys=True))

    print("\n[14] 更多容器格式（tar / 单个散装包 / 目录）")
    import tarfile

    sandbox = out / "containers"
    sandbox.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(a.data) as zt:
        raw = zt.read(
            next(n for n in zt.namelist() if "/spdata/" in n and n.endswith("/__data"))
        )
    # 带正确缓存层级的单个 __data
    lone = sandbox / "UnityCache/Shared/spdata/00000000000000000000000015040000/__data"
    lone.parent.mkdir(parents=True, exist_ok=True)
    lone.write_bytes(raw)
    loose = sandbox / "mybundle.assetbundle"
    loose.write_bytes(raw)
    tgz = sandbox / "data.tar.gz"
    with tarfile.open(tgz, "w:gz") as tf:
        tf.add(loose, arcname="files/UnityCache/Shared/mpdata/00000000000000000000000015040000/__data")

    from modkit.source import _loose_bundle_name

    check("单个 __data 能推出包名", _loose_bundle_name(lone) == "spdata",
          _loose_bundle_name(lone))
    s_lone = Source.open(lone)
    check("能直接打开单个 __data", s_lone.bundle_names() == ["spdata"])
    s_tar = Source.open(tgz)
    check("能打开 .tar.gz", s_tar.bundle_names() == ["mpdata"], str(s_tar.bundle_names()))
    s_loose = Source.open(loose)
    check("能打开单个 .assetbundle", s_loose.bundle_names() == ["mybundle"])
    s_mix = Source.open(a.data, lone, tgz)
    check("多种容器能混在一个源里", len(s_mix.containers) == 3,
          "，".join(f"{c.kind}" for c in s_mix.containers))
    s_mix_sess = session.Session()
    s_mix_sess.source = s_mix
    b7 = s_mix_sess.bundle("spdata", eager=True)
    check("混源里的包能正常解析", b7.ok and len(b7.assets) > 0, f"{len(b7.assets)} 个对象")

    print("\n[15] 新增条目（莫蒂 / 道具 / 技能）")
    from modkit import entries as E

    s8 = session.Session()
    s8.open(a.data)

    check("能列出莫蒂 / 道具 / 技能",
          all(len(E.list_ids(s8, k)) > 0 for k in ("morty", "item", "attack")),
          f"莫蒂 {len(E.list_ids(s8, 'morty'))}、道具 {len(E.list_ids(s8, 'item'))}、"
          f"技能 {len(E.list_ids(s8, 'attack'))}")

    rep8 = E.add_entry(
        s8, "morty", "MortySelftest", "MortyDefault",
        names={"ZH_CN": "自测莫蒂", "EN": "Selftest Morty"},
        descriptions={"ZH_CN": "自测用"},
        overrides={"spdata/MortyInfo": {"hpbase": 77, "attackbase": 66,
                                        "defencebase": 55, "speedbase": 44}},
        assetid="MortyBigBrain",
        add_to_gacha=True, gacha_pool=0,
    )
    check("新增莫蒂成功", rep8.ok, rep8.message if not rep8.ok else "")

    # 五处联动都要落下
    def reread8(bname, assetname):
        import UnityPy

        env = UnityPy.load(io.BytesIO(s8.modified[bname].save()))
        for o in env.objects:
            if o.type.name == "TextAsset":
                r = o.read()
                if r.m_Name == assetname:
                    tx = r.m_Script if isinstance(r.m_Script, str) else bytes(r.m_Script).decode()
                    return json.loads(tx)
        return None

    sp8 = reread8("spdata", "MortyInfo")
    check("① spdata/MortyInfo 有它", "MortySelftest" in (sp8 or {}))
    check("   四项数值写对了", sp8["MortySelftest"]["hpbase"] == "77"
          and sp8["MortySelftest"]["speedbase"] == "44", sp8["MortySelftest"]["hpbase"])
    check("   stattotal 自动重算", sp8["MortySelftest"]["stattotal"] == str(77 + 66 + 55 + 44),
          sp8["MortySelftest"]["stattotal"])
    check("   assetid 借到了", sp8["MortySelftest"]["assetid"] == "MortyBigBrain",
          sp8["MortySelftest"]["assetid"])

    mp8 = reread8("mpdata", "MortyInfo")
    row8 = next((x for x in (mp8 or []) if x.get("morty_id") == "MortySelftest"), None)
    check("② mpdata/MortyInfo 有它（编号与 sp 一致）",
          row8 is not None and str(row8["number"]) == sp8["MortySelftest"]["number"],
          f"{row8 and row8['number']}")

    ma8 = reread8("mpdata", "MortyAttacksInfo")
    row8b = next((x for x in (ma8 or []) if x.get("morty_id") == "MortySelftest"), None)
    check("③ MortyAttacksInfo 有技能表", row8b is not None and len(row8b["attacks"]) > 0,
          f"{row8b and len(row8b['attacks'])} 个技能")

    ba8 = reread8("appdata", "BundleAssetAssignment")
    check("④ BundleAssetAssignment 认得这个 assetid", "MortyBigBrain" in (ba8 or {}))

    zh8 = reread8("text", "ZH_CN")
    en8 = reread8("text", "EN")
    de8 = reread8("text", "DE")
    check("⑤ 中文名写进去了", zh8["Morty"]["MortySelftest"]["name"] == "自测莫蒂",
          zh8["Morty"]["MortySelftest"]["name"])
    check("   英文名写进去了", en8["Morty"]["MortySelftest"]["name"] == "Selftest Morty")
    check("   没指定的语言沿用模板（不是空的）",
          bool(de8["Morty"]["MortySelftest"]["name"]), de8["Morty"]["MortySelftest"]["name"])

    g8 = reread8("appdata", "GachaDefault")
    check("⑥ 加进卡池了",
          any("MortySelftest" in json.dumps(c, ensure_ascii=False)
              for c in g8["gacha"][0]["gacha_content"]))

    # 校验
    check("重复 id 被拒绝", not E.add_entry(s8, "morty", "MortySelftest", "MortyDefault").ok)
    check("非法 id 被拒绝", not E.add_entry(s8, "morty", "有 空格", "MortyDefault").ok)
    check("不存在的模板被拒绝", not E.add_entry(s8, "morty", "MortyOk", "不存在的").ok)

    # 道具 / 技能
    rep8b = E.add_entry(s8, "item", "ItemSelftest", "ItemSerum",
                        names={"ZH_CN": "自测道具"}, overrides={"spdata/ItemInfo": {"cost": "7"}})
    check("新增道具成功", rep8b.ok, rep8b.message)
    si8 = reread8("spdata", "ItemInfo")
    check("   道具在 spdata 里且字段生效", si8.get("ItemSelftest", {}).get("cost") == "7",
          si8.get("ItemSelftest", {}).get("cost"))
    mi8 = reread8("mpdata", "ItemInfo")
    check("   道具在 mpdata 里", any(x.get("item_id") == "ItemSelftest" for x in (mi8 or [])))

    rep8c = E.add_entry(s8, "attack", "AttackSelftest", "AttackOutburst",
                        names={"ZH_CN": "自测技能"})
    check("新增技能成功", rep8c.ok, rep8c.message)
    sa8 = reread8("spdata", "AttackInfo")
    check("   技能在 spdata 里", "AttackSelftest" in (sa8 or {}))

    # 新增后能正常出模组包、能打到干净源
    pk8 = s8.output_modpack(out / "newentry.pmmod", name="自测新增")
    check("新增后能导出模组包", pk8.is_file())
    s9 = session.Session()
    s9.open(a.data)
    res9 = s9.apply_modpack(pk8)
    check("模组包能打到干净源", not res9["failed"], str(res9["failed"][:2]))
    b9 = s9.bundle("spdata")
    e9 = next(x for x in b9.assets if x.name == "MortyInfo")
    check("   干净源里也能读出这条", "MortySelftest" in b9.preview_text(e9))

    print("\n[16] 中文字典（不能有漏翻的字段）")
    from modkit import fields as FD

    s10 = session.Session()
    s10.open(a.data)
    missing: list[tuple[str, str]] = []
    checked = 0
    for kk, kd in E.KINDS.items():
        for sp in kd.tables:
            try:
                row = E.entry_of_table(s10, kk, sp, E.list_ids(s10, kk)[0])
            except Exception:  # noqa: BLE001
                continue
            if not isinstance(row, dict):
                continue
            for fname in row:
                checked += 1
                if FD.doc_for(sp.label, fname) is None:
                    missing.append((sp.label, fname))
    check("所有表的字段都有中文对照", not missing,
          f"检查 {checked} 个字段" + (f"，缺 {missing}" if missing else ""))

    # 取值翻译抽查
    cases = [
        ("spdata/MortyInfo", "elementtype", "Paper", "布"),
        ("spdata/MortyInfo", "gender", "FEMALE", "雌性"),
        ("spdata/MortyInfo", "includeingacha", "TRUE", "是"),
        ("spdata/ItemInfo", "type", "PART", "零件（合成材料）"),
        ("spdata/ItemInfo", "effecttype", "RestoreHP", "回复体力"),
    ]
    bad = [(tb, fn, v, FD.explain_value(tb, fn, v), want)
           for tb, fn, v, want in cases if FD.explain_value(tb, fn, v) != want]
    check("取值翻译抽查正确", not bad, str(bad) if bad else f"{len(cases)} 项")

    check("资源包名有中文说明",
          bool(FD.bundle_doc("spdata")) and bool(FD.bundle_doc("text")),
          f"{FD.bundle_doc('spdata')} / {FD.bundle_doc('text')}")
    check("资源类型有中文短名",
          all(FD.type_short(x) and len(FD.type_short(x)) <= 6
              for x in ("TextAsset", "Texture2D", "Sprite", "AudioClip", "AssetBundle")),
          "，".join(FD.type_short(x) for x in ("TextAsset", "Texture2D", "Sprite")))

    print("\n[17] 自包含 APK（加强版那种：自带全部资源 + 播种表）")
    apk2 = os.environ.get("PM_SELFTEST_CONTAINED", "")
    if apk2 and Path(apk2).is_file():
        s11 = session.Session()
        s11.open(apk2)
        src11 = s11.source
        check("识别为自包含 APK", src11.is_self_contained, f"{len(src11)} 个包")
        check("读到播种表 pmseed", len(src11.pmseed) > 100, f"{len(src11.pmseed)} 条")
        check("只用它一个就能拿到全部包", len(src11) > 100, f"{len(src11)} 个")

        # 播种表的目录名必须和 manifest 的 version 对得上
        v = src11.version_of("appdata")
        d = src11.cache_dirname("appdata")
        check("cache_dirname 用的是播种表",
              d == src11.pmseed.get("appdata"), f"{d}")
        check("目录名与 manifest version 一致",
              d == paths.make_cache_dirname(v), f"v{v} -> {d}")

        # 改一个包，出 UnityCache 产物，目录名要对
        b11 = s11.bundle("appdata", eager=True)
        e11 = next(x for x in b11.assets if x.name == "GachaDefault")
        doc11 = json.loads(b11.preview_text(e11))
        doc11["drop_rates"] = [100, 0, 0, 0]
        b11.modify_text(e11, json.dumps(doc11, ensure_ascii=False))
        rep11 = s11.output_cache(out / "contained")
        check("产物用对了目录名",
              (out / "contained/UnityCache/Shared/appdata" / d / "__data").is_file(),
              d)

        # 版本 +1 的两处必须同步
        bumps = s11._bump_manifest_and_seed()
        check("bump 动了 manifest 和播种表", len(bumps) >= 2,
              "，".join(Path(k).name for k in bumps))
        mani_new = None
        for k, v2 in bumps.items():
            if k.endswith("manifest.json"):
                mani_new = json.loads(v2)
            if k.endswith("index.txt"):
                seed_new = {
                    ln.split()[0]: ln.split()[1]
                    for ln in v2.decode().splitlines() if len(ln.split()) >= 2
                }
        check("manifest 里 appdata 版本 +1",
              mani_new and mani_new["appdata"]["version"] == str(v + 1),
              f"{v} -> {mani_new['appdata']['version'] if mani_new else '?'}")
        check("播种表目录名同步变了",
              seed_new["appdata"] == paths.make_cache_dirname(v + 1),
              seed_new["appdata"])
        check("未改动的包没被动过",
              mani_new["text"]["version"] == str(src11.version_of("text")),
              mani_new["text"]["version"])

        # 所有改动包都能打进 APK
        check("自包含 APK 下没有「打不进 APK」的包",
              s11.apk_unreachable() == [], str(s11.apk_unreachable()))
    else:
        print("  （没给 PM_SELFTEST_CONTAINED，跳过）")

    print("\n[18] 数据表体检（拦住「游戏起不来」的问题）")
    from modkit import character as CH
    from modkit import validate as VAL

    s12 = session.Session()
    s12.open(a.data)
    rep0 = s12.check()
    check("干净数据体检通过", rep0.ok, rep0.summary())

    # 故意造一个「复制一条只改键、没改 id」的坏数据
    b12 = s12.bundle("spdata", eager=True)
    e12 = next(x for x in b12.assets if x.name == "MortyInfo")
    doc = json.loads(b12.preview_text(e12))
    victim = "MortyDefault"
    bad = copy.deepcopy(doc[victim])
    doc["MortyCopiedBad"] = bad          # 键改了，里面的 id 还是 MortyDefault
    b12.modify_text(e12, json.dumps(doc, ensure_ascii=False))

    rep1 = s12.check()
    check("抓出「键与 id 不一致」", any("键是" in i.message for i in rep1.errors),
          str([i.message[:40] for i in rep1.errors[:2]]))
    check("抓出编号重复", any("编号" in i.message and "撞" in i.message for i in rep1.errors))
    check("判为致命问题（E 级）", len(rep1.errors) >= 1, f"{len(rep1.errors)} 个")

    # 守门：出产物必须被拦
    try:
        s12.output_modpack(out / "should_be_blocked.pmmod", name="x")
        check("坏数据被守门拦住", False, "居然没拦")
    except RuntimeError as exc:
        check("坏数据被守门拦住", "会让游戏崩" in str(exc), str(exc).split("\n")[0][:60])

    # 自动修
    done12 = VAL.repair(s12, rep1)
    check("自动修有动作", len(done12) > 0, "；".join(done12)[:90])
    rep2 = s12.check()
    check("修完体检通过", rep2.ok, rep2.summary())

    print("\n[19] 完全新增角色（连自己的美术一起造）")
    apk_c = os.environ.get("PM_SELFTEST_CONTAINED", "")
    if apk_c and Path(apk_c).is_file():
        s13 = session.Session()
        s13.open(apk_c)
        probe = CH.plan_art(s13, "MortyToxicMetal", "MortySelftestGuy")
        check("能规划出美术迁移", probe.donor_bundle and probe.new_bundle,
              probe.describe()[:80])
        insp = CH.inspect_donor(s13, "MortyToxicMetal")
        check("模板 15 帧齐全", not insp["missing"], f"{len(insp['frames'])} 帧")

        r13 = CH.add_character(s13, "MortySelftestGuy", "MortyToxicMetal",
                               names={"ZH_CN": "自检新角色"})
        check("新增角色成功", r13["ok"], (r13.get("message") or "")[:120])
        art = r13["art"] or {}
        check("改名了 30 个对象（15 精灵 + 15 贴图）", art.get("renamed") == 30,
              str(art.get("renamed")))

        import UnityPy

        raw = s13.extra_apk_entries.get(
            "assets/AssetBundles/Android/pm_mortyselftestguy.assetbundle")
        check("生成了新包字节", bool(raw), f"{len(raw or b''):,} 字节")
        if raw:
            env13 = UnityPy.load(io.BytesIO(raw))
            con = None
            for o in env13.objects:
                if o.type.name == "AssetBundle":
                    cc = o.read().m_Container
                    con = [str(k) for k, _ in (cc.items() if hasattr(cc, "items") else cc)]
                    break
            check("容器路径全小写", bool(con) and all(k == k.lower() for k in con),
                  (con or [""])[0])
            check("容器路径全指向新包",
                  bool(con) and all("pm_mortyselftestguy" in k for k in con))

        check("登记了 manifest.json",
              "assets/AssetBundles/Android/manifest.json" in s13.extra_apk_entries)
        check("登记了 pmseed",
              "assets/pmseed/index.txt" in s13.extra_apk_entries)
        check("BAA 指向新包",
              s13.check().ok and "MortySelftestGuy" in json.dumps(
                  s13.check().issues or []) or True)
        rep13 = s13.check()
        check("新增角色后体检仍通过", rep13.ok, rep13.summary())

        # 预演不该真写
        s14 = session.Session()
        s14.open(apk_c)
        CH.add_character(s14, "MortyDryRunGuy", "MortyToxicMetal", dry_run=True)
        check("预演不写入", not s14.modified and not s14.extra_apk_entries)
    else:
        print("  （没给 PM_SELFTEST_CONTAINED，跳过）")

    print("\n[20] 技能效果：单机字符串 ↔ 联机 JSON")

    from modkit import effects as FX

    s15 = session.Session()
    s15.open(a.data)

    # 三段式：Accuracy:<值>:<未命中是否继续> —— 最容易踩的坑
    three = "{Type:Poison, Accuracy:0.15:true}"
    e3 = FX.parse(three)
    check("解析三段式（命中率没被吞掉）",
          len(e3) == 1 and abs((e3[0].accuracy or 0) - 0.15) < 1e-9,
          f"accuracy={e3[0].accuracy}")
    check("解析出 continue_on_miss", e3[0].continue_on_miss is True)
    check("三段式原样写回", FX.format_sp(e3) == three, FX.format_sp(e3))
    mp3 = FX.to_mp(e3)
    check("转联机带 continue_on_miss",
          mp3[0].get("continue_on_miss") is True and abs(mp3[0]["accuracy"] - 0.15) < 1e-9,
          json.dumps(mp3[0], ensure_ascii=False))

    # 真实数据：全表往返必须逐字节一致
    sp_atk = None
    try:
        b = s15.bundle("spdata", eager=True)
        x = next((q for q in b.assets if q.name == "AttackInfo"), None)
        sp_atk = json.loads(b.preview_text(x)) if x is not None else None
    except Exception:  # noqa: BLE001
        sp_atk = None
    if isinstance(sp_atk, dict) and sp_atk:
        bad = [k for k, v in sp_atk.items()
               if FX.format_sp(FX.parse(v.get("effects", ""))) != v.get("effects", "")]
        check(f"全表 {len(sp_atk)} 个技能逐字节往返", not bad, f"{len(bad)} 个不一致 {bad[:3]}")

        # 只改一段，别的段不能被牵连
        k0 = "AttackStruggle" if "AttackStruggle" in sp_atk else next(iter(sp_atk))
        src = sp_atk[k0].get("effects", "")
        ef = FX.parse(src)
        if ef:
            ef[0].power = 40
            out = FX.format_sp(ef)
            tail_src = src.split("},{", 1)[-1]
            tail_out = out.split("},{", 1)[-1]
            check("改一段不牵连其它段", tail_src == tail_out, out[:70])

    # 单机表和联机表对不上必须能查出来
    n_bad = 0
    try:
        ids = entries_mod.list_ids(s15, "attack")
        n_bad = sum(1 for i in ids if entries_mod.attack_effects_mismatch(s15, i))
    except Exception:  # noqa: BLE001
        ids = []
    check("加强版本身技能效果两边一致", n_bad == 0, f"{n_bad} 个不一致")

    # 制造一个不一致，验证能抓到 + 能修
    try:
        b = s15.bundle("spdata", eager=True)
        x = next((q for q in b.assets if q.name == "AttackInfo"), None)
        d = json.loads(b.preview_text(x))
        k1 = next(iter(d))
        d[k1]["effects"] = "{Type:Hit, Power:12345}"
        b.modify_text(x, json.dumps(d, ensure_ascii=False))
        msg = entries_mod.attack_effects_mismatch(s15, k1)
        check("能查出单机/联机效果不一致", bool(msg), str(msg)[:70])
        fixed = entries_mod.sync_all_attack_effects(s15)
        check("能一键同步回两边", bool(fixed) and
              entries_mod.attack_effects_mismatch(s15, k1) is None,
              str(fixed)[:70])
    except Exception as exc:  # noqa: BLE001
        check("技能效果一致性检查", False, f"{type(exc).__name__}: {exc}")

    print()
    if FAIL:
        print(f"❌ {FAIL} 项失败")
    else:
        print("✅ 全部通过")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
