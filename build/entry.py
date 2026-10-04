#!/usr/bin/env python3
"""打包成可执行程序时的入口。

行为跟 `python3 app/main.py` 一样，另外加两个开关：

    --web        不开图形界面，直接起网页版（服务器/手机场景）
    --port N     网页版端口

**为什么这里要把依赖都静态 import 一遍**

PyInstaller 靠静态分析决定「哪些模块要打包」。如果只在运行时用
``importlib`` 动态加载 ``app/main.py`` / ``web/server.py``，它看不到里面的
``import imgui_bundle``、``from modkit import ...``，打出来的包一跑就
``ModuleNotFoundError``。所以这里显式导入，纯粹是为了让打包器看见。
"""

from __future__ import annotations

import importlib.util
import multiprocessing
import os
import sys
from pathlib import Path


def _root() -> Path:
    """打包后资源在 ``sys._MEIPASS``；开发时就是项目根目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent


def _fix_frozen_data_paths() -> None:
    """给那些靠 ``__file__/../data`` 找数据的库补一条明路。

    ``archspec`` 会去算 ``archspec/cpu/../json/cpu/*.json``。打包后
    ``archspec/cpu/`` 可能只是归档里的模块、磁盘上没有这个目录，
    带 ``..`` 的路径就解析不了（内核要目录真实存在才能解析 ``..``）。
    它支持用环境变量指定，这里设一下兜底。
    """
    try:
        base = ROOT / "archspec" / "json" / "cpu"
        if base.is_dir() and "ARCHSPEC_CPU_DIR" not in os.environ:
            os.environ["ARCHSPEC_CPU_DIR"] = str(base)
    except Exception:  # noqa: BLE001
        pass


ROOT = _root()
_fix_frozen_data_paths()
# 注意：必须在下面那些 import 之前把项目根塞进 sys.path，
# 否则直接跑本文件时 import modkit 会失败
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------- 打包可见性
# noqa 一片：这些 import 只为让 PyInstaller 收集，代码里不直接用
import modkit  # noqa: E402,F401
from modkit import (  # noqa: E402,F401
    apkbuild,
    assetops,
    bundle,
    entries,
    fields,
    modpack,
    paths,
    session,
    source,
    sysenv,
)

try:  # 桌面界面用；网页版不需要，缺了也能跑
    import imgui_bundle  # noqa: F401
    import numpy  # noqa: F401
except ImportError:  # pragma: no cover
    imgui_bundle = None  # type: ignore[assignment]
    numpy = None  # type: ignore[assignment]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"加载不了 {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    multiprocessing.freeze_support()

    argv = list(sys.argv[1:])
    if "--web" in argv or imgui_bundle is None:
        if "--web" in argv:
            argv.remove("--web")
        sys.argv = [sys.argv[0], *argv]
        return _load("pmweb", ROOT / "web" / "server.py").main()

    sys.argv = [sys.argv[0], *argv]
    return _load("pmapp", ROOT / "app" / "main.py").main()


if __name__ == "__main__":
    sys.exit(main())
