"""编辑会话：把「源 + 改动 + 产物」串起来，GUI 与 CLI 都驱动它。

产物有四种，对应四种落地方式：

==============  ==========================================  ==========================
产物            写给谁                                       设备上要做什么
==============  ==========================================  ==========================
UnityCache 补丁  设备 ``files/UnityCache/Shared/``            adb push，**不用重装 APK**
CDN 目录        自建服务器 ``cdn/AssetBundles/<group>/``     丢进服务器目录
APK             重打包 + 签名                                重新安装
.pmmod          分享给别人                                   对方用本工具应用
==============  ==========================================  ==========================
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import apkbuild, assetops, bundle as bundle_mod, modpack, paths
from .bundle import Bundle
from .source import BundleRef, Source


@dataclass
class Session:
    """一次 mod 制作过程。"""

    source: Source | None = None
    #: 已加载的包：name -> Bundle
    bundles: dict[str, Bundle] = field(default_factory=dict)
    #: 用户手动指定的容器：name -> BundleRef
    chosen: dict[str, BundleRef] = field(default_factory=dict)
    #: 要**新增**进 APK 的条目（不只是替换）——
    #: 「完全新增角色」会造一个新的 .assetbundle 塞进来
    extra_apk_entries: dict[str, bytes] = field(default_factory=dict)

    def add_apk_entry(self, path: str, data: bytes) -> None:
        """登记一个要新增/覆盖进 APK 的条目。"""
        self.extra_apk_entries[path] = data

    def apk_additions(self) -> dict[str, bytes]:
        """哪些条目是**新增**的（用来跟「替换」区分开，报告里好写）。"""
        return dict(self.extra_apk_entries)
    log: list[str] = field(default_factory=list)

    # ------------------------------------------------------------ 日志
    def say(self, msg: str) -> None:
        self.log.append(msg)
        if len(self.log) > 500:
            del self.log[:100]

    # ------------------------------------------------------------ 打开
    def open(self, *paths_in, label: str | None = None) -> Source:
        if self.source is not None:
            self.source.close()
        self.source = Source.open(*paths_in, label=label)
        self.bundles.clear()
        self.chosen.clear()
        self.extra_apk_entries.clear()
        n = len(self.source)
        self.say(f"已打开 {self.source.label}：{n} 个资源包")
        for warn in self._sanity_warnings():
            self.say(warn)
        return self.source

    def _sanity_warnings(self) -> list[str]:
        assert self.source is not None
        out = []
        if "text" not in self.source.refs and "spdata" not in self.source.refs:
            out.append("提示：这个源里没有 text/spdata，可能不是游戏数据包")

        if self.source.is_self_contained:
            n = len(self.source.pmseed)
            out.append(
                f"这个 APK **自带全部 {len(self.source)} 个资源包**"
                + (f"（含播种表 pmseed/index.txt，{n} 条）" if n else "")
                + "，不用再另外打开数据包了"
            )
            out.append(
                "两条路都通：① 重打包 APK（重装即生效，会带上全部改动）"
                " ② 出 UnityCache 产物（adb push，不用重装）"
            )
        elif not any(r.layout == "cache" for lst in self.source.refs.values() for r in lst):
            out.append("提示：没发现 UnityCache（下载缓存）。改完只能走 APK / CDN 路线")
        return out

    # ------------------------------------------------------------ 追加 / 移除源
    def add_source(self, *paths_in) -> list[str]:
        """往当前源里追加文件，而不是替换掉它。

        这样一次只选一个文件也没关系 —— 可以分几次把 apk、数据包、解包目录
        一个个加进来。已加载的包和已有的改动都不受影响。
        """
        if self.source is None:
            self.open(*paths_in)
            return [c.label for c in self.source.containers]
        before = len(self.source)
        try:
            added = self.source.add(*paths_in)
        except ValueError as exc:
            self.say(f"✗ 追加失败：{exc}")
            return []
        if not added:
            self.say("这些文件已经在源里了，跳过")
            return []
        self.say(
            f"追加源：{'，'.join(added)} —— 包数 {before} → {len(self.source)}"
        )
        for warn in self._sanity_warnings():
            if warn not in self.log:
                self.say(warn)
        return added

    def remove_container(self, index: int) -> bool:
        """移除一个源容器。有未导出的改动时拒绝，避免把改动弄丢。"""
        assert self.source is not None
        if index < 0 or index >= len(self.source.containers):
            return False
        victim = str(self.source.containers[index].root)

        # 这个容器里有没有已改动的包？（按 root 判断，不看会漂移的下标）
        in_use = [
            name
            for name, b in self.modified.items()
            if b.ref.root == victim
        ]
        if in_use:
            self.say(f"✗ {'，'.join(in_use)} 还有未导出的改动，先导出或还原再移除")
            return False

        # 只丢掉属于这个容器的已加载包；**其它容器的改动必须留着**
        for name in list(self.bundles):
            if self.bundles[name].ref.root == victim:
                del self.bundles[name]
        for name in list(self.chosen):
            if self.chosen[name].root == victim:
                del self.chosen[name]

        label = self.source.remove_member(index)
        if not self.source.containers:
            self.source.close()
            self.source = None
            self.bundles.clear()
            self.chosen.clear()
            self.say(f"已移除 {label}，源已清空")
            return True
        self.say(f"已移除 {label}，剩余 {len(self.source)} 个包")
        return True

    # ------------------------------------------------------------ 取包
    def ref_for(self, name: str) -> BundleRef | None:
        if self.source is None:
            return None
        if name in self.chosen:
            return self.chosen[name]
        return self.source.primary(name)

    def refs_for(self, name: str) -> list[BundleRef]:
        if self.source is None:
            return []
        return self.source.find(name)

    def choose(self, name: str, ref: BundleRef) -> None:
        """同一逻辑包有多个副本时，指定改哪一个。"""
        self.chosen[name] = ref
        self.bundles.pop(name, None)
        self.say(f"{name}：改用 {ref.layout_label} 里的那份")

    def bundle(self, name: str, *, eager: bool = False) -> Bundle | None:
        if self.source is None:
            return None
        if name in self.bundles:
            return self.bundles[name]
        ref = self.ref_for(name)
        if ref is None:
            return None
        try:
            raw = self.source.read(ref)
        except Exception as exc:  # noqa: BLE001
            self.say(f"读取 {name} 失败：{exc}")
            return None
        b = bundle_mod.load(ref, raw, eager=eager)
        self.bundles[name] = b
        if eager and not b.ok:
            self.say(f"⚠ {name} 解析失败：{b.error}")
        return b

    # ------------------------------------------------------------ 改动
    @property
    def modified(self) -> dict[str, Bundle]:
        return {k: b for k, b in self.bundles.items() if b.prebuilt or b.dirty}

    def modified_assets(self) -> list[tuple[str, int]]:
        return [(name, pid) for name, b in self.modified.items() for pid in sorted(b.dirty)]

    def reset(self, name: str) -> None:
        """丢弃某个包的全部改动。"""
        if name in self.bundles:
            b = self.bundles[name]
            if b.ref is not None and self.source is not None:
                b.raw = self.source.read(b.ref)
            self.bundles.pop(name, None)
            self.say(f"已还原 {name}")

    def reset_all(self) -> None:
        for name in list(self.modified):
            self.reset(name)

    # ------------------------------------------------------------ 出厂体检
    def check(self) -> "object":
        """跑一遍数据表体检，返回 ``validate.Report``。"""
        from . import validate as validate_mod

        return validate_mod.validate(self)

    def guard(self, *, allow_broken: bool = False, what: str = "产物") -> "object":
        """出产物之前的守门人。

        主键重复这类问题会让游戏**直接起不来**，而且从 JSON 上肉眼很难看出来
        （复制一条现成的来改，只改了键没改里面的 id，表面看完全正常）。
        所以这里硬拦一道：有致命问题就不让出，除非明确说 ``allow_broken``。
        """
        rep = self.check()
        if rep.issues:
            self.say(rep.summary())
            for i in rep.issues[:20]:
                self.say("  " + i.line())
            if len(rep.issues) > 20:
                self.say(f"  …还有 {len(rep.issues) - 20} 条")
        if rep.errors and not allow_broken:
            raise RuntimeError(
                f"数据表里有 {len(rep.errors)} 个**会让游戏崩**的问题，先修再出{what}。\n"
                "  命令行： tools/cli.py check <源>      看详情\n"
                "          tools/cli.py fix   <源>      自动修\n"
                "  图形界面：工具栏「体检」按钮。\n"
                "  确实想强行导出，加 allow_broken=True（命令行 --allow-broken）。"
            )
        return rep

    # ------------------------------------------------------------ 产物 1：UnityCache
    def cache_dirname(self, name: str) -> str | None:
        """这个包在 UnityCache 里该用哪个目录名。

        优先用 APK 里的 ``assets/pmseed/index.txt``（自包含 APK 的播种表），
        那是游戏真正认的名字；没有就按 manifest 的 version 推算。
        """
        if self.source is None:
            return None
        return self.source.cache_dirname(name)

    def bundle_version(self, name: str) -> int | None:
        assert self.source is not None
        ref = self.ref_for(name)
        if ref is not None and ref.version is not None:
            return ref.version
        return self.source.version_of(name)

    def output_cache(
        self,
        out_dir: str | Path,
        *,
        only_modified: bool = True,
        allow_broken: bool = False,
        progress=None,
    ) -> dict:
        """生成可直接 adb push 的 UnityCache 目录。

        ``only_modified=False`` 时连未改动的包一起复制（换设备重建缓存用）。
        """
        assert self.source is not None
        self.guard(what="UnityCache 产物") if not allow_broken else None
        out = Path(out_dir)
        root = out / "UnityCache" / "Shared"
        root.mkdir(parents=True, exist_ok=True)

        names = list(self.modified) if only_modified else self.source.bundle_names()
        written, skipped = [], []
        for i, name in enumerate(names):
            if progress:
                progress(i + 1, len(names), name)
            dirname = self.cache_dirname(name)
            if dirname is None:
                skipped.append(f"{name}（拿不到 version，无法确定目录名）")
                continue
            dest = root / name / dirname
            dest.mkdir(parents=True, exist_ok=True)

            if name in self.modified:
                data = self.modified[name].save()
            else:
                ref = self.ref_for(name)
                if ref is None:
                    skipped.append(name)
                    continue
                data = self.source.read(ref)
            (dest / "__data").write_bytes(data)
            (dest / "__info").write_bytes(paths.make_bundle_info())
            written.append(name)

        (root / "__info").write_bytes(paths.make_shared_info())
        self._write_cache_readme(out, written)
        self.say(f"UnityCache 产物：{len(written)} 个包 → {root}")
        if skipped:
            self.say("⚠ 跳过：" + "，".join(skipped))
        return {"dir": str(root), "written": written, "skipped": skipped}

    def _write_cache_readme(self, out: Path, written: list[str]) -> None:
        base = paths.DEVICE_FILES_DIR
        pkg = paths.DEFAULT_PACKAGE
        lines = [
            "# 推送到设备（不需要重装 APK）",
            "",
            "```bash",
            f"BASE={base}",
            f"adb push UnityCache {base}/",
            f'adb shell "chown -R 10289:10289 {base}"',
            f'adb shell "chmod -R 777 {base}"',
            "```",
            "",
            "如果 `chown` 报错，先 `adb root`。",
            "",
            "**注意**：这份产物只含被改动的包。设备上原本的缓存要保留，",
            "直接把 `UnityCache/` 合并覆盖上去即可（同名目录会被替换）。",
            "",
            "## 本次内容",
            "",
        ]
        for n in written:
            lines.append(f"- `{n}`")
        lines += ["", f"包名：`{pkg}`", ""]
        (out / "推送说明.md").write_text("\n".join(lines), encoding="utf-8")
        (out / "push.sh").write_text(
            "#!/bin/sh\n"
            "# 把改好的资源推到设备（需要 adb 与 root）\n"
            "set -e\n"
            f'BASE="{base}"\n'
            f'cd "$(dirname "$0")"\n'
            "adb root || true\n"
            'adb push UnityCache "$BASE/"\n'
            f'adb shell "chown -R 10289:10289 {base}" || true\n'
            f'adb shell "chmod -R 777 {base}" || true\n'
            'echo "完成，启动游戏验证。"\n',
            encoding="utf-8",
        )
        (out / "push.sh").chmod(0o755)

    # ------------------------------------------------------------ 产物 2：CDN
    def output_cdn(
        self,
        out_dir: str | Path,
        *,
        group: str = "AssetBundle-android:1011-ios:1011__7b175320",
        platform: str = "Android",
        update_manifest: bool = True,
    ) -> dict:
        """生成自建服务器用的 CDN 目录，并顺手更新 manifest 里的 version。"""
        assert self.source is not None
        out = Path(out_dir)
        target = out / "AssetBundles" / group / platform
        target.mkdir(parents=True, exist_ok=True)

        written = []
        mani: dict = {}
        mani_path = out / "Aliases" / "rat" / platform / "manifest.json"
        if update_manifest:
            for cand in [
                Path("cdn/Aliases/rat") / platform / "manifest.json",
                out / "manifest.json",
            ]:
                if cand.is_file():
                    try:
                        mani = json.loads(cand.read_text())
                        break
                    except Exception:  # noqa: BLE001
                        pass

        for name, b in self.modified.items():
            (target / f"{name}.assetbundle").write_bytes(b.save())
            written.append(name)
            if update_manifest and name in mani and isinstance(mani[name], dict):
                # 内容变了，把 version 抬 1，客户端才会认为是新包
                try:
                    mani[name]["version"] = str(int(mani[name].get("version", 1)) + 1)
                except (TypeError, ValueError):
                    pass

        if update_manifest and mani:
            mani_path.parent.mkdir(parents=True, exist_ok=True)
            mani_path.write_text(json.dumps(mani, indent=1, ensure_ascii=False), encoding="utf-8")

        self.say(f"CDN 产物：{len(written)} 个包 → {target}")
        return {"dir": str(target), "written": written, "manifest": str(mani_path) if mani else None}

    # ------------------------------------------------------------ 产物 3：APK
    def _same_ref(self, a: BundleRef | None, b: BundleRef) -> bool:
        # 用 root 而不是 member：增删源之后 member 会漂移
        return a is not None and a.same_as(b)

    def _replicate_edits(self, name: str, src: Bundle, ref: BundleRef) -> bytes | None:
        """把 ``src`` 上的改动重放到同一逻辑包的另一份副本 ``ref`` 上。

        为什么不能直接复制字节：同一个包的不同平台副本内容是不一样的
        （``text`` 的 Android 版 750 KB、.iOS 版 1045 KB，是两个构建）。
        把 Android 的字节塞进 .iOS 槽位会毁掉那个包。
        正确做法是按**资源身份**（type + name，退化时用 path_id）在目标副本上
        重新应用同样的改动。
        """
        assert self.source is not None
        snap = src.pending_snapshot()
        if not snap:
            # 成品包（来自 .pmmod）没法重放，只好不碰其它副本
            return None
        try:
            other = bundle_mod.load(ref, self.source.read(ref))
            other.assets
        except Exception as exc:  # noqa: BLE001
            self.say(f"⚠ {name} 的 {ref.layout_label}·{ref.platform} 副本打不开：{exc}")
            return None

        src_index = {a.path_id: a for a in src.assets}
        missed = 0
        for pid, (kind, val) in snap.items():
            se = src_index.get(pid)
            target = None
            if se is not None:
                target = next(
                    (a for a in other.assets if a.type == se.type and a.name == se.name), None
                )
            if target is None:
                target = next((a for a in other.assets if a.path_id == pid), None)
            if target is None:
                missed += 1
                continue
            if kind == "text":
                other.modify_text(target, val)
            elif kind == "image":
                other.modify_image(target, val)
            elif kind == "tree":
                other.modify_typetree(target, val)
        if missed:
            self.say(f"⚠ {name}·{ref.platform}：有 {missed} 处改动在副本里找不到对应资源")
        return other.save() if other.dirty else None

    def apk_replacements(self) -> dict[str, bytes]:
        """哪些改动能打进 APK，以及打进去的字节。

        同一个逻辑包在 APK 里可能有**多份**（``text`` 就同时有 ``Android``
        和 ``.iOS``）。被编辑的那一份直接回写；其它副本按资源身份重放同样
        的改动，而不是粗暴地复制字节。
        """
        out: dict[str, bytes] = {}
        for name, b in self.modified.items():
            refs = [r for r in self.refs_for(name) if r.layout == "apk"]
            if not refs:
                continue
            primary = self.ref_for(name)
            for r in refs:
                if self._same_ref(primary, r):
                    out[r.container] = b.raw if b.prebuilt else b.save()
                else:
                    data = self._replicate_edits(name, b, r)
                    if data:
                        out[r.container] = data
        return out

    def apk_diagnosis(self) -> dict:
        """说清楚「为什么有些改动打不进 APK」，以及该怎么办。

        只回一句「这些包不在 APK 里，打了也没用」太糊弄人了 —— 用户会以为
        工具坏了。实际情况通常是：**打开的是 dp.apk，而它里面只有 text 一个包**，
        改的 appdata/spdata 来自数据包，本来就不在那个 APK 里。

        返回 ``{has_apk, apk_labels, in_apk, not_in_apk, where, self_contained, advice}``。
        """
        refs_all = list(getattr(self.source, "refs", {}).values()) if self.source else []
        apk_refs = [r for lst in refs_all for r in lst if r.layout == "apk"]
        apk_labels = sorted({f"{r.platform or '?'}" for r in apk_refs})
        self_contained = bool(self.source and self.source.is_self_contained)

        in_apk: list[str] = []
        not_in_apk: list[str] = []
        where: dict[str, list[str]] = {}
        for name in self.modified:
            refs = self.refs_for(name)
            where[name] = [r.layout_label for r in refs] or ["（找不到）"]
            if any(r.layout == "apk" for r in refs):
                in_apk.append(name)
            else:
                not_in_apk.append(name)

        advice: list[str] = []
        if not apk_refs:
            advice.append(
                "你还没有打开任何 APK。"
                "点上面「追加源…」把 APK 加进来，"
                "或者直接用「UnityCache」那个标签页 —— 那条路不需要 APK。"
            )
        elif not_in_apk:
            n = len(not_in_apk)
            advice.append(
                f"你打开的 APK 里**没有**这 {n} 个包"
                f"（{'、'.join(not_in_apk[:6])}{'…' if n > 6 else ''}）。"
                f"它们的位置："
                + "；".join(f"{k} 在{'/'.join(v)}" for k, v in list(where.items()) if k in not_in_apk)
            )
            advice.append(
                "三条出路："
                "① **换「加强版」APK**（PocketMortys-加强版.apk，自带全部 155 个包，"
                "改完直接重打包就行）"
                "② 走 **UnityCache 路线**（adb push 到设备，不用重装 APK，"
                "这条路对缓存里的包本来就是对的）"
                "③ 只改 APK 里有的包（你这份 APK 里只有 "
                + ("、".join(sorted({n for n in self.source.bundle_names()
                                    if any(r.layout == "apk" for r in self.refs_for(n))})[:4])
                   or "无")
                + "）"
            )
        if self_contained and not not_in_apk and self.modified:
            advice.append(
                f"这个 APK 自带全部资源，{len(self.modified)} 个改动包都会被打进去。"
            )
        return {
            "has_apk": bool(apk_refs),
            "apk_labels": apk_labels,
            "in_apk": in_apk,
            "not_in_apk": not_in_apk,
            "where": where,
            "self_contained": self_contained,
            "advice": advice,
        }

    def apk_unreachable(self) -> list[str]:
        """改了但打不进 APK 的包（它们不在 APK 里）。"""
        out = []
        for name in self.modified:
            if not any(r.layout == "apk" for r in self.refs_for(name)):
                out.append(name)
        return out

    def _bump_manifest_and_seed(self) -> dict[str, bytes]:
        """把被改动包的 version +1，并同步 pmseed 的目录名。

        **为什么要这么做**：游戏把资源播种到
        ``files/UnityCache/Shared/<包名>/<目录名>/``，而**目录名是从 manifest 的
        version 算出来的**。装 modded APK 到一台已经跑过游戏的设备上时，如果
        version 没变，目录名就没变，游戏会认为缓存还是新鲜的，**不会重新播种** ——
        改动就不生效。

        把 version 抬 1 → 目录名变 → 缓存未命中 → 游戏从 APK 重新播种。

        只动两处**纯文本**的地方，风险可控：

        * ``assets/AssetBundles/<平台>/manifest.json``
        * ``assets/pmseed/index.txt``

        **不动** ``assets/AssetBundle.dat`` —— 那是 .NET BinaryFormatter 流，
        改里面的长度前缀容易把文件写坏，而它存的只是「上次加载过的 manifest」，
        与 manifest.json 不一致反而正是我们要的「缓存过期」信号。

        顶层 manifest 的 ``version``（如 1011）也**不动**：那是和服务器对齐的
        清单版本，动了可能触发整包重新下载。
        """
        assert self.source is not None
        out: dict[str, bytes] = {}
        names = list(self.modified)
        if not names:
            return out

        # ---- manifest.json（每个平台目录一份）
        for c in self.source.containers:
            for path in c.names():
                if not path.endswith("manifest.json") or "AssetBundles/" not in path:
                    continue
                try:
                    mani = json.loads(c.read(path))
                except Exception:  # noqa: BLE001
                    continue
                hit = 0
                for n in names:
                    meta = mani.get(n)
                    if not isinstance(meta, dict):
                        continue
                    try:
                        meta["version"] = str(int(meta.get("version", 1)) + 1)
                        hit += 1
                    except (TypeError, ValueError):
                        pass
                if hit:
                    out[path] = json.dumps(mani, ensure_ascii=False).encode("utf-8")
                    self.say(f"manifest 版本 +1：{path}（{hit} 个包）")

        # ---- pmseed/index.txt
        for c in self.source.containers:
            for path in c.names():
                if not path.endswith("pmseed/index.txt"):
                    continue
                try:
                    text = c.read(path).decode("utf-8", errors="replace")
                except Exception:  # noqa: BLE001
                    continue
                lines = []
                changed = 0
                for line in text.splitlines():
                    parts = line.split()
                    if len(parts) >= 2 and parts[0] in self.modified:
                        # 目录名 = 12 个零字节 + **小端 4 字节** version 的 hex。
                        # 注意别当大端整数解 —— 那会算出个天文数字（踩过）。
                        v = paths.parse_cache_dirname(parts[1])
                        if v is not None:
                            parts[1] = paths.make_cache_dirname(v + 1)
                            changed += 1
                        lines.append(" ".join(parts))
                    else:
                        lines.append(line)
                if changed:
                    out[path] = ("\n".join(lines) + "\n").encode("utf-8")
                    self.say(f"播种表目录名已更新：{path}（{changed} 条）")
        return out

    def output_apk(
        self,
        src_apk: str | Path,
        out_apk: str | Path,
        *,
        keystore: str | Path | None = None,
        do_sign: bool = True,
        bump_versions: bool = False,
        allow_broken: bool = False,
        progress=None,
    ) -> dict:
        self.guard(what="APK", allow_broken=allow_broken)
        reps = self.apk_replacements()
        # extra_apk_entries 里可能两种都有：
        #   · 真·新增（新造的 .assetbundle）→ 以 STORED 追加
        #   · 改写既有条目（manifest.json / pmseed/index.txt）→ 当替换
        # 不去猜，全丢给 repack —— 它会按「原 APK 里有没有这个名字」自动分流。
        adds = dict(self.extra_apk_entries)
        if bump_versions:
            try:
                reps.update(self._bump_manifest_and_seed())
            except Exception as exc:  # noqa: BLE001
                self.say(f"⚠ 版本 +1 失败，按原版本打包：{exc}")
        if not reps:
            self.say("⚠ 没有任何改动能打进 APK（改的包不在 APK 里）")
        report = apkbuild.build_apk(
            src_apk, out_apk, reps, new_entries=adds,
            keystore=keystore, do_sign=do_sign, progress=progress,
        )
        for line in report.get("steps", []):
            self.say(line)
        unreachable = self.apk_unreachable()
        if unreachable:
            self.say(
                "⚠ 这些包不在 APK 内，需要走 UnityCache 路线："
                + "，".join(unreachable)
            )
        return report

    # ------------------------------------------------------------ 产物 4：.pmmod
    def output_modpack(
        self,
        path: str | Path,
        *,
        name: str,
        author: str = "",
        description: str = "",
        prebuilt: bool = False,
        allow_broken: bool = False,
    ) -> Path:
        """打一个模组包。``prebuilt=True`` 连回写好的 bundle 一起塞进去。"""
        self.guard(what="模组包", allow_broken=allow_broken)
        pack = modpack.ModPack(name=name, author=author, description=description)
        payloads: dict[tuple[str, int], bytes] = {}

        for bname, b in self.modified.items():
            for pid in sorted(b.dirty):
                entry = next((a for a in b.assets if a.path_id == pid), None)
                if entry is None:
                    continue
                try:
                    payload = _entry_payload(b, entry)
                except Exception as exc:  # noqa: BLE001
                    self.say(f"⚠ 导出 {bname}#{pid} 失败：{exc}")
                    continue
                pack.edits.append(modpack.make_edit(b, entry, payload))
                payloads[(bname, pid)] = payload
            if prebuilt:
                try:
                    pack.bundles[bname] = b.save()
                except Exception as exc:  # noqa: BLE001
                    self.say(f"⚠ 回写 {bname} 失败：{exc}")

        out = pack.save(path, payloads)
        self.say(f"模组包已写出：{out}（{pack.summary()}）")
        return out

    # ------------------------------------------------------------ 产物 5：散装 bundle
    def export_bundles(self, out_dir: str | Path) -> dict:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        written = []
        for name, b in self.modified.items():
            p = out / f"{name}.assetbundle"
            p.write_bytes(b.save())
            written.append(str(p))
        self.say(f"已导出 {len(written)} 个 bundle → {out}")
        return {"dir": str(out), "files": written}

    # ------------------------------------------------------------ 应用模组包
    def apply_modpack(self, path: str | Path) -> dict:
        """把 .pmmod 打到当前源上。"""
        assert self.source is not None
        pack = modpack.ModPack.load(path)
        self.say(f"应用模组：{pack.name}（{pack.summary()}）")

        # 成品级模组直接可用
        for name, data in pack.bundles.items():
            ref = self.ref_for(name)
            if ref is None:
                self.say(f"⚠ 源里没有 {name}，跳过成品包")
                continue
            b = bundle_mod.load(ref, data)
            b.prebuilt = True  # 成品字节，直接用
            self.bundles[name] = b
        if pack.bundles:
            self.say(f"载入成品包 {len(pack.bundles)} 个")

        applied, failed = 0, []
        if pack.edits:
            payloads = pack.asset_bytes(path)
            for e in pack.edits:
                b = self.bundle(e.bundle, eager=True)
                if b is None:
                    # 关键信息：是「当前源里没有这个包」，不是模组坏了
                    have = ", ".join(self.source.bundle_names()[:8]) if self.source else ""
                    failed.append(
                        f"{e.bundle}：**当前打开的源里没有这个包**"
                        + (f"（这个源只有：{have}…）" if have else "")
                        + " —— 用「追加源…」把含它的文件（数据包 zip / 加强版 APK）加进来"
                    )
                    continue
                if not b.ok:
                    failed.append(f"{e.bundle}：这个包解析失败（{b.error}）")
                    continue
                # 先按 path_id 找，不行再按 type+name 找（跨副本时 path_id 会变）
                entry = next((a for a in b.assets if a.path_id == e.path_id), None)
                if entry is None:
                    entry = next(
                        (a for a in b.assets if a.type == e.type and a.name == e.name), None
                    )
                if entry is None:
                    failed.append(f"{e.bundle}：找不到资源 {e.type} {e.name or e.path_id}")
                    continue
                payload = payloads.get(e.key())
                if payload is None:
                    failed.append(f"{e.bundle}/{e.name}：模组里缺这个文件")
                    continue
                try:
                    modpack.apply_payload(b, entry, payload)
                    applied += 1
                except Exception as exc:  # noqa: BLE001
                    failed.append(f"{e.bundle}/{e.name}：{exc}")

        self.say(f"源码级替换成功 {applied} 处")
        for f in failed:
            self.say(f"⚠ {f}")
        return {"applied": applied, "failed": failed}


# ---------------------------------------------------------------- helpers


def _entry_payload(b: Bundle, entry) -> bytes:
    """把一个资源对象序列化成「喂给 import_asset 的那种文件」。"""
    import io

    kind = assetops.asset_kind(entry)
    if kind == assetops.KIND_TEXT:
        return b.preview_text(entry).encode("utf-8")
    if kind == assetops.KIND_IMAGE:
        img = b.preview_image(entry)
        if img is None:
            raise ValueError("解不出图像")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    if kind == assetops.KIND_TREE:
        tree = b.read_typetree(entry)
        if tree is None:
            raise ValueError("读不出类型树")
        return json.dumps(tree, ensure_ascii=False).encode("utf-8")
    raise ValueError(f"{entry.type} 不支持打包")
