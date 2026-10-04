"""APK 重打包 / 对齐 / 签名。

三条硬性要求，缺一条就装不上：

1. ``resources.arsc`` 必须**不压缩**且 **4 字节对齐** —— targetSdk ≥ 30 的硬性校验。
   所以不能整包重压，只能读原 APK、只换指定条目、其余连同压缩方式原样拷贝。
2. ``zipalign`` 必须在签名**之前**做。
3. 必须用 v2/v3 方案签名（``apksigner``），只做 v1 的 ``jarsigner`` 装不上。
   旧签名的 ``META-INF/*.SF|RSA|DSA|MF`` 要先删掉。

工具自动发现顺序：环境变量 → 本机已有 SDK → PATH。
本机上 ``pocketmortys-server/.android-sdk/build-tools/34.0.0/`` 是现成的。
"""

from __future__ import annotations

import os
import subprocess
import zipfile
from pathlib import Path

from . import sysenv

DEFAULT_STOREPASS = "pmnet123"
DEFAULT_ALIAS = "pmnet"

#: 签名相关条目，重打包时要丢掉
_SIG_SUFFIX = (".SF", ".RSA", ".DSA", ".EC")
_SIG_EXACT = {"META-INF/MANIFEST.MF"}


def is_signature(name: str) -> bool:
    up = name.upper()
    if up in _SIG_EXACT:
        return True
    if up.endswith(".IDSIG"):
        return True
    if up.startswith("META-INF/") and up.endswith(_SIG_SUFFIX):
        return True
    return False


# ---------------------------------------------------------------- 工具发现


# 跨平台的查找逻辑都挪到 sysenv 里了（Windows / Linux / Termux 都覆盖）
find_build_tool = sysenv.find_build_tool
find_java_home = sysenv.find_java_home


def default_keystore() -> Path | None:
    """找一个现成的签名密钥。

    跨平台地找几个位置：环境变量 → 隔壁前期工程那把 → 工具自己的目录。
    都没有就返回 ``None``，由调用方现场生成一把（见 ``make_keystore``）。
    """
    home = Path.home()
    cands: list[Path] = []
    env = os.environ.get("PM_MODKIT_KEYSTORE")
    if env:
        cands.append(Path(env))
    cands += [
        home / ".pm-modkit" / "modkit.keystore",
        Path(__file__).resolve().parent.parent / "modkit.keystore",
        Path.cwd() / "modkit.keystore",
        # 隔壁那个前期工程里已经有一把（开发机上常见）
        home / "Documents/deepseek-harness/default-workspace/pocketmortys-server/build/pmnet.keystore",
        Path.cwd().parent / "pocketmortys-server/build/pmnet.keystore",
    ]
    for p in cands:
        if p.is_file():
            return p
    return None


def toolchain_status() -> dict[str, str | None]:
    return {
        "apksigner": find_build_tool("apksigner"),
        "zipalign": find_build_tool("zipalign"),
        "keytool": find_build_tool("keytool"),
        "java": find_java_home(),
        "keystore": str(default_keystore()) if default_keystore() else None,
    }


# ---------------------------------------------------------------- 重打包


def repack(
    src: str | Path,
    dst: str | Path,
    replacements: dict[str, bytes],
    *,
    new_entries: dict[str, bytes] | None = None,
    drop_signature: bool = True,
    progress=None,
) -> dict:
    """只替换指定条目，保留其余条目的压缩方式与属性。

    ``replacements`` 是 ``{zip 内路径: 新内容}``（**已存在**的条目）。
    ``new_entries`` 是**新增**的条目 —— 这些会以 **STORED（不压缩）** 写入。

    新增条目为什么不压缩：APK 里有些资源是游戏**直接读**的（不是先拷到缓存
    再用），Unity 用 ``jar:`` 方式读时需要条目不压缩、可随机定位。不压缩虽然
    大一点，但**任何读法都能读**，不会因为读法不同而翻车。
    （实测：加强版里 ``text`` 是 STORED，其余 154 个包是 DEFLATED ——
    正好对应「直接读」和「先播种」两种用法。）
    """
    new_entries = new_entries or {}
    src, dst = Path(src), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    stats = {"replaced": [], "added": [], "missing": [], "dropped": 0, "total": 0}

    with zipfile.ZipFile(src) as zin:
        names = set(zin.namelist())
        for k in replacements:
            if k not in names:
                stats["missing"].append(k)
        for k in new_entries:
            if k in names:
                # 已经存在就当替换处理，免得出重复条目
                replacements.setdefault(k, new_entries[k])
        new_entries = {k: v for k, v in new_entries.items() if k not in names}

        infos = zin.infolist()
        n = len(infos)
        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as out:
            for i, item in enumerate(infos):
                name = item.filename
                if drop_signature and is_signature(name):
                    stats["dropped"] += 1
                    continue
                data = replacements.get(name)
                if data is not None:
                    stats["replaced"].append((name, len(data)))
                else:
                    data = zin.read(name)

                store = (
                    item.compress_type == zipfile.ZIP_STORED
                    or name == "resources.arsc"
                    or name.endswith(".arsc")
                )
                zi = zipfile.ZipInfo(name, date_time=item.date_time)
                zi.compress_type = zipfile.ZIP_STORED if store else zipfile.ZIP_DEFLATED
                zi.external_attr = item.external_attr
                zi.internal_attr = item.internal_attr
                zi.create_system = item.create_system
                out.writestr(zi, data)
                stats["total"] += 1
                if progress and (i % 200 == 0 or i == n - 1):
                    progress(i + 1, n)

            # 新增条目追加在最后（不压缩）
            for name, data in new_entries.items():
                zi = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
                zi.compress_type = zipfile.ZIP_STORED
                zi.external_attr = 0o644 << 16
                zi.create_system = 3
                out.writestr(zi, data)
                stats["added"].append((name, len(data)))
                stats["total"] += 1
    return stats


# ---------------------------------------------------------------- 对齐 / 签名


def zipalign(apk: str | Path, *, alignment: int = 4) -> tuple[bool, str]:
    tool = find_build_tool("zipalign")
    if not tool:
        return False, "找不到 zipalign（装 Android build-tools，或设置 ANDROID_HOME）"
    apk = Path(apk)
    tmp = apk.with_suffix(apk.suffix + ".aligned")
    env = _env_with_java()
    r = subprocess.run(
        [tool, "-f", "-p", str(alignment), str(apk), str(tmp)],
        capture_output=True,
        text=True,
        env=env,
    )
    if r.returncode != 0:
        tmp.unlink(missing_ok=True)
        return False, (r.stderr or r.stdout).strip()[:400]
    tmp.replace(apk)
    return True, "已 4 字节对齐"


def sign(
    apk: str | Path,
    keystore: str | Path,
    *,
    storepass: str = DEFAULT_STOREPASS,
    alias: str = DEFAULT_ALIAS,
) -> tuple[bool, str]:
    tool = find_build_tool("apksigner")
    if not tool:
        return False, "找不到 apksigner（装 Android build-tools，或设置 ANDROID_HOME）"
    env = _env_with_java()
    r = subprocess.run(
        [
            tool, "sign",
            "--ks", str(keystore),
            "--ks-pass", f"pass:{storepass}",
            "--key-pass", f"pass:{storepass}",
            "--ks-key-alias", alias,
            "--v1-signing-enabled", "false",
            "--v2-signing-enabled", "true",
            "--v3-signing-enabled", "true",
            str(apk),
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    if r.returncode != 0:
        return False, (r.stderr or r.stdout).strip()[:400]
    return True, "已用 v2/v3 方案签名"


def verify(apk: str | Path) -> tuple[bool, str]:
    tool = find_build_tool("apksigner")
    if not tool:
        return False, "找不到 apksigner"
    r = subprocess.run(
        [tool, "verify", "--print-certs", str(apk)],
        capture_output=True, text=True, env=_env_with_java(),
    )
    return r.returncode == 0, (r.stdout or r.stderr).strip()[:600]


def make_keystore(
    path: str | Path,
    *,
    storepass: str = DEFAULT_STOREPASS,
    alias: str = DEFAULT_ALIAS,
    dname: str = "CN=PM Modkit, O=Pocket Mortys Mods, C=US",
) -> tuple[bool, str]:
    path = Path(path)
    if path.is_file():
        return True, "已存在"
    keytool = find_build_tool("keytool")
    if not keytool:
        return False, "找不到 keytool（装 JDK）"
    path.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        [
            keytool, "-genkeypair", "-keystore", str(path),
            "-alias", alias, "-keyalg", "RSA", "-keysize", "2048",
            "-validity", "10000",
            "-storepass", storepass, "-keypass", storepass, "-dname", dname,
        ],
        capture_output=True, text=True, env=_env_with_java(),
    )
    if r.returncode != 0:
        return False, (r.stderr or r.stdout).strip()[:400]
    return True, "已生成"


def _env_with_java() -> dict[str, str]:
    env = dict(os.environ)
    jh = find_java_home()
    if jh:
        env["PATH"] = jh + os.pathsep + env.get("PATH", "")
        env.setdefault("JAVA_HOME", str(Path(jh).parent))
    return env


# ---------------------------------------------------------------- 高层封装


def build_apk(
    src_apk: str | Path,
    out_apk: str | Path,
    replacements: dict[str, bytes],
    *,
    new_entries: dict[str, bytes] | None = None,
    keystore: str | Path | None = None,
    storepass: str = DEFAULT_STOREPASS,
    alias: str = DEFAULT_ALIAS,
    do_align: bool = True,
    do_sign: bool = True,
    progress=None,
) -> dict:
    """换条目 → 对齐 → 签名，一步到位。返回过程报告。"""
    report: dict = {"steps": []}

    stats = repack(src_apk, out_apk, replacements, new_entries=new_entries,
                   progress=progress)
    report["repack"] = stats
    report["steps"].append(
        f"重打包完成：替换 {len(stats['replaced'])} 项"
        + (f"，新增 {len(stats['added'])} 项" if stats.get("added") else "")
        + f"，丢弃旧签名 {stats['dropped']} 项"
    )
    if stats["missing"]:
        report["steps"].append(f"⚠ 以下条目不在原 APK 里：{stats['missing']}")

    if do_align:
        ok, msg = zipalign(out_apk)
        report["steps"].append(("✓ " if ok else "✗ ") + f"zipalign：{msg}")
        report["aligned"] = ok

    if do_sign:
        ks = Path(keystore) if keystore else default_keystore()
        if ks is None or not ks.is_file():
            ks = sysenv.ensure_dir(Path.home() / ".pm-modkit") / "modkit.keystore"
            ok, msg = make_keystore(ks, storepass=storepass, alias=alias)
            report["steps"].append(("✓ " if ok else "✗ ") + f"生成 keystore：{msg}")
        ok, msg = sign(out_apk, ks, storepass=storepass, alias=alias)
        report["steps"].append(("✓ " if ok else "✗ ") + f"签名：{msg}")
        report["signed"] = ok
        report["keystore"] = str(ks)

    return report
