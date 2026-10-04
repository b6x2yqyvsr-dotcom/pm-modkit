# 「加强版」自包含 APK 工作流

`PocketMortys-加强版.apk` 和之前那个 `已修改安装包dp.apk` **不是一回事**，
工作方式也不一样。

---

## 一句话区别

| | 普通 APK（dp.apk） | **加强版 APK** |
|---|---|---|
| APK 里有什么 | 只有 `text` 一个包 | **全部 155 个包** |
| 还要不要数据包 | **要**（`口蘑数据包.zip`） | **不要**，一个文件就够 |
| 改完怎么生效 | 只能走 UnityCache（adb push） | **重打包 APK** 或 UnityCache 都行 |
| 适合谁 | 已经装好游戏、想微调 | 想打一个「装完就是 mod 版」的整包 |

---

## 它是怎么做到的：pmseed 播种机制

加强版的 APK 里多了一份东西：

```
assets/AssetBundles/Android/manifest.json      156 条，真清单（含每个包的 version）
assets/AssetBundles/Android/*.assetbundle      155 个资源包，全部在这
assets/pmseed/index.txt                        播种表
```

`assets/pmseed/index.txt` 长这样（156 行）：

```
anatomyparkbundle2 00000000000000000000000066000000
anime101 000000000000000000000000eb030000
appdata 000000000000000000000000d3070000
text 000000000000000000000000ec030000
...
```

每行是 **`<包名> <缓存目录名>`**。游戏首次运行时会照着这张表，把 APK 里的
`assets/AssetBundles/Android/<包名>.assetbundle` 播种到设备上的：

```
/sdcard/Android/data/com.conspiracyrick.pocketmortys/files/
    UnityCache/Shared/<包名>/<目录名>/__data
```

**所以：把改好的包写回 APK → 重装 → 游戏重新播种 → 改动生效。**

> 那个 `<目录名>` 不是随机的，它是 **12 个零字节 + 小端 4 字节的 version**：
> `000000000000000000000000d3070000` → `d3 07 00 00` → **2003**。
> 也就是说**目录名由 manifest 里的 version 决定** —— 这一点很关键，见下文。

---

## 怎么用

### 图形界面 / 网页界面

**只打开加强版 APK 一个文件就行**，不用再拖数据包。打开后工具栏会显示：

> ★ 自带全部 155 个包

然后照常改东西（改数据表、换贴图、新增莫蒂……），最后「导出…」，
两个方向都通：

| 产物 | 什么时候用 |
|---|---|
| **APK 重打包** | 想要一个「装完即 mod 版」的整包，给别人分享 |
| **UnityCache 产物** | 已经有安装在设备上，adb push 就行，不用重装 |

### 命令行

```bash
# 改数据表 + 换贴图，直接写回 APK
python3 tools/cli.py pack-apk PocketMortys-加强版.apk \
    --mod 我的模组.pmmod \
    -o PocketMortys-加强版-mod.apk

# 强制让设备丢掉旧缓存、重新播种（见下节）
python3 tools/cli.py pack-apk PocketMortys-加强版.apk \
    --mod 我的模组.pmmod --bump \
    -o PocketMortys-加强版-mod.apk

# 只想出 UnityCache 产物（不用重装）
python3 tools/cli.py apply 我的模组.pmmod PocketMortys-加强版.apk \
    -o 产物 --mode cache
cd 产物/UnityCache产物 && ./push.sh
```

改完的包会**逐个**写回 APK：

```
将替换 APK 内 2 个条目：
  assets/AssetBundles/Android/appdata.assetbundle
  assets/AssetBundles/Android/anime101.assetbundle
重打包完成：替换 2 项，丢弃旧签名 3 项
✓ zipalign：已 4 字节对齐
✓ 签名：已用 v2/v3 方案签名
✓ apksigner verify: V3.0 Signer: ...
```

实测 316 MB 的加强版，替换 + 对齐 + 签名 **约 28 秒**。

---

## 装到已经有旧缓存的设备上：`--bump`

**这是最容易踩的坑。**

游戏靠**缓存目录名**判断「这份缓存还新鲜吗」。而目录名 = f(version)。
如果你改了包但没改 version，目录名就没变 → 游戏认为缓存还有效 →
**不会重新播种** → 改动不生效。

所以：

| 场景 | 要不要 `--bump` |
|---|---|
| 装到**全新**设备 / 先清过应用数据 | 不用，首次运行自然会播种 |
| 装到**已经跑过游戏**的设备（覆盖安装） | **要**，否则旧缓存还在 |

`--bump` 干的事：

1. 把被改动包的 `version` **+1**（写在 `assets/AssetBundles/*/manifest.json`）
2. 同步更新 `assets/pmseed/index.txt` 里对应的目录名

```
appdata    v2003 → v2004    目录名 d3070000 → d4070000  ✓ 对得上
anime101   v1003 → v1004    目录名 eb030000 → ec030000  ✓ 对得上
未改动的 154 个包：一个字没动
```

**它不动的东西**（都是故意的）：

- **顶层 `version`（1011）不动** —— 那是和服务器对齐的清单版本，
  动了可能触发整包重新下载
- **`assets/AssetBundle.dat` 不动** —— 那是 .NET BinaryFormatter 流，
  改里面的长度前缀容易把文件写坏；而且它存的只是「上次加载过的清单」，
  和 `manifest.json` 不一致**恰好就是我们要的「缓存过期」信号**

> 手机上更省事的做法：**设置 → 应用 → 口袋莫蒂 → 清除数据**，再装 modded APK。
> 效果和 `--bump` 一样，还不用动清单。

---

## 一个完整的例子

```bash
cd pm-modkit

# 1) 看看里面有什么
python3 tools/cli.py list ~/Desktop/PocketMortys-加强版.apk | head -20
#    155 个包（APK 内置 156）

# 2) 导出要改的表当模板
python3 tools/cli.py dump ~/Desktop/PocketMortys-加强版.apk \
    --bundle appdata --name GachaDefault -o 模板/

# 3) 编辑 模板/appdata/appdata__TextAsset__GachaDefault.json
#    把 "drop_rates": [80,12,6,2] 改成 [100,0,0,0]

# 4) 写配方
cat > recipe.json <<'JSON'
{
  "name": "抽卡保底",
  "edits": [
    {"bundle": "appdata", "asset": "GachaDefault",
     "type": "TextAsset", "file": "模板/appdata/appdata__TextAsset__GachaDefault.json"}
  ]
}
JSON

# 5) 打模组包
python3 tools/cli.py build-mod ~/Desktop/PocketMortys-加强版.apk \
    --recipe recipe.json -o 抽卡保底.pmmod

# 6) 写回 APK 并签名
python3 tools/cli.py pack-apk ~/Desktop/PocketMortys-加强版.apk \
    --mod 抽卡保底.pmmod --bump -o 加强版-mod.apk

# 7) 装（签名变了，先卸载旧的）
adb uninstall com.conspiracyrick.pocketmortys
adb install -g 加强版-mod.apk
```

---

## 为什么换新签名要卸载重装

工具用自己那把 keystore（`~/.pm-modkit/modkit.keystore`）签名，
和你手机上装的版本签名不同，Android 不允许覆盖安装签名不同的包。

```bash
adb uninstall com.conspiracyrick.pocketmortys
adb install -g 加强版-mod.apk
```

`-g` 是「装上就给全部权限」，省得进游戏再点一堆授权弹窗。

> 想避免卸载：把工具指向原来那把 keystore。
> 图形界面在「导出…→ APK 重打包」里有 keystore 输入框，
> 命令行加 `--keystore 路径`，或者设环境变量 `PM_MODKIT_KEYSTORE`。

---

## 两套 APK 混着用会怎样

工具**同时**打开两个 APK 也行，它会把能看到的包都合并成一个源，
同一个包有多份副本时（比如 `text` 在两边都有），左栏「编辑哪一份」可以选。

但一般没必要 —— **加强版一个文件就够了**，包还更全（那份 `text` 是 2.6 MB
的完整版，而旧 dp.apk 里那份只有 750 KB）。

---

## 「这些包不在 APK 里」是什么意思

这是最常见的一个困惑。**工具没有坏 —— 它说的通常是事实**：
你改的包**确实不在**你打开的那个 APK 里。

### 先搞清楚：包是从哪个文件来的

| 你打开的文件 | 里面有什么 | 改得到的包 |
|---|---|---|
| `dp.apk` | **只有 `text` 一个** | 只有 `text` |
| `口蘑数据包.zip` | 155 个包（下载缓存） | 全部，但**都不在 APK 里** |
| `dp.apk` + `数据包.zip` | 两者合并 | 全部；`text` 在 APK 里，其余在缓存里 |
| **`加强版.apk`** | **全部 155 个** | 全部，**都在 APK 里** ✅ |

最典型的坑：**打开 `dp.apk` + 数据包，改了 `spdata`**。
`spdata` 来自数据包（下载缓存），`dp.apk` 里根本没有它 ——
所以「打不进 APK」是对的。

工具现在会把这些话说明白：

```
⚠ 这 1 个包不在你打开的 APK 里，打了也没用：appdata
· 你打开的 APK 里没有这 1 个包（appdata）。它们的位置：appdata 在下载缓存
· 三条出路：
  ① 换「加强版」APK（PocketMortys-加强版.apk，自带全部 155 个包，改完直接重打包就行）
  ② 走 UnityCache 路线（adb push 到设备，不用重装 APK）
  ③ 只改 APK 里有的包（你这份 APK 里只有 text）
▶ 每个改动包到底在哪        ← 展开能逐个看
```

### 怎么选

- **想重打包一个「装完即 mod 版」的整包** → 换用**加强版 APK**，一个文件搞定
- **就是想改数据包里的包，又不想重装** → 走 **UnityCache 路线**，那才是它的正道
- **只想改 `text`** → 用现在的 `dp.apk` 就行

> UnityCache 路线不需要 APK，也不需要 apksigner，是**最省事**的一条路。
> 别把它当成退而求其次 —— 它本来就比重打包 APK 更适合日常迭代。

### 另一种情况：模组应用后「没有任何改动」

如果你在命令行看到：

```
应用模组：成功 0 处，失败 1 处
⚠ appdata：当前打开的源里没有这个包（这个源只有：text…）
```

意思一样 —— **模组改的包不在你打开的源里**。
把含它的文件也传进来即可：

```bash
# 加上数据包
python3 tools/cli.py pack-apk dp.apk 口蘑数据包(1).zip --mod x.pmmod -o out.apk
# 或者直接用加强版
python3 tools/cli.py pack-apk PocketMortys-加强版.apk --mod x.pmmod -o out.apk
```

---

## 常见问题

**Q：重打包后装上去，进游戏没变化？**
八成是旧缓存还在。加 `--bump` 重新打，或者先「清除应用数据」再装。

**Q：装的时候报 `INSTALL_FAILED_UPDATE_INCOMPATIBLE`？**
签名不同。`adb uninstall com.conspiracyrick.pocketmortys` 之后再装。

**Q：`--bump` 会不会让游戏去服务器重新下载资源？**
有可能 —— 如果服务器上的清单版本和 APK 里的对不上，游戏可能认为需要更新。
私服的话把服务器那份 manifest 的 version 同步一下就行；
不想担这个风险就用「清除应用数据」的办法。

**Q：能只改 `text` 不重打整个 APK 吗？**
可以。改完走「UnityCache 产物」路线，`adb push` 一下就行，不用重装。

**Q：重打包 316 MB 会不会很慢？**
实测约 28 秒（解压后 1.37 GB，4365 个条目）。打个包喝口水的事。
