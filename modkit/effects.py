"""技能效果的两种编码 —— 以及它们之间的互相转换。

问题
----
同一个技能效果，游戏存了**两份**，编码完全不同：

``spdata/AttackInfo``（单机）里 ``effects`` 是一个**自定义字符串**：

.. code-block:: text

    {Type:Hit, Power:25},{Type:Hit, Power:20, ToSelf:true}

``mpdata/AttackInfo``（联机）里是**真 JSON 列表**：

.. code-block:: json

    [{"type": "Hit", "power": 25}, {"type": "Hit", "power": 20, "to_self": true}]

**它们是同一件事。** 谁只改一边，单机和联机就会出现两个不一样的技能。
原来的工具把两边当两个互不相干的文本框，改一边另一边不动 —— 这就是那个坑。

实测全表 690 个技能，两种编码**语义 100% 等价**（段数、每段的 Type 全对得上），
所以可以做到可靠的双向转换。

字符串 DSL 的语法
-----------------
::

    effects := block ("," block)*
    block   := "{" pair ("," pair)* "}"
    pair    := Key ":" Value

键一共就 6 个（实测 690 个技能里出现过的全集）：

===========  ======  ==================  ==================================
字符串键      联机键   含义                备注
===========  ======  ==================  ==================================
``Type``     ``type``       效果类型      必填
``Power``    ``power``      威力
``Accuracy`` ``accuracy``   命中率（0~1）
``Stat``     ``stat``       改哪个属性
``ToSelf``   ``to_self``    作用在自己身上
``Percent``  ``percent``    按百分比
===========  ======  ==================  ==================================

**注意 ``Accuracy`` 是个三段式**：``Accuracy:<命中率>:<未命中是否继续>``。

.. code-block:: text

    {Type:Poison, Accuracy:0.15:true}     → 命中 0.15，没打中也继续触发
    {Type:Poison, Accuracy:0.5}           → 命中 0.5，没打中就结束

也就是说 **一个 pair 里可能出现两个冒号**，第三段是 ``continue_on_miss``。
实测 690 个技能里有 **203 处**这种写法，正好等于联机数据里 203 个
``continue_on_miss``（逐条核对 1364/1364 全对）。

**这是最容易踩的坑**：按「一个冒号」去切，``0.15:true`` 会整段变成字符串，
命中率被**静默丢掉** —— 于是单机打起来跟联机不一样，而且从数据上看不出来。

``Type`` 取值：``Hit``（造成伤害）、``Stat``（改属性）、``Paralyse``（麻痹）、
``Absorb``（吸血）、``Poison``（中毒）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

#: 字符串键 → 联机键
SP_TO_MP_KEY = {
    "Type": "type",
    "Power": "power",
    "Accuracy": "accuracy",
    "Stat": "stat",
    "ToSelf": "to_self",
    "Percent": "percent",
}
MP_TO_SP_KEY = {v: k for k, v in SP_TO_MP_KEY.items()}

#: 效果类型的中文名
TYPE_LABEL = {
    "Hit": "造成伤害",
    "Stat": "改属性",
    "Paralyse": "麻痹",
    "Absorb": "吸血",
    "Poison": "中毒",
}

#: 可以被 Stat 改的属性
STAT_LABEL = {
    "Attack": "攻击",
    "Defence": "防御",
    "Speed": "速度",
    "Accuracy": "命中率",     # 改的是「命中率」这个属性
    "Evasion": "闪避",
    "Hp": "体力",
    "Critical": "暴击",
    "PP": "PP",
}

#: 属性（元素）
ELEMENT_LABEL = {
    "Rock": "石头",
    "Paper": "布",
    "Scissors": "剪刀",
}

#: 哪些键是布尔
_BOOL_KEYS = {"ToSelf", "to_self"}

#: 哪些键是数值
_NUM_KEYS = {"Power", "Accuracy", "Percent", "power", "accuracy", "percent"}

_BLOCK_RE = re.compile(r"\{([^}]*)\}")
_NUM_RE = re.compile(r"^-?\d+(\.\d+)?$")


@dataclass
class Effect:
    """一个效果段。字段名统一用**字符串**那套（Type/Power/...），对联机友好。"""

    type: str = "Hit"
    power: float | None = None
    accuracy: float | None = None
    #: 没打中要不要继续触发；``None`` 表示原数据里没这个键
    continue_on_miss: bool | None = None
    stat: str | None = None
    to_self: bool = False
    percent: float | None = None

    #: 原始字符串里的键序（写回去时尽量保序，减少无谓 diff）
    order: list[str] = field(default_factory=list)
    #: 解析不出来的原始片段（尽量原样保留，别丢用户数据）
    raw: str | None = None
    #: 解析时的原文。没被改过的段**原样写回** ——
    #: 这样归一化（``False``→``false``、``0.20``→``0.2``）不会波及没动过的技能，
    #: 一个字节都不会变。
    source: str | None = None
    _orig: dict | None = None

    def label(self) -> str:
        bits = [TYPE_LABEL.get(self.type, self.type)]
        if self.stat:
            bits.append(STAT_LABEL.get(self.stat, self.stat))
        if self.power is not None:
            bits.append(f"威力 {_fmt(self.power)}")
        if self.accuracy is not None:
            bits.append(f"命中 {_fmt(self.accuracy)}")
        if self.continue_on_miss is not None:
            bits.append("没中继续" if self.continue_on_miss else "没中结束")
        if self.percent:
            bits.append(f"{_fmt(self.percent)}%")
        if self.to_self:
            bits.append("对自己")
        return " · ".join(bits)


def _fmt(v: float) -> str:
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _to_num(s: str):
    s = s.strip()
    if _NUM_RE.match(s):
        f = float(s)
        return int(f) if f.is_integer() else f
    return s


# ---------------------------------------------------------------- 解析


def parse(text: str) -> list[Effect]:
    """把单机的字符串解析成结构。解析不了的段原样塞进 ``raw``，不丢数据。"""
    out: list[Effect] = []
    if not text:
        return out
    if isinstance(text, list):        # 已经是联机格式，顺手吃下去
        return from_mp(text)
    for m in _BLOCK_RE.finditer(str(text)):
        body = m.group(1)
        vals: dict[str, object] = {}
        order: list[str] = []
        ok = True
        for part in body.split(","):
            part = part.strip()
            if not part:
                continue
            if ":" not in part:
                ok = False
                break
            k, _, rest = part.partition(":")
            k, rest = k.strip(), rest.strip()
            order.append(k)
            # Accuracy 是三段式：Accuracy:<值>:<未命中是否继续>
            if k == "Accuracy" and ":" in rest:
                val, _, flag = rest.rpartition(":")
                vals["Accuracy"] = _to_num(val.strip())
                vals["ContinueOnMiss"] = flag.strip().lower() == "true"
                continue
            vals[k] = _to_num(rest)
        if not ok or "Type" not in vals:
            out.append(Effect(raw=m.group(0), source=m.group(0)))
            continue
        e = Effect(
            type=str(vals.get("Type", "Hit")),
            power=vals.get("Power") if isinstance(vals.get("Power"), (int, float)) else None,
            accuracy=vals.get("Accuracy") if isinstance(vals.get("Accuracy"), (int, float)) else None,
            continue_on_miss=vals.get("ContinueOnMiss") if isinstance(vals.get("ContinueOnMiss"), bool) else None,
            stat=str(vals["Stat"]) if vals.get("Stat") is not None else None,
            to_self=str(vals.get("ToSelf", "")).lower() in ("true", "1", "yes"),
            percent=vals.get("Percent") if isinstance(vals.get("Percent"), (int, float)) else None,
            order=[k for k in order if k != "ContinueOnMiss"],
            source=m.group(0),
        )
        e._orig = to_mp([Effect(
            type=e.type, power=e.power, accuracy=e.accuracy,
            continue_on_miss=e.continue_on_miss, stat=e.stat,
            to_self=e.to_self, percent=e.percent,
        )])[0]
        out.append(e)
    return out


def from_mp(data) -> list[Effect]:
    """把联机的 JSON 列表转成结构。"""
    out: list[Effect] = []
    if not isinstance(data, list):
        return out
    for d in data:
        if not isinstance(d, dict):
            continue
        # 游戏自己有一处把 to_self 写成了 toself，读的时候要容错
        to_self = d.get("to_self", d.get("toself", False))
        out.append(Effect(
            type=str(d.get("type") or "Hit"),
            power=d.get("power") if isinstance(d.get("power"), (int, float)) else None,
            accuracy=d.get("accuracy") if isinstance(d.get("accuracy"), (int, float)) else None,
            continue_on_miss=d.get("continue_on_miss")
            if isinstance(d.get("continue_on_miss"), bool) else None,
            stat=str(d["stat"]) if d.get("stat") is not None else None,
            to_self=bool(to_self),
            percent=d.get("percent") if isinstance(d.get("percent"), (int, float)) else None,
            order=[MP_TO_SP_KEY.get(k, k) for k in d],
        ))
    return out


# ---------------------------------------------------------------- 序列化


def format_sp(effects: list[Effect]) -> str:
    """结构 → 单机字符串。"""
    return ",".join(format_sp_one(e) for e in effects)


def format_sp_one(e: Effect) -> str:
    if e.raw:
        return e.raw
    # 没改过就原样吐回去，保证「只改一个技能」不会顺手改到别的
    if e.source and e._orig is not None:
        now = to_mp([Effect(
            type=e.type, power=e.power, accuracy=e.accuracy,
            continue_on_miss=e.continue_on_miss, stat=e.stat,
            to_self=e.to_self, percent=e.percent,
        )])[0]
        if now == e._orig:
            return e.source
    parts: list[tuple[str, object]] = []
    # 按原键序输出，缺的补在后面
    order = [k for k in (e.order or []) if k in SP_TO_MP_KEY]
    for k in ("Type", "Power", "Accuracy", "Stat", "ToSelf", "Percent"):
        if k not in order and _has(e, k):
            order.append(k)
    for k in order:
        v = _get(e, k)
        if v is None or v is False:
            continue
        if k == "Accuracy" and e.continue_on_miss is not None:
            # 三段式要写回去
            parts.append((k, f"{_fmt(v)}:{'true' if e.continue_on_miss else 'false'}"))
            continue
        parts.append((k, "true" if v is True else _fmt(v) if isinstance(v, (int, float)) else str(v)))
    if not parts:
        parts = [("Type", e.type)]
    if not any(k == "Type" for k, _ in parts):
        parts.insert(0, ("Type", e.type))
    return "{" + ", ".join(f"{k}:{v}" for k, v in parts) + "}"


def to_mp(effects: list[Effect]) -> list[dict]:
    """结构 → 联机 JSON 列表。"""
    out: list[dict] = []
    for e in effects:
        d: dict[str, object] = {"type": e.type}
        if e.power is not None:
            d["power"] = e.power
        if e.accuracy is not None:
            d["accuracy"] = e.accuracy
        if e.continue_on_miss is not None:
            d["continue_on_miss"] = e.continue_on_miss
        if e.stat is not None:
            d["stat"] = e.stat
        if e.to_self:
            d["to_self"] = True
        if e.percent is not None:
            d["percent"] = e.percent
        out.append(d)
    return out


def _has(e: Effect, key: str) -> bool:
    return _get(e, key) is not None


def _get(e: Effect, key: str):
    return {
        "Type": e.type,
        "Power": e.power,
        "Accuracy": e.accuracy,
        "Stat": e.stat,
        "ToSelf": e.to_self or None,
        "Percent": e.percent,
    }.get(key)


# ---------------------------------------------------------------- 一致性


def normalize_sp(text: str) -> str:
    """把字符串规范化（统一键序/空格），方便比较。"""
    return format_sp(parse(text))


def normalize_mp(data) -> list[dict]:
    return to_mp(from_mp(data))


def same(sp_text: str, mp_data) -> bool:
    """两边的语义是否一致。"""
    a = parse(sp_text)
    b = from_mp(mp_data)
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        if to_mp([x])[0] != to_mp([y])[0]:
            return False
    return True


def diff_label(sp_text: str, mp_data) -> str:
    """人话描述两边差在哪。"""
    a, b = parse(sp_text), from_mp(mp_data)
    if len(a) != len(b):
        return f"段数不同：单机 {len(a)} 段，联机 {len(b)} 段"
    for i, (x, y) in enumerate(zip(a, b), 1):
        if to_mp([x])[0] != to_mp([y])[0]:
            return f"第 {i} 段不同：单机「{x.label()}」，联机「{y.label()}」"
    return "一致"


# ---------------------------------------------------------------- 作用索引


def describe(text: str) -> str:
    """一句话概括效果（列表里显示用）。"""
    return " → ".join(e.label() for e in parse(text)) or "（无效果）"
