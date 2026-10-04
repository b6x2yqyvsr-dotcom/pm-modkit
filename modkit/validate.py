"""数据表体检：在写入 bundle 之前，先把「会导致游戏起不来」的问题找出来。

为什么要做这个
--------------
游戏把 ``MortyInfo`` 这类表读进内存后，会按主键建字典。**主键重复**在 C# 里是
``ArgumentException: An item with the same key has already been added`` —— 直接崩，
表现就是「游戏进不去」。

而这些错误**手工改 JSON 时极容易犯**。典型的一种：复制一条现成的莫蒂来改，
只改了字典的键和本地化里的名字，**忘了改记录内部的 ``id`` 字段**：

.. code-block:: json

    "MortyAyin": {          // ← 键改了
      "id": "MortyGotron",  // ← 里面没改，于是 MortyGotron 出现了两次
      "number": "422",      // ← 编号也跟着重复
      ...
    }

原版数据是**完全干净**的（实测 564 条莫蒂：键==id、编号唯一、
spdata/mpdata/技能表/本地化四边齐全、技能引用全部存在）。
所以这些不变量可以作为硬性检查。

检查项
------
``E`` 级（会崩游戏，必须修）：

1. dict 表：键与记录内 ``id`` 不一致
2. list 表：主键重复
3. 莫蒂编号重复
4. 技能表里 ``morty_id`` 重复

``W`` 级（不会立刻崩，但功能不对）：

5. spdata 有、mpdata 没有（联机看不到）
6. spdata 有、技能表没有（学不到技能）
7. 本地化缺条目（名字显示成 key）
8. ``assetid`` 没在 BundleAssetAssignment 登记（形象空白）
9. ``attacks`` 引用了不存在的技能
10. ``evolution`` 指向不存在的莫蒂
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from . import entries as entries_mod
from .session import Session

LEVEL_ERROR = "E"
LEVEL_WARN = "W"

LEVEL_LABEL = {LEVEL_ERROR: "会崩游戏", LEVEL_WARN: "功能不对"}


@dataclass
class Issue:
    level: str
    table: str
    entry: str
    message: str
    #: 能自动修的话，这里写怎么修
    fixable: bool = False

    def line(self) -> str:
        tag = "✗" if self.level == LEVEL_ERROR else "⚠"
        return f"{tag} [{self.table}] {self.entry}：{self.message}"


@dataclass
class Report:
    issues: list[Issue] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == LEVEL_ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.level == LEVEL_WARN]

    @property
    def ok(self) -> bool:
        return not self.issues

    def summary(self) -> str:
        if not self.issues:
            return f"✓ 体检通过（查了 {len(self.checked)} 张表）"
        e, w = len(self.errors), len(self.warnings)
        parts = []
        if e:
            parts.append(f"{e} 个**会崩游戏**的问题")
        if w:
            parts.append(f"{w} 个功能问题")
        return "✗ " + "，".join(parts)


# ---------------------------------------------------------------- 取表


def _read(sess: Session, bundle: str, asset: str):
    """读一张表；读不到返回 None（不抛异常，体检要能继续跑）。"""
    try:
        b = sess.bundle(bundle, eager=True)
    except Exception:  # noqa: BLE001
        return None
    if b is None or not b.ok:
        return None
    e = next((a for a in b.assets if a.name == asset and a.type == "TextAsset"), None)
    if e is None:
        return None
    try:
        return json.loads(b.preview_text(e))
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------- 检查


def validate(sess: Session) -> Report:
    """跑一遍全部检查。读不到的表会被跳过，不报错。"""
    rep = Report()

    sp = _read(sess, "spdata", "MortyInfo")
    mp = _read(sess, "mpdata", "MortyInfo")
    ma = _read(sess, "mpdata", "MortyAttacksInfo")
    atk = _read(sess, "spdata", "AttackInfo")
    baa = _read(sess, "appdata", "BundleAssetAssignment")

    if isinstance(sp, dict):
        rep.checked.append("spdata/MortyInfo")
        _check_dict_key_id(rep, "spdata/MortyInfo", sp)
        _check_unique_number(rep, "spdata/MortyInfo", sp)
    if isinstance(mp, list):
        rep.checked.append("mpdata/MortyInfo")
        _check_list_pk(rep, "mpdata/MortyInfo", mp, "morty_id")
    if isinstance(ma, list):
        rep.checked.append("mpdata/MortyAttacksInfo")
        _check_list_pk(rep, "mpdata/MortyAttacksInfo", ma, "morty_id")

    # 跨表一致性
    if isinstance(sp, dict):
        sp_ids = set(sp)
        if isinstance(mp, list):
            mp_ids = {x.get("morty_id") for x in mp if isinstance(x, dict)}
            for k in sorted(sp_ids - mp_ids):
                rep.issues.append(Issue(LEVEL_WARN, "mpdata/MortyInfo", k,
                                        "spdata 里有，mpdata 里没有 → 联机模式下看不到",
                                        fixable=True))
        if isinstance(ma, list):
            ma_ids = {x.get("morty_id") for x in ma if isinstance(x, dict)}
            for k in sorted(sp_ids - ma_ids):
                rep.issues.append(Issue(LEVEL_WARN, "mpdata/MortyAttacksInfo", k,
                                        "技能表里没有 → 学不到任何技能", fixable=True))
        if isinstance(baa, dict):
            for k, v in sorted(sp.items()):
                aid = (v or {}).get("assetid")
                if aid and aid not in baa:
                    rep.issues.append(Issue(LEVEL_WARN, "appdata/BundleAssetAssignment", k,
                                            f"assetid {aid!r} 没登记 → 形象会是空白",
                                            fixable=True))
        if isinstance(atk, dict):
            for k, v in sorted(sp.items()):
                for a in str((v or {}).get("attacks", "")).split(","):
                    aid = a.split(":")[0].strip()
                    if aid and aid not in atk:
                        rep.issues.append(Issue(LEVEL_WARN, "spdata/AttackInfo", k,
                                                f"引用了不存在的技能 {aid!r}"))
                        break
            for k, v in sorted(sp.items()):
                evo = (v or {}).get("evolution") or ""
                if evo and evo not in sp:
                    rep.issues.append(Issue(LEVEL_WARN, "spdata/MortyInfo", k,
                                            f"进化目标是 {evo!r}，表里没有这只莫蒂"))

    # 技能效果：单机字符串 DSL 和联机 JSON 列表是同一件事的两种编码，
    # 只改一边就会变成两个不一样的技能（从数据上看不出来，最坑）
    if isinstance(sp, dict):
        try:
            names = entries_mod.list_ids(sess, "attack")
        except Exception:  # noqa: BLE001
            names = []
        bad = []
        for aid in names:
            msg = entries_mod.attack_effects_mismatch(sess, aid)
            if msg:
                bad.append((aid, msg))
        if names:
            rep.checked.append("spdata/mpdata 技能效果一致性")
        for aid, msg in bad[:20]:
            rep.issues.append(Issue(
                LEVEL_ERROR, "攻击/AttackInfo", aid,
                f"单机表和联机表的**效果对不上** —— {msg}",
                fixable=True,
            ))
        if len(bad) > 20:
            rep.issues.append(Issue(
                LEVEL_ERROR, "攻击/AttackInfo", f"（还有 {len(bad) - 20} 个）",
                "批量同步一下就好", fixable=True))

    # 本地化
    if isinstance(sp, dict):
        loc = _read(sess, "text", "ZH_CN")
        if isinstance(loc, dict) and isinstance(loc.get("Morty"), dict):
            rep.checked.append("text/ZH_CN")
            section = loc["Morty"]
            for k in sorted(set(sp) - set(section)):
                rep.issues.append(Issue(LEVEL_WARN, "text/ZH_CN", k,
                                        "本地化里没有 → 名字会显示成 key", fixable=True))
            for k in sorted(set(section) - set(sp)):
                rep.issues.append(Issue(LEVEL_WARN, "text/ZH_CN", k,
                                        "本地化里有，但 MortyInfo 里没有这只莫蒂"))
    return rep


def _check_dict_key_id(rep: Report, table: str, data: dict) -> None:
    for k, v in data.items():
        if not isinstance(v, dict):
            continue
        inner = v.get("id")
        if inner is not None and inner != k:
            dup = inner in data
            rep.issues.append(Issue(
                LEVEL_ERROR, table, k,
                f"键是 {k!r}，但记录里 id 是 {inner!r}"
                + ("（这条 id 在表里已经存在 → **主键重复，游戏会崩**）" if dup else ""),
                fixable=True,
            ))


def _check_unique_number(rep: Report, table: str, data: dict) -> None:
    seen: dict[str, str] = {}
    for k, v in data.items():
        if not isinstance(v, dict):
            continue
        n = str(v.get("number", ""))
        if not n:
            continue
        if n in seen:
            rep.issues.append(Issue(
                LEVEL_ERROR, table, k,
                f"编号 {n} 和 {seen[n]!r} 撞了 → **编号重复，游戏会崩**",
                fixable=True,
            ))
        else:
            seen[n] = k


def _check_list_pk(rep: Report, table: str, data: list, key: str) -> None:
    seen: dict[str, int] = {}
    for i, x in enumerate(data):
        if not isinstance(x, dict):
            continue
        k = x.get(key)
        if k is None:
            continue
        if k in seen:
            rep.issues.append(Issue(
                LEVEL_ERROR, table, str(k),
                f"主键 {k!r} 出现了两次（第 {seen[k]} 条和第 {i} 条）→ **主键重复，游戏会崩**",
                fixable=True,
            ))
        else:
            seen[k] = i


# ---------------------------------------------------------------- 自动修复


def repair(sess: Session, rep: Report | None = None, *, dry_run: bool = False) -> list[str]:
    """把能自动修的问题修掉。返回「做了什么」的列表。

    修复策略（都按「本地化的键」为准 —— 那通常是用户真正想要的新名字）：

    1. 键 ≠ 记录内 id → 把记录内 id 改成键
    2. 编号重复 → 重新分配一个没被占用的编号
    3. mpdata / 技能表缺条目 → 从 spdata 那条派生出来补上
    4. assetid 没登记 → 补一条（沿用同名资源的包）
    5. 本地化缺条目 → 补一条占位
    """
    rep = rep or validate(sess)
    done: list[str] = []
    if not rep.issues:
        return done

    sp_b = sess.bundle("spdata", eager=True)
    mp_b = sess.bundle("mpdata", eager=True)
    sp = _read(sess, "spdata", "MortyInfo")
    if not isinstance(sp, dict):
        return ["（读不到 spdata/MortyInfo，没法自动修）"]

    changed_sp = False
    changed_mp = False
    changed_ma = False

    def write_text(bundle_obj, asset_name: str, obj) -> None:
        e = next((a for a in bundle_obj.assets if a.name == asset_name), None)
        if e is not None:
            bundle_obj.modify_text(e, json.dumps(obj, ensure_ascii=False))

    # ---- 1. 键 ≠ id
    for k, v in list(sp.items()):
        if isinstance(v, dict) and v.get("id") not in (None, k):
            old = v["id"]
            v["id"] = k
            changed_sp = True
            done.append(f"把 {k} 的记录内 id 从 {old!r} 改成 {k!r}")

    # ---- 2. 编号重复
    used = {str(v.get("number")) for v in sp.values() if isinstance(v, dict)}
    if sp:
        nxt = max((int(x) for x in used if x.isdigit()), default=0) + 1
    else:
        nxt = 1
    seen_n: dict[str, str] = {}
    for k, v in sp.items():
        if not isinstance(v, dict):
            continue
        n = str(v.get("number", ""))
        if n and n in seen_n:
            while str(nxt) in used:
                nxt += 1
            v["number"] = str(nxt)
            done.append(f"把 {k} 的编号从 {n} 改成 {nxt}（{n} 已被 {seen_n[n]!r} 占用）")
            used.add(str(nxt))
            seen_n[str(nxt)] = k
            changed_sp = True
            nxt += 1
        elif n:
            seen_n[n] = k

    # ---- 3a. list 表去重（复制粘贴最容易留下重复主键）
    mp = _read(sess, "mpdata", "MortyInfo")
    ma = _read(sess, "mpdata", "MortyAttacksInfo")
    for lst, key, label in ((mp, "morty_id", "mpdata/MortyInfo"),
                            (ma, "morty_id", "mpdata/MortyAttacksInfo")):
        if not isinstance(lst, list):
            continue
        seen: set = set()
        keep: list = []
        dropped = 0
        for x in lst:
            k = x.get(key) if isinstance(x, dict) else None
            if k is not None and k in seen:
                dropped += 1
                continue
            if k is not None:
                seen.add(k)
            keep.append(x)
        if dropped:
            lst[:] = keep
            done.append(f"{label}：删掉了 {dropped} 条重复主键的条目（重复主键会让游戏崩）")
            if label.endswith("MortyInfo"):
                changed_mp = True
            else:
                changed_ma = True

    # ---- 3b. mpdata 缺条目
    if isinstance(mp, list):
        have = {x.get("morty_id") for x in mp if isinstance(x, dict)}
        for k, v in sp.items():
            if k in have or not isinstance(v, dict):
                continue
            mp.append({
                "morty_id": k,
                "asset_id": v.get("assetid"),
                "number": int(v.get("number") or 0),
                "gender": v.get("gender"),
                "height": v.get("height"),
                "weight": v.get("weight"),
                "division": int(v.get("division") or 0),
                "element": v.get("elementtype") or None,
                "evolution_req": None,
            })
            changed_mp = True
            done.append(f"给 {k} 补了 mpdata/MortyInfo 条目（联机才看得到）")
    if isinstance(ma, list):
        have_a = {x.get("morty_id") for x in ma if isinstance(x, dict)}
        for k, v in sp.items():
            if k in have_a or not isinstance(v, dict):
                continue
            atk_list = []
            for part in str(v.get("attacks", "")).split(","):
                part = part.strip()
                if not part or ":" not in part:
                    continue
                aid, _, lv = part.partition(":")
                try:
                    atk_list.append({"attack_id": aid.strip(), "level": int(lv.strip() or 1)})
                except ValueError:
                    continue
            ma.append({"morty_id": k, "attacks": atk_list})
            changed_ma = True
            done.append(f"给 {k} 补了 MortyAttacksInfo（{len(atk_list)} 个技能）")

    if dry_run:
        return done

    if changed_sp and sp_b is not None:
        write_text(sp_b, "MortyInfo", sp)
    if (changed_mp or changed_ma) and mp_b is not None:
        if changed_mp and isinstance(mp, list):
            write_text(mp_b, "MortyInfo", mp)
        if changed_ma and isinstance(ma, list):
            write_text(mp_b, "MortyAttacksInfo", ma)

    # ---- 3c. 技能效果同步（单机 ↔ 联机）
    fixed = entries_mod.sync_all_attack_effects(sess, only_broken=True)
    done += fixed

    # ---- 4. 本地化缺条目
    loc_b = sess.bundle("text", eager=True)
    if loc_b is not None and loc_b.ok:
        for lang_entry in [a for a in loc_b.assets if a.type == "TextAsset"]:
            try:
                loc = json.loads(loc_b.preview_text(lang_entry))
            except Exception:  # noqa: BLE001
                continue
            section = loc.get("Morty")
            if not isinstance(section, dict):
                continue
            missing = [k for k in sp if k not in section]
            if not missing:
                continue
            for k in missing:
                section[k] = {"name": k, "description": "", "characteristic": ""}
                done.append(f"给 {lang_entry.name} 补了 {k} 的占位文案（名字先写成 id）")
            loc_b.modify_text(lang_entry, json.dumps(loc, ensure_ascii=False))

    # ---- 5. assetid 登记
    baa_b = sess.bundle("appdata", eager=True)
    baa = _read(sess, "appdata", "BundleAssetAssignment")
    if baa_b is not None and isinstance(baa, dict):
        changed = False
        for k, v in sp.items():
            aid = (v or {}).get("assetid")
            if aid and aid not in baa:
                baa[aid] = {"id": aid, "version": None}
                changed = True
                done.append(f"给 assetid {aid!r} 补了 BundleAssetAssignment 记录"
                            "（version 需要你自己填对，否则形象还是空白）")
        if changed:
            e = next((a for a in baa_b.assets if a.name == "BundleAssetAssignment"), None)
            if e is not None:
                baa_b.modify_text(e, json.dumps(baa, ensure_ascii=False))

    return done
