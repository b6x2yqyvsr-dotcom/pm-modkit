"""新增条目：往数据表里**加**莫蒂 / 道具 / 技能。

为什么用「克隆」而不是「空白新建」
----------------------------------
这些表是游戏逻辑直接吃的，字段一个都不能少、类型也不能错。凭空造一条很容易
漏字段，进游戏就是崩溃或者隐身。所以这里的做法是：

1. 找一条**现有的**条目当模板（比如 ``MortyDefault``）
2. 深拷贝它
3. 只改 id、编号、名字这些该变的东西
4. 写回

这样字段天然齐全、类型天然正确。

一张表不够：加一只莫蒂要同时改 5 处
------------------------------------
::

    spdata/MortyInfo              dict，字段是**字符串**，attacks 是一长串
                                  "AttackA:1, AttackB:6"
    mpdata/MortyInfo              list，字段是**有类型的**（number 是 int）
    mpdata/MortyAttacksInfo       list，attacks 是 [{attack_id, level}]
    appdata/BundleAssetAssignment assetid → 它住在哪个资源包
    text/<11 种语言>              本地化，Morty 段下的 {name, description, characteristic}

少改任何一处都会出问题：只改 spdata，单机能看见但联机看不到；
不改本地化，名字那一栏就是空白或者 key。

关于美术
--------
``assetid`` 决定用哪套图。默认**沿用模板的 assetid**，也就是新莫蒂长得和模板一样
（相当于换色/换数值的「皮肤」）。想让它有自己的图，需要往资源包里新增
Sprite/Texture2D 对象 —— 那是另一个量级的工作，本模块不做，但支持你把
``assetid`` 指向**已存在的**另一只莫蒂（借它的图）。
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from typing import Any

from . import assetops
from .session import Session

# ---------------------------------------------------------------- 常量

#: text 包里的语言
LANGS = ["ZH_CN", "ZH_TW", "EN", "JP", "KO", "DE", "ES", "FR", "IT", "PT_BR", "RU"]

#: 默认要写哪几种语言（其余语言沿用模板的文案，不会变空）
DEFAULT_LANGS = ["ZH_CN", "EN"]

_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{1,62}$")


@dataclass
class TableSpec:
    """一张要改的表。"""

    bundle: str
    asset: str
    shape: str  # 'dict'（顶层键就是 id）| 'list'
    key: str = ""  # list 的主键字段名
    number_field: str | None = None  # 自动编号用哪个字段
    number_type: type = int  # int 或 str
    #: 需要一起改成新 id 的字段（例如 assetid 有时也想跟着改）
    id_fields: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return f"{self.bundle}/{self.asset}"


#: 莫蒂：5 处联动（BundleAssetAssignment 单独处理）
MORTY_TABLES = [
    TableSpec("spdata", "MortyInfo", "dict", "id", "number", str, ("id",)),
    TableSpec("mpdata", "MortyInfo", "list", "morty_id", "number", int, ("morty_id",)),
    TableSpec("mpdata", "MortyAttacksInfo", "list", "morty_id", None, int, ("morty_id",)),
]

#: 道具
ITEM_TABLES = [
    TableSpec("spdata", "ItemInfo", "dict", "id", "displayorder", str, ("id",)),
    TableSpec("mpdata", "ItemInfo", "list", "item_id", "sortorder", int, ("item_id",)),
]

#: 技能
ATTACK_TABLES = [
    TableSpec("spdata", "AttackInfo", "dict", "id", None, int, ("id",)),
    TableSpec("mpdata", "AttackInfo", "list", "attack_id", None, int, ("attack_id",)),
]


@dataclass
class Kind:
    """一类可新增的东西。"""

    key: str
    label: str
    tables: list[TableSpec]
    loc_section: str
    #: 是否要在 BundleAssetAssignment 里登记
    needs_asset_assignment: bool = False
    #: 有没有「抽卡池」这回事。莫蒂、道具的表里有 includeingacha / in_gacha，
    #: 技能的表（AttackInfo）压根没这两个字段 —— 所以技能不给这个选项
    has_gacha: bool = False
    #: 本地化的字段名 -> 默认值
    loc_fields: tuple[str, ...] = ("name", "description")
    #: id 前缀建议
    id_prefix: str = ""


KINDS: dict[str, Kind] = {
    "morty": Kind(
        key="morty",
        label="莫蒂",
        tables=MORTY_TABLES,
        loc_section="Morty",
        needs_asset_assignment=True,
        has_gacha=True,
        loc_fields=("name", "description", "characteristic"),
        id_prefix="Morty",
    ),
    "item": Kind(
        key="item",
        label="道具",
        tables=ITEM_TABLES,
        loc_section="Item",
        loc_fields=("name", "description"),
        id_prefix="Item",
        has_gacha=True,
    ),
    "attack": Kind(
        key="attack",
        label="技能",
        tables=ATTACK_TABLES,
        loc_section="Attack",
        loc_fields=("name", "description"),
        id_prefix="Attack",
    ),
}


# ---------------------------------------------------------------- 报表


@dataclass
class AddReport:
    ok: bool = False
    message: str = ""
    changes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    preview: list[tuple[str, str, str]] = field(default_factory=list)
    #: 收集到的数值，供界面回显
    numbers: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        if not self.ok:
            return self.message
        out = [self.message]
        for c in self.changes:
            out.append("  " + c)
        for w in self.warnings:
            out.append("  ⚠ " + w)
        return "\n".join(out)


# ---------------------------------------------------------------- 读取


def _get_json(sess: Session, spec: TableSpec) -> tuple[Any, Any, Any]:
    """读出一张表：返回 (bundle, entry, data)。"""
    b = sess.bundle(spec.bundle, eager=True)
    if b is None or not b.ok:
        raise ValueError(f"打不开 {spec.bundle}（{b.error if b else '没有这个包'}）")
    entry = next((a for a in b.assets if a.name == spec.asset and a.type == "TextAsset"), None)
    if entry is None:
        raise ValueError(f"{spec.bundle} 里没有 TextAsset {spec.asset}")
    data = json.loads(b.preview_text(entry))
    return b, entry, data


def list_ids(sess: Session, kind_key: str) -> list[str]:
    """列出某类现有的全部 id（以第一张表为准）。"""
    kind = KINDS[kind_key]
    _, _, data = _get_json(sess, kind.tables[0])
    if isinstance(data, dict):
        return sorted(data)
    return sorted(str(x.get(kind.tables[0].key, "")) for x in data)


def entry_of_table(sess: Session, kind_key: str, spec: TableSpec, entry_id: str) -> dict | None:
    """从**指定表**里取出某条条目（界面要分别编辑 spdata / mpdata）。"""
    _b, _e, data = _get_json(sess, spec)
    if isinstance(data, dict):
        return data.get(entry_id)
    return next((x for x in data if x.get(spec.key) == entry_id), None)


def entry_of(sess: Session, kind_key: str, entry_id: str) -> dict | None:
    """取出某条现有条目的原始数据（以第一张表为准，用于界面做表单初值）。"""
    kind = KINDS[kind_key]
    _, _, data = _get_json(sess, kind.tables[0])
    if isinstance(data, dict):
        return data.get(entry_id)
    return next((x for x in data if x.get(kind.tables[0].key) == entry_id), None)


def next_number(data: Any, spec: TableSpec) -> int | None:
    if spec.number_field is None:
        return None
    vals: list[int] = []
    rows = data.values() if isinstance(data, dict) else data
    for row in rows:
        if not isinstance(row, dict):
            continue
        v = row.get(spec.number_field)
        try:
            vals.append(int(v))
        except (TypeError, ValueError):
            continue
    return (max(vals) + 1) if vals else 1


def existing_numbers(sess: Session, kind_key: str) -> dict[str, int]:
    """每种编号字段当前的最大值（给界面提示用）。"""
    out: dict[str, int] = {}
    for spec in KINDS[kind_key].tables:
        if spec.number_field is None:
            continue
        try:
            _, _, data = _get_json(sess, spec)
        except ValueError:
            continue
        n = next_number(data, spec)
        if n is not None:
            out[f"{spec.bundle}/{spec.number_field}"] = n - 1
    return out


# ---------------------------------------------------------------- 校验


def validate(sess: Session, kind_key: str, new_id: str, clone_from: str) -> str | None:
    """返回错误信息；``None`` 表示通过。"""
    if kind_key not in KINDS:
        return f"不认识这类：{kind_key}"
    if not _ID_RE.match(new_id or ""):
        return "id 只能用字母数字下划线，且以字母开头（2~63 位）"
    kind = KINDS[kind_key]
    if clone_from == new_id:
        return "新 id 不能和模板一样"
    # id 不能和任何一张表里现有的撞
    for spec in kind.tables:
        try:
            _, _, data = _get_json(sess, spec)
        except ValueError as exc:
            return str(exc)
        rows = data.values() if isinstance(data, dict) else data
        for row in rows:
            if not isinstance(row, dict):
                continue
            names = {str(row.get(f, "")) for f in (spec.key, *spec.id_fields) if f}
            if new_id in names:
                return f"{new_id} 已经存在于 {spec.label}"
    # 模板必须存在
    ids = set()
    for spec in kind.tables:
        try:
            _, _, data = _get_json(sess, spec)
        except ValueError:
            continue
        rows = data.values() if isinstance(data, dict) else data
        for row in rows:
            if isinstance(row, dict):
                ids.add(str(row.get(spec.key if spec.shape == "list" else "id", "")))
    if clone_from not in ids:
        return f"找不到模板条目 {clone_from}"
    return None


# ---------------------------------------------------------------- 应用


def coerce_like(original: Any, v: Any) -> Any:
    """把界面/命令行给的值，转成和原字段一致的类型。

    mpdata 的表是有类型的（``number`` 是 int、``in_gacha`` 是 bool、
    ``element`` 可能是 null），而界面拿到的永远是字符串。
    不按原类型转换的话，``565`` 会变成字符串 ``"565"``，游戏解析就炸了。
    """
    if isinstance(original, bool):
        return str(v).strip().lower() in ("true", "1", "是", "yes", "y", "t")
    if isinstance(original, int) and not isinstance(original, bool):
        try:
            return int(float(str(v).strip()))
        except (TypeError, ValueError):
            return original
    if isinstance(original, float):
        try:
            return float(str(v).strip())
        except (TypeError, ValueError):
            return original
    if isinstance(original, str):
        return str(v)
    # 原值是 null / list / dict：留空就当 null，否则原样放字符串
    if original is None:
        s = str(v).strip()
        return None if s in ("", "null", "None") else v
    return v


def _apply_overrides(row: dict, overrides: dict[str, Any]) -> dict:
    for k, v in overrides.items():
        row[k] = coerce_like(row[k], v) if k in row else v
    return row


def _donor_assetid(sess: Session, kind: Kind, clone_from: str) -> str | None:
    """从模板条目里读出它的 assetid / asset_id。"""
    for spec in kind.tables:
        if spec.shape != "dict":
            continue
        try:
            _, _, data = _get_json(sess, spec)
        except ValueError:
            return None
        row = data.get(clone_from) if isinstance(data, dict) else None
        if isinstance(row, dict):
            return row.get("assetid") or row.get("asset_id")
    return None


def add_entry(
    sess: Session,
    kind_key: str,
    new_id: str,
    clone_from: str,
    *,
    display_name: str = "",
    description: str = "",
    names: dict[str, str] | None = None,
    descriptions: dict[str, str] | None = None,
    langs: list[str] | None = None,
    overrides: dict[str, dict[str, Any]] | None = None,
    assetid: str | None = None,
    add_to_gacha: bool = False,
    gacha_pool: int = 0,
    dry_run: bool = False,
) -> AddReport:
    """新增一条莫蒂 / 道具 / 技能。

    - ``overrides``：按表覆盖字段，形如 ``{"spdata/MortyInfo": {"hpbase": 60}}``
    - ``display_name`` / ``description``：写进 ``langs`` 指定的语言（默认中英）
    - ``names`` / ``descriptions``：按语言分别给，形如 ``{"EN": "My Morty"}``，
      优先级高于 ``display_name``
    - ``assetid``：让新条目借用哪个**已存在**的美术资源（默认沿用模板的）
    - ``langs`` 没列到的语言沿用模板文案，不会变成空白
    """
    rep = AddReport()
    err = validate(sess, kind_key, new_id, clone_from)
    if err:
        rep.message = "✗ " + err
        return rep

    kind = KINDS[kind_key]
    langs = [x for x in (langs or DEFAULT_LANGS) if x in LANGS]
    overrides = overrides or {}
    names = dict(names or {})
    descriptions = dict(descriptions or {})
    pending_writes: list[tuple[Any, Any, str]] = []  # (bundle, entry, 新文本)
    touched: set[tuple[int, int]] = set()

    def queue(b, entry, obj) -> None:
        """排队一次写入；同一 (包, 资源) 只保留最后一次。"""
        key = (id(b), entry.path_id)
        if key in touched:
            pending_writes[:] = [
                (x, y, z) for (x, y, z) in pending_writes
                if not (id(x) == id(b) and y.path_id == entry.path_id)
            ]
        touched.add(key)
        pending_writes.append((b, entry, json.dumps(obj, ensure_ascii=False)))

    # 新条目用哪套美术：用户指定 > 模板自带
    donor_asset = _donor_assetid(sess, kind, clone_from)
    target_asset = assetid or donor_asset

    # ---------- 1. 各数据表 ----------
    #
    # 注意：**模板不一定每张表里都有**。道具最典型 —— 44 条单机 + 51 条联机，
    # 两边都有的只有 28 条。挑到「只有单机」的那只，联机表里就没得克隆。
    # 这时候不报错退出，而是照着那张表的结构**补一条出来**（见 synthesize_row）。
    source_row = None
    try:
        source_row = entry_of_table(sess, kind, kind.tables[0], clone_from)
    except Exception:  # noqa: BLE001
        source_row = None

    for spec in kind.tables:
        b, entry, data = _get_json(sess, spec)
        is_dict = isinstance(data, dict)

        if is_dict:
            donor = data.get(clone_from)
            if donor is None:
                # 第一张表都没有就没救了；后面的表可以补
                if spec is kind.tables[0]:
                    rep.message = f"✗ {spec.label} 里没有模板 {clone_from}"
                    return rep
                any_row = next((v for v in data.values() if isinstance(v, dict)), None)
                row = synthesize_row(spec, source_row or {}, new_id, any_row)
                mapped = row.pop("__mapped__", [])
                kept = row.pop("__kept__", [])
                data[new_id] = row
                rep.changes.append(
                    f"{spec.label}：表里没有模板，照着结构补了一条"
                    f"（对上 {len(mapped)} 个字段；{'、'.join(kept[:4])}"
                    f"{'…' if len(kept) > 4 else ''} 沿用了默认值，记得核对）")
            else:
                row = copy.deepcopy(donor)
                for f in ("id", *spec.id_fields):
                    if f in row:
                        row[f] = new_id
                data[new_id] = row
        else:
            donor = next((x for x in data if x.get(spec.key) == clone_from), None)
            if donor is None:
                if spec is kind.tables[0]:
                    rep.message = f"✗ {spec.label} 里没有模板 {clone_from}"
                    return rep
                any_row = next((x for x in data if isinstance(x, dict)), None)
                row = synthesize_row(spec, source_row or {}, new_id, any_row)
                mapped = row.pop("__mapped__", [])
                kept = row.pop("__kept__", [])
                data.append(row)
                rep.changes.append(
                    f"{spec.label}：表里没有模板，照着结构补了一条"
                    f"（对上 {len(mapped)} 个字段；{'、'.join(kept[:4])}"
                    f"{'…' if len(kept) > 4 else ''} 沿用了默认值，记得核对）")
            else:
                row = copy.deepcopy(donor)
                row[spec.key] = new_id
                for f in spec.id_fields:
                    if f in row:
                        row[f] = new_id
                data.append(row)

        # 编号：取全表最大值 +1
        if spec.number_field:
            n = next_number(data, spec)
            if n is not None:
                rep.numbers[spec.label] = n
                row[spec.number_field] = str(n) if spec.number_type is str else n

        # 美术资源指向
        if target_asset:
            for f in ("assetid", "asset_id"):
                if f in row:
                    row[f] = target_asset

        _apply_overrides(row, overrides.get(spec.label, {}))

        # 莫蒂的四项基础值变了，stattotal 要跟着重算，否则对不上
        if kind_key == "morty" and is_dict and "stattotal" in row:
            try:
                row["stattotal"] = str(
                    int(row["hpbase"]) + int(row["attackbase"])
                    + int(row["defencebase"]) + int(row["speedbase"])
                )
            except (KeyError, TypeError, ValueError):
                rep.warnings.append("stattotal 没能自动重算（有一项不是数字）")

        queue(b, entry, data)
        num = row.get(spec.number_field) if spec.number_field else None
        rep.changes.append(
            f"{spec.label}：新增 {new_id}" + (f"（编号 {num}）" if num is not None else "")
        )

    # ---------- 2. BundleAssetAssignment ----------
    if kind.needs_asset_assignment and target_asset:
        b, entry, baa = _get_json(sess, TableSpec("appdata", "BundleAssetAssignment", "dict"))
        if target_asset in baa:
            rep.changes.append(
                f"appdata/BundleAssetAssignment：沿用 {target_asset}"
                f"（{baa[target_asset].get('version')}）"
            )
            if target_asset != clone_from:
                rep.warnings.append(f"新条目的形象来自 {target_asset}")
        else:
            src = baa.get(donor_asset) if donor_asset else None
            if not src:
                rep.warnings.append(
                    f"assetid {target_asset} 没有任何资源包记录，游戏里可能不显示形象"
                )
            else:
                baa[target_asset] = {"id": target_asset, "version": src.get("version")}
                queue(b, entry, baa)
                rep.changes.append(
                    f"appdata/BundleAssetAssignment：登记 {target_asset} → {src.get('version')}"
                )
                rep.warnings.append(
                    f"要自己确认资源包里有名为 {target_asset} 的贴图/精灵，否则是空白"
                )

    # ---------- 3. 本地化 ----------
    loc_bundle = sess.bundle("text", eager=True)
    if loc_bundle is None or not loc_bundle.ok:
        rep.warnings.append("打不开 text 包，本地化没写（游戏里名字可能显示成 key）")
    else:
        wrote: list[str] = []
        for lang in LANGS:
            entry = next(
                (a for a in loc_bundle.assets if a.name == lang and a.type == "TextAsset"), None
            )
            if entry is None:
                continue
            data = json.loads(loc_bundle.preview_text(entry))
            section = data.get(kind.loc_section)
            if not isinstance(section, dict):
                rep.warnings.append(f"{lang} 里没有 {kind.loc_section} 段，跳过")
                continue
            donor = section.get(clone_from)
            if donor is None:
                rep.warnings.append(f"{lang}.{kind.loc_section} 里没有模板 {clone_from}，跳过")
                continue

            item = copy.deepcopy(donor)
            # 该语言有没有给新文案？
            nm = names.get(lang) or (display_name if lang in langs else "")
            ds = descriptions.get(lang) or (description if lang in langs else "")
            if nm:
                item["name"] = nm
                wrote.append(lang)
            if ds and "description" in item:
                item["description"] = ds
            section[new_id] = item
            queue(loc_bundle, entry, data)
        rep.changes.append(
            f"text/{kind.loc_section}：{len(LANGS)} 种语言的条目"
            + (f"（{'/'.join(sorted(set(wrote)))} 写了新名字，其余沿用模板）" if wrote else "")
        )

    # ---------- 4. 抽卡池 ----------
    if add_to_gacha and not kind.has_gacha:
        rep.warnings.append(f"{kind.label}没有抽卡池这回事，忽略「加进卡池」")
        add_to_gacha = False

    if add_to_gacha:
        gspec = TableSpec("appdata", "GachaDefault", "dict")
        try:
            gb, gentry, gdata = _get_json(sess, gspec)
            pools = gdata.get("gacha") or []
            if 0 <= gacha_pool < len(pools):
                reward = {"morty": "MORTY", "item": "ITEM", "attack": "ATTACK"}.get(
                    kind_key, kind_key.upper()
                )
                pools[gacha_pool].setdefault("gacha_content", []).append(
                    {"quantity": 1, "reward": reward, "parameters": {"ids": [new_id]}}
                )
                queue(gb, gentry, gdata)
                rep.changes.append(
                    f"appdata/GachaDefault：加进第 {gacha_pool + 1} 个卡池"
                    f"（{pools[gacha_pool].get('gacha_id')}）"
                )
            else:
                rep.warnings.append(f"卡池序号 {gacha_pool} 不存在，没加")
        except Exception as exc:  # noqa: BLE001
            rep.warnings.append(f"抽卡池没改成功：{exc}")

    rep.message = f"✓ 新增{kind.label} {new_id}（模板 {clone_from}）"

    if dry_run:
        rep.ok = True
        rep.changes.append("（预演模式，没有真正写入）")
        return rep

    # ---------- 5. 写回 ----------
    for b, entry, text in pending_writes:
        try:
            b.modify_text(entry, text)
        except Exception as exc:  # noqa: BLE001
            rep.message = f"✗ 写入 {b.name}/{entry.display} 失败：{exc}"
            return rep
    rep.ok = True
    return rep



def gacha_pools(sess: Session) -> list[str]:
    """现有卡池的名字，给界面下拉用。"""
    try:
        _, _, data = _get_json(sess, TableSpec("appdata", "GachaDefault", "dict"))
    except ValueError:
        return []
    return [str(p.get("gacha_id", f"#{i}")) for i, p in enumerate(data.get("gacha") or [])]


# ---------------------------------------------------------------- 技能效果同步


def get_attack_effects(sess, attack_id: str) -> list:
    """取一个技能的效果（从单机表读，解析成结构）。

    单机表和联机表要是对不上，以**单机表**为准 —— 那是游戏的主数据。
    """
    from . import effects as FX

    sp = entry_of_table(sess, "attack", ATTACK_TABLES[0], attack_id)
    if not isinstance(sp, dict):
        return []
    return FX.parse(sp.get("effects", ""))


def attack_effects_mismatch(sess, attack_id: str) -> str | None:
    """单机表和联机表的效果对不上就返回人话描述，对得上返回 ``None``。"""
    from . import effects as FX

    sp = entry_of_table(sess, "attack", ATTACK_TABLES[0], attack_id)
    if not isinstance(sp, dict):
        return None
    mp = entry_of_table(sess, "attack", ATTACK_TABLES[1], attack_id)
    if not isinstance(mp, dict):
        return None            # 联机表里没有这个技能，不算冲突
    if FX.same(sp.get("effects", ""), mp.get("effects")):
        return None
    return FX.diff_label(sp.get("effects", ""), mp.get("effects"))


def set_attack_effects(sess, attack_id: str, effects: list) -> list[str]:
    """把一个技能的效果**同时**写进单机表和联机表，返回做了什么。

    这两张表是同一件事的两种编码（字符串 DSL / JSON 列表）。只写一边，
    单机和联机就会变成两个不一样的技能 —— 这正是原来那个坑。
    """
    from . import effects as FX

    done: list[str] = []
    sp_text = FX.format_sp(effects)
    mp_list = FX.to_mp(effects)

    # 单机表：dict
    b = sess.bundle(ATTACK_TABLES[0].bundle, eager=True)
    if b is not None and b.ok:
        e = next((a for a in b.assets if a.name == ATTACK_TABLES[0].asset), None)
        if e is not None:
            import json as _json

            data = _json.loads(b.preview_text(e))
            if isinstance(data, dict) and attack_id in data:
                old = data[attack_id].get("effects", "")
                if old != sp_text:
                    data[attack_id]["effects"] = sp_text
                    b.modify_text(e, _json.dumps(data, ensure_ascii=False))
                    done.append(f"单机表 effects 已更新（{len(effects)} 段）")

    # 联机表：list，主键是 attack_id
    b2 = sess.bundle(ATTACK_TABLES[1].bundle, eager=True)
    if b2 is not None and b2.ok:
        e2 = next((a for a in b2.assets if a.name == ATTACK_TABLES[1].asset), None)
        if e2 is not None:
            import json as _json

            data2 = _json.loads(b2.preview_text(e2))
            if isinstance(data2, list):
                hit = False
                for row in data2:
                    if isinstance(row, dict) and row.get("attack_id") == attack_id:
                        row["effects"] = mp_list
                        row["pp_stat"] = row.get("pp_stat")
                        hit = True
                if hit:
                    b2.modify_text(e2, _json.dumps(data2, ensure_ascii=False))
                    done.append(f"联机表 effects 已同步（{len(mp_list)} 段）")
    return done


def sync_all_attack_effects(sess, only_broken: bool = True) -> list[str]:
    """把全部技能的效果按单机表同步到联机表。"""
    from . import effects as FX

    done: list[str] = []
    fixed = 0
    for aid in list_ids(sess, "attack"):
        if only_broken and attack_effects_mismatch(sess, aid) is None:
            continue
        eff = get_attack_effects(sess, aid)
        if not eff:
            continue
        if set_attack_effects(sess, aid, eff):
            fixed += 1
    if fixed:
        done.append(f"同步了 {fixed} 个技能的效果（单机 ↔ 联机）")
    return done


# ---------------------------------------------------------------- 单/联机覆盖


def _norm_field(name: str) -> str:
    """字段名归一：``item_id`` / ``itemid`` 视为同一个。"""
    return name.replace("_", "").lower()


#: 单机表和联机表**名字真的不一样**的那几对（归一化也对不上的）。
#: 只放有把握的 —— 拿不准的宁可不映射，让它沿用结构模板的默认值，
#: 至少不会写进去一个错误的数。
FIELD_ALIAS = [
    ("displayorder", "sortorder"),          # 排序位
    ("usableinworld", "use_world"),
    ("usableincraft", "use_craft"),
    ("usableinbattle", "use_battle"),
    ("useonself", "use_self"),
    ("includeingacha", "in_gacha"),
    ("effectstattype", "effect_stat"),
    ("effecttype", "effect_type"),
    ("spbaglimit", "mp_premium_cost"),
]


def _alias_pairs() -> dict[str, str]:
    """归一化名 → 归一化名 的双向映射。"""
    out: dict[str, str] = {}
    for a, b in FIELD_ALIAS:
        na, nb = _norm_field(a), _norm_field(b)
        out[na] = nb
        out[nb] = na
    return out


def table_coverage(sess, kind: str) -> dict[str, list[str]]:
    """每个 id 出现在哪几张表里。用来在界面上标「两边都有 / 只有单机」。"""
    kd = KINDS[kind]
    tables = [sp.label for sp in kd.tables]
    out: dict[str, list[str]] = {}
    for i in list_ids(sess, kind):
        have = []
        for sp in kd.tables:
            row = entry_of_table(sess, kind, sp, i)
            if row is not None:
                have.append(sp.label)
        out[i] = have
    return out


def _coerce_like(value, like):
    """把值转成和 ``like`` 同一个类型。

    单机表里什么都是字符串（``"cost": "0"``、``"use_world": "TRUE"``），
    联机表里是真正的 int / bool（``0`` / ``true``）。照搬字符串过去，
    游戏解析 JSON 时类型就错了 —— 这是必须转的一步。
    """
    if like is None or isinstance(value, type(like)) and not isinstance(like, bool):
        return value
    try:
        if isinstance(like, bool):
            if isinstance(value, bool):
                return value
            return str(value).strip().lower() in ("true", "1", "yes", "y", "是")
        if isinstance(like, int):
            return int(float(value))
        if isinstance(like, float):
            return float(value)
        if isinstance(like, str):
            return str(value)
    except (TypeError, ValueError):
        return like            # 转不过去就沿用结构模板的值，别塞个错的进去
    return value


def synthesize_row(target: TableSpec, source_row: dict, new_id: str,
                   donor_row: dict | None = None) -> dict:
    """**没有现成的模板行**时，照着结构造一条。

    典型场景：道具只有 28/44 两边都有，你挑到「只有单机」的那只，
    联机表里就没有对应的行可克隆。这时候：

    1. 从目标表里随便拿一行当**结构模板**（保证字段齐全、类型正确）
    2. 按**归一化后的字段名**把源行的值搬过去
       （``id``↔``item_id``、``assetid``↔``asset_id``、``effecttype``↔``effect_type``）
    3. 主键设成新的 id

    这样不写死映射表也能对上大部分字段 —— 对不上的保持结构模板的值，
    在报告里会提示你去核对。
    """
    row = dict(donor_row) if isinstance(donor_row, dict) else {}
    src = {_norm_field(k): v for k, v in (source_row or {}).items()}
    alias = _alias_pairs()
    mapped, kept = [], []
    for f in list(row):
        nf = _norm_field(f)
        hit = nf if nf in src else alias.get(nf)
        if hit and hit in src and src[hit] not in (None, ""):
            row[f] = _coerce_like(src[hit], row.get(f))
            mapped.append(f)
        else:
            kept.append(f)
    for f in target.id_fields:
        row[f] = new_id
    if target.key:
        row[target.key] = new_id
    row["__mapped__"] = mapped
    row["__kept__"] = kept
    return row

