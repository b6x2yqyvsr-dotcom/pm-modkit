# 口蘑 Mod 工坊 · pm-modkit

《口袋莫蒂》Pocket Mortys（`com.conspiracyrick.pocketmortys`，V2.41.0 / Unity
2022.3.62f2 + IL2CPP）的**模组制作工具**。

- **两个界面**：桌面用 Dear ImGui（`imgui-bundle`），另有**网页版**给手机用
- 支持 **Windows / Linux / macOS / Android(Termux)**；iOS 用浏览器连
- 核心功能：**解包 / 浏览 / 预览 / 替换 / 回写 / 打包**
- 改完能出三种产物：**UnityCache 补丁**（不用重装 APK）、**重签名 APK**、**`.pmmod` 模组包**
- 资源包里的**每一个对象**都能解出来改（文本 / 贴图 / 类型树）
- 支持**新增**莫蒂 / 道具 / 技能（自动联动 5 张表 + 11 种语言的本地化）
- 能打开的容器：**目录、zip 家族（apk/apks/obb/jar/unity3d/bundle）、tar 家族、单个散装资源包**

文档：

| 文档 | 讲什么 |
|---|---|
| **[docs/加模组原理.md](docs/加模组原理.md)** | 资源格式分析、数据表全清单、三个坑 —— 想搞懂原理读这个 |
| **[docs/pmmod使用说明.md](docs/pmmod使用说明.md)** | `.pmmod` 怎么做、怎么用、格式规范 |
| **[docs/新增条目说明.md](docs/新增条目说明.md)** | **加**新莫蒂 / 道具 / 技能（要同时改 5 处表） |
| **[docs/跨平台说明.md](docs/跨平台说明.md)** | 各平台怎么装怎么跑、手机端怎么用、哪些**跑不了** |
| **[docs/加强版APK工作流.md](docs/加强版APK工作流.md)** | **自包含 APK**（加强版）怎么用、pmseed 播种原理、版本 bump |
| **[examples/](examples/)** | 三个能直接跑通的完整例子 |

---

## 四个平台

```bash
bash build-macos.sh        # macOS → dist/口蘑Mod工坊.app + .dmg
build-windows.bat          # Windows → dist\口蘑Mod工坊\口蘑Mod工坊.exe
bash android/termux-install.sh   # Android → 网页版，浏览器里用
python3 web/server.py --port 8765 --host 0.0.0.0   # iOS / 局域网 → Safari 添加主屏
```

手机端走的是**网页版**（后端 Python 跑 Termux 里，或跑在电脑上用手机连），
因为 UnityPy 的原生扩展没有 Android / iOS 轮子。
细节和功能对照见 **[docs/打包与安装.md](docs/打包与安装.md)**。

## 快速开始

工具分**桌面界面**和**网页界面**两种，核心功能一样。

### Windows

```bat
setup.bat        :: 双击，一次性装环境
启动.bat          :: 桌面图形界面
启动网页版.bat     :: 网页界面（手机连同一个 Wi-Fi 也能用）
```

### Linux / macOS

```bash
sh setup.sh        # 一次性装环境
./启动.sh           # 桌面图形界面
./启动网页版.sh      # 网页界面
```

### 手机

- **Android**：在 [Termux](https://f-droid.org/packages/com.termux/) 里跑
  `bash android/termux-install.sh`，然后浏览器开 `http://127.0.0.1:8765`
- **iOS**：电脑上 `./启动网页版.sh`，iPhone 用 Safari 连同 Wi-Fi 的那个地址

> **为什么手机不用原生界面**：`imgui-bundle` 没有 Android / iOS 的预编译轮子，
> 装不上。详见 **[docs/跨平台说明.md](docs/跨平台说明.md)**。

### 两种游戏文件，两种玩法

| 你手上是 | 怎么做 |
|---|---|
| `dp.apk` **+** `口蘑数据包.zip` | 两个一起打开；改完主要走 **UnityCache**（adb push，不用重装） |
| **`PocketMortys-加强版.apk`**（自带全部资源） | **只打开它一个**；改完可以**直接重打包 APK**，装上即生效 |

加强版里自带 155 个资源包和一张 `pmseed` 播种表，工具会自动认出来并提示。
细节见 **[docs/加强版APK工作流.md](docs/加强版APK工作流.md)**。

> **遇到「这些包不在 APK 里」？** 那通常是事实：`dp.apk` 里**只有 `text` 一个包**，
> 你改的 `spdata`/`appdata` 来自数据包。工具会把「每个改动包到底在哪」逐个列出来，
> 并给出三条出路。详见
> [这一节](docs/加强版APK工作流.md#这些包不在-apk-里是什么意思)。

### 打成免安装程序

```bash
pip install pyinstaller
python3 build/build_app.py          # 产物在 dist/口蘑Mod工坊/
```

三平台的自动打包见 [打包与安装.md](docs/打包与安装.md)。

> 也可以直接把文件拖到启动脚本上，或当参数传（**可以给多个**）：
> `./启动.sh dp.apk "口蘑数据包(1).zip"`

### 一次加多个文件

- **「打开源…」** —— 在文件对话框里按住 **⌘** 可以一次选中多个
  （`dp.apk` 和 `口蘑数据包.zip` 一起选就行）
- **「追加源…」** —— **不替换**当前的源，往后面加。
  一次只选一个也没关系，分几次加齐即可
- 左栏顶部的 **「来源（N）」** 列出当前打开的所有文件/目录，
  可以逐个「移除」或「关闭全部」

> 移除某个来源时，如果它里面还有没导出的改动，工具会**拒绝并提示**，
> 不会默默把你的改动丢掉；其它来源里的改动也不会受影响。

> **建议两个一起打开**：APK 提供内置的 `text`，数据包提供 155 个下载缓存包。
> 工具会把它们合并成一个源。

---

## 界面长什么样

### 桌面版（Dear ImGui）



```
┌───────────────────────── 菜单栏 · 工具栏 ─────────────────────────┐
│ 打开源… 追加源… 应用模组… 导出…   ● 1 个包 / 1 处改动已就绪        │
├───────────┬───────────────┬───────────────────────────────────┤
│ 来源(2) ▸  │ 资源列表        │ 预览 / 替换                        │
│ 资源包 155 │ 类型筛选 + 搜索 │ 贴图可缩放看像素、文本可看 JSON      │
│ 带来源角标  │ 带类型角标      │ 导出 / 替换 / 撤销 / 编辑文本       │
├───────────┴───────────────┴───────────────────────────────────┤
│ 待应用改动 │ 日志                                                │
└───────────────────────────────────────────────────────────────┘
```

四处会用到的地方：

1. **左栏「编辑哪一份」** —— 同一个包在多处存在时（`text` 就有三份），
   这里选改哪一份。**这是最容易出错的地方**，选错了改完不生效。
2. **中栏类型下拉 + 搜索** —— 一个包上万对象也不卡，列表做了裁剪。
3. **右栏「替换…」** —— 贴图给 PNG/JPG，文本给 JSON/TXT。尺寸不符会自动缩放。
4. **「导出…」页** —— 选产物形式，然后生成。

---

## 能改什么

资源包里**每一个对象**都能解出来看和导出。按处理方式分四类：

| 分类 | 类型 | 操作 | 说明 |
|---|---|---|---|
| **文本** | `TextAsset` 及其全部子类（`MonoScript`、`ShaderInclude`、`PackageManifest`、`AssemblyDefinition*` …） | 查看 / 编辑 / 替换 / 导出 | 游戏数据表**全是 JSON** |
| **贴图** | `Texture2D`、`Cubemap` | 查看 / 替换 / 导出 | 直接给 PNG，尺寸不符自动缩放 |
| **类型树** | 其余**所有**类型：`Material`、`AnimationClip`、`Shader`、`GameObject`、`Transform`、`SpriteRenderer`、`Animator`、`MonoBehaviour` … | 查看 / 编辑 / 替换 / 导出 | 编辑类型树 JSON，属于高级用法 |
| **只读** | `Sprite`（只是「引用 + 裁切框」）、`AudioClip`（FSB 流式）、`AssetBundle`（包元数据） | 查看 / 导出 | 音频导出为 wav |

实测各类型都能读出来 → 改完回写 → 重新解包内容一致。

数量分布（155 个包全扫一遍）：`Sprite` 12,846、`Texture2D` 12,601、
`GameObject`/`Transform` 各 477、`SpriteRenderer` 315、`AudioClip` 310、
`MonoBehaviour` 63、`TextAsset` 50、`AnimationClip` 30、`AnimatorController` 30、
`Shader`/`Material` 各 12、`MonoScript` 8、`Cubemap` 1。

具体能改哪些数值，见
[数据表全清单](docs/加模组原理.md#4-数据表全清单模组主战场) ——
抽卡掉率、莫蒂属性、技能威力、道具、商店、竞技场奖励都在里面。

### 能打开的容器格式

> 下面这些是**读**的入口。想**新增**条目（加莫蒂/道具），见
> [新增条目说明](docs/新增条目说明.md)。

| 形式 | 说明 |
|---|---|
| 目录 | 已解包的数据目录 |
| `zip` 家族 | `.zip` `.apk` `.apks` `.obb` `.jar` `.unity3d` `.bundle` `.pak` |
| `tar` 家族 | `.tar` `.tar.gz` `.tgz` `.tar.bz2` `.tar.xz` |
| **单个散装文件** | 一个 `.assetbundle`，或缓存里的一个 `__data` |

散装文件也能直接打开 —— 放在缓存层级里的 `__data` 会自动认出包名
（`Shared/spdata/<ver>/__data` → `spdata`）。

常用入口：

- **「解包整包…」**（资源列表面板）—— 把一个包里的所有对象一次性导出来，
  贴图→png、文本→json、其余→类型树 json、音频→wav
- `tools/cli.py dump` —— 同样的能力，命令行版

### 还能「新增」条目

不止改现有的，也能**加新的**：工具栏「**新增条目…**」可以造新莫蒂 / 道具 / 技能。

做法是**克隆一条现有的再改** —— 因为这些表的字段一个都不能少，凭空造容易崩游戏。
工具会自动把该联动的地方全改掉：

```
✓ 新增莫蒂 MortyCoolRick（模板 MortyDefault）
  spdata/MortyInfo            ①  单机数据（字符串字段）
  mpdata/MortyInfo            ②  联机数据（有类型，和①格式不同！）
  mpdata/MortyAttacksInfo     ③  技能学习表
  appdata/BundleAssetAssignment ④ 形象资源登记
  text/Morty（11 种语言）      ⑤  本地化名称与描述
  appdata/GachaDefault            可选：加进抽卡池
```

命令行同理：

```bash
python3 tools/cli.py entries 口蘑数据包.zip        # 先看有哪些、各多少条
python3 tools/cli.py add-entry 口蘑数据包.zip \
    --kind morty --id MortyCoolRick --from MortyDefault \
    --name "酷里克莫蒂" --assetid MortyBigBrain --gacha 0 \
    --set spdata/MortyInfo.hpbase=88 --modpack 新增莫蒂.pmmod -o 产物
```

**拖拽添加**：把 APK / 数据包 zip 直接拖到窗口上（桌面挂 GLFW 原生拖放，网页用 HTML5），
一次多个、拖文件夹也行、已有源时自动追加。

**「新增条目」左右两栏**：左边挑模板（可搜 id/中文名、按数值筛），右边填名字/数值/美术，
BEFORE/AFTER 并排对比，底部操作条固定。技能不再显示没用的「加进抽卡池」。

**新增条目可以直接挑图**：勾「独立美术」后逐帧选图（道具是图标+正面两帧），
还能批量选图按文件名自动对应。所有要创建的 id/主键都会**预设并列出**。

**道具的模板不再挑不出来**：单机 44 条 / 联机 51 条里只有 28 条两边都有，
现在缺的那张表会照着结构自动补齐，并按目标表的类型正确转换（字符串→int/bool）。

**图形化「图鉴」**：直接看角色的立绘、15 帧动作、数值条和技能效果（已翻成中文）。

**技能效果的坑已修**：单机表是字符串 DSL、联机表是真 JSON，
其中 `Accuracy:0.15:true` 是**三段式**（命中率 0.15 + 没中继续），
按「一个冒号」切会把命中率静默丢掉。现在 690/690 逐字节往返、两边一起写、体检能查。
细节见 [新增条目说明](docs/新增条目说明.md)。

**想造真·新角色**（有自己独立的 15 帧美术）：
```bash
python3 tools/cli.py add-character 口蘑数据包.zip --id MortyMyGuy --from MortyToxicMetal \
    --name "我的原创莫蒂" --image Icon=图标.png
```
图形界面在「新增条目」里勾「连美术一起造」。
细节见 [完全新增一个角色](docs/新增条目说明.md#完全新增一个角色连美术一起造)。

**出产物前会自动体检**（主键重复之类会让游戏起不来的问题，直接拦住）：

```bash
python3 tools/cli.py check 加强版.apk    # 看问题
python3 tools/cli.py fix   加强版.apk --apk 加强版.apk --apk-out 修好的.apk
```

界面里**每个字段都带中文名和取值含义**（`hpbase` → 体力种族值、`badgereq` → 需要徽章、
`elementtype` → 属性），还有一张可随时查的「字段中英文对照」表（菜单 帮助 → 字段中英文对照…）。
完整说明见 **[docs/新增条目说明.md](docs/新增条目说明.md)**。

### 一个最小例子：抽卡全保底

1. 左栏选 `appdata`
2. 中栏选 `TextAsset GachaDefault`
3. 右栏点「编辑文本」
4. 把 `"drop_rates": [80, 12, 6, 2]` 改成 `[100, 0, 0, 0]`
5. 「应用到资源」→ 底部「待应用改动」会出现这条
6. 点「导出…」→ 选 **UnityCache（推荐）** → 「生成 UnityCache 产物」
7. 产物目录里有 `push.sh`，跑它（或照抄 `推送说明.md` 里的 adb 命令）

`examples/` 下有两个**能直接跑通**的完整例子：
[抽卡保底](examples/1-抽卡保底/recipe.json) 和
[换莫蒂贴图](examples/2-换贴图/recipe.json)。

---

## 三种产物

### 1. UnityCache 补丁 ★ 推荐

生成 `UnityCache/Shared/<包名>/<目录名>/__data`，推到设备即可，**不用重装 APK**。

```bash
BASE=/sdcard/Android/data/com.conspiracyrick.pocketmortys/files
adb root
adb push UnityCache "$BASE/"
adb shell "chown -R 10289:10289 $BASE"
adb shell "chmod -R 777 $BASE"
```

产物里自带 `push.sh`，直接跑就行。目录名（12 个零 + 小端 version 的 hex）
会保持和原来一致 —— 这是 Unity 认定「缓存还新鲜」的依据，不能改。

### 2. `.pmmod` 模组包

体积小、可分享。两种形态：

- **源码级**（默认）：只存替换文件和它该盖到哪个资源上，应用时现场回写。
  同一份模组既能打到 APK 也能打到缓存。通常几十 KB。
- **成品级**（勾选「内嵌成品 bundle」）：直接存回写好的包，免依赖但体积大。

完整用法（怎么做、怎么用、格式规范、常见问题）见
**[.pmmod 使用说明](docs/pmmod使用说明.md)**。

### 3. APK 重打包 + 签名

只对**本来就在 APK 里**的包（当前只有 `text`）有效。其它包会明确告诉你
「打不进去，走 UnityCache」。

重打包遵守三条硬性要求：`resources.arsc` 不压缩 + 4 字节对齐、先对齐后签名、
v2/v3 方案签名。实测 150.8 MB 的包 **16.6 秒**完成，`apksigner verify` 通过。

> 用本工具签的 APK 与手机上原装的签名不同，需要**先卸载再装**。

---

## 网页界面

给手机和无图形环境用的。它是个自带的小 HTTP 服务（只用标准库），

```bash
./启动网页版.sh            # 默认 8765 端口
# 或
.venv/bin/python web/server.py --port 8765 dp.apk 口蘑数据包.zip
```

启动后控制台会打印两个地址：`http://127.0.0.1:8765`（本机）和
`http://192.168.x.x:8765`（**手机填这个**，需同 Wi-Fi）。

界面是手机优先的（底部标签栏），能做的事跟桌面版一致：打开源（填路径或
**直接上传文件**）、浏览资源、看贴图、改数据表、上传替换贴图、新增莫蒂/道具、
生成产物。左侧的英文资源名和字段名同样带中文对照。

它**不依赖 `imgui-bundle`**，所以在 Termux / 服务器 / 树莓派上都能跑。

---

## 命令行

图形界面能做的，命令行都能做，方便批处理和复现。

```bash
# 看有哪些包
.venv/bin/python tools/cli.py list 口蘑数据包.zip

# 看包里有什么
.venv/bin/python tools/cli.py list 口蘑数据包.zip --bundle spdata --type TextAsset

# 导出资源当模板
.venv/bin/python tools/cli.py dump 口蘑数据包.zip --bundle appdata --name GachaDefault -o 模板/

# 按配方做模组包
.venv/bin/python tools/cli.py build-mod 口蘑数据包.zip --recipe recipe.json -o 我的模组.pmmod

# 把模组打到源上，出 UnityCache 产物
.venv/bin/python tools/cli.py apply 我的模组.pmmod 口蘑数据包.zip -o 产物/ --mode cache

# 打进 APK（重打包 + 对齐 + 签名）
.venv/bin/python tools/cli.py pack-apk dp.apk --mod 我的模组.pmmod -o dp-mod.apk

# 指定改哪一份副本（默认取下载缓存）
.venv/bin/python tools/cli.py dump dp.apk --bundle text --name ZH_CN --ref text=apk:Android -o 模板/

# 工具链自检
.venv/bin/python tools/cli.py doctor
```

配方 `recipe.json`（`file` 路径相对配方文件所在目录）：

```json
{
  "name": "抽卡全保底",
  "author": "某人",
  "description": "把稀有度掉率全压到最高档",
  "edits": [
    {"bundle": "appdata", "asset": "GachaDefault",
     "type": "TextAsset", "file": "edits/gacha.json"},
    {"bundle": "anime101", "asset": "CharacterAnimeRickBack",
     "type": "Texture2D", "file": "edits/rick.png"}
  ]
}
```

---

## 目录结构

```
pm-modkit/
├── app/main.py            桌面图形界面（Dear ImGui）
├── web/                   网页界面（手机 / 无图形环境用，只用标准库）
│   ├── server.py          自带的小 HTTP 服务 + JSON 接口
│   └── index.html         单文件前端，手机优先
├── android/               Android(Termux) 安装与启动脚本
├── build/                 打包成免安装程序
├── modkit/                核心库，不依赖 GUI
│   ├── paths.py           UnityCache 目录名 / __info 编解码
│   ├── source.py          「源」抽象：目录 / zip / tar / 散装包 → 统一索引
│   ├── bundle.py          单个资源包的读 / 改 / 回写
│   ├── assetops.py        按类型导入导出（文本 / 贴图 / 音频 / 类型树）
│   ├── modpack.py         .pmmod 模组包格式
│   ├── entries.py         新增条目：往数据表里加莫蒂 / 道具 / 技能
│   ├── apkbuild.py        APK 重打包 / zipalign / apksigner
│   └── session.py         编辑会话：源 + 改动 + 产物生成
├── tools/
│   ├── cli.py             命令行
│   ├── selftest.py        核心库端到端自测（不需要 GUI）
│   └── webtest.py         网页版接口端到端自测
├── examples/              能直接跑通的模组例子
│   ├── 1-抽卡保底/        改 appdata/GachaDefault 的掉率
│   ├── 2-换贴图/          换 anime101 里的一张 Texture2D
│   └── 3-新增莫蒂/        加一只新莫蒂（联动 5 张表）
├── docs/
│   ├── 加模组原理.md       资源结构分析与加模组原理 ★
│   ├── 加强版APK工作流.md  自包含 APK 的用法与 pmseed 播种原理
│   ├── 跨平台说明.md       各平台支持情况与安装方式
│   ├── pmmod使用说明.md    .pmmod 怎么做、怎么用、格式规范
│   └── 新增条目说明.md     加新莫蒂 / 道具 / 技能
├── setup.sh / setup.bat   一次性建环境（Linux+macOS / Windows）
├── 启动.sh / 启动.bat      桌面图形界面
├── 启动网页版.sh / .bat    网页界面
└── 启动.command            macOS 双击启动
```

---

## 自测

```bash
.venv/bin/python tools/selftest.py \
    --apk 已修改安装包dp.apk --data "口蘑数据包(1).zip"
```

覆盖：打开源 → 改数据表 → 改贴图 → 出 UnityCache 产物 → 重新解包校验改动
真的生效 → 打 `.pmmod` → 打到干净会话 → 导出导入回环 → 大文本不截断 → 路径工具。

界面本身也能无人值守跑一遍（把各面板、各资源类型都渲染一遍抓错）：

```bash
PM_MODKIT_AUTOTEST=20 .venv/bin/python app/main.py dp.apk 口蘑数据包.zip
```

---

## 已知限制

除下面几条，资源包里**其它对象都能解出来改**（文本 / 贴图 / 类型树通吃）：

- **音频只能导出**，不能替换（FSB 流式，需重编码）
- **Sprite 不能直接替换**，要改它引用的 Texture2D
- **类型树是「高级模式」**：JSON 结构复杂，改错字段可能导致该资源加载失败。
  改之前先「导出」留个底，改坏了点「撤销此资源」能还原
- **加新角色**没验证过（要动 `MortyInfo` + 贴图 + `BundleAssetAssignment`）
- **运行时生效与否取决于改对了哪一份副本** —— 工具没法在这台机器上跑安卓游戏，
  格式层面已经验证到底，上机验证的步骤见
  [怎么验证改对了](docs/加模组原理.md#9-怎么验证改对了)
