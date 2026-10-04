# `.pmmod` 模组包怎么用

`.pmmod` 就是个 **zip**，里面装「改了哪些资源、改成什么样」。它是这个工具的
模组分发格式 —— 做好了发给别人，别人一键就能套到自己的游戏上。

一句话记住：

> **`.pmmod` 是「菜谱」，不是「菜」。**
> 它不含整个游戏，只含你改过的那几个文件（通常几十 KB）。

---

## 目录

- [两种形态，先搞清楚你在做哪种](#两种形态先搞清楚你在做哪种)
- [怎么做一个 .pmmod](#怎么做一个-pmmod)
- [怎么用一个 .pmmod](#怎么用一个-pmmod)
- [三个完整例子](#三个完整例子)
- [格式规范（想自己生成的话）](#格式规范想自己生成的话)
- [常见问题](#常见问题)

---

## 两种形态，先搞清楚你在做哪种

工具里导出 `.pmmod` 时有一个勾选框「**内嵌成品 bundle**」：

|  | 源码级（默认，**不勾**） | 成品级（**勾上**） |
|---|---|---|
| 里面存什么 | 你改的那几个文件（png / json） | 整个回写好的资源包 |
| 体积 | 几 KB ~ 几百 KB | 几 MB ~ 几十 MB |
| 应用时 | 现场打开你的游戏数据、把文件盖进去 | 直接用包里的成品，不碰原始数据 |
| 对资源包版本 | **必须和作者的一致** | 无所谓，字节已经是结果了 |
| 适用 | 同版本玩家之间分享 | 换版本也能用 / 想省事 |

**默认用源码级就行**，体积小、能看清楚改了什么。

> ⚠️ 源码级模组是「按资源身份」（类型 + 名字 / path_id）盖上去的。
> 如果你的游戏数据版本和作者不一样，资源对不上，应用时会明确报
> **「找不到资源 xxx」** —— 这时候改用成品级，或者自己照着做。

---

## 怎么做一个 `.pmmod`

### 办法一：图形界面（推荐）

1. `./启动.command` 打开界面，用「打开源…」把 **APK + 数据包** 都选上
2. 左栏选资源包 → 中栏选资源 → 右栏改成你想要的样子：
   - 数据表：点「**编辑文本**」，改 JSON，点「应用到资源」
   - 贴图：点「**替换…**」，选一张 PNG
3. 改完后底部「**待应用改动**」会列出所有改动，确认没漏
4. 点工具栏「**导出…**」→ 切到「**.pmmod 模组包**」标签页
5. 填模组名/作者/说明（可选勾「内嵌成品 bundle」）→ 点「**导出 .pmmod…**」

### 办法二：命令行 + 配方（适合批量、可复现）

写一个 `recipe.json`：

```json
{
  "name": "抽卡保底",
  "author": "你的名字",
  "description": "把抽卡掉率全压到第一档",
  "edits": [
    {"bundle": "appdata", "asset": "GachaDefault",
     "type": "TextAsset", "file": "edits/GachaDefault.json"},
    {"bundle": "anime101", "asset": "CharacterAnimeRickBack",
     "type": "Texture2D", "file": "edits/替换图.png"}
  ]
}
```

> `file` 的相对路径是**相对配方文件所在目录**。
> `asset` 也可以写成 `"path_id": 123456`。

```bash
# 先把你想要改的资源导出来当模板
.venv/bin/python tools/cli.py dump 口蘑数据包.zip --bundle appdata --name GachaDefault -o 模板/

# 照着模板改好，放到 edits/ 下，然后打包
.venv/bin/python tools/cli.py build-mod 口蘑数据包.zip --recipe recipe.json -o 我的模组.pmmod

# 想要成品级就加 --prebuilt
.venv/bin/python tools/cli.py build-mod 口蘑数据包.zip --recipe recipe.json -o 我的模组.pmmod --prebuilt
```

配方支持 `"resize": false` 来禁止贴图自动缩放（尺寸不符时直接报错）。

### 办法三：直接手搓（高级）

`examples/` 下有两个能直接跑的完整例子，照着改就行。

---

## 怎么用一个 `.pmmod`

拿到别人的 `.pmmod` 之后，**选一种落地方式**：

### 图形界面

1. 工具栏「**应用模组…**」→ 选 `.pmmod`
2. 底部「待应用改动」会出现模组里的每一条改动（**成品包会标「成品包，来自 .pmmod」**）
3. 点「导出…」选产物形式：
   - **UnityCache（推荐）** —— 不用重装 APK
   - **APK 重打包** —— 重新签名，要卸载重装
   - **CDN 目录** —— 丢给私服

### 命令行

```bash
# 打成 UnityCache 产物（推荐，不用重装 APK）
.venv/bin/python tools/cli.py apply 别人的模组.pmmod 口蘑数据包.zip -o 产物/ --mode cache

# 打成 CDN 目录
.venv/bin/python tools/cli.py apply 别人的模组.pmmod 口蘑数据包.zip -o 产物/ --mode cdn

# 直接打进 APK（自动重打包 + 对齐 + 签名）
.venv/bin/python tools/cli.py pack-apk dp.apk --mod 别人的模组.pmmod -o dp-mod.apk

# 想指定改哪一份副本（同一个包有多份时）
.venv/bin/python tools/cli.py pack-apk dp.apk --mod 模组.pmmod --ref text=apk:Android -o dp-mod.apk
```

### 推上设备

`--mode cache` 的产物里自带 `push.sh` 和 `推送说明.md`：

```bash
cd 产物/UnityCache产物
./push.sh          # 内部就是 adb root + adb push + chown/chmod
```

手动的话就是：

```bash
BASE=/sdcard/Android/data/com.conspiracyrick.pocketmortys/files
adb root
adb push UnityCache "$BASE/"
adb shell "chown -R 10289:10289 $BASE"
adb shell "chmod -R 777 $BASE"
```

然后启动游戏即可。**不用卸载重装。**

---

## 三个完整例子

仓库里 `examples/` 有两个**能直接跑**的例子，做完就是一个 `.pmmod`。

### 例 1：抽卡保底

```bash
.venv/bin/python tools/cli.py build-mod 口蘑数据包.zip \
    --recipe "examples/1-抽卡保底/recipe.json" -o 抽卡保底.pmmod
```

它改的是 `appdata/GachaDefault`：

| 字段 | 原来 | 改成 |
|---|---|---|
| `drop_rates` | `[80, 12, 6, 2]` | `[100, 0, 0, 0]` |
| `gacha_promo_chance` | `-1` | `-1` |

改完抽卡次次出第一档。想换个档位就编辑
`examples/1-抽卡保底/edits/GachaDefault.json` 里的 `drop_rates`。

### 例 2：换莫蒂贴图

```bash
.venv/bin/python tools/cli.py build-mod 口蘑数据包.zip \
    --recipe "examples/2-换贴图/recipe.json" -o 换贴图.pmmod
```

把 `anime101` 里的 `CharacterAnimeRickBack` 换成 `替换图.png`。

`edits/_模板-原图.png` 是**原图**（709×708），照着它画就行。
尺寸不一样也没关系，工具会自动缩放到原尺寸（想禁掉就在配方里写
`"resize": false`）。

### 例 3：改汉化文案

```bash
# 1) 导出中文表（注意用 --ref 指定 APK 里那一份，不然改的是缓存那份）
.venv/bin/python tools/cli.py dump dp.apk --bundle text --name ZH_CN \
    --ref "text=apk:Android" -o 模板/

# 2) 编辑 模板/text/text__TextAsset__ZH_CN.json，比如把 PLAYER_NAME 改掉

# 3) 写配方并打包
cat > recipe.json <<'JSON'
{
  "name": "中文修正",
  "edits": [{"bundle": "text", "asset": "ZH_CN", "type": "TextAsset",
             "file": "模板/text/text__TextAsset__ZH_CN.json"}]
}
JSON
.venv/bin/python tools/cli.py build-mod dp.apk --recipe recipe.json \
    --ref "text=apk:Android" -o 中文修正.pmmod
```

---

## 格式规范（想自己生成的话）

`.pmmod` 就是个 zip：

```
mod.json
assets/appdata/TextAsset__1234__GachaDefault.txt
assets/anime101/Texture2D__5678__CharacterAnimeRickBack.png
bundles/appdata.assetbundle          # 仅成品级模组才有
```

`mod.json`：

```json
{
  "format": "pmmod/1",
  "name": "抽卡保底",
  "author": "示例",
  "description": "把抽卡掉率全压到第一档",
  "created": "2026-05-20T14:41:00",
  "game": "com.conspiracyrick.pocketmortys",
  "edits": [
    {
      "bundle": "appdata",
      "type": "TextAsset",
      "path_id": 1234567890,
      "name": "GachaDefault",
      "file": "assets/appdata/TextAsset__1234567890__GachaDefault.txt",
      "sha256": "……"
    }
  ]
}
```

要点：

- `edits[].file` 是 zip 内的相对路径，内容就是**导出格式**的原样文件
  （文本→UTF-8 文本，贴图→PNG 字节，类型树→JSON 文本）
- 应用时**先按 `path_id` 找，找不到再按 `type` + `name` 找** ——
  所以跨版本也能有一定成功率
- `bundles/<包名>.assetbundle` 是可选的成品包，优先级高于 `edits`
- `format` 目前是 `pmmod/1`

自己生成最小可用版本，其实只要 `mod.json` + 那一个文件就行，
`sha256` 字段可以不填。

---

## 常见问题

**Q：应用模组时报「找不到资源 xxx」？**
你的游戏数据版本和作者的不一样，`path_id` 和名字都对不上。
让作者改用**成品级**（勾「内嵌成品 bundle」）重发一个，或者你照着
[怎么做](#怎么做一个-pmmod) 自己做一遍。

**Q：推上去了但游戏里没变化？**
大概率**改错了副本**。同一个包可能在 APK 和下载缓存里各有一份
（`text` 就有三份）。在图形界面中栏顶部的「**编辑哪一份**」里换一个再试；
命令行加 `--ref 包名=来源:平台`，例如 `--ref text=apk:Android`。

**Q：`.pmmod` 能合并吗？**
不能自动合并。两个模组改了**同一个资源**时，后应用的那个生效。
改不同资源的话，依次「应用模组…」即可，改动会累加在「待应用改动」里。

**Q：怎么知道模组里改了什么？**
`.pmmod` 是 zip，直接解开看 `mod.json` 的 `edits` 列表。
图形界面「应用模组」后，底部「待应用改动」也会逐条列出来。

**Q：能改哪些东西？**
资源包里**每一个对象**都能解出来改：

| 分类 | 类型 | 怎么改 |
|---|---|---|
| 文本 | `TextAsset` 及其全部子类 | 编辑 JSON / 文本 |
| 贴图 | `Texture2D`、`Cubemap` | 换 PNG |
| 类型树 | `Material`、`AnimationClip`、`Shader`、`GameObject`、`Transform`、`SpriteRenderer`、`Animator`、`MonoBehaviour` … | 编辑类型树 JSON |
| 只读 | `Sprite`（改它引用的 Texture2D）、`AudioClip`（只能导出 wav） | — |

**Q：模组包里有游戏本体数据吗？**
没有。源码级模组只含你改的那几个文件，通常几十 KB。
