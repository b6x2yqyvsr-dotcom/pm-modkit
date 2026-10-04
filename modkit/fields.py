"""中文对照表：把游戏里的英文技术名翻成人话。

游戏的数据表和资源名全是英文技术标识（``hpbase``、``badgereq``、
``includeingacha``、``CharacterAnimeRickBack``…），直接摆给用户看等于没给。
这个模块集中维护这些对照，界面只管查表。

取值含义是**从真实数据里统计出来的**，例如：

* ``gender`` 只有 ``MALE``(533 条) / ``FEMALE``(31 条)
* ``elementtype`` 只有 ``Rock`` / ``Paper`` / ``Scissors`` / 空 —— 就是石头剪刀布
* ``type``（道具）只有 ``ITEM``(18) / ``PART``(26)

拿不准的一律标「推测」，不编。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Doc:
    """一个字段的说明。"""

    label: str
    help: str = ""
    #: 取值 -> 含义
    values: dict[str, str] = field(default_factory=dict)
    #: 会被自动重算，手改没用
    auto: bool = False
    #: 常改的字段，界面上高亮
    common: bool = False


# ---------------------------------------------------------------- 取值字典

V_GENDER = {"MALE": "雄性", "FEMALE": "雌性", "GENDERLESS": "无性别"}
V_ELEMENT = {"": "无属性", "None": "无属性", "Rock": "石头", "Paper": "布", "Scissors": "剪刀"}
V_BOOL = {"TRUE": "是", "FALSE": "否", "true": "是", "false": "否"}
V_ITEMTYPE = {"ITEM": "道具（直接用）", "PART": "零件（合成材料）"}
V_EFFECT = {
    "": "无",
    "RestoreHP": "回复体力",
    "RestorePP": "回复技能次数",
    "StatIncrease": "提升能力",
    "Cure": "治疗异常状态",
    "Revive": "复活",
    "Capture": "捕捉野生莫蒂",
    "DamageHP": "直接扣体力",
    "ShinyPotion": "闪光药水（推测）",
}
V_EFFECTSTAT = {
    "": "无",
    "All": "全部能力",
    "Attack": "攻击",
    "Defence": "防御",
    "Speed": "速度",
    "Level": "等级",
    "Poison": "中毒",
    "Paralysis": "麻痹",
}
V_DIVISION = {
    "1": "第 1 档",
    "2": "第 2 档",
    "3": "第 3 档",
    "4": "第 4 档",
}


# ---------------------------------------------------------------- 字段字典

FIELD_DOCS: dict[str, dict[str, Doc]] = {
    # ============================== 莫蒂（单机表）==============================
    "spdata/MortyInfo": {
        "id": Doc("内部 ID", "游戏认这个。只能用字母数字下划线，且不能和现有的重复"),
        "assetid": Doc("美术 ID", "决定用哪套图。填已存在的资源才会显示形象"),
        "number": Doc("图鉴编号", "图鉴里的序号，自动取最大值 +1", auto=True),
        "gender": Doc("性别", "影响外观文案", V_GENDER),
        "evolution": Doc("进化成", "填另一只莫蒂的 ID；留空表示不进化"),
        "evolutiontier": Doc("进化阶段", "1 = 初始形态，2/3… = 进化几次"),
        "height": Doc("身高", "只是展示文本，随便写，例如 5'2\"", common=False),
        "weight": Doc("体重", "只是展示文本，例如 110.2 lbs"),
        "badgereq": Doc("需要徽章", "-1 表示无要求；正数表示要拿到对应数量的徽章才能用"),
        "division": Doc("档位", "稀有度分档，抽卡按档位给", V_DIVISION),
        "includeingacha": Doc("能抽到", "是否进随机抽卡池", V_BOOL),
        "elementtype": Doc("属性", "石头剪子布三系相克", V_ELEMENT),
        "defeatedxp": Doc("击败经验", "打败它给多少经验", common=True),
        "hpbase": Doc("体力种族值", "越高越耐打", common=True),
        "attackbase": Doc("攻击种族值", "越高打得越疼", common=True),
        "defencebase": Doc("防御种族值", "越高越抗打", common=True),
        "speedbase": Doc("速度种族值", "越高越先出手", common=True),
        "stattotal": Doc("种族值总和", "自动按 体力+攻击+防御+速度 重算，不用手填", auto=True),
        "attacks": Doc(
            "技能学习表",
            "格式：技能ID:等级，逗号分隔。例：AttackOutburst:1, AttackCry:6",
            common=True,
        ),
    },
    # ============================== 莫蒂（联机表）==============================
    "mpdata/MortyInfo": {
        "morty_id": Doc("内部 ID", "必须和单机表里的 id 完全一致"),
        "asset_id": Doc("美术 ID", "必须和单机表里的 assetid 一致"),
        "number": Doc("图鉴编号", "整数，和单机表保持一致", auto=True),
        "gender": Doc("性别", "", V_GENDER),
        "height": Doc("身高", "展示文本"),
        "weight": Doc("体重", "展示文本"),
        "division": Doc("档位", "整数（单机表那边是字符串）", V_DIVISION),
        "element": Doc("属性", "null 或 Rock/Paper/Scissors", V_ELEMENT),
        "evolution_req": Doc("进化条件", "null 表示不进化"),
    },
    "mpdata/MortyAttacksInfo": {
        "morty_id": Doc("内部 ID", "对应 MortyInfo 里的 id"),
        "attacks": Doc("技能学习表", "结构：每条是 {attack_id: 技能ID, level: 学会等级}"),
    },
    # ============================== 道具（单机表）==============================
    "spdata/ItemInfo": {
        "id": Doc("内部 ID", "不能和现有的重复"),
        "assetid": Doc("美术 ID", "图标用哪套图"),
        "type": Doc("类别", "道具还是合成材料", V_ITEMTYPE),
        "cost": Doc("价格", "商店售价", common=True),
        "displayorder": Doc("排序", "商店/背包里的排列顺序", auto=True),
        "purchasebadgereq": Doc("购买需徽章", "0 表示无要求"),
        "spbaglimit": Doc("背包上限", "最多能带几个", common=True),
        "rewardbadgereq": Doc("奖励需徽章", "作为奖励发放时的门槛"),
        "rarity": Doc("稀有度", "数值越大越稀有", common=True),
        "includeingacha": Doc("能抽到", "", V_BOOL),
        "effecttype": Doc("效果类型", "这道具做什么", V_EFFECT, common=True),
        "effectvalue": Doc("效果数值", "配合效果类型，例如回复多少点体力", common=True),
        "effectstattype": Doc("作用项", "提升/治疗哪个能力或异常", V_EFFECTSTAT),
        "usableinworld": Doc("世界可用", "在探索地图上能不能用", V_BOOL),
        "usableincraft": Doc("合成可用", "能不能当合成材料", V_BOOL),
        "usableinbattle": Doc("战斗可用", "战斗中能不能用", V_BOOL),
        "useonself": Doc("对自己用", "是给自己还是给对面", V_BOOL),
        "attackeranimation": Doc("攻击方动画", "动画 ID，一般留空"),
        "defenderanimation": Doc("受击方动画", "例：CaptureGun"),
        "battlepoints": Doc("战斗点数", "一般留空"),
    },
    # ============================== 道具（联机表）==============================
    "mpdata/ItemInfo": {
        "item_id": Doc("内部 ID", "必须和单机表一致"),
        "asset_id": Doc("美术 ID", "必须和单机表一致"),
        "type": Doc("类别", "", V_ITEMTYPE),
        "cost": Doc("价格", "整数"),
        "store_level": Doc("上架等级", "商店解锁等级"),
        "reward_level_lower": Doc("奖励等级下限", ""),
        "reward_level_upper": Doc("奖励等级上限", ""),
        "effect_type": Doc("效果类型", "", V_EFFECT),
        "effect_stat": Doc("作用项", "", V_EFFECTSTAT),
        "use_world": Doc("世界可用", "", V_BOOL),
        "use_craft": Doc("合成可用", "", V_BOOL),
        "use_battle": Doc("战斗可用", "", V_BOOL),
        "use_self": Doc("对自己用", "", V_BOOL),
        "use_account": Doc("账号级使用", "推测：用了对账号全局生效"),
        "attacker_animation": Doc("攻击方动画", ""),
        "defender_animation": Doc("受击方动画", ""),
        "sortorder": Doc("排序", "整数", auto=True),
        "in_gacha": Doc("能抽到", "", V_BOOL),
        "mp_premium_cost": Doc("联机高级价格", "推测"),
    },
    # ============================== 技能 ==============================
    "spdata/AttackInfo": {
        "id": Doc("内部 ID", "不能和现有的重复"),
        "elementtype": Doc("属性", "石头剪子布", V_ELEMENT),
        "pp": Doc("可用次数", "-1 表示无限次", common=True),
        "effects": Doc(
            "效果",
            "格式：{Type:Hit, Power:50, Accuracy:0.95}，多条用逗号分隔。"
            "Type 常见 Hit（造成伤害）；Power 威力；Accuracy 命中率(0~1)；"
            "ToSelf:true 表示作用在自己身上",
            common=True,
        ),
    },
    "mpdata/AttackInfo": {
        "attack_id": Doc("内部 ID", "必须和单机表一致"),
        "pp_stat": Doc("可用次数", "-1 表示无限", common=True),
        "element": Doc("属性", "null 或 Rock/Paper/Scissors", V_ELEMENT),
        "effects": Doc(
            "效果",
            "结构：每条是 {type/power/accuracy/to_self} 的对象",
            common=True,
        ),
    },
    # ============================== 其他 ==============================
    "appdata/GachaDefault": {
        "drop_rates": Doc("掉率", "按档位的权重，例如 [80,12,6,2] 表示各档占比"),
        "gacha_promo_chance": Doc("促销概率", "-1 表示关闭"),
        "gacha": Doc("卡池列表", "每个池子有 gacha_id / cost / gacha_content"),
    },
    "spdata/RecipeInfo": {
        "id": Doc("配方 ID", ""),
        "number": Doc("排序", "", auto=True),
        "slot1": Doc("材料 1", "道具 ID"),
        "slot2": Doc("材料 2", "道具 ID"),
        "slot3": Doc("材料 3", "道具 ID"),
        "resultid": Doc("产物", "合成出来的道具 ID"),
    },
    "mpdata/RecipeInfo": {
        "recipe_id": Doc("配方 ID", ""),
        "sortorder": Doc("排序", "", auto=True),
        "item_id_1": Doc("材料 1", ""),
        "item_id_2": Doc("材料 2", ""),
        "item_id_3": Doc("材料 3", ""),
        "item_id_result": Doc("产物", ""),
    },
}


# ---------------------------------------------------------------- 资源包字典

#: 资源包名 -> 里面装的是什么
BUNDLE_DOCS: dict[str, str] = {
    "spdata": "单机数据表（莫蒂、技能、道具、任务、世界…）",
    "mpdata": "联机数据表（和单机表格式不同，两边都要改）",
    "appdata": "全局配置（抽卡掉率、资源归属）",
    "text": "本地化文本（11 种语言）",
    "preload": "启动预加载资源（音频等）",
    "anatomyparkbundle2": "解剖公园主题包",
}

#: 按前缀猜资源包内容
BUNDLE_PREFIX_DOCS: list[tuple[str, str]] = [
    ("anime", "角色动画与立绘"),
    ("anatomypark", "解剖公园主题包"),
    ("ep", "剧情章节资源"),
    ("au", "音频资源"),
    ("tg", "贴图资源"),
    ("v1.", "版本更新资源"),
    ("v2.", "版本更新资源"),
]


def bundle_doc(name: str) -> str:
    """给资源包名补一个人话说明。"""
    if name in BUNDLE_DOCS:
        return BUNDLE_DOCS[name]
    for pre, doc in BUNDLE_PREFIX_DOCS:
        if name.startswith(pre):
            return doc
    return ""


# ---------------------------------------------------------------- 资源类型字典

#: Unity 对象类型 -> 中文名
TYPE_DOCS: dict[str, str] = {
    "TextAsset": "文本 / 数据表",
    "Texture2D": "贴图",
    "Sprite": "精灵图（只读）",
    "Cubemap": "立方体贴图",
    "AudioClip": "音频（只读）",
    "MonoBehaviour": "脚本数据（剧情等）",
    "AssetBundle": "包自身元数据",
    "Material": "材质",
    "Shader": "着色器",
    "AnimationClip": "动画片段",
    "Animator": "动画控制器实例",
    "AnimatorController": "动画控制器",
    "GameObject": "游戏对象",
    "Transform": "变换（位置/缩放）",
    "SpriteRenderer": "精灵渲染器",
    "MonoScript": "脚本（无源码）",
    "Font": "字体",
    "Mesh": "网格",
    "RenderTexture": "渲染贴图",
}


#: 列表角标用的**短名**（长名会被裁掉）
TYPE_SHORT: dict[str, str] = {
    "TextAsset": "文本",
    "Texture2D": "贴图",
    "Sprite": "精灵图",
    "Cubemap": "立方体贴图",
    "AudioClip": "音频",
    "MonoBehaviour": "脚本数据",
    "AssetBundle": "包元数据",
    "Material": "材质",
    "Shader": "着色器",
    "AnimationClip": "动画片段",
    "Animator": "动画器",
    "AnimatorController": "动画控制",
    "GameObject": "游戏对象",
    "Transform": "变换",
    "SpriteRenderer": "精灵渲染",
    "MonoScript": "脚本",
    "Font": "字体",
    "Mesh": "网格",
    "RenderTexture": "渲染贴图",
}


def type_doc(t: str) -> str:
    return TYPE_DOCS.get(t, "")


def type_short(t: str) -> str:
    """列表角标用的短名；没有就退回原类型名前 6 个字符。"""
    return TYPE_SHORT.get(t) or t[:6]


# ---------------------------------------------------------------- 查询


def doc_for(table: str, name: str) -> Doc | None:
    return FIELD_DOCS.get(table, {}).get(name)


def field_label(table: str, name: str) -> str:
    """字段 -> 「中文名」，查不到就退回原名。"""
    d = doc_for(table, name)
    return d.label if d else name


def explain_value(table: str, name: str, value) -> str:
    """把一个字段值翻译成人话；没有对照就返回空串。"""
    d = doc_for(table, name)
    if d is None or not d.values:
        return ""
    key = "" if value is None else str(value)
    # 布尔在两边写法不同（TRUE / true），统一试一遍
    for k in (key, key.upper(), key.lower(), key.capitalize()):
        if k in d.values:
            return d.values[k]
    return ""


def explain_table(table: str) -> str:
    """``spdata/MortyInfo`` -> 「单机数据表 · 莫蒂」这种。"""
    if "/" not in table:
        return table
    bundle, asset = table.split("/", 1)
    b = bundle_doc(bundle)
    return f"{b} · {asset}" if b else table
