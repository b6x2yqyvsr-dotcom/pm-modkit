"""完全新增一个角色：连同**它自己的美术**一起造出来。

和 `entries.add_entry` 的区别
-----------------------------
`add_entry` 是「克隆一条数据 + 借用别人的图」；这里做的是**真·新角色**：
美术也复制一份、改成自己的名字，从此这只莫蒂有独立的 15 张图，想怎么画怎么画。

游戏怎么找一只莫蒂的图
----------------------
先把 ``MortyInfo.assetid`` 拿去查 ``appdata/BundleAssetAssignment``，得到它住在
哪个包；然后按**固定路径**从那个包里取图：

    assets/resourceassetbundles/<包名>/morties/<assetid 小写>/<assetid 小写><动作>.png

``<动作>`` 一共 15 个，实测 564 只莫蒂全都一样：

    Back  Front  Icon
    Down_1 Down_2 Down_3 Down_4
    Side_1 Side_2 Side_3 Side_4
    Up_1   Up_2   Up_3   Up_4

每个动作在包里对应**一对同名对象**：一个 ``Sprite`` + 一个 ``Texture2D``
（1:1，不是共享图集 —— 所以换图不会影响别的角色）。

做法
----
1. 复制模板莫蒂**所在的整个包**
2. 把 ``<模板assetid><动作>`` 这 30 个对象改名为 ``<新assetid><动作>``
3. 重写包内 ``m_Container`` 的资源路径
4. 用你给的图替换对应帧（没给的沿用模板）
5. 存成**新包** ``<新包名>.assetbundle``
6. 在 APK 里登记这个新包：加文件 + 写 ``manifest.json`` + 写 ``pmseed/index.txt``
7. ``BundleAssetAssignment`` 里登记新的 assetid → 新包
8. 最后照常写莫蒂的 5 张表

这样原角色**一点没动**，新角色是完全独立的一份。
"""

from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import entries as entries_mod
from .session import Session

#: 一个莫蒂的 15 个动作帧（实测全表 564 只完全一致）
FRAMES = [
    "Back", "Front", "Icon",
    "Down_1", "Down_2", "Down_3", "Down_4",
    "Side_1", "Side_2", "Side_3", "Side_4",
    "Up_1", "Up_2", "Up_3", "Up_4",
]

#: 道具只有两帧（实测 44 个道具全是 Icon + Front）
ITEM_FRAMES = ["Icon", "Front"]

#: 按类型给帧集合，以及路径里的分类段
FRAME_SETS = {"morty": FRAMES, "item": ITEM_FRAMES}
CATEGORY = {"morty": "morties", "item": "items"}

#: 道具两帧的中文名
ITEM_FRAME_LABEL = {"Icon": "图标", "Front": "正面"}


def frames_for(kind: str) -> list[str]:
    return FRAME_SETS.get(kind, FRAMES)


def category_for(kind: str) -> str:
    return CATEGORY.get(kind, "morties")

#: 帧的中文说明，界面上给用户看
FRAME_LABEL = {
    "Back": "背面", "Front": "正面", "Icon": "图鉴图标",
    "Down_1": "朝下·第1帧", "Down_2": "朝下·第2帧",
    "Down_3": "朝下·第3帧", "Down_4": "朝下·第4帧",
    "Side_1": "朝侧·第1帧", "Side_2": "朝侧·第2帧",
    "Side_3": "朝侧·第3帧", "Side_4": "朝侧·第4帧",
    "Up_1": "朝上·第1帧", "Up_2": "朝上·第2帧",
    "Up_3": "朝上·第3帧", "Up_4": "朝上·第4帧",
}

_CONTAINER_RE = re.compile(
    r"^assets/resourceassetbundles/(?P<bundle>[^/]+)/(?P<cat>morties|items)"
    r"/(?P<folder>[^/]+)/(?P<file>[^/]+)$",
    re.IGNORECASE,
)


@dataclass
class ArtPlan:
    """新条目的美术从哪来、到哪去。"""

    donor_asset: str
    donor_bundle: str
    new_asset: str
    new_bundle: str
    kind: str = "morty"
    frames: list[str] = field(default_factory=list)
    #: 模板包里还有哪些别的角色（会被一起复制过去，只是不引用）
    other_morties: list[str] = field(default_factory=list)

    def describe(self) -> str:
        return (
            f"美术：{self.donor_bundle} 里的 {self.donor_asset}"
            f"（{len(self.frames)} 帧） → 新包 {self.new_bundle} 里的 {self.new_asset}"
        )


# ---------------------------------------------------------------- 规划


def _baa(sess: Session) -> dict:
    b = sess.bundle("appdata", eager=True)
    e = next((a for a in b.assets if a.name == "BundleAssetAssignment"), None)
    if e is None:
        raise ValueError("appdata 里没有 BundleAssetAssignment")
    return json.loads(b.preview_text(e))


def plan_art(sess: Session, clone_from: str, new_asset: str,
             new_bundle: str | None = None, kind: str = "morty") -> ArtPlan:
    """根据模板条目，算出美术怎么迁。"""
    sp = entries_mod.entry_of(sess, kind, clone_from)
    if not isinstance(sp, dict):
        raise ValueError(f"找不到模板条目 {clone_from}")
    _assert_kind(kind)
    donor_asset = sp.get("assetid") or clone_from
    baa = _baa(sess)
    meta = baa.get(donor_asset)
    if not isinstance(meta, dict) or not meta.get("version"):
        raise ValueError(
            f"模板的 assetid {donor_asset!r} 在 BundleAssetAssignment 里没有对应的包，"
            "没法定位它的美术"
        )
    donor_bundle = str(meta["version"])
    if not new_bundle:
        # 包名只要不和现有的撞就行；用固定前缀便于识别
        new_bundle = "pm_" + re.sub(r"[^a-z0-9]", "", new_asset.lower())[:24]
    return ArtPlan(
        donor_asset=donor_asset,
        donor_bundle=donor_bundle,
        new_asset=new_asset,
        new_bundle=new_bundle,
        kind=kind,
        frames=list(frames_for(kind)),
    )


def _assert_kind(kind: str) -> None:
    if kind not in FRAME_SETS:
        raise ValueError(
            f"{kind!r} 没有独立美术的概念（目前只支持：{'、'.join(FRAME_SETS)}）")


def inspect_donor(sess: Session, clone_from: str, kind: str = "morty") -> dict:
    """看看模板的美术长什么样（给界面预览用）。"""
    _assert_kind(kind)
    plan = plan_art(sess, clone_from, "ProbeAsset", kind=kind)
    src = _read_bundle(sess, plan.donor_bundle)
    import UnityPy

    env = UnityPy.load(io.BytesIO(src))
    frames: dict[str, dict] = {}
    others: set[str] = set()
    for o in env.objects:
        if o.type.name != "Texture2D":
            continue
        d = o.read()
        nm = getattr(d, "m_Name", "") or ""
        if nm.startswith(plan.donor_asset):
            suf = nm[len(plan.donor_asset):]
            frames[suf] = {"name": nm, "w": d.m_Width, "h": d.m_Height,
                           "format": int(d.m_TextureFormat)}
        elif nm:
            m = re.match(r"^(Morty[A-Za-z0-9_]*?)(Back|Front|Icon|Down_\d|Side_\d|Up_\d)$", nm)
            if m:
                others.add(m.group(1))
    return {
        "kind": kind,
        "donor_asset": plan.donor_asset,
        "donor_bundle": plan.donor_bundle,
        "frames": frames,
        "other_morties": sorted(others),
        "missing": [f for f in frames_for(kind) if f not in frames],
    }


# ---------------------------------------------------------------- 读包


def _read_bundle(sess: Session, name: str) -> bytes:
    ref = sess.ref_for(name)
    if ref is None:
        raise ValueError(f"源里没有资源包 {name}")
    return sess.source.read(ref)


# ---------------------------------------------------------------- 造美术


def prune_supported(plan: ArtPlan) -> bool:
    """现在一律会裁剪（``build_art(prune=True)``），所以共享包不再是问题。"""
    return False


def build_art(sess: Session, plan: ArtPlan, images: dict[str, str | Path] | None = None,
              *, prune: bool = True, progress=None) -> dict:
    """复制 + 改名 + 换图 + **裁掉用不着的对象**，返回新包的字节和统计。

    为什么要裁剪：模板可能住在**共享大包**里（道具全在 31 MB 的 ``v1.0.0``，
    ``preload`` 也有 11 MB）。不裁的话，加一个道具就要复制 31 MB。
    裁到只剩这个条目的对象之后，新包通常只有几十 KB。

    裁剪是安全的：``SerializedFile.save()`` 会把保留下来的对象**重新紧凑写入**，
    我们同时把 ``m_Container`` 和 ``m_PreloadTable`` 一起重建成只剩这个条目的路径，
    不会留下悬空指针。
    """
    import UnityPy
    from PIL import Image

    images = images or {}
    src = _read_bundle(sess, plan.donor_bundle)
    env = UnityPy.load(io.BytesIO(src))
    sf = list(env.file.files.values())[0]

    old_low = plan.donor_asset.lower()
    new_low = plan.new_asset.lower()
    cat = category_for(plan.kind)

    # ---- 1) 先算出要保留哪些对象（按**原名字**判断）
    keep: set[int] = set()
    for pid, o in sf.objects.items():
        if o.type.name in ("Sprite", "Texture2D"):
            if (o.peek_name() or "").startswith(plan.donor_asset):
                keep.add(pid)

    # ---- 2) 改名 + 换图：**必须一趟做完**
    #
    # UnityPy 的坑：``obj.save()`` 之后从同一个 ObjectReader ``read()`` 拿到的还是
    # 旧值（reader 和 set_raw_data 不同步）。分两个循环做的话，第二个循环看到的是
    # 旧名字，换图会被静默跳过 —— 踩过一次。
    renamed = 0
    replaced: list[str] = []
    skipped: list[str] = []
    for pid, o in sf.objects.items():
        if pid not in keep:
            continue
        d = o.read()
        nm = getattr(d, "m_Name", "") or ""
        frame = nm[len(plan.donor_asset):]
        d.m_Name = plan.new_asset + frame
        renamed += 1
        if o.type.name == "Texture2D" and frame in images:
            path = images[frame]
            try:
                img = Image.open(path)
                img.load()
                if img.mode not in ("RGB", "RGBA"):
                    img = img.convert("RGBA")
                if img.size != (d.m_Width, d.m_Height):
                    img = img.resize((d.m_Width, d.m_Height), Image.LANCZOS)
                    skipped.append(f"{frame}（尺寸不符，已缩放到 {d.m_Width}×{d.m_Height}）")
                d.image = img
                replaced.append(frame)
            except Exception as exc:  # noqa: BLE001
                skipped.append(f"{frame}：{type(exc).__name__}: {exc}")
        d.save()
        if progress:
            progress(renamed, len(keep))

    for frame in images:
        if frame not in plan.frames:
            skipped.append(f"{frame}：模板里没有这一帧，忽略了")

    # ---- 3) 重建容器与预载表
    #
    # 路径**必须全小写**：原版就是这个约定 ——
    #   assets/resourceassetbundles/v1.3.0/items/itemcourierflap/itemcourierflapicon.png
    # 对象名是 ``ItemCourierFlapIcon``，但路径里全小写。游戏按字符串精确查，
    # 大小写不对就找不到（道具/角色隐身或直接崩）。
    container_before = container_after = 0
    for o in sf.objects.values():
        if o.type.name != "AssetBundle":
            continue
        d = o.read()
        cont = d.m_Container
        items = list(cont.items()) if hasattr(cont, "items") else list(cont)
        container_before = len(items)

        def fix(path: str) -> str | None:
            m = _CONTAINER_RE.match(str(path))
            if not m or m.group("folder").lower() != old_low:
                return None          # 不是这个条目的路径，裁掉
            fname = m.group("file").lower()
            stem = fname[:-4] if fname.endswith(".png") else fname
            frame = stem[len(old_low):] if stem.startswith(old_low) else ""
            return (f"assets/resourceassetbundles/{plan.new_bundle.lower()}"
                    f"/{cat}/{new_low}/{new_low}{frame}.png")

        rebuilt = []
        for k, v in items:
            nk = fix(k)
            if nk is not None:
                rebuilt.append((nk, v))
        d.m_Container = rebuilt
        d.m_PreloadTable = [v.asset for _, v in rebuilt]
        d.save()
        container_after = len(rebuilt)
        break

    # ---- 4) 裁掉用不着的对象
    kept_before = len(sf.objects)
    if prune:
        sf.objects = {pid: o for pid, o in sf.objects.items()
                      if pid in keep or o.type.name == "AssetBundle"}

    data = env.file.save(packer="original")
    return {
        "bytes": bytes(data),
        "renamed": renamed,
        "replaced": replaced,
        "skipped": skipped,
        "container_before": container_before,
        "container_after": container_after,
        "size": len(data),
        "src_size": len(src),
        "objects_before": kept_before,
        "objects_after": len(sf.objects),
    }


# ---------------------------------------------------------------- 登记到 APK


def register_bundle(sess: Session, plan: ArtPlan, bundle_bytes: bytes) -> list[str]:
    """把新包写进 APK：加文件 + manifest.json + pmseed/index.txt。

    这三处缺一不可 —— 少一处游戏就找不到这个包。
    """
    notes: list[str] = []

    # 1) APK 里的新文件（走 session 的「额外条目」通道）
    plat = "Android"
    apk_ref = None
    if sess.source is not None:
        for r in sess.source.refs.get(plan.donor_bundle, []):
            if r.layout == "apk":
                apk_ref = r
                break
    if apk_ref is not None and apk_ref.platform:
        plat = apk_ref.platform
    target = f"assets/AssetBundles/{plat}/{plan.new_bundle}.assetbundle"
    sess.add_apk_entry(target, bundle_bytes)
    notes.append(f"新增 APK 条目 {target}（{len(bundle_bytes):,} 字节）")

    # 2) manifest.json：加一条，version 从 1 起
    written = _patch_manifest(sess, plan, plat)
    notes += written

    # 3) pmseed/index.txt：加一行，目录名按 version 算
    notes += _patch_pmseed(sess, plan)

    # 4) BundleAssetAssignment
    b = sess.bundle("appdata", eager=True)
    e = next((a for a in b.assets if a.name == "BundleAssetAssignment"), None)
    if e is None:
        raise ValueError("appdata 里没有 BundleAssetAssignment")
    baa = json.loads(b.preview_text(e))
    baa[plan.new_asset] = {"id": plan.new_asset, "version": plan.new_bundle}
    b.modify_text(e, json.dumps(baa, ensure_ascii=False))
    notes.append(
        f"BundleAssetAssignment：{plan.new_asset} → {plan.new_bundle}"
    )
    return notes


def _patch_manifest(sess: Session, plan: ArtPlan, plat: str) -> list[str]:
    """在 APK 的各份 manifest.json 里给新包加一条。"""
    out: list[str] = []
    assert sess.source is not None
    for c in sess.source.containers:
        for name in c.names():
            if not name.endswith("manifest.json") or "AssetBundles/" not in name:
                continue
            try:
                mani = json.loads(c.read(name))
            except Exception:  # noqa: BLE001
                continue
            if not isinstance(mani, dict):
                continue
            # 只往「条目多」的那份（真清单）里加；1 条的种子桩不加
            real = len([k for k, v in mani.items() if isinstance(v, dict)])
            if real <= 1:
                continue
            if plan.new_bundle in mani:
                continue
            sample = next((v for v in mani.values() if isinstance(v, dict)), {})
            entry = {"id": plan.new_bundle, "version": "1"}
            if "crc" in sample:
                entry["crc"] = "0"
            if "url" in sample:
                url = str(sample.get("url", ""))
                entry["url"] = re.sub(r"/[^/]+\.assetbundle$",
                                      f"/{plan.new_bundle}.assetbundle", url)
            mani[plan.new_bundle] = entry
            sess.add_apk_entry(name, json.dumps(mani, ensure_ascii=False).encode("utf-8"))
            out.append(f"manifest.json 登记 {plan.new_bundle}（v1）")
    return out


def _patch_pmseed(sess: Session, plan: ArtPlan) -> list[str]:
    """在播种表里给新包加一行，目录名按 version=1 算。"""
    from . import paths as pm_paths

    out: list[str] = []
    assert sess.source is not None
    dirname = pm_paths.make_cache_dirname(1)
    for c in sess.source.containers:
        for name in c.names():
            if not name.endswith("pmseed/index.txt"):
                continue
            try:
                text = c.read(name).decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                continue
            lines = text.splitlines()
            if any(ln.split()[:1] == [plan.new_bundle] for ln in lines if ln.split()):
                continue
            lines.append(f"{plan.new_bundle} {dirname}")
            sess.add_apk_entry(name, ("\n".join(lines) + "\n").encode("utf-8"))
            out.append(f"播种表登记 {plan.new_bundle} → {dirname}")
    return out


# ---------------------------------------------------------------- 一站式


def add_character(
    sess: Session,
    new_id: str,
    clone_from: str,
    *,
    kind: str = "morty",
    new_asset: str | None = None,
    new_bundle: str | None = None,
    images: dict[str, str | Path] | None = None,
    names: dict[str, str] | None = None,
    descriptions: dict[str, str] | None = None,
    overrides: dict[str, dict] | None = None,
    add_to_gacha: bool = False,
    gacha_pool: int = 0,
    dry_run: bool = False,
) -> dict:
    """造一个**完全的新角色**：自己的 id、自己的形象、自己的数值。

    返回 ``{ok, message, plan, art, notes}``。
    """
    result: dict = {"ok": False, "message": "", "plan": None, "art": None, "notes": []}
    try:
        new_asset = new_asset or new_id
        plan = plan_art(sess, clone_from, new_asset, new_bundle, kind=kind)
        result["plan"] = plan
    except Exception as exc:  # noqa: BLE001
        result["message"] = f"✗ 规划美术失败：{exc}"
        return result

    # 新 id / 新 assetid 都不能和现有的撞
    err = entries_mod.validate(sess, kind, new_id, clone_from)
    if err:
        result["message"] = f"✗ {err}"
        return result
    baa = _baa(sess)
    if new_asset in baa:
        result["message"] = (
            f"✗ assetid {new_asset!r} 已经被占用了（BundleAssetAssignment 里已有）。"
            "换一个名字，比如加个后缀"
        )
        return result
    if plan.new_bundle in (sess.source.bundle_names() if sess.source else []):
        result["message"] = f"✗ 包名 {plan.new_bundle!r} 已经存在，换一个"
        return result

    # 模板所在的包如果是「共享包」（里面还有别的角色），复制过去会带上无关数据。
    # 单角色包（psh*/tg* 那种）最干净。
    try:
        plan.other_morties = inspect_donor(sess, clone_from, kind).get("other_morties") or []
    except Exception:  # noqa: BLE001
        plan.other_morties = []

    warn = ""
    # 裁剪之后，模板所在的包是不是「共享包」已经无所谓了 ——
    # 新包只会留下这个条目自己的对象。这里只在**没法裁**的时候提醒。
    if plan.other_morties and not prune_supported(plan):
        warn = (
            f"\n  ⚠ 模板所在的包 {plan.donor_bundle} 是共享包，"
            f"里面有 {len(plan.other_morties)} 个别的条目，新包会带上它们。"
        )

    if dry_run:
        result["ok"] = True
        result["message"] = (
            f"✓ 预演通过\n  {plan.describe()}\n"
            f"  APK 里会新增：assets/AssetBundles/Android/{plan.new_bundle}.assetbundle\n"
            f"  并改写 manifest.json、pmseed/index.txt、BundleAssetAssignment"
            + warn
        )
        return result

    # 1) 造美术（顺便裁掉用不着的对象）
    try:
        art = build_art(sess, plan, images)
    except Exception as exc:  # noqa: BLE001
        result["message"] = f"✗ 生成美术失败：{exc}"
        return result
    result["art"] = art

    # 2) 登记到 APK
    try:
        notes = register_bundle(sess, plan, art["bytes"])
    except Exception as exc:  # noqa: BLE001
        result["message"] = f"✗ 登记新包失败：{exc}"
        return result
    result["notes"] = notes

    # 3) 莫蒂的数据表（复用 add_entry，但 assetid 指向新美术，且不再改 BAA）
    rep = entries_mod.add_entry(
        sess, kind, new_id, clone_from,
        names=names or {}, descriptions=descriptions or {},
        overrides=overrides or {},
        assetid=new_asset,
        add_to_gacha=add_to_gacha, gacha_pool=gacha_pool,
    )
    if not rep.ok:
        result["message"] = f"✗ 数据表写入失败：{rep.message}"
        return result

    result["ok"] = True
    # add_entry 会因为「assetid 不是模板那个」而提示「形象来自 X」——
    # 这里是我们自己刚登记的新美术，那句话反而误导，去掉
    rep.warnings = [w for w in rep.warnings if "形象来自" not in w]
    what = "角色" if kind == "morty" else "道具"
    lines = [f"✓ 完全新增{what} {new_id}（模板 {clone_from}）", f"  {plan.describe()}"]
    lines.append(f"  改名对象 {art['renamed']} 个，容器路径 {art['container_after']} 条")
    if art.get("objects_before") != art.get("objects_after"):
        lines.append(
            f"  裁掉用不着的对象：{art['objects_before']} → {art['objects_after']}"
            f"（{art['src_size']:,} → {art['size']:,} 字节）")
    if art["replaced"]:
        lines.append(f"  替换了 {len(art['replaced'])} 帧图：{'、'.join(art['replaced'][:8])}")
    if art["skipped"]:
        lines.append(f"  ⚠ {len(art['skipped'])} 项要留意：" + "；".join(art["skipped"][:4]))
    if plan.other_morties:
        lines.append(
            f"  ⚠ 模板所在的包 {plan.donor_bundle} 是**共享包**，里面还有 "
            f"{len(plan.other_morties)} 只别的角色（{'、'.join(plan.other_morties[:4])}…），"
            "新包会把它们也复制一份（只是不引用，纯粹占体积）。"
            "想干净点就换个「只装一只角色」的模板（例如 psh011 / tg016 那种）"
        )
    lines += ["  " + n for n in notes]
    lines += [c for c in rep.changes]
    for w in rep.warnings:
        lines.append("  ⚠ " + w)
    result["message"] = "\n".join(lines)
    return result
