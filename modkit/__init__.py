"""口蘑 Mod 工坊 —— Pocket Mortys 模组工具核心库。

不依赖 GUI，可单独作为库/命令行使用。GUI 在 ``app/`` 下。

模块分工::

    paths.py      UnityCache 目录名 / __info 的编解码
    sysenv.py     跨平台：路径、字体、Android 构建工具查找
    source.py     「源」抽象：APK、数据包 zip、解包目录
    bundle.py     单个 AssetBundle 的读取 / 改动 / 回写
    assetops.py   按资源类型导入导出（贴图、文本、音频、MonoBehaviour）
    modpack.py    .pmmod 模组包格式
    apkbuild.py   APK 重打包 / zipalign / apksigner
    session.py    编辑会话：源 + 待应用改动 + 产物生成
    entries.py    新增条目：往数据表里加莫蒂 / 道具 / 技能
    fields.py     中文字典：英文字段名 / 资源名 / 取值 的对照
    validate.py   数据表体检：主键重复之类的「游戏进不去」问题
    character.py  完全新增角色：连自己的 15 帧美术一起造
    effects.py    技能效果的两种编码（单机字符串 / 联机 JSON）互转
"""

__version__ = "1.0.0"

from . import paths, sysenv, source, bundle, assetops, modpack, apkbuild, session, entries, fields, validate, character, effects

__all__ = [
    "paths",
    "sysenv",
    "source",
    "bundle",
    "assetops",
    "modpack",
    "apkbuild",
    "session",
    "entries",
    "fields",
    "validate",
    "character",
    "effects",
    "__version__",
]
