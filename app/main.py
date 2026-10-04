#!/usr/bin/env python3
"""口蘑 Mod 工坊 —— 用 Dear ImGui 搭的模组制作器。

    python3 app/main.py [可选: apk 或 数据包.zip ...]

界面分四块：

    ┌──────────────────────── 菜单栏 ────────────────────────┐
    │ 工具栏：打开源 / 应用模组 / 导出                         │
    ├──────────┬──────────────┬─────────────────────────────┤
    │ 资源包    │ 资源列表      │ 预览 / 替换                  │
    │ （带筛选）│ （类型+搜索） │ 贴图放大、文本查看、音频信息   │
    ├──────────┴──────────────┴─────────────────────────────┤
    │ 待应用改动                        | 日志                 │
    └───────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from imgui_bundle import hello_imgui, immapp, imgui, immvision, portable_file_dialogs as pfd
from PIL import Image

from modkit import apkbuild, assetops, bundle as bundle_mod, fields as F
from modkit import paths, session as session_mod, sysenv
from modkit.bundle import Bundle

# ---------------------------------------------------------------- 外观

# 中文字体按平台挑（Windows 微软雅黑 / Linux Noto / macOS Arial Unicode / Termux 系统字体）
FONT_CANDIDATES = sysenv.cjk_font_candidates()

ACCENT = (0.36, 0.72, 1.0, 1.0)
WARN = (1.0, 0.72, 0.28, 1.0)
OK = (0.42, 0.86, 0.5, 1.0)
DIM = (0.62, 0.62, 0.66, 1.0)

#: 预览用的最大边（超过就缩略，避免每帧传巨大纹理给 GPU）
PREVIEW_MAX = 900

# 注意：portable_file_dialogs 的过滤器是**扁平成对**的列表，
# 形如 ["名称", "*.a *.b", "名称2", "*.c"]。
# 写成单个通配符列表会让对话框的过滤器名称变成乱码、而且只匹配一种扩展名，
# 于是「多选」就选不出另一类文件 —— 这个坑踩过一次。
F_ALL = ["所有文件", "*"]
F_SOURCES = [
    "游戏文件 (*.apk *.zip *.assetbundle *.tar *.gz *.xz)",
    "*.apk *.apks *.obb *.jar *.zip *.assetbundle *.bundle *.tar *.tar.gz *.tgz *.tar.bz2 *.tar.xz *.xz",
    "安装包 (*.apk *.apks *.obb)", "*.apk *.apks *.obb",
    "压缩包 (*.zip *.tar *.tar.gz *.tgz *.tar.bz2 *.tar.xz)", "*.zip *.tar *.tar.gz *.tgz *.tar.bz2 *.tar.xz",
    "资源包 (*.assetbundle *.bundle)", "*.assetbundle *.bundle",
    *F_ALL,
]
F_APK = ["安装包 (*.apk)", "*.apk", *F_ALL]
F_ZIP = ["压缩包 (*.zip *.tar *.tar.gz *.xz)", "*.zip *.tar *.tar.gz *.tgz *.tar.bz2 *.tar.xz", *F_ALL]
F_PMMOD = ["模组包 (*.pmmod)", "*.pmmod", *F_ALL]
F_IMAGE = ["图片 (*.png *.jpg *.jpeg *.webp *.bmp)", "*.png *.jpg *.jpeg *.webp *.bmp", *F_ALL]
F_TEXT = ["文本 (*.json *.txt *.csv)", "*.json *.txt *.csv", *F_ALL]
F_JSON = ["JSON (*.json)", "*.json", *F_ALL]


def _text_colored(col, text: str) -> None:
    imgui.push_style_color(imgui.Col_.text, col)
    imgui.text(text)
    imgui.pop_style_color()


def _badge(text: str, col) -> None:
    """彩色小标签。

    注意：**不要**在结尾调 ``same_line()`` —— 那会让下一个控件仍落在同一行，
    ImGui 的 cursor 不垂直推进，``ListClipper`` 会直接断言失败。
    需要并排的调用方自己先 ``same_line()``。
    """
    imgui.push_style_color(imgui.Col_.text, col)
    imgui.text(text)
    imgui.pop_style_color()


# ---------------------------------------------------------------- 拖拽添加
#
# 桌面端挂的是 **GLFW 原生的文件拖放回调**，从访达/资源管理器直接拖进来就行。
# hello_imgui 没暴露这个，但给了两样东西：
#   · ``backend_pointers.glfw_window`` / ``get_glfw_window_address()`` —— 窗口句柄
#   · ``callbacks.post_init_add_platform_backend_callbacks`` —— 注册后端回调的时机
# 剩下用 ctypes 调 imgui_bundle 自带的 libglfw 就够了。
#
# 拿到路径后**不能当场加**：GLFW 回调是在事件循环里同步调的，此时 ImGui 正处在
# 一帧中间，动状态容易出事。所以先塞进队列，下一帧再处理。
DROP_QUEUE: list[str] = []
_DROP_CB = None          # 必须保住引用，否则回调会被 GC 掉、直接段错误


def _glfw_lib():
    """把 imgui_bundle 自带的 libglfw 加载起来（哪个平台就先找到哪个）。"""
    import ctypes
    import glob
    import os

    try:
        import imgui_bundle

        base = os.path.dirname(imgui_bundle.__file__)
    except Exception:  # noqa: BLE001
        return None
    names = ("libglfw.dylib", "libglfw.3.dylib", "libglfw.so", "libglfw.so.3",
             "glfw3.dll", "glfw.dll")
    for n in names:
        p = os.path.join(base, n)
        if os.path.isfile(p):
            try:
                lib = ctypes.CDLL(p)
                lib.glfwSetDropCallback
                return lib
            except Exception:  # noqa: BLE001
                continue
    for p in glob.glob(os.path.join(base, "*glfw*")):
        if p.endswith((".dylib", ".so", ".dll")):
            try:
                lib = ctypes.CDLL(p)
                lib.glfwSetDropCallback
                return lib
            except Exception:  # noqa: BLE001
                continue
    return None


def install_file_drop() -> bool:
    """挂上系统级文件拖放。挂不上就返回 False（网页版和手动按钮仍然可用）。"""
    global _DROP_CB
    if _DROP_CB is not None:
        return True
    import ctypes

    try:
        from imgui_bundle import hello_imgui

        addr = hello_imgui.get_glfw_window_address()
    except Exception:  # noqa: BLE001
        return False
    if not addr:
        return False
    lib = _glfw_lib()
    if lib is None:
        return False

    CB = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                          ctypes.POINTER(ctypes.c_char_p))

    def _on_drop(window, count, paths):
        try:
            got = [paths[i].decode("utf-8", "replace") for i in range(count) if paths[i]]
        except Exception:  # noqa: BLE001
            got = []
        DROP_QUEUE.extend(got)

    cb = CB(_on_drop)
    try:
        lib.glfwSetDropCallback.argtypes = [ctypes.c_void_p, CB]
        lib.glfwSetDropCallback.restype = CB
        lib.glfwSetDropCallback(ctypes.c_void_p(addr), cb)
    except Exception:  # noqa: BLE001
        return False
    _DROP_CB = cb
    return True


# ---------------------------------------------------------------- 视觉主题
#
# 取自设计参考：深色底 + 琥珀黄做主强调 + 绿色表示「改完之后」，
# 拉丁小标用等宽大写，中文标题大而粗。
YELLOW = (1.00, 0.83, 0.15, 1.0)   # 主强调（计划号 / BEFORE / 选中）
GREEN = (0.36, 0.86, 0.50, 1.0)    # AFTER / 成功
CARD = (0.105, 0.105, 0.125, 1.0)  # 卡片底
LINE = (0.22, 0.22, 0.26, 1.0)     # 分隔线

#: 加载好的字体句柄（``load_fonts`` 里填）
FONTS: dict[str, object] = {"ui": None, "mono": None}


def _mono(text: str) -> None:
    """等宽小号大写拉丁文 —— 设计稿里那种副标题。"""
    f = FONTS.get("mono")
    if f is not None:
        imgui.push_font(f, 0.0)
    _text_colored(DIM, text)
    if f is not None:
        imgui.pop_font()


def _chip(text: str, col=None, *, filled: bool = False) -> None:
    """药丸标签。``filled`` 是实心（像「进行中」那种）。"""
    col = col or DIM
    if filled:
        imgui.push_style_color(imgui.Col_.button, (col[0], col[1], col[2], 0.85))
        imgui.push_style_color(imgui.Col_.text, (0.06, 0.06, 0.08, 1.0))
        imgui.button(text)
        imgui.pop_style_color(2)
    else:
        imgui.push_style_color(imgui.Col_.button, (0.16, 0.16, 0.19, 1.0))
        imgui.push_style_color(imgui.Col_.text, col)
        imgui.button(text)
        imgui.pop_style_color(2)


def _section(title: str, latin: str = "", *, color=None) -> None:
    """`▸ 标题` + 等宽大写下标，设计稿的分节样式。"""
    imgui.spacing()
    _text_colored(color or YELLOW, "▸ " + title)
    if latin:
        imgui.same_line()
        _mono("  " + latin.upper())
    imgui.spacing()


def _step(n: int, title: str, *, done: bool = False, active: bool = True) -> None:
    """步骤指示：①②③④。"""
    col = GREEN if done else (YELLOW if active else DIM)
    imgui.text_colored(col, "①②③④⑤"[n - 1] if n <= 5 else str(n))
    imgui.same_line()
    _text_colored(col, title)


def _bar(fraction: float, width: float = 120.0, height: float = 6.0,
         color=None) -> None:
    """细长的进度/数值条。"""
    col = color or YELLOW
    p = imgui.get_cursor_screen_pos()
    imgui.get_window_draw_list().add_rect_filled(
        imgui.ImVec2(p.x, p.y + 4), imgui.ImVec2(p.x + width, p.y + 4 + height),
        imgui.get_color_u32(imgui.ImVec4(0.18, 0.18, 0.21, 1.0)), 3.0)
    w = max(2.0, width * max(0.0, min(1.0, fraction)))
    imgui.get_window_draw_list().add_rect_filled(
        imgui.ImVec2(p.x, p.y + 4), imgui.ImVec2(p.x + w, p.y + 4 + height),
        imgui.get_color_u32(imgui.ImVec4(*col)), 3.0)
    imgui.dummy(imgui.ImVec2(width, height + 8))


def _ellipsis(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def _tip(text: str) -> None:
    if imgui.is_item_hovered():
        imgui.set_tooltip(text)


def _center_next_window(size: imgui.ImVec2) -> None:
    """让下一个窗口在首次出现时居中，别盖住主面板。"""
    imgui.set_next_window_size(size, imgui.Cond_.first_use_ever)
    vp = imgui.get_main_viewport()
    imgui.set_next_window_pos(
        imgui.ImVec2(vp.work_pos.x + (vp.work_size.x - size.x) * 0.5,
                     vp.work_pos.y + (vp.work_size.y - size.y) * 0.5),
        imgui.Cond_.first_use_ever,
    )


# ---------------------------------------------------------------- App


class ModkitApp:
    def __init__(self, initial_paths: list[str] | None = None):
        self.sess = session_mod.Session()
        self.initial_paths = [p for p in (initial_paths or []) if Path(p).exists()]
        #: >0 时进入无人值守自检：渲染这么多帧、把各面板都走一遍、然后退出
        self.autotest = int(os.environ.get("PM_MODKIT_AUTOTEST", "0") or "0")
        self._frame = 0

        # --- 选择状态
        self.sel_bundle: str | None = None
        self.sel_pid: int | None = None
        self.bundle_filter = ""
        self.asset_filter = ""
        self.type_filter = "全部"
        self.only_modified = False
        self.show_sources = True

        # --- 缓存
        self._img_cache: dict[tuple[str, int, int], np.ndarray] = {}
        self._text_cache: dict[tuple[str, int, int], str] = {}
        self._preview_err: str = ""

        # --- 任务线程
        self._q: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self.task_label: str = ""
        self.task_done_at: float = 0.0

        # --- 导出设置
        self.out_dir = str(sysenv.default_output_dir())
        self.mod_name = "我的模组"
        self.mod_author = ""
        self.mod_desc = ""
        self.mod_prebuilt = False
        self.apk_src = ""
        self.apk_out = ""
        self.apk_sign = True
        self.apk_bump = False
        self.apk_keystore = str(apkbuild.default_keystore() or "")
        self.cache_full = False
        self.show_export = False
        self.export_tab = ""
        self.show_new_entry = False
        self.show_field_help = False
        self.show_check = False
        self.show_gallery = False
        self._gal_reset()
        self.show_text_editor = False
        self.text_buf = ""
        self.text_edit_target: tuple[str, int] | None = None
        self.show_log = True

        if self.initial_paths:
            self.open_source(self.initial_paths)
            self._guess_apk()

    # ------------------------------------------------------------ 任务

    def busy(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    def start(self, label: str, fn) -> None:
        """把耗时活儿丢到后台线程，结果经队列回主线程。"""
        if self.busy():
            self.log(f"⏳ 正在忙（{self.task_label}），稍后再试")
            return
        self.task_label = label
        self.log(f"▶ {label}")

        def runner():
            try:
                res = fn()
                self._q.put(("ok", label, res))
            except Exception as exc:  # noqa: BLE001
                self._q.put(("err", label, f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"))

        self._worker = threading.Thread(target=runner, daemon=True)
        self._worker.start()

    def _poll(self) -> None:
        while True:
            try:
                kind, label, payload = self._q.get_nowait()
            except queue.Empty:
                return
            self.task_done_at = time.time()
            if kind == "ok":
                self.log(f"✓ {label} 完成")
                if isinstance(payload, str) and payload:
                    self.log("   " + payload)
            else:
                self.log(f"✗ {label} 失败：{payload}")
                self._preview_err = str(payload)

    def log(self, msg: str) -> None:
        self.sess.say(msg)

    # ------------------------------------------------------------ 源

    def open_source(self, paths_in: list[str]) -> None:
        try:
            self.sess.open(*paths_in)
        except Exception as exc:  # noqa: BLE001
            self.log(f"✗ 打开失败：{exc}")
            return
        self.sel_bundle = None
        self.sel_pid = None
        self._img_cache.clear()
        self._text_cache.clear()
        self._guess_apk()
        names = self.sess.source.bundle_names() if self.sess.source else []
        self.sel_bundle = next((n for n in ["spdata", "mpdata", "appdata", "text"] if n in names),
                               names[0] if names else None)

    def _guess_apk(self) -> None:
        if not self.sess.source:
            return
        for c in self.sess.source.containers:
            if c.kind == "zip" and c.root.suffix.lower() == ".apk":
                if not self.apk_src:
                    self.apk_src = str(c.root)
                    self.apk_out = str(c.root.with_name(c.root.stem + "-mod.apk"))
                break

    def cur_bundle(self) -> Bundle | None:
        if not self.sel_bundle:
            return None
        return self.sess.bundle(self.sel_bundle, eager=True)

    def cur_entry(self):
        b = self.cur_bundle()
        if b is None or self.sel_pid is None:
            return None
        return next((a for a in b.assets if a.path_id == self.sel_pid), None)

    # ------------------------------------------------------------ 文件对话框

    def pick_open(self, title: str, filters: list[str] | None = None, multi: bool = False) -> list[str]:
        opt = pfd.opt.multiselect if multi else pfd.opt.none
        dlg = pfd.open_file(title, str(Path.home()), filters or F_ALL, opt)
        res = dlg.result()
        return res or []

    def pick_save(self, title: str, default: str, filters: list[str] | None = None) -> str:
        return pfd.save_file(title, default, filters or F_ALL).result() or ""

    def pick_folder(self, title: str, default: str = "") -> str:
        return pfd.select_folder(title, default or str(Path.home())).result() or ""

    # ------------------------------------------------------------ 渲染

    def _drain_drops(self) -> None:
        """把拖进来的文件/文件夹变成来源。一帧处理一次。"""
        if not DROP_QUEUE:
            return
        got = list(DROP_QUEUE)
        DROP_QUEUE.clear()
        paths: list[str] = []
        for p in got:
            if Path(p).is_dir():
                # 拖文件夹进来就当「追加源」处理它里面的压缩包/APK
                for f in sorted(Path(p).rglob("*")):
                    if f.suffix.lower() in (".apk", ".zip", ".tar", ".gz", ".mod", ".pmmod"):
                        paths.append(str(f))
            else:
                paths.append(p)
        if not paths:
            self.log("拖进来的东西里没有能认的包（.apk / .zip / .tar）")
            return
        names = "、".join(Path(x).name for x in paths[:4]) + ("…" if len(paths) > 4 else "")
        if self.sess.source is not None:
            # 已经有源了就当「追加源」，别把用户打开的东西冲掉
            self.log(f"拖入 {len(paths)} 个文件，追加为来源：{names}")
            try:
                self.sess.add_source(*paths)
            except Exception as exc:  # noqa: BLE001
                self.log(f"✗ 追加失败：{exc}")
                return
            self.log(f"✓ 现在的来源：{len(self.sess.source.containers)} 个容器"
                     f"、{len(self.sess.source.bundle_names())} 个资源包")
        else:
            self.log(f"拖入 {len(paths)} 个文件，打开为来源：{names}")
            self.open_source(paths)

    def draw(self) -> None:
        self._drain_drops()
        self._poll()
        if self.autotest:
            self._frame += 1
            self._autotest_step(self._frame)
        self._draw_menu()
        self._draw_toolbar()
        avail = imgui.get_content_region_avail()
        bottom_h = 210.0
        top_h = max(160.0, avail.y - bottom_h - 14.0)
        left_w = 280.0
        mid_w = 400.0
        right_w = max(320.0, avail.x - left_w - mid_w - 24.0)

        imgui.begin_child("##bundles", imgui.ImVec2(left_w, top_h), True)
        self._draw_bundles()
        imgui.end_child()
        imgui.same_line()
        imgui.begin_child("##assets", imgui.ImVec2(mid_w, top_h), True)
        self._draw_assets()
        imgui.end_child()
        imgui.same_line()
        imgui.begin_child("##preview", imgui.ImVec2(right_w, top_h), True)
        self._draw_preview()
        imgui.end_child()

        imgui.begin_child("##bottom", imgui.ImVec2(0, bottom_h), True)
        self._draw_bottom()
        imgui.end_child()

        if self.show_export:
            self._draw_export_window()
        if self.show_new_entry:
            self._draw_new_entry()
        if self.show_field_help:
            self._draw_field_help()
        if self.show_check:
            self._draw_check()
        if self.show_gallery:
            self._draw_gallery()
        if self.show_text_editor:
            self._draw_text_editor()

    # ------------------------------------------------------------ 菜单

    def _draw_menu(self) -> None:
        if not imgui.begin_menu_bar():
            return
        if imgui.begin_menu("文件"):
            if imgui.menu_item("打开游戏文件…（可 ⌘ 多选）")[0]:
                got = self.pick_open("打开游戏文件（可按住 ⌘ 多选多个）", F_SOURCES, multi=True)
                if got:
                    self.open_source(got)
            if imgui.menu_item("追加文件到当前源…（可 ⌘ 多选）")[0]:
                got = self.pick_open("追加文件（可按住 ⌘ 多选多个）", F_SOURCES, multi=True)
                if got:
                    self.sess.add_source(*got)
            if imgui.menu_item("追加一个目录…")[0]:
                got = self.pick_folder("选择已解包的数据目录", str(Path.home()))
                if got:
                    self.sess.add_source(got)
            imgui.separator()
            if imgui.menu_item("只打开数据包 zip…")[0]:
                got = self.pick_open("打开数据包 zip", F_ZIP, multi=False)
                if got:
                    self.open_source(got)
            imgui.begin_disabled(self.sess.source is None)
            if imgui.menu_item("关闭全部源")[0]:
                self._close_all()
            imgui.end_disabled()
            imgui.separator()
            if imgui.menu_item("应用 .pmmod 模组…")[0]:
                got = self.pick_open("选择模组包", F_PMMOD, multi=False)
                if got:
                    self.start("应用模组", lambda: self.sess.apply_modpack(got[0]))
            if imgui.menu_item("导出成品 .pmmod…")[0]:
                self.show_export = True
            imgui.separator()
            if imgui.menu_item("退出")[0]:
                hello_imgui.get_runner_params().app_shall_exit = True
            imgui.end_menu()

        if imgui.begin_menu("模组"):
            if imgui.menu_item("新增莫蒂 / 道具 / 技能…")[0]:
                self.show_new_entry = True
            imgui.separator()
            if imgui.menu_item("保存为模组包 .pmmod…")[0]:
                self.show_export = True
            if imgui.menu_item("还原全部改动")[0]:
                self.sess.reset_all()
                self._img_cache.clear()
                self._text_cache.clear()
            imgui.end_menu()

        if imgui.begin_menu("帮助"):
            if imgui.menu_item("字段中英文对照…")[0]:
                self.show_field_help = True
            imgui.separator()
            if imgui.menu_item("工具链自检")[0]:
                self.start("工具链自检", self._toolchain_report)
            imgui.end_menu()
        imgui.end_menu_bar()

    def _toolchain_report(self) -> str:
        st = apkbuild.toolchain_status()
        lines = [f"  {k}: {v or '未找到'}" for k, v in st.items()]
        return "\n".join(lines)

    # ------------------------------------------------------------ 工具栏

    def _draw_toolbar(self) -> None:
        if imgui.button("打开源…"):
            got = self.pick_open("打开游戏文件（可按住 ⌘ 多选多个）", F_SOURCES, multi=True)
            if got:
                self.open_source(got)
        _tip("打开 APK / 数据包 zip / 解包目录。\n可以一次选多个，也可以分几次「追加源」")
        imgui.same_line()
        if imgui.button("追加源…"):
            got = self.pick_open("追加文件到当前源（可按住 ⌘ 多选多个）", F_SOURCES, multi=True)
            if got:
                self.sess.add_source(*got)
        _tip("不替换现有的源，往后面加。\n一次只选一个也没关系，可以分多次加齐")
        imgui.same_line()
        imgui.begin_disabled(self.sess.source is None)
        if imgui.button("图鉴"):
            self.show_gallery = True
            self._gal_reset()
        _tip("图形化看角色：立绘、15 帧动作、数值条、技能效果")
        imgui.same_line()
        if imgui.button("体检"):
            self._run_check()
        _tip("检查数据表里有没有「会让游戏崩」的问题\n（主键重复、编号冲突之类）。出产物前会自动拦一道")
        imgui.same_line()
        if imgui.button("新增条目…"):
            self.show_new_entry = True
        _tip("往数据表里加新的莫蒂 / 道具 / 技能。\n做法是克隆一条现有的再改，字段不会漏")
        imgui.same_line()
        if imgui.button("应用模组…"):
            got = self.pick_open("选择模组包", F_PMMOD, multi=False)
            if got:
                self.start("应用模组", lambda: self.sess.apply_modpack(got[0]))
        imgui.same_line()
        if imgui.button("导出…"):
            self.show_export = True
        imgui.end_disabled()

        imgui.same_line()
        imgui.text("  ")
        imgui.same_line()
        n_mod = len(self.sess.modified)
        n_assets = len(self.sess.modified_assets())
        if n_mod:
            _badge(f"● {n_mod} 个包 / {n_assets} 处改动已就绪", WARN)
        else:
            _badge("○ 尚无改动", DIM)

        if self.sess.source is not None:
            src = self.sess.source
            imgui.same_line()
            imgui.text("  ")
            imgui.same_line()
            _badge(f"源：{src.label}", DIM)
            if src.is_self_contained:
                imgui.same_line()
                _badge(f"★ 自带全部 {len(src)} 个包", OK)
                if imgui.is_item_hovered():
                    imgui.set_tooltip(
                        "这个 APK 里已经包含全部资源包"
                        + (f"（含播种表 pmseed/index.txt，{len(src.pmseed)} 条）"
                           if src.pmseed else "")
                        + "\n\n只开它一个就够了。\n"
                        "重打包它 = 一次带上所有改动，装上即生效。"
                    )
        if self.busy():
            imgui.same_line()
            imgui.text("  ")
            imgui.same_line()
            _badge(f"⏳ {self.task_label}…", ACCENT)

    # ------------------------------------------------------------ 来源清单

    def _close_all(self) -> None:
        if self.sess.source is not None:
            self.sess.source.close()
        self.sess.source = None
        self.sess.bundles.clear()
        self.sess.chosen.clear()
        self.sel_bundle = None
        self.sel_pid = None
        self._img_cache.clear()
        self._text_cache.clear()
        self.log("已关闭全部源")

    def _draw_containers(self) -> None:
        """列出当前打开的文件/目录，可以逐个加减。"""
        src = self.sess.source
        if src is None:
            return
        expanded, self.show_sources = imgui.collapsing_header(
            f"来源（{len(src.containers)}）##srcs", self.show_sources
        )
        if not expanded:
            return
        if imgui.small_button("追加文件…"):
            got = self.pick_open("追加文件到当前源（可按住 ⌘ 多选多个）", F_SOURCES, multi=True)
            if got:
                self.sess.add_source(*got)
        imgui.same_line()
        if imgui.small_button("追加目录…"):
            got = self.pick_folder("选择已解包的数据目录", str(Path.home()))
            if got:
                self.sess.add_source(got)
        imgui.same_line()
        if imgui.small_button("关闭全部"):
            self._close_all()
            return

        for i, c in enumerate(src.containers):
            kind = "zip" if c.kind == "zip" else "目录"
            imgui.bullet()
            imgui.same_line()
            imgui.text(_ellipsis(c.label, 22))
            if imgui.is_item_hovered():
                imgui.set_tooltip(f"{c.root}\n类型：{kind}")
            imgui.same_line(imgui.get_window_width() - 44)
            if imgui.small_button(f"移除##c{i}"):
                self.sess.remove_container(i)
                self._img_cache.clear()
                self._text_cache.clear()
                break
        imgui.separator()

    # ------------------------------------------------------------ 资源包列表

    def _draw_bundles(self) -> None:
        src = self.sess.source
        imgui.text(f"资源包  （{len(src)} 个）" if src is not None else "资源包")
        imgui.separator()
        if src is None:
            _text_colored(DIM, "还没打开源。\n\n点上面「打开源…」，\n把 dp.apk 和 口蘑数据包.zip\n一起选中（按住 ⌘ 可多选）。\n\n"
                               "也可以一个个来：\n先选一个，再用「追加源…」加下一个。")
            return

        self._draw_containers()
        if src.is_self_contained and len(src.containers) == 1:
            _text_colored(OK, "★ 这个 APK 自带全部资源，不用再开数据包；重打包它即可一次带上所有改动。")
        _, self.bundle_filter = imgui.input_text_with_hint("##bfilter", "筛选包名", self.bundle_filter)
        _, self.only_modified = imgui.checkbox("只看已改动的包", self.only_modified)

        names = self.sess.source.bundle_names()
        if self.only_modified:
            names = [n for n in names if n in self.sess.modified]
        if self.bundle_filter:
            f = self.bundle_filter.lower()
            names = [n for n in names if f in n.lower()]

        imgui.text(f"{len(names)} 个")
        imgui.separator()

        clipper = imgui.ListClipper()
        clipper.begin(len(names))
        while clipper.step():
            for i in range(clipper.display_start, clipper.display_end):
                name = names[i]
                b = self.sess.bundles.get(name)
                mod = name in self.sess.modified
                ref = self.sess.ref_for(name)

                label = f"{'● ' if mod else ''}{_ellipsis(name, 20)}"
                if imgui.selectable(label, name == self.sel_bundle)[0]:
                    self.sel_bundle = name
                    self.sel_pid = None
                if ref is not None and imgui.is_item_hovered():
                    vers = self.sess.bundle_version(name)
                    bdoc = F.bundle_doc(name)
                    imgui.set_tooltip(
                        f"{name}" + (f" —— {bdoc}" if bdoc else "")
                        + f"\n来源：{ref.layout_label}"
                        + (f" · {ref.platform}" if ref.platform else "")
                        + f"\nversion：{vers if vers is not None else '未知'}"
                        + f"\n大小：{(ref.size or 0) / 1024:.0f} KB"
                        + f"\n对象数：{len(b.assets) if b and b.assets else '未加载'}"
                    )
                if ref is not None:
                    imgui.same_line(imgui.get_window_width() - 74)
                    _badge({"cache": "缓存", "apk": "APK", "cdn": "CDN", "loose": "散装"}.get(
                        ref.layout, ref.layout), WARN if ref.layout == "apk" else DIM)
        clipper.end()

    # ------------------------------------------------------------ 资源列表

    def _draw_assets(self) -> None:
        b = self.cur_bundle()
        if b is None:
            imgui.text("资源")
            imgui.separator()
            _text_colored(DIM, "左边选一个资源包。")
            return
        if not b.ok:
            _text_colored((1, .4, .4, 1), f"这个包解析失败：\n{b.error}")
            return

        assets = b.assets
        imgui.text(f"{self.sel_bundle}  ·  {len(assets)} 个对象")
        bdoc = F.bundle_doc(self.sel_bundle or "")
        if bdoc:
            imgui.same_line()
            _badge(bdoc, DIM)
        self._draw_ref_chooser()
        imgui.separator()

        counts = b.type_counts()
        types = ["全部"] + [f"{F.type_short(k)}（{k}）" for k in counts]
        type_keys = ["全部"] + list(counts)
        imgui.set_next_item_width(150)
        cur_t = type_keys.index(self.type_filter) if self.type_filter in type_keys else 0
        ch, idx = imgui.combo("类型", cur_t, types)
        if ch:
            self.type_filter = type_keys[idx]
        imgui.same_line()
        imgui.begin_disabled(self.busy())
        if imgui.small_button("解包整包…"):
            got = self.pick_folder(f"把 {self.sel_bundle} 解包到哪个目录", self.out_dir)
            if got:
                self._dump_bundle(self.sel_bundle or "", got)
        imgui.end_disabled()
        _tip("把这个包里的所有对象按类型导出：\n贴图→png、文本→json、其余→类型树 json、音频→wav")
        imgui.same_line()
        imgui.set_next_item_width(-1)
        _, self.asset_filter = imgui.input_text_with_hint("##afilter", "搜名字 / path_id", self.asset_filter)

        shown = self._filtered_assets(assets)
        imgui.text(f"匹配 {len(shown)}")
        imgui.separator()

        clipper = imgui.ListClipper()
        clipper.begin(len(shown))
        while clipper.step():
            for i in range(clipper.display_start, clipper.display_end):
                e = shown[i]
                kind = assetops.asset_kind(e)
                mod = e.path_id in b.dirty
                tag = {"image": "图", "text": "文", "audio": "音", "tree": "树"}.get(kind, "·")
                label = f"{'●' if mod else ' '} [{tag}] {e.display}  ##{e.path_id}"
                if imgui.selectable(label, e.path_id == self.sel_pid)[0]:
                    self.sel_pid = e.path_id
                if imgui.is_item_hovered():
                    tdoc = F.type_doc(e.type)
                    imgui.set_tooltip(
                        f"{e.type}" + (f" —— {tdoc}" if tdoc else "") + f"\n{e.name or '(无名)'}\n"
                        f"path_id: {e.path_id}\n大小: {e.size:,} 字节\n"
                        f"{assetops.replace_note(e) or '可替换'}"
                    )
                imgui.same_line(imgui.get_window_width() - 74)
                _badge(F.type_short(e.type), DIM)
        clipper.end()

    def _draw_ref_chooser(self) -> None:
        """同一个包在多处存在时（例如 text 同时在 APK 和下载缓存里），让用户选改哪一份。

        改错地方会很迷惑：改了缓存但游戏读的是 APK 里那份，就完全不生效。
        """
        refs = self.sess.refs_for(self.sel_bundle or "")
        if len(refs) <= 1:
            return
        cur_ref = self.sess.ref_for(self.sel_bundle or "")
        labels = []
        for r in refs:
            lab = r.layout_label + (f"·{r.platform}" if r.platform else "")
            if r.version is not None:
                lab += f" v{r.version}"
            lab += f"  ({(r.size or 0) / 1024:.0f} KB)"
            labels.append(lab)
        cur = refs.index(cur_ref) if cur_ref in refs else 0

        imgui.set_next_item_width(-1)
        ch, idx = imgui.combo("编辑哪一份", cur, labels)
        if ch and idx != cur:
            self.sess.choose(self.sel_bundle or "", refs[idx])
            self.sel_pid = None
            self._img_cache.clear()
            self._text_cache.clear()

    def _filtered_assets(self, assets):
        out = assets
        if self.type_filter != "全部":
            out = [a for a in out if a.type == self.type_filter]
        if self.asset_filter:
            f = self.asset_filter.lower()
            out = [a for a in out if f in a.search_key()]
        return out

    # ------------------------------------------------------------ 预览

    def _draw_preview(self) -> None:
        if self.busy():
            # 后台任务可能正在解析同一个包，先别去碰它
            imgui.text("预览")
            imgui.separator()
            _text_colored(ACCENT, f"⏳ {self.task_label}…\n\n完成后就能继续看。")
            return
        b = self.cur_bundle()
        e = self.cur_entry()
        if b is None or e is None:
            imgui.text("预览")
            imgui.separator()
            _text_colored(DIM, "选一个资源看内容。\n\n贴图可以直接看，\n文本可以看 JSON。")
            return

        kind = assetops.asset_kind(e)
        mod = e.path_id in b.dirty

        tdoc = F.type_doc(e.type)
        _badge(f"{tdoc}（{e.type}）" if tdoc else e.type, ACCENT)
        imgui.same_line()
        _badge(e.name or "(无名)", WARN if mod else (1, 1, 1, 1))
        if mod:
            imgui.same_line()
            _badge("已修改", WARN)
        imgui.text(f"path_id {e.path_id} · {e.size:,} 字节")

        # 操作按钮
        imgui.separator()
        if imgui.button("导出…"):
            self._export_asset(b, e)
        imgui.same_line()
        can = assetops.can_replace(e)
        imgui.begin_disabled(not can)
        if imgui.button("替换…"):
            self._replace_asset(b, e)
        imgui.end_disabled()
        if not can and assetops.replace_note(e):
            imgui.same_line()
            _text_colored(DIM, assetops.replace_note(e))
        imgui.same_line()
        imgui.begin_disabled(not mod)
        if imgui.button("撤销此资源"):
            b.revert_asset(e.path_id)
            self._img_cache.clear()
            self._text_cache.clear()
            self.log(f"已撤销 {b.name} / {e.display}")
        imgui.end_disabled()

        imgui.separator()

        if kind == assetops.KIND_IMAGE:
            self._preview_image(b, e)
        elif kind == assetops.KIND_TEXT:
            self._preview_text(b, e)
        elif kind == assetops.KIND_AUDIO:
            self._preview_audio(b, e)
        elif kind == assetops.KIND_TREE:
            self._preview_tree(b, e)
        else:
            _text_colored(DIM, f"{e.type} 没有预览。\n可以「导出…」看原始字节。")

    def _preview_image(self, b: Bundle, e) -> None:
        key = (b.name, e.path_id, 1 if e.path_id in b.dirty else 0)
        arr = self._img_cache.get(key)
        if arr is None:
            try:
                img = b.preview_image(e)
            except Exception as exc:  # noqa: BLE001
                _text_colored((1, .4, .4, 1), f"解不出图：{exc}")
                return
            if img is None:
                _text_colored(DIM, "解不出图（可能是 Sprite 图集，看它引用的 Texture2D）")
                return
            if max(img.size) > PREVIEW_MAX:
                r = PREVIEW_MAX / max(img.size)
                img = img.resize((max(1, int(img.width * r)), max(1, int(img.height * r))), Image.LANCZOS)
            # 用 np.array 复制一份：PIL 转出来的数组是只读的，
            # nanobind 的类型转换只接受可写、C 连续的 ndarray
            arr = np.ascontiguousarray(np.array(img.convert("RGBA")))
            self._img_cache[key] = arr
        h, w = arr.shape[:2]
        imgui.text(f"{w} × {h}")
        params = immvision.ImageParams()
        params.show_options_button = True
        params.show_pixel_info = True
        params.image_display_size = (
            int(min(w, imgui.get_content_region_avail().x - 10)),
            int(min(h, imgui.get_content_region_avail().y - 20)),
        )
        immvision.image(f"##img{b.name}{e.path_id}", arr, params)

    def _preview_text(self, b: Bundle, e) -> None:
        key = (b.name, e.path_id, 2 if e.path_id in b.dirty else 0)
        txt = self._text_cache.get(key)
        if txt is None:
            try:
                txt = b.preview_text(e)
            except Exception as exc:  # noqa: BLE001
                _text_colored((1, .4, .4, 1), f"读不出文本：{exc}")
                return
            self._text_cache[key] = txt
        imgui.text(f"{len(txt):,} 字符")
        imgui.same_line()
        if imgui.small_button("编辑文本"):
            self.text_buf = txt
            self.text_edit_target = (b.name, e.path_id)
            self.show_text_editor = True
        head = txt[:20000]
        if len(txt) > len(head):
            _text_colored(WARN, f"（预览只显示前 20000 字符，完整内容请「导出…」）")
        imgui.begin_child("##textscroll", imgui.ImVec2(0, 0), True)
        imgui.text_unformatted(head)
        imgui.end_child()

    def _preview_audio(self, b: Bundle, e) -> None:
        try:
            imgui.text(b.audio_summary(e))
        except Exception as exc:  # noqa: BLE001
            imgui.text(f"读不出信息：{exc}")
        _text_colored(DIM, "音频是 FSB 流式资源，本工具只支持导出成 wav。")

    def _preview_tree(self, b: Bundle, e) -> None:
        tree = b.read_typetree(e)
        if tree is None:
            _text_colored(DIM, "读不出类型树。")
            return
        import json

        s = json.dumps(tree, indent=2, ensure_ascii=False)
        imgui.text(f"类型树 {len(s):,} 字符")
        imgui.same_line()
        if imgui.small_button("编辑类型树"):
            self.text_buf = s
            self.text_edit_target = (b.name, e.path_id)
            self.show_text_editor = True
        imgui.begin_child("##treescroll", imgui.ImVec2(0, 0), True)
        imgui.text_unformatted(s[:40000])
        imgui.end_child()

    # ------------------------------------------------------------ 导出 / 替换动作

    def _dump_bundle(self, name: str, out_dir: str) -> None:
        """把一个包整个解出来。

        关键：**在主线程先把字节取好**，再丢给后台线程去解析导出。
        后台线程用的是全新解析的 Bundle，不和界面共用同一个 UnityPy 实例，
        免得两边同时读一个 env 出问题。
        """
        b = self.sess.bundle(name, eager=True)
        if b is None or not b.ok:
            self.log(f"✗ {name} 打不开，无法解包")
            return
        try:
            data = b.save()  # 主线程：带上未导出的改动
        except Exception as exc:  # noqa: BLE001
            self.log(f"✗ {name} 回写失败：{exc}")
            return
        ref = b.ref
        total = len(b.assets)

        def job() -> str:
            fresh = bundle_mod.load(ref, data)
            fresh.assets
            target = Path(out_dir) / name
            ok = fail = 0
            errors: list[str] = []
            for a in fresh.assets:
                try:
                    assetops.export_asset(fresh, a, target)
                    ok += 1
                except Exception as exc:  # noqa: BLE001
                    fail += 1
                    if len(errors) < 3:
                        errors.append(f"{a.type} {a.display}: {exc}")
            msg = f"{name}：成功 {ok} / 共 {total}，失败 {fail} → {target}"
            if errors:
                msg += "\n   " + "\n   ".join(errors)
            return msg

        self.start(f"解包 {name}", job)

    def _export_asset(self, b: Bundle, e) -> None:
        default = str(Path(self.out_dir) / assetops.suggest_filename(b, e))
        ext = Path(default).suffix or ".bin"
        dest = self.pick_save(f"导出 {e.display}", default, [f"{ext} 文件", f"*{ext}", *F_ALL])
        if not dest:
            return

        def job():
            p = Path(dest)
            p.parent.mkdir(parents=True, exist_ok=True)
            if assetops.asset_kind(e) in (assetops.KIND_TEXT, assetops.KIND_IMAGE, assetops.KIND_AUDIO, assetops.KIND_TREE):
                # 走正常导出再改名，保证格式正确
                tmp = p.parent / ("_mk_tmp_" + assetops.suggest_filename(b, e))
                got = assetops.export_asset(b, e, p.parent)
                if got != p:
                    got.replace(p)
                if tmp.exists():
                    tmp.unlink()
            else:
                assetops.export_raw(b, e, p.parent)
            return str(p)

        self.start(f"导出 {e.display}", job)

    def _replace_asset(self, b: Bundle, e) -> None:
        kind = assetops.asset_kind(e)
        filters = {
            assetops.KIND_IMAGE: F_IMAGE,
            assetops.KIND_TEXT: F_TEXT,
            assetops.KIND_TREE: F_JSON,
        }.get(kind, F_ALL)
        got = self.pick_open(f"选择替换文件（{e.type}）", filters, multi=False)
        if not got:
            return
        res = assetops.import_asset(b, e, got[0], resize=True)
        if res.ok:
            self.log(f"✓ {b.name} / {e.display}：{res.message}")
            self._img_cache.clear()
            self._text_cache.clear()
        else:
            self.log(f"✗ 替换失败：{res.message}")

    # ------------------------------------------------------------ 底部：改动 + 日志

    def _draw_bottom(self) -> None:
        if not imgui.begin_tab_bar("##bottomtabs"):
            return
        if imgui.begin_tab_item("待应用改动")[0]:
            mods = self.sess.modified
            if not mods:
                _text_colored(DIM, "还没有改动。选一个资源，点「替换…」或「编辑文本」就会记在这里。")
            else:
                if imgui.button("清空全部改动"):
                    self.sess.reset_all()
                    self._img_cache.clear()
                    self._text_cache.clear()
                imgui.same_line()
                if imgui.button("立即导出…"):
                    self.show_export = True
                imgui.separator()
                imgui.begin_child("##modlist", imgui.ImVec2(0, 0), False)
                for name, b in sorted(mods.items()):
                    ref = self.sess.ref_for(name)
                    lay = ref.layout_label if ref else "?"
                    if b.prebuilt:
                        _text_colored(OK, f"● {name}   （成品包，来自 .pmmod）")
                        continue
                    imgui.text(f"● {name}")
                    imgui.same_line()
                    _badge(f"[{lay}]", WARN if lay == "APK 内置" else DIM)
                    imgui.same_line()
                    if imgui.small_button(f"还原##{name}"):
                        self.sess.reset(name)
                        self._img_cache.clear()
                        self._text_cache.clear()
                    for pid in sorted(b.dirty):
                        e = next((a for a in b.assets if a.path_id == pid), None)
                        if e is None:
                            continue
                        imgui.bullet()
                        imgui.same_line()
                        imgui.text(f"{e.type}  {e.display}")
                        imgui.same_line()
                        if imgui.small_button(f"撤销##{name}{pid}"):
                            b.revert_asset(pid)
                            self._img_cache.clear()
                            self._text_cache.clear()
                imgui.end_child()
            imgui.end_tab_item()

        if imgui.begin_tab_item("日志")[0]:
            if imgui.button("清空"):
                self.sess.log.clear()
            imgui.same_line()
            imgui.text(f"{len(self.sess.log)} 行")
            imgui.separator()
            imgui.begin_child("##logscroll", imgui.ImVec2(0, 0), False)
            for line in self.sess.log[-400:]:
                if line.startswith("✓"):
                    _text_colored(OK, line)
                elif line.startswith(("✗", "⚠", "⏳")):
                    _text_colored(WARN, line)
                elif line.startswith("▶"):
                    _text_colored(ACCENT, line)
                else:
                    imgui.text(line)
            if imgui.get_scroll_y() >= imgui.get_scroll_max_y() - 4:
                imgui.set_scroll_here_y(1.0)
            imgui.end_child()
            imgui.end_tab_item()
        imgui.end_tab_bar()

    # ------------------------------------------------------------ 文本编辑窗口

    def _draw_text_editor(self) -> None:
        _center_next_window(imgui.ImVec2(900, 620))
        opened, self.show_text_editor = imgui.begin("编辑文本 / 类型树", self.show_text_editor)
        if not opened:
            imgui.end()
            return

        tgt = self.text_edit_target
        if tgt is None:
            imgui.text("没有目标")
            imgui.end()
            return
        bname, pid = tgt
        imgui.text(f"{bname}  ·  path_id {pid}  ·  {len(self.text_buf):,} 字符")

        is_tree = False
        b = self.sess.bundles.get(bname)
        e = next((a for a in b.assets if a.path_id == pid), None) if b else None
        if e is not None:
            is_tree = assetops.asset_kind(e) == assetops.KIND_TREE

        if not is_tree and imgui.small_button("格式化 JSON"):
            import json

            try:
                self.text_buf = json.dumps(json.loads(self.text_buf), indent=2, ensure_ascii=False)
            except Exception as exc:  # noqa: BLE001
                self.log(f"⚠ JSON 解析失败：{exc}")

        imgui.same_line()
        imgui.begin_disabled(b is None or e is None)
        if imgui.button("应用到资源"):
            if b is not None and e is not None:
                try:
                    if is_tree:
                        import json

                        b.modify_typetree(e, json.loads(self.text_buf))
                    else:
                        b.modify_text(e, self.text_buf)
                    self.log(f"✓ {bname} / {e.display} 文本已更新")
                    self._text_cache.clear()
                    self.show_text_editor = False
                except Exception as exc:  # noqa: BLE001
                    self.log(f"✗ 应用失败：{exc}")
        imgui.end_disabled()
        imgui.same_line()
        if imgui.button("关闭"):
            self.show_text_editor = False

        imgui.separator()
        size = imgui.get_content_region_avail()
        _, self.text_buf = imgui.input_text_multiline(
            "##editor", self.text_buf, imgui.ImVec2(size.x, size.y - 8)
        )
        imgui.end()

    # ------------------------------------------------------------ 导出窗口

    def _draw_export_window(self) -> None:
        _center_next_window(imgui.ImVec2(760, 580))
        opened, self.show_export = imgui.begin("导出 / 生成产物", self.show_export)
        if not opened:
            imgui.end()
            return

        mods = self.sess.modified
        if not mods:
            _text_colored(WARN, "还没有任何改动，先改点东西再来。")
            imgui.separator()
        imgui.text(f"待导出：{len(mods)} 个包，{len(self.sess.modified_assets())} 处改动")
        imgui.separator()

        if imgui.begin_tab_bar("##exporttabs"):
            # ---------------------------------------------- UnityCache
            _flags = imgui.TabItemFlags_.set_selected if self.export_tab == "cache" else 0
            if imgui.begin_tab_item("UnityCache（推荐）", None, _flags)[0]:
                _text_colored(OK, "推到设备缓存目录即可生效，不用重装 APK。")
                _text_colored(DIM, f"目标：{paths.DEVICE_FILES_DIR}/UnityCache/")
                imgui.separator()
                imgui.set_next_item_width(-90)
                _, self.out_dir = imgui.input_text("输出目录", self.out_dir)
                imgui.same_line()
                if imgui.button("选…"):
                    got = self.pick_folder("选择输出目录", self.out_dir)
                    if got:
                        self.out_dir = got
                _, self.cache_full = imgui.checkbox(
                    "连未改动的包一起导出（换设备重建整个缓存用，体积大）", self.cache_full)
                imgui.separator()
                imgui.begin_disabled(not mods or self.busy())
                if imgui.button("生成 UnityCache 产物"):
                    self.start("生成 UnityCache 产物", self._do_cache)
                imgui.end_disabled()
                imgui.end_tab_item()

            # ---------------------------------------------- pmmod
            _flags = imgui.TabItemFlags_.set_selected if self.export_tab == "pmmod" else 0
            if imgui.begin_tab_item(".pmmod 模组包", None, _flags)[0]:
                _text_colored(DIM, "体积小，可分享。别人用本工具「应用模组」即可。")
                imgui.separator()
                imgui.set_next_item_width(-1)
                _, self.mod_name = imgui.input_text("模组名", self.mod_name)
                imgui.set_next_item_width(-1)
                _, self.mod_author = imgui.input_text("作者", self.mod_author)
                imgui.set_next_item_width(-1)
                _, self.mod_desc = imgui.input_text("说明", self.mod_desc)
                _, self.mod_prebuilt = imgui.checkbox(
                    "内嵌成品 bundle（免依赖，体积大）", self.mod_prebuilt)
                imgui.separator()
                imgui.begin_disabled(not mods or self.busy())
                if imgui.button("导出 .pmmod…"):
                    default = str(Path(self.out_dir) / f"{self.mod_name or 'mod'}.pmmod")
                    dest = self.pick_save("保存模组包", default, F_PMMOD)
                    if dest:
                        self.start("导出模组包", lambda: str(
                            self.sess.output_modpack(
                                dest, name=self.mod_name, author=self.mod_author,
                                description=self.mod_desc, prebuilt=self.mod_prebuilt)))
                imgui.end_disabled()
                imgui.end_tab_item()

            # ---------------------------------------------- APK
            _flags = imgui.TabItemFlags_.set_selected if self.export_tab == "apk" else 0
            if imgui.begin_tab_item("APK 重打包", None, _flags)[0]:
                d = self.sess.apk_diagnosis()
                if not d["has_apk"]:
                    _text_colored(WARN, "⚠ 你还没有打开任何 APK")
                elif d["not_in_apk"]:
                    _text_colored(
                        WARN,
                        f"⚠ 这 {len(d['not_in_apk'])} 个包不在你打开的 APK 里，打了也没用："
                        + "，".join(d["not_in_apk"][:8]),
                    )
                elif d["in_apk"]:
                    _text_colored(OK, f"✓ {len(d['in_apk'])} 个改动包都在 APK 内，能打进去。")
                else:
                    _text_colored(DIM, "还没有改动。")

                for a in d["advice"]:
                    _text_colored(ACCENT if not d["not_in_apk"] else DIM, "· " + a)

                if d["where"]:
                    if imgui.collapsing_header("每个改动包到底在哪##apkwhere"):
                        for name, locs in d["where"].items():
                            mark = "✓" if name in d["in_apk"] else "✗"
                            imgui.text(f"  {mark} {name}")
                            imgui.same_line()
                            _badge("在 " + "/".join(locs), OK if mark == "✓" else WARN)

                imgui.separator()
                imgui.set_next_item_width(-70)
                _, self.apk_src = imgui.input_text("原 APK", self.apk_src)
                imgui.same_line()
                if imgui.button("选##apksrc"):
                    got = self.pick_open("选择原 APK", F_APK, multi=False)
                    if got:
                        self.apk_src = got[0]
                        if not self.apk_out:
                            self.apk_out = str(Path(got[0]).with_name(Path(got[0]).stem + "-mod.apk"))
                imgui.set_next_item_width(-70)
                _, self.apk_out = imgui.input_text("输出 APK", self.apk_out)
                imgui.same_line()
                if imgui.button("选##apkout"):
                    got = self.pick_save("保存 APK", self.apk_out or "mod.apk", F_APK)
                    if got:
                        self.apk_out = got
                _, self.apk_sign = imgui.checkbox("重签名（必须，否则装不上）", self.apk_sign)
                _, self.apk_keystore = imgui.input_text("keystore", self.apk_keystore)
                st = apkbuild.toolchain_status()
                if not st["apksigner"] or not st["zipalign"]:
                    _text_colored(WARN, "⚠ 找不到 apksigner / zipalign，签名会失败")
                imgui.separator()
                imgui.begin_disabled(not mods or self.busy() or not self.apk_src)
                if imgui.button("重打包 + 签名"):
                    self.start("APK 重打包", self._do_apk)
                imgui.end_disabled()
                if not self.apk_src:
                    _text_colored(DIM, "先选一个原 APK。")
                imgui.end_tab_item()

            # ---------------------------------------------- CDN
            _flags = imgui.TabItemFlags_.set_selected if self.export_tab == "cdn" else 0
            if imgui.begin_tab_item("CDN 目录", None, _flags)[0]:
                _text_colored(DIM, "丢进自建服务器的 cdn/AssetBundles/<group>/Android/ 即可。")
                imgui.separator()
                imgui.begin_disabled(not mods or self.busy())
                if imgui.button("生成 CDN 目录"):
                    self.start("生成 CDN 目录", self._do_cdn)
                imgui.end_disabled()
                imgui.end_tab_item()

            imgui.end_tab_bar()
        imgui.end()

    # ------------------------------------------------------------ 无人值守自检

    def _autotest_step(self, f: int) -> None:
        # 没有数据源的时候，跟数据表有关的步骤跑不了（CI 的冒烟测试就没带源）。
        # 这里统一跳过，让「空手启动」也能跑完自检。
        if self.sess.source is None and f in (16, 17, 18, 19, 20, 21, 22, 23, 24, 25):
            if f == 24:
                print("[自检] 没打开数据源，跳过数据相关步骤", flush=True)
            return

        """把每个面板、每种资源类型都走一遍，用来在没人的时候抓 API 错误。"""
        try:
            if f == 2:
                self.open_source(self.initial_paths)
                self.log(f"[自检] 打开 {len(self.initial_paths)} 个路径")
            elif f == 3:
                self.sel_bundle, self.sel_pid = "appdata", None
            elif f == 4:
                b = self.cur_bundle()
                e = next((a for a in b.assets if a.type == "TextAsset"), None) if b else None
                if e:
                    self.sel_pid = e.path_id
            elif f == 5:
                self._autotest_find("preload", "Texture2D") or self._autotest_find("anime101", "Texture2D")
            elif f == 6:
                self._autotest_find("preload", "AudioClip")
            elif f == 7:
                for nm in ("ep802", "ep803", "ep807"):
                    if self._autotest_find(nm, "MonoBehaviour"):
                        break
            elif f == 8:
                b = self.sess.bundle("appdata", eager=True)
                e = next((a for a in b.assets if a.name == "GachaDefault"), None) if b else None
                if e:
                    self.sel_bundle, self.sel_pid = "appdata", e.path_id
                    b.modify_text(e, b.preview_text(e).replace('"drop_rates": [', '"drop_rates": [', 1))
                self.show_export = True
            elif f == 9:
                self.show_text_editor = True
                self.text_edit_target = (self.sel_bundle, self.sel_pid)
                self.text_buf = '{\n  "自检": "中文渲染测试",\n  "ok": true\n}'
            elif f == 10:
                self.type_filter = "Texture2D"
            elif f == 11:
                self.type_filter = "全部"
                self.asset_filter = "morty"
            elif f == 12:
                self.asset_filter = ""
                self.only_modified = True
            elif f == 13:
                self.only_modified = False
                self.show_text_editor = False
            elif f == 14:
                # 收尾摆一个好看的画面：选一张贴图
                self.show_export = False
                self._autotest_find("anime101", "Texture2D")
            elif f == 15:
                self.show_export = os.environ.get("PM_MODKIT_SHOT_EXPORT") == "1"
            elif f == 16:
                self.show_export = False
                self.show_new_entry = True
            elif f == 17:
                self.ne_kind = "morty"
                self.ne_clone = "MortyDefault"
                self.ne_loaded = ""
                self._ne_load_donor()
                assert self.ne_table_fields, "新增条目向导没读到模板字段"
                assert self.ne_id, "没给出默认新 ID"
                n = sum(len(v) for v in self.ne_table_fields.values())
                print(f"[自检] 新增条目向导：{len(self.ne_table_fields)} 张表 / {n} 个字段，默认 ID={self.ne_id}",
                      flush=True)
            elif f == 20:
                # 模拟拖放：往队列里塞两个包，下一帧应该被消费掉
                DROP_QUEUE.extend([self.initial_paths[0], self.initial_paths[-1]])
                print(f"[自检] 拖放队列塞入 {len(DROP_QUEUE)} 个", flush=True)
            elif f == 21:
                # 图鉴要能真的把 15 帧读出来
                self.show_gallery = True
                self._gal_reset()
                self.gal_clone = "MortyToxicMetal"
                info = self._gal_morty_info("MortyToxicMetal")
                self.gal_info = info
                assert info.get("assetid"), "图鉴拿不到 assetid"
                assert info["row"].get("hpbase"), "图鉴读不到数值"
                self._gal_load_art(info)
                self.gal_loaded = self.gal_clone
                assert len(self.gal_frames) >= 10, (
                    f"图鉴只读到 {len(self.gal_frames)} 帧美术")
                print(f"[自检] 图鉴读到 {len(self.gal_frames)} 帧美术"
                      f"（{info['assetid']} @ {info['bundle']}）", flush=True)
            elif f == 22:
                self.show_field_help = False
                if os.environ.get("PM_MODKIT_SHOT_GAL") == "1":
                    self.show_export = False
                    return
                if os.environ.get("PM_MODKIT_SHOT_NE") == "1":
                    # 要截「新增条目」的图，别关它；顺便把图鉴收起来
                    self.show_export = False
                    self.show_gallery = False
                    self.show_check = False
                    self.ne_kind = "morty"
                    self.ne_clone = "MortyToxicMetal"
                    self.ne_loaded = ""
                    self._ne_load_donor()
                    # 截图时可以指定看哪个类型
                    _k = os.environ.get("PM_MODKIT_SHOT_NE_KIND", "morty")
                    self.ne_kind = _k
                    self.ne_loaded = ""
                    if _k == "item":
                        self.ne_clone = "ItemSerum"
                        self.ne_id = "ItemMyPotion"
                        self.ne_name_zh = "我的药水"
                    elif _k == "attack":
                        self.ne_clone = "AttackStruggle"
                        self.ne_id = "AttackMyMove"
                        self.ne_name_zh = "我的招式"
                    else:
                        self.ne_clone = "MortyToxicMetal"
                        self.ne_id = "MortyMyOwnGuy"
                        self.ne_name_zh = "我的原创莫蒂"
                        self.ne_full_char = True
                        self.ne_images = {"Icon": "/tmp/newchar/Icon.png"}
                    self._ne_load_donor()
                    self._ne_scan_frames()
                    return
                self.show_new_entry = False
                if os.environ.get("PM_MODKIT_SHOT_CHECK") == "1":
                    # 摆出体检画面给截图用
                    self._run_check()
                else:
                    # 摆出「包不在 APK 里」的诊断画面给截图用
                    self.show_export = True
                    self.export_tab = "apk"
            elif f == 25:
                if any(os.environ.get(k) == "1" for k in (
                        "PM_MODKIT_SHOT_APK", "PM_MODKIT_SHOT_CHECK", "PM_MODKIT_SHOT_NE",
                        "PM_MODKIT_SHOT_GAL")):
                    return
                self.show_new_entry = False
                self.show_field_help = True
                miss = []
                from modkit import entries as E
                from modkit import fields as FF
                for kk, kd in E.KINDS.items():
                    for sp in kd.tables:
                        try:
                            row = E.entry_of_table(self.sess, kk, sp, E.list_ids(self.sess, kk)[0])
                        except Exception:
                            continue
                        if isinstance(row, dict):
                            miss += [(sp.label, x) for x in row if FF.doc_for(sp.label, x) is None]
                assert not miss, f"有字段没有中文对照：{miss}"
                print("[自检] 字段对照完整，无遗漏", flush=True)
            elif f == 18:
                # 换成道具看看
                self.ne_kind = "item"
                self.ne_clone = "ItemSerum"
                self.ne_loaded = ""
                self._ne_load_donor()
                assert self.ne_table_fields, "道具模板没读到"
                print(f"[自检] 道具模板 {len(self.ne_table_fields)} 张表", flush=True)
            elif f == 19:
                if os.environ.get("PM_MODKIT_SHOT_NE") != "1":
                    self.show_new_entry = False
                else:
                    self.ne_kind = "morty"
                    self.ne_clone = "MortyDefault"
                    self.ne_loaded = ""
                    self._ne_load_donor()
                    self.ne_name_zh = "我的莫蒂"
            elif f >= 28:
                print(f"[自检] 渲染 {f} 帧无异常，退出", flush=True)
                hello_imgui.get_runner_params().app_shall_exit = True
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            hello_imgui.get_runner_params().app_shall_exit = True
            raise

    def _autotest_find(self, bundle_name: str, type_name: str) -> bool:
        b = self.sess.bundle(bundle_name, eager=True)
        if b is None or not b.ok:
            return False
        e = next((a for a in b.assets if a.type == type_name), None)
        if e is None:
            return False
        self.sel_bundle, self.sel_pid = bundle_name, e.path_id
        return True

    # ------------------------------------------------------------ 体检

    def _run_check(self) -> None:
        if self.sess.source is None:
            self.log("✗ 先打开源")
            return
        rep = self.sess.check()
        self.check_report = rep
        self.show_check = True
        self.log(rep.summary())
        for i in rep.issues[:30]:
            self.log("  " + i.line())

    def _draw_check(self) -> None:
        _center_next_window(imgui.ImVec2(760, 560))
        opened, self.show_check = imgui.begin("数据表体检", self.show_check)
        if not opened:
            imgui.end()
            return
        rep = getattr(self, "check_report", None)
        if rep is None:
            imgui.text("还没跑过。点工具栏的「体检」。")
            imgui.end()
            return

        if rep.ok:
            _text_colored(OK, rep.summary())
            imgui.separator()
            _text_colored(DIM, "数据表没发现问题：主键唯一、编号不冲突、跨表引用齐全。")
            imgui.end()
            return

        _text_colored(WARN if rep.errors else DIM, rep.summary())
        _text_colored(
            DIM,
            "主键重复这类问题会让游戏**直接起不来**，而 JSON 上肉眼看不出来 ——"
            "复制一条现成的来改、只改了键没改里面的 id，表面看完全正常。",
        )
        imgui.separator()
        imgui.begin_child("##checklist", imgui.ImVec2(0, -46), True)
        for i in rep.issues:
            _text_colored((1, .45, .45, 1) if i.level == "E" else WARN, i.line())
        imgui.end_child()
        imgui.separator()
        imgui.begin_disabled(self.busy() or not rep.errors)
        if imgui.button("自动修（主键/编号/缺条目）"):
            self.start("自动修数据表", self._do_fix)
        imgui.end_disabled()
        imgui.same_line()
        if imgui.button("关闭"):
            self.show_check = False
        imgui.end()

    def _do_fix(self) -> str:
        from modkit import validate as V

        before = self.sess.check()
        done = V.repair(self.sess, before)
        after = self.sess.check()
        lines = ["修了这些："] + ["  · " + d for d in done] + ["", "修之后：" + after.summary()]
        for i in after.issues[:10]:
            lines.append("  " + i.line())
        self.check_report = after
        self._img_cache.clear()
        self._text_cache.clear()
        return "\n".join(lines)

    # ------------------------------------------------------------ 字段对照表

    def _draw_field_help(self) -> None:
        """把中文字典摆出来给用户查。"""
        _center_next_window(imgui.ImVec2(860, 700))
        opened, self.show_field_help = imgui.begin("字段中英文对照（看不懂的字段来这查）", self.show_field_help)
        if not opened:
            imgui.end()
            return
        _text_colored(DIM, "游戏数据表里的字段名都是英文技术标识，这里是它们的中文意思。")
        imgui.separator()

        imgui.begin_child("##fieldhelp", imgui.ImVec2(0, 0), True)
        for i, table in enumerate(sorted(F.FIELD_DOCS)):
            docs = F.FIELD_DOCS[table]
            if i == 0:
                # 第一张表默认展开，让人一眼看懂这个窗口是干嘛的
                imgui.set_next_item_open(True, imgui.Cond_.first_use_ever)
            if not imgui.collapsing_header(f"{table}   —— {F.explain_table(table)}##fh_{table}"):
                continue
            for name, d in docs.items():
                imgui.text(f"{d.label}")
                imgui.same_line()
                _badge(name, ACCENT)
                if d.common:
                    imgui.same_line()
                    _badge("常改", WARN)
                if d.auto:
                    imgui.same_line()
                    _badge("自动", DIM)
                if d.help:
                    _text_colored(DIM, "    " + d.help)
                if d.values:
                    _text_colored(DIM, "    取值：" + "，".join(
                        f"{k or '(空)'}={v}" for k, v in d.values.items()))
        imgui.end_child()
        imgui.end()

    # ------------------------------------------------------------ 新增条目向导

    def _ne_ensure_state(self) -> None:
        if not hasattr(self, "ne_kind"):
            self.ne_kind = "morty"
            self.ne_clone = ""
            self.ne_id = ""
            self.ne_name_zh = ""
            self.ne_name_en = ""
            self.ne_desc = ""
            self.ne_asset = ""
            self.ne_gacha = False
            self.ne_pool = 0
            self.ne_table_fields = {}
            self.ne_table_idx = 0
            self.ne_only_common = False
            self.ne_expand_fields = True
            self.ne_full_char = False
            self.ne_filter = "all"
            self.ne_sort = 0
            self.ne_stats = {}
            self.ne_images = {}
            self.ne_frames = []
            self.ne_frame_msg = ""
            self.ne_donor_asset = ""
            self.ne_donor_bundle = ""
            self._ne_img_cache = {}
            self.ne_stats_base = {}
            self._ne_rows_cache = None
            self.ne_clone_filter = ""
            self.ne_msg = ""
            self.ne_loaded = ""
            self._ne_name_cache = {}

    def _ne_kind(self):
        from modkit import entries as E

        return E.KINDS.get(self.ne_kind)

    def _ne_loc_names(self, section):
        """id -> 中文名，给下拉列表显示用。"""
        self._ne_ensure_state()
        if section in self._ne_name_cache:
            return self._ne_name_cache[section]
        out = {}
        try:
            b = self.sess.bundle("text", eager=True)
            e = next((a for a in b.assets if a.name == "ZH_CN" and a.type == "TextAsset"), None)
            if e is not None:
                data = json.loads(b.preview_text(e))
                for k, v in (data.get(section) or {}).items():
                    if isinstance(v, dict):
                        out[k] = str(v.get("name", ""))
        except Exception:  # noqa: BLE001
            pass
        self._ne_name_cache[section] = out
        return out

    def _ne_assets(self):
        """BundleAssetAssignment 里的全部 assetid。"""
        from modkit import entries as E

        try:
            _, _, data = E._get_json(
                self.sess, E.TableSpec("appdata", "BundleAssetAssignment", "dict")
            )
            return sorted({str(v.get("assetid") or v.get("id") or "")
                           for v in data.values() if isinstance(v, dict)} - {""})
        except Exception:  # noqa: BLE001
            return []

    def _ne_load_donor(self) -> None:
        """按当前选的模板，把**每张表**的字段都读进来（可以分开编辑）。"""
        from modkit import entries as E

        if not self.ne_clone:
            return
        key = f"{self.ne_kind}:{self.ne_clone}"
        if key == self.ne_loaded:
            return
        kind = self._ne_kind()
        if kind is None:
            return
        self.ne_table_fields = {}
        for spec in kind.tables:
            try:
                row = E.entry_of_table(self.sess, self.ne_kind, spec, self.ne_clone)
            except Exception:  # noqa: BLE001
                row = None
            if isinstance(row, dict):
                self.ne_table_fields[spec.label] = {
                    k: ("" if v is None else (v if isinstance(v, str)
                                             else json.dumps(v, ensure_ascii=False)))
                    for k, v in row.items()
                }
        if not self.ne_table_fields:
            self.ne_msg = f"✗ 读不出模板 {self.ne_clone}"
            return
        self.ne_loaded = key
        self.ne_table_idx = 0
        # 四项基础值单独拎出来给滑条用
        self.ne_stats_base = self._ne_stats_from_fields()
        # 换模板必须重置数值 —— 否则滑条还停在上一个模板的值上，
        # 和左边 BEFORE 显示的模板数值对不上（踩过）
        self.ne_stats = dict(self.ne_stats_base)
        first = next(iter(self.ne_table_fields.values()))
        self.ne_asset = str(first.get("assetid") or first.get("asset_id") or "")
        if not self.ne_id:
            self.ne_id = kind.id_prefix + "MyCustom"
        self.ne_msg = ""
        loc = self._ne_loc_names(kind.loc_section)
        if not self.ne_name_zh and loc.get(self.ne_clone):
            self.ne_name_zh = loc[self.ne_clone] + "（改）"

    def _ne_apply(self, dry_run: bool) -> str:
        """普通新增走 entries.add_entry；勾了「完全新增角色」走 character.add_character。"""
        from modkit import character as CH
        from modkit import entries as E

        kind = self._ne_kind()
        if kind is None or not self.ne_clone:
            return "✗ 先选一个模板"
        overrides = {t: dict(f) for t, f in self.ne_table_fields.items()}

        if getattr(self, "ne_full_char", False) and self.ne_kind == "morty":
            images = dict(getattr(self, "ne_images", {}) or {}) if self.ne_full_char else {}
            res = CH.add_character(
                self.sess, self.ne_id.strip(), self.ne_clone,
                kind=self.ne_kind,
                images=images,
                names={"ZH_CN": self.ne_name_zh.strip(), "EN": self.ne_name_en.strip()},
                descriptions={"ZH_CN": self.ne_desc.strip(), "EN": self.ne_desc.strip()},
                overrides=overrides,
                add_to_gacha=self.ne_gacha, gacha_pool=self.ne_pool,
                dry_run=dry_run,
            )
            return res["message"]

        rep = E.add_entry(
            self.sess,
            self.ne_kind,
            self.ne_id.strip(),
            self.ne_clone,
            names={"ZH_CN": self.ne_name_zh.strip(), "EN": self.ne_name_en.strip()},
            descriptions={"ZH_CN": self.ne_desc.strip(), "EN": self.ne_desc.strip()},
            overrides=overrides,
            assetid=self.ne_asset or None,
            add_to_gacha=self.ne_gacha,
            gacha_pool=self.ne_pool,
            dry_run=dry_run,
        )
        return rep.summary()
    # ------------------------------------------------------------ 新增条目 v2
    #
    # 设计参考：更少点击 / 更快检索 / 更顺滑的流程。
    # 落地成四件事：
    #   1. 「快速开始」预设 —— 一下把整个表单配好，不用一项项点
    #   2. 数值用滑条 + 快捷调节，不用去认 hpbase 这种字段名
    #   3. 模板检索带分类筛选和排序，选中后立刻出 BEFORE/AFTER 对比
    #   4. 步骤条 + 底部固定的操作条，随时知道自己在第几步

    #: 「快速开始」预设
    NE_PRESETS = [
        ("clone", "照抄一只，只改名字", "CLONE & RENAME",
         "把你选的模板原样复制，只换 id 和名字。最保险，数值一点不动。"),
        ("strong", "强化版（属性 ×1.5）", "BUFFED ×1.5",
         "四项基础值乘 1.5，顺便加进抽卡池。"),
        ("max", "属性拉满", "MAX STATS",
         "四项都拉到现有数据的最大值（体力150/攻击140/防御135/速度155）。"),
        ("original", "完全原创（带独立美术）", "ORIGINAL ART",
         "连 15 帧美术一起复制改名、放进新包。这只角色有自己独立的形象。"),
    ]

    #: 模板筛选（就留这几个最常用的）
    NE_FILTERS = [
        ("all", "全部", None),
        ("hp", "高体力", ("hpbase", 110)),
        ("atk", "高攻击", ("attackbase", 110)),
        ("spd", "高速", ("speedbase", 110)),
    ]

    #: 基础值滑条的上限（实测最大 150/140/135/155，留点余量）
    STAT_FIELDS = [
        ("hpbase", "体力", "HP", 255),
        ("attackbase", "攻击", "ATK", 255),
        ("defencebase", "防御", "DEF", 255),
        ("speedbase", "速度", "SPD", 255),
    ]

    def _ne_stats_from_fields(self) -> dict:
        """从模板字段里取出四项基础值。"""
        from modkit import entries as E

        kind = self._ne_kind()
        out: dict[str, int] = {}
        if kind is None:
            return out
        fields = self.ne_table_fields.get(kind.tables[0].label, {})
        for k, _l, _e, lim in self.STAT_FIELDS:
            try:
                out[k] = int(float(fields.get(k, 0) or 0))
            except (TypeError, ValueError):
                out[k] = 0
        return out

    def _ne_stats_commit(self) -> None:
        """把滑条的值写回字段表（出参走的是 overrides）。"""
        from modkit import entries as E

        kind = self._ne_kind()
        if kind is None:
            return
        label = kind.tables[0].label
        fields = self.ne_table_fields.setdefault(label, {})
        for k, v in (self.ne_stats or {}).items():
            fields[k] = str(v)

    def _draw_new_entry(self) -> None:
        self._ne_ensure_state()
        from modkit import entries as E

        _center_next_window(imgui.ImVec2(1120, 960))
        opened, self.show_new_entry = imgui.begin(
            "新增条目", self.show_new_entry)
        if not opened:
            imgui.end()
            return

        if self.sess.source is None:
            _text_colored(WARN, "先打开数据源（口蘑数据包.zip 或加强版 APK）。")
            imgui.end()
            return

        # ---------------- 标题
        imgui.text("新增条目")
        imgui.same_line()
        _text_colored(DIM, "（克隆一条现有的再改，字段和类型天然齐全）")
        imgui.separator()
        # 主体可滚动，底部操作条固定 —— 小屏也够得着「创建」。
        # 高度按**视口**算：hello_imgui 给窗口开了 auto-resize，按窗口剩余空间
        # 算会越算越大、把底部推出屏幕（踩过）。
        vp = imgui.get_main_viewport()
        # 窗口自身开销（标题 + 底部操作条 + 边距）大约 190
        body_h = min(880.0, max(200.0, vp.work_size.y - 170))
        imgui.begin_child("##nebody", imgui.ImVec2(0, body_h), True)
        # ---------------- ① 类型
        _step(1, "选类型", done=True)
        kinds = list(E.KINDS)
        avail = imgui.get_content_region_avail().x
        cw = max(90.0, (avail - 24) / 3)
        for i, k in enumerate(kinds):
            on = self.ne_kind == k
            if on:
                imgui.push_style_color(imgui.Col_.button, (0.28, 0.24, 0.06, 1.0))
                imgui.push_style_color(imgui.Col_.text, YELLOW)
            if imgui.button(f"{E.KINDS[k].label}##kind{k}", imgui.ImVec2(cw, 0)):
                self.ne_kind = k
                self.ne_clone, self.ne_loaded, self.ne_id = "", "", ""
                self.ne_table_fields, self.ne_msg = {}, ""
                self.ne_stats, self.ne_stats_base = {}, {}
            if on:
                imgui.pop_style_color(2)
            if i < len(kinds) - 1:
                imgui.same_line()

        kind = self._ne_kind()
        assert kind is not None

        try:
            ids = E.list_ids(self.sess, self.ne_kind)
        except Exception as exc:  # noqa: BLE001
            _text_colored(WARN, f"读不到{kind.label}列表：{exc}")
            imgui.end_child()
            imgui.end()
            return

        # ---------------- 两栏：左挑模板，右填参数
        #
        # 一行到底的话内容太长约等于一直在滚。分成两栏之后，
        # 左边专心挑、右边专心填，竖着占的地方少一半。
        tbl_flags = (imgui.TableFlags_.resizable | imgui.TableFlags_.sizing_stretch_prop
                     | imgui.TableFlags_.no_saved_settings)
        if not imgui.begin_table("##necols", 2, tbl_flags):
            imgui.end_child()
            imgui.end()
            return
        imgui.table_setup_column("模板", imgui.TableColumnFlags_.width_fixed, 340)
        imgui.table_setup_column("详情", imgui.TableColumnFlags_.width_stretch)

        imgui.table_next_row()
        imgui.table_set_column_index(0)
        _step(2, "选模板", done=bool(self.ne_clone), active=True)
        iw = imgui.get_content_region_avail().x
        # 搜索框占满，清空按钮跟在右边
        imgui.set_next_item_width(iw - 34)
        _, self.ne_clone_filter = imgui.input_text_with_hint(
            "##nefilter", f"搜 id 或中文名（共 {len(ids)} 条）", self.ne_clone_filter)
        imgui.same_line()
        if imgui.small_button("×"):
            self.ne_clone_filter = ""

        loc = self._ne_loc_names(kind.loc_section)
        rows = self._ne_template_rows(ids, loc)

        # 分类筛选
        for i, (key, label, _x) in enumerate(self.NE_FILTERS):
            on = self.ne_filter == key
            if on:
                imgui.push_style_color(imgui.Col_.button, (0.20, 0.20, 0.26, 1.0))
                imgui.push_style_color(imgui.Col_.text, YELLOW)
            if imgui.button(f"{label}##flt{key}"):
                self.ne_filter = key
            if on:
                imgui.pop_style_color(2)
            imgui.same_line()
        imgui.set_next_item_width(0)
        ch, self.ne_sort = imgui.combo("排序", self.ne_sort,
                                       ["编号", "体力", "攻击", "速度"])

        shown = self._ne_apply_filter(rows)
        opt = [f"{r['zh']}  ·  {r['id']}" if r["zh"] else r["id"] for r in shown[:400]]
        idxs = [r["id"] for r in shown[:400]]

        left_w = max(260.0, iw * 0.46)
        # 高度用**窗口体高**算。在表格单元格里用 get_content_region_avail() 是
        # 循环依赖（行高由内容决定，内容又想撑满行高），会把整行压塌。
        list_h = max(200.0, body_h - 150)
        imgui.begin_child("##nelist", imgui.ImVec2(0, list_h), True)
        for i, r in enumerate(shown[:400]):
            on = self.ne_clone == r["id"]
            if on:
                imgui.push_style_color(imgui.Col_.header, (0.28, 0.24, 0.06, 1.0))
            if imgui.selectable(f"{r['zh'] or r['id']}##t{i}", on, 0, imgui.ImVec2(0, 0))[0]:
                self.ne_clone = r["id"]
                self.ne_loaded = ""
                self.ne_images = {}
                self.ne_frames = []
                self._ne_img_cache = {}
            if on:
                imgui.pop_style_color()
            imgui.same_line()
            _mono(r["id"])
            # 只有莫蒂才有体力/攻击这些数值；道具和技能显示别的
            if self.ne_kind == "morty":
                imgui.same_line(max(0.0, imgui.get_window_width() - 92))
                _text_colored(DIM, f"{r['hp']:>3}/{r['atk']:>3}/{r['spd']:>3}")
            else:
                tail = self._ne_row_tail(r)
                if tail:
                    imgui.same_line(max(0.0, imgui.get_window_width() - 130))
                    _text_colored(DIM, tail)
        if len(shown) > 400:
            _text_colored(DIM, f"…还有 {len(shown) - 400} 条，缩小搜索范围")
        imgui.end_child()

        # ---------------- 右栏
        imgui.table_set_column_index(1)
        self._draw_before_after(rows, loc)
        imgui.separator()
        self._ne_load_donor()
        if not self.ne_table_fields:
            _text_colored(DIM, "先选一个模板条目。")
            if self.ne_msg:
                imgui.separator()
                _text_colored(WARN, self.ne_msg)
            imgui.end_table()
            imgui.end_child()
            imgui.end()
            return

        # ---------------- ③ 起名
        _step(3, "起名", done=bool(self.ne_name_zh), active=True)
        imgui.set_next_item_width(-1)
        _, self.ne_id = imgui.input_text("##neid", self.ne_id)
        imgui.same_line()
        _mono("ID")
        _text_colored(DIM, f"以 {kind.id_prefix} 开头，字母数字下划线")
        half = (imgui.get_content_region_avail().x - 16) * 0.5
        imgui.set_next_item_width(half)
        _, self.ne_name_zh = imgui.input_text("##nezh", self.ne_name_zh)
        imgui.same_line()
        imgui.set_next_item_width(half)
        _, self.ne_name_en = imgui.input_text("##nen", self.ne_name_en)
        imgui.same_line()
        _mono("中文 / EN")
        imgui.spacing()
        self._draw_id_preview()

        # ---------------- ④ 调数值
        if self.ne_kind == "morty":
            _step(4, "调数值", done=True, active=True)
            self._draw_stat_sliders()

        # ---------------- 开关
        imgui.spacing()
        _step(4 if self.ne_kind == "morty" else 3,
              "美术" if self.ne_kind in ("morty", "item") else "选项", done=True)
        if self.ne_kind in ("morty", "item"):
            self._draw_art_section()
        imgui.spacing()
        # 只有表里真有 includeingacha / in_gacha 的类型才给这个选项（技能没有）
        if self._ne_kind() is not None and self._ne_kind().has_gacha:
            _, self.ne_gacha = imgui.checkbox("加进抽卡池", self.ne_gacha)
        else:
            self.ne_gacha = False
        if self.ne_gacha:
            imgui.same_line()
            pools = E.gacha_pools(self.sess)
            imgui.set_next_item_width(230)
            pi = min(self.ne_pool, max(0, len(pools) - 1))
            ch, pi = imgui.combo("##nepool", pi, pools or ["（没有卡池）"])
            self.ne_pool = pi

        # ---------------- 高级：全部字段
        if imgui.collapsing_header("高级：全部字段（想精确控制再来）##nefields"):
            for i, spec in enumerate(kind.tables):
                if i:
                    imgui.separator()
                _text_colored(YELLOW, f"▸ {spec.label}")
                imgui.same_line()
                _mono("  " + F.explain_table(spec.label))
                _, self.ne_only_common = imgui.checkbox(
                    f"只看常改字段（★）##neoc{i}", self.ne_only_common)
                imgui.begin_child(f"##nef{i}", imgui.ImVec2(0, 170), True)
                for fn, val in list(self.ne_table_fields.get(spec.label, {}).items()):
                    d = F.doc_for(spec.label, fn)
                    if self.ne_only_common and not (d and d.common):
                        continue
                    mark = "★ " if (d and d.common) else ""
                    auto = "（自动）" if (d and d.auto) else ""
                    imgui.text(f"{mark}{d.label if d else fn}{auto}")
                    if d and d.help:
                        _tip(d.help)
                    imgui.same_line()
                    _mono(fn)
                    meaning = F.explain_value(spec.label, fn, val)
                    if meaning:
                        imgui.same_line()
                        _text_colored(GREEN, f"= {meaning}")
                    if d and d.values:
                        keys = list(d.values)
                        cur = keys.index(val) if val in keys else -1
                        imgui.set_next_item_width(-1)
                        c2, i2 = imgui.combo(f"##nv_{spec.label}_{fn}", cur,
                                             [f"{k}　{d.values[k]}" for k in keys])
                        if c2 and i2 >= 0:
                            self.ne_table_fields[spec.label][fn] = keys[i2]
                    else:
                        imgui.set_next_item_width(-1)
                        c2, v2 = imgui.input_text(f"##nv_{spec.label}_{fn}", val)
                        if c2:
                            self.ne_table_fields[spec.label][fn] = v2
                imgui.end_child()

        imgui.end_table()
        imgui.end_child()
        # ---------------- 底部操作条（固定，不随滚动跑）
        imgui.separator()
        if self.ne_msg:
            imgui.begin_child("##nemsg", imgui.ImVec2(0, 110), True)
            for line in self.ne_msg.split("\n"):
                col = GREEN if line.startswith("✓") else (
                    WARN if ("✗" in line or "⚠" in line) else DIM)
                _text_colored(col, line)
            imgui.end_child()
        imgui.begin_disabled(self.busy())
        if imgui.button("预演（不写入）", imgui.ImVec2(150, 0)):
            self.ne_msg = self._ne_apply(dry_run=True)
        imgui.same_line()
        imgui.push_style_color(imgui.Col_.button, (0.16, 0.34, 0.20, 1.0))
        if imgui.button("创建", imgui.ImVec2(150, 0)):
            self.ne_msg = self._ne_apply(dry_run=False)
            if self.ne_msg.startswith("✓"):
                self._img_cache.clear()
                self._text_cache.clear()
                self._ne_name_cache = {}
        imgui.pop_style_color()
        imgui.end_disabled()
        imgui.same_line()
        if imgui.button("关闭"):
            self.show_new_entry = False
        imgui.end()

    # ------------------------------------------------------------ 角色图鉴（图形化）
    #
    # 把「数据」变成「看得见的东西」：直接显示这只莫蒂的立绘、15 帧动作、
    # 数值条、技能列表。光看 JSON 很难判断自己改出来的是个什么角色。

    #: 图鉴里按这个顺序摆 15 帧
    GAL_ORDER = [
        ("Front", "正面"), ("Back", "背面"), ("Icon", "图标"),
        ("Down_1", "朝下1"), ("Down_2", "朝下2"), ("Down_3", "朝下3"), ("Down_4", "朝下4"),
        ("Side_1", "朝侧1"), ("Side_2", "朝侧2"), ("Side_3", "朝侧3"), ("Side_4", "朝侧4"),
        ("Up_1", "朝上1"), ("Up_2", "朝上2"), ("Up_3", "朝上3"), ("Up_4", "朝上4"),
    ]

    def _gal_reset(self) -> None:
        self.gal_clone = ""
        self.gal_filter = ""
        self.gal_frames = {}
        self.gal_icon = None
        self.gal_loaded = ""
        self.gal_msg = ""
        self.gal_info = {}

    def _gal_morty_info(self, morty_id: str) -> dict:
        """取一只莫蒂的信息：数值、属性、技能、以及美术在哪个包。"""
        from modkit import entries as E

        row = E.entry_of_table(self.sess, "morty", E.MORTY_TABLES[0], morty_id)
        if not isinstance(row, dict):
            return {}
        assetid = row.get("assetid") or ""
        bundle = ""
        try:
            b = self.sess.bundle("appdata", eager=True)
            e = next((a for a in b.assets if a.name == "BundleAssetAssignment"), None)
            if e is not None:
                baa = json.loads(b.preview_text(e))
                bundle = str((baa.get(assetid) or {}).get("version") or "")
        except Exception:  # noqa: BLE001
            pass
        return {"row": row, "assetid": assetid, "bundle": bundle}

    def _gal_load_art(self, info: dict) -> None:
        """把 15 帧和图标读成图片。"""
        self.gal_frames = {}
        self.gal_icon = None
        assetid, bundle = info.get("assetid"), info.get("bundle")
        if not assetid or not bundle:
            self.gal_msg = "这只莫蒂没有登记美术包"
            return
        try:
            b = self.sess.bundle(bundle, eager=True)
        except Exception as exc:  # noqa: BLE001
            self.gal_msg = f"读不到美术包 {bundle}：{exc}"
            return
        if b is None or not b.ok:
            self.gal_msg = f"读不到美术包 {bundle}"
            return
        want = {f"{assetid}{f}" for f, _zh in self.GAL_ORDER}
        got = 0
        for e in b.assets:
            if e.type != "Texture2D" or e.name not in want:
                continue
            key = (bundle, e.path_id, 1 if e.path_id in b.dirty else 0)
            arr = self._img_cache.get(key)
            if arr is None:
                try:
                    img = b.preview_image(e)
                except Exception:  # noqa: BLE001
                    img = None
                if img is None:
                    continue
                r = min(1.0, 140.0 / max(img.size))
                if r < 1.0:
                    img = img.resize((max(1, int(img.width * r)), max(1, int(img.height * r))),
                                     Image.LANCZOS)
                arr = np.ascontiguousarray(np.array(img.convert("RGBA")))
                self._img_cache[key] = arr
            frame = e.name[len(assetid):]
            self.gal_frames[frame] = arr
            got += 1
        self.gal_msg = "" if got else f"包里没找到 {assetid} 的美术"
        # numpy 数组不能直接当布尔用
        icon = self.gal_frames.get("Icon")
        self.gal_icon = icon if icon is not None else self.gal_frames.get("Front")

    def _draw_gallery(self) -> None:
        vp = imgui.get_main_viewport()
        _center_next_window(imgui.ImVec2(min(1120.0, vp.work_size.x - 60),
                                         min(820.0, vp.work_size.y - 60)))
        opened, self.show_gallery = imgui.begin("角色图鉴", self.show_gallery)
        if not opened:
            imgui.end()
            return
        if self.sess.source is None:
            _text_colored(WARN, "先打开数据源。")
            imgui.end()
            return

        from modkit import entries as E

        try:
            ids = E.list_ids(self.sess, "morty")
        except Exception as exc:  # noqa: BLE001
            _text_colored(WARN, f"读不到莫蒂列表：{exc}")
            imgui.end()
            return
        loc = self._ne_loc_names("Morty")

        iw = imgui.get_content_region_avail().x
        imgui.set_next_item_width(260)
        _, self.gal_filter = imgui.input_text_with_hint(
            "##galf", f"搜 id 或中文名（{len(ids)} 只）", self.gal_filter)
        imgui.same_line()
        _text_colored(DIM, "点左边选一只，这里直接显示它的立绘和 15 帧动作")

        f = (self.gal_filter or "").strip().lower()
        shown = [i for i in ids if not f or f in i.lower() or f in (loc.get(i) or "").lower()]

        left = max(240.0, iw * 0.30)
        imgui.begin_child("##gallist", imgui.ImVec2(left, -30), True)
        for i, mid in enumerate(shown[:300]):
            on = self.gal_clone == mid
            if on:
                imgui.push_style_color(imgui.Col_.header, (0.28, 0.24, 0.06, 1.0))
            if imgui.selectable(f"{loc.get(mid) or mid}##g{i}", on, 0,
                                imgui.ImVec2(0, 0))[0]:
                self.gal_clone = mid
                self.gal_loaded = ""
            if on:
                imgui.pop_style_color()
            imgui.same_line()
            _mono(mid)
        if len(shown) > 300:
            _text_colored(DIM, f"…还有 {len(shown) - 300} 只")
        imgui.end_child()

        imgui.same_line()
        imgui.begin_child("##galview", imgui.ImVec2(0, -30), True)
        if not self.gal_clone:
            _text_colored(DIM, "左边挑一只。")
        else:
            if self.gal_loaded != self.gal_clone or not getattr(self, "gal_info", None):
                info = self._gal_morty_info(self.gal_clone)
                self.gal_info = info
                self._gal_load_art(info)
                self.gal_loaded = self.gal_clone
            self._draw_gallery_body(loc)
        imgui.end_child()
        imgui.end()

    def _draw_gallery_body(self, loc: dict) -> None:
        info = getattr(self, "gal_info", {}) or {}
        row = info.get("row") or {}

        def num(f, d=0):
            try:
                return int(float(row.get(f, d) or 0))
            except (TypeError, ValueError):
                return d

        # ---- 左边大图 + 右边数值
        imgui.begin_group()
        if self.gal_icon is not None:
            h, w = self.gal_icon.shape[:2]
            scale = min(1.0, 180.0 / max(w, h))
            p = immvision.ImageParams()
            p.image_display_size = (int(w * scale), int(h * scale))
            p.show_options_button = False
            p.show_options_panel = False
            p.show_pixel_info = False
            p.show_image_info = False
            p.show_zoom_buttons = False
            p.show_school_paper_background = False
            immvision.image("##galicon", self.gal_icon, p)
        else:
            _text_colored(DIM, "（没有图标）")
        imgui.end_group()

        imgui.same_line(0, 18)
        imgui.begin_group()
        imgui.text(loc.get(self.gal_clone, "") or self.gal_clone)
        _mono(self.gal_clone)
        _text_colored(DIM, f"美术包 {info.get('bundle') or '（无）'} · assetid {info.get('assetid') or '（无）'}")
        imgui.spacing()
        from modkit import fields as F

        total = 0
        for key, zh, en in [("hpbase", "体力", "HP"), ("attackbase", "攻击", "ATK"),
                            ("defencebase", "防御", "DEF"), ("speedbase", "速度", "SPD")]:
            v = num(key)
            total += v
            imgui.text(zh)
            imgui.same_line(52)
            _mono(en)
            imgui.same_line(96)
            _bar(min(1.0, v / 200.0), 150, 7, YELLOW)
            imgui.same_line()
            imgui.text(str(v))
        imgui.text("总和")
        imgui.same_line(52)
        _mono("TOTAL")
        imgui.same_line(96)
        _text_colored(YELLOW, str(total))
        imgui.spacing()
        el = (row.get("elementtype") or "").strip()
        el_zh = {"Rock": "石头", "Paper": "布", "Scissors": "剪刀"}.get(el, el or "—")
        badge = str(row.get("badgereq", ""))
        _text_colored(DIM, f"属性 {el_zh} · 编号 {row.get('number')} · 档位 {row.get('division')}"
                           f" · 徽章需求 {'无' if badge in ('-1', '', 'None') else badge}")
        gacha = str(row.get("includeingacha", "")).upper()
        _text_colored(GREEN if gacha == "TRUE" else DIM,
                      "可以抽到" if gacha == "TRUE" else "不在抽卡池")
        evo = (row.get("evolution") or "").strip()
        if evo:
            _text_colored(DIM, f"进化 → {loc.get(evo, evo)}")
        imgui.end_group()

        # ---- 技能
        imgui.spacing()
        atk_ids = [x.split(":")[0].strip()
                   for x in str(row.get("attacks", "")).split(",") if x.strip()]
        if atk_ids:
            _text_colored(YELLOW, f"▸ 技能（{len(atk_ids)} 个）")
            from modkit import effects as FX
            b = self.sess.bundle("spdata", eager=True)
            amap = {}
            try:
                e = next((a for a in b.assets if a.name == "AttackInfo"), None)
                if e is not None:
                    amap = json.loads(b.preview_text(e))
            except Exception:  # noqa: BLE001
                pass
            for a in atk_ids[:8]:
                arow = amap.get(a) or {}
                _text_colored(DIM, f"  {a}")
                if arow:
                    imgui.same_line()
                    _text_colored(GREEN, " " + FX.describe(arow.get("effects", "")))

        # ---- 15 帧
        if self.gal_frames:
            imgui.spacing()
            _text_colored(YELLOW, f"▸ 动作帧（{len(self.gal_frames)}/15）")
            avail = imgui.get_content_region_avail().x
            per = max(1, int(avail // 120))
            for n, (frame, zh) in enumerate(self.GAL_ORDER):
                arr = self.gal_frames.get(frame)
                if arr is None:
                    continue
                if n % per:
                    imgui.same_line(0, 8)
                imgui.begin_group()
                p = immvision.ImageParams()
                p.image_display_size = (104, 104)
                # 缩略图只要图，不要那一排缩放/取色按钮
                p.show_options_button = False
                p.show_options_panel = False
                p.show_pixel_info = False
                p.show_image_info = False
                p.show_zoom_buttons = False
                p.show_school_paper_background = False
                immvision.image(f"##galf{frame}", arr, p)
                _text_colored(DIM, zh)
                imgui.end_group()
        elif self.gal_msg:
            _text_colored(WARN, self.gal_msg)

    # ------------------------------------------------------------ 向导：独立美术

    def _ne_kind_frames(self) -> list[str]:
        from modkit import character as CH

        return CH.frames_for(self.ne_kind or "morty")

    def _ne_scan_frames(self) -> None:
        """看看模板到底有哪几帧，并给每帧准备缩略图缓存。"""
        from modkit import character as CH

        self.ne_images = dict(getattr(self, "ne_images", {}) or {})
        self.ne_frames = []
        self.ne_frame_msg = ""
        if not self.ne_clone:
            return
        try:
            info = CH.inspect_donor(self.sess, self.ne_clone, self.ne_kind)
        except Exception as exc:  # noqa: BLE001
            self.ne_frame_msg = f"读不到模板美术：{exc}"
            return
        self.ne_donor_asset = info.get("donor_asset", "")
        self.ne_donor_bundle = info.get("donor_bundle", "")
        self.ne_frames = [f for f, _d in info.get("frames", {}).items()]
        order = CH.frames_for(self.ne_kind)
        self.ne_frames.sort(key=lambda f: order.index(f) if f in order else 99)
        if not self.ne_frames:
            self.ne_frame_msg = f"模板 {self.ne_donor_asset} 在包里找不到美术对象"

    def _ne_frame_label(self, frame: str) -> str:
        from modkit import character as CH

        if self.ne_kind == "item":
            return CH.ITEM_FRAME_LABEL.get(frame, frame)
        return CH.FRAME_LABEL.get(frame, frame)

    def _ne_pick_image(self, frame: str) -> None:
        """给某一帧选一张图。"""
        dlg = pfd.open_file(f"给「{self._ne_frame_label(frame)}」选一张图",
                            None, ["图片", "*.png *.jpg *.jpeg *.bmp *.webp"])
        if not dlg.result():
            return
        self.ne_images[frame] = dlg.result()[0]
        self._ne_img_cache.pop(frame, None)

    def _ne_pick_many(self) -> None:
        """一次选多张，按**文件名**自动往帧上套。

        文件名里带帧名（``Icon``/``Front``/``Down_1``…，不分大小写）就套到那一帧；
        只有一张图的话就套到图标帧。
        """
        dlg = pfd.open_file("批量选图（文件名里带帧名会自动对应）", None,
                            ["图片", "*.png *.jpg *.jpeg *.bmp *.webp"],
                            pfd.opt.multiselect_files)
        files = list(dlg.result() or [])
        if not files:
            return
        from modkit import character as CH

        order = CH.frames_for(self.ne_kind)
        hit = 0
        for path in files:
            stem = Path(path).stem
            low = stem.lower()
            best = ""
            for f in sorted(order, key=len, reverse=True):
                if f.lower() in low or low.endswith(f.lower()):
                    best = f
                    break
            if not best and len(files) == 1:
                best = "Icon" if "Icon" in order else order[0]
            if best:
                self.ne_images[best] = path
                self._ne_img_cache.pop(best, None)
                hit += 1
        self.ne_frame_msg = (
            f"批量选入 {hit}/{len(files)} 张（按文件名匹配帧）"
            if hit else "文件名里没认出帧名，试试单张选，或者把文件名改成 Icon.png / Front.png"
        )

    def _ne_frame_thumb(self, frame: str):
        """这一帧的小预览：选了图就显示选的图，没选就显示模板的图。"""
        cache = self._ne_img_cache
        key = (frame, self.ne_images.get(frame, ""))
        if key in cache:
            return cache[key]
        arr = None
        path = self.ne_images.get(frame)
        if path:
            try:
                img = Image.open(path)
                img.load()
                r = min(1.0, 64.0 / max(img.size))
                if r < 1.0:
                    img = img.resize((max(1, int(img.width * r)), max(1, int(img.height * r))),
                                     Image.LANCZOS)
                arr = np.ascontiguousarray(np.array(img.convert("RGBA")))
            except Exception:  # noqa: BLE001
                arr = None
        cache[key] = arr
        return arr

    def _draw_art_section(self) -> None:
        """独立美术：逐帧选图 + 一眼看清哪些换过。"""
        from modkit import character as CH

        kind_zh = "角色" if self.ne_kind == "morty" else "道具"
        _, self.ne_full_char = imgui.checkbox(
            f"独立美术（这只{kind_zh}用自己的图，不动别人）", self.ne_full_char)
        _tip("勾上：把模板的美术复制一份改成新名字、裁掉用不着的对象、放进一个新包，\n"
             "然后可以用你给的图替换其中任意几帧。\n\n"
             "不勾：沿用模板的形象（借皮，你给的图会被忽略）。")
        if not self.ne_full_char:
            if self.ne_images:
                _text_colored(WARN, f"  ⚠ 已选 {len(self.ne_images)} 张图，但没勾「独立美术」，创建时会被忽略")
            return
        if not self.ne_clone:
            _text_colored(DIM, "  先选模板。")
            return
        if not self.ne_frames:
            self._ne_scan_frames()
        if not self.ne_frames:
            _text_colored(WARN, "  " + (self.ne_frame_msg or "模板没有可替换的美术"))
            return

        _text_colored(DIM, f"  模板美术来自 {self.ne_donor_bundle} / {self.ne_donor_asset}"
                           f"（{len(self.ne_frames)} 帧，不换的沿用模板）")
        if imgui.small_button("批量选图…"):
            self._ne_pick_many()
        imgui.same_line()
        if imgui.small_button("全部用模板"):
            self.ne_images.clear()
            self._ne_img_cache.clear()
        imgui.same_line()
        n = len(self.ne_images)
        _text_colored(GREEN if n else DIM, f"已换 {n}/{len(self.ne_frames)} 帧")
        if self.ne_frame_msg:
            _text_colored(DIM, "  " + self.ne_frame_msg)

        per = 5
        for i, frame in enumerate(self.ne_frames):
            if i % per:
                imgui.same_line(0, 10)
            imgui.begin_group()
            thumb = self._ne_frame_thumb(frame)
            if thumb is not None:
                p = immvision.ImageParams()
                p.image_display_size = (68, 68)
                p.show_options_button = False
                p.show_options_panel = False
                p.show_pixel_info = False
                p.show_image_info = False
                p.show_zoom_buttons = False
                p.show_school_paper_background = False
                immvision.image(f"##neimg{frame}", thumb, p)
            else:
                imgui.dummy(imgui.ImVec2(68, 68))
            chosen = frame in self.ne_images
            _text_colored(GREEN if chosen else DIM,
                          ("● " if chosen else "○ ") + self._ne_frame_label(frame))
            if imgui.small_button(f"选图##pic{frame}"):
                self._ne_pick_image(frame)
            if chosen:
                imgui.same_line()
                if imgui.small_button(f"×##clr{frame}"):
                    self.ne_images.pop(frame, None)
            imgui.end_group()

    def _ne_bundle_name(self, nid: str) -> str:
        from modkit import character as CH
        import re as _re

        return "pm_" + _re.sub(r"[^a-z0-9]", "", nid.lower())[:24]

    def _draw_id_preview(self) -> None:
        """把这次会创建的所有 id / 主键都列出来，省得一个个猜。"""
        from modkit import entries as E

        kind = self._ne_kind()
        if kind is None:
            return
        nid = (self.ne_id or "").strip()
        if not nid:
            return
        # 独立美术走的是 character.add_character，assetid 就是新 id；
        # 借皮走的是 add_entry，assetid 用下拉框选的（默认模板那个）
        if self.ne_full_char:
            asset = nid
        else:
            asset = self.ne_asset or ""
            if not asset:
                try:
                    asset = E._donor_assetid(self.sess, self.ne_kind, self.ne_clone) or ""
                except Exception:  # noqa: BLE001
                    asset = ""
        rows = []
        for spec in kind.tables:
            rows.append((f"{spec.label}", spec.key or "id", nid))
        if asset:
            rows.append(("appdata/BundleAssetAssignment", "id", asset))
        if self.ne_full_char:
            rows.append((f"assets/AssetBundles/*/{self._ne_bundle_name(nid)}", "新资源包", ""))
        rows.append((f"text/*.{kind.loc_section}", "键", nid))
        _text_colored(DIM, "  这次会创建的 id：")
        for table, field, val in rows:
            imgui.text(f"     {table}")
            imgui.same_line(330)
            _mono(field)
            if val:
                imgui.same_line()
                _text_colored(GREEN, val)

    # ------------------------------------------------------------ 新增条目的子部件

    def _ne_template_rows(self, ids: list[str], loc: dict) -> list[dict]:
        """把模板列表整理成可筛选的结构（带数值，方便排序过滤）。"""
        from modkit import entries as E

        cached = getattr(self, "_ne_rows_cache", None)
        key = f"{self.ne_kind}:{len(ids)}"
        if cached and cached.get("key") == key:
            return cached["rows"]
        rows: list[dict] = []
        for i in ids:
            row = None
            try:
                row = E.entry_of_table(self.sess, self.ne_kind,
                                       self._ne_kind().tables[0], i)
            except Exception:  # noqa: BLE001
                pass
            row = row if isinstance(row, dict) else {}

            def num(f: str) -> int:
                try:
                    return int(float(row.get(f, 0) or 0))
                except (TypeError, ValueError):
                    return 0

            rows.append({
                "id": i, "zh": loc.get(i, ""),
                "hp": num("hpbase"), "atk": num("attackbase"),
                "spd": num("speedbase"), "def": num("defencebase"),
                "element": str(row.get("elementtype") or ""),
                "evolution": str(row.get("evolution") or ""),
                # 原始整行 —— 道具/技能没有体力攻击这些字段，
                # 得拿真正属于它们的字段来展示
                "raw": row,
            })
        self._ne_rows_cache = {"key": key, "rows": rows}
        return rows

    def _ne_row_tail(self, r: dict) -> str:
        """列表右边那一小截：道具看效果/稀有度，技能看属性和威力。"""
        from modkit import effects as FX
        from modkit import fields as F

        row = r.get("raw") or {}
        table = self._ne_kind().tables[0].label
        if self.ne_kind == "attack":
            eff = FX.parse(row.get("effects", ""))
            power = next((e.power for e in eff if e.power is not None), None)
            el = (row.get("elementtype") or "").strip()
            el_zh = {"Rock": "石头", "Paper": "布", "Scissors": "剪刀"}.get(el, "")
            bits = [el_zh] if el_zh else []
            if power is not None:
                bits.append(f"威力{int(power)}")
            return " ".join(bits)
        # 道具
        et = str(row.get("effecttype", "") or "")
        ev = str(row.get("effectvalue", "") or "")
        rar = str(row.get("rarity", "") or "")
        name = F.explain_value(table, "effecttype", et) or et
        return f"{name}{(' ' + ev) if ev else ''}  稀有{rar}" if name else f"稀有{rar}"

    def _ne_apply_filter(self, rows: list[dict]) -> list[dict]:
        out = rows
        f = (self.ne_clone_filter or "").strip().lower()
        if f:
            out = [r for r in out if f in r["id"].lower() or f in (r["zh"] or "").lower()]
        key, label, cond = next(
            (x for x in self.NE_FILTERS if x[0] == self.ne_filter), self.NE_FILTERS[0])
        if cond:
            out = [r for r in out if r.get(cond[0], 0) >= cond[1]]
        elif key == "evo":
            out = [r for r in out if r["evolution"]]
        elif key in ("Rock", "Paper", "Scissors"):
            out = [r for r in out if r["element"] == key]
        sort_key = ["id", "hp", "atk", "spd"][max(0, min(3, self.ne_sort))]
        if sort_key == "id":
            out = sorted(out, key=lambda r: r["id"])
        else:
            out = sorted(out, key=lambda r: -r[sort_key])
        return out

    def _draw_before_after(self, rows: list[dict], loc: dict) -> None:
        """BEFORE / AFTER 对比卡。

        莫蒂才有体力/攻击/防御/速度 —— 道具和技能的表里压根没这几个字段，
        对它们画一堆空条子没有意义，改成显示各自真正有用的属性。
        """
        donor = next((r for r in rows if r["id"] == self.ne_clone), None)
        if donor is None:
            _text_colored(DIM, "左边挑一个，这里会显示对比。")
            return

        if self.ne_kind != "morty":
            self._draw_field_compare(donor, loc)
            return

        _text_colored(YELLOW, "BEFORE · 模板")
        imgui.same_line()
        _mono("  " + donor["id"])
        imgui.text(f"{donor['zh'] or donor['id']}")
        base = {"hp": donor["hp"], "atk": donor["atk"],
                "def": donor["def"], "spd": donor["spd"]}
        self._draw_stat_bars(base, YELLOW, donor_total_only=False)

        imgui.spacing()
        _text_colored(GREEN, "AFTER · 新建")
        imgui.same_line()
        _mono("  " + (self.ne_id or "?"))
        stats = getattr(self, "ne_stats", {}) or {}
        now = {
            "hp": stats.get("hpbase", donor["hp"]),
            "atk": stats.get("attackbase", donor["atk"]),
            "def": stats.get("defencebase", donor["def"]),
            "spd": stats.get("speedbase", donor["spd"]),
        }
        self._draw_stat_bars(now, GREEN, base=base)

    def _draw_field_compare(self, donor: dict, loc: dict) -> None:
        """道具 / 技能的「关键属性」卡（没有体力攻击那一栏）。"""
        from modkit import effects as FX
        from modkit import fields as F

        row = donor.get("raw") or {}
        table = self._ne_kind().tables[0].label

        _text_colored(YELLOW, "BEFORE · 模板")
        imgui.same_line()
        _mono("  " + donor["id"])
        imgui.text(f"{donor['zh'] or donor['id']}")
        imgui.spacing()

        if self.ne_kind == "attack":
            # 技能：属性 / PP / 效果
            el = (row.get("elementtype") or "").strip()
            _text_colored(DIM, "属性 ")
            imgui.same_line()
            _text_colored(GREEN, {"Rock": "石头", "Paper": "布",
                                  "Scissors": "剪刀"}.get(el, el or "无属性"))
            imgui.same_line()
            _text_colored(DIM, f"   PP {row.get('pp') or '-'}")
            _text_colored(DIM, "效果")
            imgui.push_text_wrap_pos(max(120.0, imgui.get_content_region_avail().x))
            _text_colored(GREEN, FX.describe(row.get("effects", "")))
            imgui.pop_text_wrap_pos()
        else:
            # 道具：类型 / 效果 / 价格 / 稀有度 / 用途
            pairs = [
                ("type", "类型"), ("effecttype", "效果"), ("effectvalue", "效果值"),
                ("cost", "价格"), ("rarity", "稀有度"), ("spbaglimit", "背包上限"),
            ]
            for f, _zh in pairs:
                v = str(row.get(f, "") or "").strip()
                if not v:
                    continue
                d = F.doc_for(table, f)
                meaning = F.explain_value(table, f, v)
                imgui.text(d.label if d else f)
                imgui.same_line(120)
                _text_colored(GREEN, meaning or v)
            flags = []
            for f, zh in (("includeingacha", "能抽到"), ("usableinworld", "世界里能用"),
                          ("usableincraft", "合成能用"), ("usableinbattle", "战斗里能用"),
                          ("useonself", "对自己用")):
                if str(row.get(f, "")).upper() == "TRUE":
                    flags.append(zh)
            if flags:
                _text_colored(DIM, " · ".join(flags))

        imgui.spacing()
        _text_colored(GREEN, "AFTER · 新建")
        imgui.same_line()
        _mono("  " + (self.ne_id or "?"))
        imgui.text(self.ne_name_zh or self.ne_id or "?")
        _text_colored(DIM, "上面这些先沿用模板；要精确调就到下面「高级：全部字段」里改。")

    def _draw_stat_bars(self, v: dict, color, *, base: dict | None = None,
                        donor_total_only: bool = False) -> None:
        labels = [("hp", "体力", "HP"), ("atk", "攻击", "ATK"),
                  ("def", "防御", "DEF"), ("spd", "速度", "SPD")]
        limit = 200.0
        for k, zh, en in labels:
            val = int(v.get(k, 0))
            imgui.text(f"{zh}")
            imgui.same_line()
            _mono(en)
            imgui.same_line(120)
            _bar(min(1.0, val / limit), 130, 6, color)
            imgui.same_line()
            txt = f"{val:>3}"
            if base is not None and k in base and base[k] != val:
                d = val - base[k]
                txt += f"  ({d:+d})"
            _text_colored(color, txt)
        total = int(v.get("hp", 0) + v.get("atk", 0) + v.get("def", 0) + v.get("spd", 0))
        imgui.text("总和")
        imgui.same_line()
        _mono("TOTAL")
        imgui.same_line(120)
        _text_colored(color, str(total))

    def _draw_stat_sliders(self) -> None:
        """四项基础值用滑条调 —— 不用去认 hpbase 这种字段名。"""
        stats = getattr(self, "ne_stats", None)
        base = getattr(self, "ne_stats_base", None)
        if not stats or not base:
            return
        # 一行一项：中文名 | 等宽缩写 | 滑条 | 数值（+ 与模板的差）
        for k, zh, en, lim in self.STAT_FIELDS:
            val = int(stats.get(k, 0))
            imgui.text(zh)
            imgui.same_line(52)
            _mono(en)
            imgui.same_line(96)
            imgui.set_next_item_width(-72)
            ch, v2 = imgui.slider_int(f"##sl{k}", val, 1, lim)
            if ch:
                stats[k] = v2
            imgui.same_line()
            d = int(stats.get(k, 0)) - int(base.get(k, 0))
            col = GREEN if d > 0 else (WARN if d < 0 else DIM)
            _text_colored(col, f"{int(stats.get(k, 0)):>3}" + (f"  {d:+d}" if d else ""))
        self._ne_stats_commit()
        total = sum(int(stats.get(k, 0)) for k, *_ in self.STAT_FIELDS)
        base_total = sum(int(base.get(k, 0)) for k, *_ in self.STAT_FIELDS)
        imgui.text("总和")
        imgui.same_line(52)
        _mono("TOTAL")
        imgui.same_line(96)
        dt = total - base_total
        _text_colored(GREEN if dt > 0 else (WARN if dt < 0 else DIM),
                      f"{total}" + (f"   （模板 {base_total}，{dt:+d}）" if dt else ""))
        # 快捷调节
        if imgui.small_button("平均"):
            avg = sum(int(base[k]) for k, *_ in self.STAT_FIELDS) // 4
            self.ne_stats = {k: avg for k, *_ in self.STAT_FIELDS}
            self._ne_stats_commit()
        imgui.same_line()
        if imgui.small_button("+10%"):
            self.ne_stats = {k: min(255, int(int(stats.get(k, 0)) * 1.1))
                             for k, *_ in self.STAT_FIELDS}
            self._ne_stats_commit()
        imgui.same_line()
        if imgui.small_button("拉满"):
            self.ne_stats = {"hpbase": 150, "attackbase": 140,
                             "defencebase": 135, "speedbase": 155}
            self._ne_stats_commit()
        imgui.same_line()
        if imgui.small_button("恢复模板"):
            self.ne_stats = dict(base)
            self._ne_stats_commit()

    # ------------------------------------------------------------ 导出动作

    def _do_cache(self) -> str:
        rep = self.sess.output_cache(Path(self.out_dir) / "UnityCache产物", only_modified=not self.cache_full)
        return f"写出 {len(rep['written'])} 个包 → {rep['dir']}\n已生成 push.sh 与 推送说明.md"

    def _do_apk(self) -> str:
        rep = self.sess.output_apk(self.apk_src, self.apk_out, keystore=self.apk_keystore or None,
                                   do_sign=self.apk_sign, bump_versions=self.apk_bump)
        lines = list(rep.get("steps", []))
        lines.append(f"输出：{self.apk_out}")
        return "\n".join(lines)

    def _do_cdn(self) -> str:
        rep = self.sess.output_cdn(Path(self.out_dir) / "CDN产物")
        return f"写出 {len(rep['written'])} 个包 → {rep['dir']}"


# ---------------------------------------------------------------- 入口


def load_fonts() -> None:
    """中文字体 + 等宽字体。等宽的用于设计稿那种大写拉丁小标。"""
    for cand in FONT_CANDIDATES:
        if Path(cand).is_file():
            try:
                FONTS["ui"] = hello_imgui.load_font(cand, 16.5)
                break
            except Exception:  # noqa: BLE001
                continue
    mono = sysenv.find_mono_font()
    if mono:
        try:
            FONTS["mono"] = hello_imgui.load_font(mono, 12.5)
        except Exception:  # noqa: BLE001
            FONTS["mono"] = None


def main() -> int:
    # ImmVision 要求先声明通道顺序（2024-10 起的破坏性变更）
    immvision.use_rgb_color_order()

    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    app = ModkitApp(args)

    params = hello_imgui.RunnerParams()
    params.callbacks.show_gui = app.draw
    params.callbacks.load_additional_fonts = load_fonts
    # 后端起来之后再挂 GLFW 的文件拖放回调（这时窗口句柄才有效）
    params.callbacks.post_init_add_platform_backend_callbacks = install_file_drop
    params.app_window_params.window_title = "口蘑 Mod 工坊 — Pocket Mortys 模组制作器"
    params.app_window_params.window_geometry.size = (1560, 980)
    params.imgui_window_params.default_imgui_window_type = (
        hello_imgui.DefaultImGuiWindowType.provide_full_screen_window
    )
    params.imgui_window_params.show_status_bar = False
    try:
        immapp.run(params)
    except KeyboardInterrupt:
        return 130

    shot = os.environ.get("PM_MODKIT_SHOT")
    if shot and app.autotest:
        try:
            buf = hello_imgui.final_app_window_screenshot()
            arr = np.asarray(buf)
            Image.fromarray(arr).save(shot)
            print(f"[自检] 截图已保存：{shot}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[自检] 截图失败：{exc}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
