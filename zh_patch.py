#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude Desktop 中文化 —— 新版（ion-dist.asar）补丁引擎

背景
----
Claude 4.42xx 起把前端资源从松散目录 `Resources/ion-dist/` 收进了
`Resources/ion-dist.asar`。旧补丁写松散文件，于是升级后直接报
「未找到 ion-dist/assets 目录」而失效。

这个 asar 被锁死：
  1. Electron fuse `EnableEmbeddedAsarIntegrityValidation` = Enabled
  2. Info.plist 的 ElectronAsarIntegrity 记录了 asar **头部 JSON** 的 SHA256
  3. 每个文件在头部里还有自己的 integrity（整文件 SHA256 + 每 4MB 分块 SHA256）

所以「解包 → 改 → 重新打包」必须同时满足：
  - 重新计算每个文件的 integrity，否则读文件时进程直接退出
  - 重新计算头部 JSON 的 SHA256 写回 Info.plist，否则启动即崩

本模块把这些都封进一个命令里，并额外处理语言选择器：
  - 部署 i18n/zh-Hans.json + i18n/dynamic/zh-Hans.json
  - 注入语言白名单数组
  - 注入语言名称映射表

用法
----
  python3 zh_patch.py sync                # 从当前 Claude.app 抽取 ion-dist 到 .work/
  python3 zh_patch.py stage               # 部署翻译 + 注入（只改工作树，不打包）
  python3 zh_patch.py pack                # 工作树 → ion-dist.asar，并更新 Info.plist
  python3 zh_patch.py apply               # sync + stage + pack（一步到位）
  python3 zh_patch.py verify              # 校验当前 app 的 integrity / 中文文件
  python3 zh_patch.py restore             # 从备份还原官方原版
  python3 zh_patch.py info                # 打印环境与版本信息
  python3 zh_patch.py doctor              # 环境体检（macOS / Python / 权限 / 端点）
  python3 zh_patch.py status              # JSON 状态（GUI 用）

关于签名：改 Resources/ 或 Framework 二进制后，macOS 会拒绝启动签名破损的包，
且 Electron 44 起还会校验「ElectronAsarIntegrity 整表摘要」（烧在
Electron Framework 的 __DATA_CONST,__asar_integrity 段里），所以 apply 会：
  重新打包 asar → 更新 Info.plist 里的 hash → 重算并写入整表摘要 → 从内到外 ad-hoc 重签
代价是应用签名身份改变，钥匙串里的旧条目（Electron safeStorage）可能需要重新授权一次。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import mmap
import os
import plistlib
import re
import shutil
import struct
import subprocess
import tempfile
import sys
import time
from pathlib import Path

import claude_paths as P

APP = P.APP
RES = P.RES
INFO_PLIST = P.INFO_PLIST
ION_ASAR = P.ION_ASAR
ION_DIR = P.ION_DIR
LANG = P.LANG
LANG_NAME = P.LANG_NAME
LANG_LOCAL = P.LANG_LOCAL

PATCH_DIR = P.PATCH_DIR
ZH_SRC = P.ZH_SRC
WORK = P.WORK
WORK_TREE = P.WORK_TREE
WORK_BACKUP = P.WORK_BACKUP

BLOCK = 4 * 1024 * 1024

# Electron 44+ 把「ElectronAsarIntegrity 字典」的 SHA256 摘要烧进了
# Electron Framework 的 __DATA_CONST,__asar_integrity 段：
#   [32 字节哨兵][used(1)][version(1)][digest(32)]
# 只改 Info.plist 的 hash 而不改这个摘要，Electron 会判定整张表无效并 FATAL
# （archive.cc: "Failed to get integrity for validatable asar archive"）。
# 哨兵由 Electron 源码写死；Electron 换哨兵时可用环境变量覆盖：
#   CLAUDE_ZH_SENTINEL=<32 字节 ASCII 或 64 位 hex>
INTEGRITY_SENTINELS = [b"AGbevlPCksUGKNL8TSn7wGmJEuJsXb2A"]
FRAMEWORK_BIN = (P.FRAMEWORKS / "Electron Framework.framework"
                 / "Versions" / "A" / "Electron Framework")

# 语言白名单数组。不写死具体语言列表——Claude 增删语言后依然能命中；
# 只要是以 "en-US" 开头、含 ≥5 个语言码的数组就认为是白名单。
WHITELIST_RE = re.compile(
    r'\["en-US"(?:\s*,\s*"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,4})?"){4,}\]')
NAME_MAP_RE = re.compile(
    r'"en-US"\s*:\s*\{name:"[^"]*",localName:"[^"]*"\}')
NAME_MAP_ENTRY = '"%s":{name:"%s",localName:"%s"}' % (LANG, LANG_NAME, LANG_LOCAL)


# ---------------------------------------------------------------- asar 读写

def read_header(path: Path) -> tuple[dict, int, int, bytes]:
    """解析 asar 头部。

    返回 (header_dict, json_start, json_size, json_bytes)：
      - 文件布局为 [4][b][c][e][json...]，b/c 是 pickle 长度，e 才是 JSON 字节数
      - Electron 的 integrity 校验 = sha256(json_bytes)，不含对齐填充
    """
    with open(path, "rb") as f:
        head = f.read(16)
        if len(head) < 16:
            raise ValueError(f"不是合法的 asar 文件: {path}")
        json_size = struct.unpack("<I", head[12:16])[0]
        f.seek(16)
        raw = f.read(json_size)
    if len(raw) != json_size:
        raise ValueError(f"asar 头部读取不完整: {path}")
    return json.loads(raw.decode("utf-8")), 16, json_size, raw


def header_sha256(path: Path) -> str:
    return hashlib.sha256(read_header(path)[3]).hexdigest()


class Asar:
    """极简 asar 读器（够用即可，不引外部依赖）。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.header, self.json_start, self.json_size, _ = read_header(self.path)
        # 数据区起点 = 8 (外层 pickle 头) + b，b 在偏移 4
        with open(self.path, "rb") as f:
            b = struct.unpack("<I", f.read(8)[4:8])[0]
        self.base = 8 + b

    def _node(self, rel: str):
        node = self.header
        for part in rel.split("/"):
            node = node.get("files", {}).get(part)
            if node is None:
                return None
        return node

    def exists(self, rel: str) -> bool:
        return self._node(rel) is not None

    def list_files(self):
        out = []

        def walk(node, prefix):
            for k, v in node.get("files", {}).items():
                p = prefix + "/" + k if prefix else k
                if "files" in v:
                    walk(v, p)
                else:
                    out.append((p, v))

        walk(self.header, "")
        return out

    def read(self, rel: str) -> bytes:
        node = self._node(rel)
        if node is None:
            raise KeyError(rel)
        if node.get("unpacked"):
            raise NotImplementedError("unpacked 条目: " + rel)
        with open(self.path, "rb") as f:
            f.seek(self.base + int(node["offset"]))
            return f.read(node["size"])

    def read_text(self, rel: str) -> str:
        return self.read(rel).decode("utf-8")


def file_integrity(data: bytes) -> dict:
    """按 Electron/asar 规范计算：整文件 SHA256 + 每 4MB 分块 SHA256。"""
    blocks = []
    for off in range(0, len(data), BLOCK):
        blocks.append(hashlib.sha256(data[off:off + BLOCK]).hexdigest())
    if not blocks:  # 空文件也算一块
        blocks.append(hashlib.sha256(b"").hexdigest())
    return {
        "algorithm": "SHA256",
        "hash": hashlib.sha256(data).hexdigest(),
        "blockSize": BLOCK,
        "blocks": blocks,
    }


def asar_pack(src_dir: Path, dst: Path):
    """把目录打包成 Electron 可读的 asar（含每文件 integrity）。

    格式（与 @electron/asar 完全一致）：
      外层 pickle: [payload_size=4][b]              b = 头部 pickle 总长度
      头部 pickle: [payload_size=c][e][json][pad]   c = 4 + 对齐后的 json 长度
                                                    e = 未对齐的 json 字节数
    """
    files = []
    for root, dirs, names in os.walk(src_dir):
        dirs.sort()
        for name in sorted(names):
            full = Path(root) / name
            rel = full.relative_to(src_dir).as_posix()
            files.append((rel, full))
    files.sort(key=lambda x: x[0])

    header = {"files": {}}
    blobs = []
    offset = 0
    for rel, full in files:
        data = full.read_bytes()
        parts = rel.split("/")
        node = header
        for p in parts[:-1]:
            node = node["files"].setdefault(p, {"files": {}})
        entry = {"size": len(data), "offset": str(offset),
                 "integrity": file_integrity(data)}
        if full.stat().st_mode & 0o100:
            entry["executable"] = True
        node["files"][parts[-1]] = entry
        blobs.append(data)
        offset += len(data)

    json_bytes = json.dumps(header, separators=(",", ":")).encode("utf-8")
    pad = (4 - len(json_bytes) % 4) % 4
    padded = json_bytes + b"\x00" * pad
    c = 4 + len(padded)
    b = 4 + c
    head = struct.pack("<IIII", 4, b, c, len(json_bytes))

    tmp = dst.with_suffix(dst.suffix + ".tmp")
    with open(tmp, "wb") as out:
        out.write(head)
        out.write(padded)
        for blob in blobs:
            out.write(blob)
    os.replace(tmp, dst)
    return {"files": len(files), "bytes": dst.stat().st_size,
            "header_sha256": hashlib.sha256(json_bytes).hexdigest()}


# ---------------------------------------------------------------- 环境

def app_version() -> str:
    try:
        with open(INFO_PLIST, "rb") as f:
            return plistlib.load(f).get("CFBundleShortVersionString", "?")
    except Exception:
        return "?"


def ion_dist_source() -> tuple[Path, str]:
    kind = P.layout()
    return (ION_ASAR, kind) if kind == "asar" else (ION_DIR, kind)


def plist_asar_hash() -> str | None:
    try:
        with open(INFO_PLIST, "rb") as f:
            info = plistlib.load(f)
    except Exception:
        return None
    return (info.get("ElectronAsarIntegrity", {})
            .get("Resources/ion-dist.asar", {}).get("hash"))


def set_plist_asar_hash(new_hash: str) -> str | None:
    with open(INFO_PLIST, "rb") as f:
        info = plistlib.load(f)
    old = (info.get("ElectronAsarIntegrity", {})
           .get("Resources/ion-dist.asar", {}).get("hash"))
    integ = info.setdefault("ElectronAsarIntegrity", {})
    entry = integ.setdefault("Resources/ion-dist.asar", {})
    entry["algorithm"] = "SHA256"
    entry["hash"] = new_hash
    with open(INFO_PLIST, "wb") as f:
        plistlib.dump(info, f)
    return old


# ---------------------------------------------------------------- integrity 摘要

def integrity_digest(integ: dict) -> str:
    """按 Electron 的算法算出 ElectronAsarIntegrity 字典的 SHA256。

    = SHA256( 按 key 排序后依次拼接 key + algorithm + hash 的 UTF-8 字节 )
    """
    h = hashlib.sha256()
    for k in sorted(integ.keys()):
        item = integ[k] or {}
        h.update(k.encode("utf-8"))
        h.update(str(item.get("algorithm", "")).encode("utf-8"))
        h.update(str(item.get("hash", "")).encode("utf-8"))
    return h.hexdigest()


def plist_integrity_dict() -> dict:
    with open(INFO_PLIST, "rb") as f:
        return plistlib.load(f).get("ElectronAsarIntegrity", {}) or {}


def sentinels() -> list[bytes]:
    """哨兵候选（默认一个；可用 CLAUDE_ZH_SENTINEL 覆盖，逗号分隔）。"""
    env = os.environ.get("CLAUDE_ZH_SENTINEL")
    if not env:
        return INTEGRITY_SENTINELS
    out = []
    for part in env.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            b = bytes.fromhex(part)
        except ValueError:
            b = part.encode("utf-8")
        if len(b) == 32:
            out.append(b)
    if not out:
        print("  ⚠ CLAUDE_ZH_SENTINEL 无效（需 32 字节 ASCII 或 64 位 hex），已忽略")
    return out or INTEGRITY_SENTINELS


def mmap_find_first(path: Path, needle: bytes) -> int | None:
    """在文件里搜索第一个匹配（mmap，不把几百 MB 的 Framework 读进内存）。"""
    try:
        with open(path, "rb") as f:
            try:
                m = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
            except (ValueError, OSError):
                return None
            try:
                i = m.find(needle)
                return i if i >= 0 else None
            finally:
                m.close()
    except OSError:
        return None


def framework_targets() -> list[Path]:
    """可能存放 __asar_integrity 段的 Mach-O 文件。"""
    if FRAMEWORK_BIN.is_file():
        return [FRAMEWORK_BIN]
    # 布局变化时在 Frameworks 下按内容找
    return [p for p in P.FRAMEWORKS.rglob("Electron Framework") if p.is_file()]


def read_framework_digest() -> tuple[int, int, str] | None:
    """读取第一个哨兵槽：(used, version, digest_hex)。"""
    for path in framework_targets():
        for sent in sentinels():
            i = mmap_find_first(path, sent)
            if i is None:
                continue
            try:
                with open(path, "rb") as f:
                    f.seek(i)
                    blob = f.read(66)
                if len(blob) >= 66:
                    return blob[32], blob[33], blob[34:66].hex()
            except OSError:
                continue
    return None


def patch_framework_digest(new_digest: str) -> int:
    """把新的摘要写进 Electron Framework 的 __asar_integrity 段。

    返回改写处数（该文件含 arm64 / x86_64 两份，通常 2 处）。
    """
    want = bytes.fromhex(new_digest)
    total = 0
    for path in framework_targets():
        try:
            with open(path, "r+b") as f:
                m = mmap.mmap(f.fileno(), 0)
                try:
                    for sent in sentinels():
                        i = 0
                        while True:
                            i = m.find(sent, i)
                            if i < 0:
                                break
                            m[i + 32] = 1          # used
                            m[i + 33] = 1          # version
                            m[i + 34:i + 66] = want
                            total += 1
                            i += 66
                    m.flush()
                finally:
                    m.close()
        except OSError as e:
            print(f"  ⚠ 无法改写 {path.name}: {e}")
    return total


# ---------------------------------------------------------------- 重签

def sign_targets() -> list[Path]:
    """需要签名的嵌套代码：所有 Mach-O 文件 + 所有 bundle（深度从深到浅）。"""
    bundles, machos = [], []
    for dp, dn, fn in os.walk(APP):
        for name in dn:
            if name.endswith((".app", ".framework", ".xpc")):
                bundles.append(Path(dp) / name)
        for name in fn:
            p = Path(dp) / name
            if p.is_symlink():
                continue
            try:
                out = subprocess.run(["file", "-b", str(p)],
                                     capture_output=True, text=True).stdout
            except Exception:
                continue
            if "Mach-O" in out:
                machos.append(p)
    items = []
    for group in (machos, bundles):
        group.sort(key=lambda p: len(p.parts), reverse=True)
        items.extend(group)
    return items


# ad-hoc 签名下，Apple 签发的「受限」entitlements 无法通过 AMFI 校验：
# 进程一 exec 就被 SIGKILL，open 表现为 EBADMACHO / "Launchd job spawn failed"。
# 这些项只对 Developer ID / App Store 签名有效，本地补丁必须剔除。
RESTRICTED_ENTITLEMENTS = {
    "com.apple.application-identifier",
    "com.apple.developer.team-identifier",
    "keychain-access-groups",
}


def read_entitlements(path: Path) -> dict:
    r = subprocess.run(["codesign", "-d", "--entitlements", ":-", str(path)],
                       capture_output=True)
    if r.returncode != 0 or not r.stdout:
        return {}
    try:
        ent = plistlib.loads(r.stdout)
        return ent if isinstance(ent, dict) else {}
    except Exception:
        return {}


def scrub_entitlements(ent: dict) -> dict:
    return {k: v for k, v in ent.items()
            if k not in RESTRICTED_ENTITLEMENTS
            and not k.startswith("com.apple.developer.")}


def _sign_item(path: Path) -> bool:
    """重签单个代码对象；返回是否剔除了受限 entitlements。"""
    cmd = ["codesign", "--force", "--sign", "-", "--timestamp=none"]
    ent = read_entitlements(path)
    clean = scrub_entitlements(ent) if ent else {}
    if ent and clean != ent:
        with tempfile.NamedTemporaryFile("wb", suffix=".plist", delete=False) as f:
            plistlib.dump(clean, f)
            tmp = f.name
        try:
            subprocess.run(cmd + ["--entitlements", tmp, str(path)],
                           capture_output=True)
        finally:
            os.unlink(tmp)
        return True
    subprocess.run(cmd + ["--preserve-metadata=entitlements", str(path)],
                   capture_output=True)
    return False


def sign_all() -> tuple[bool, str]:
    """从内到外重签整个 app（ad-hoc）。

    改 Resources/ 与 Framework 二进制后，macOS 会拒绝启动签名破损的包，
    所以必须重签。ad-hoc 签名会改变应用身份，钥匙串里的旧条目可能要求重新授权
    （Electron safeStorage 会提示一次）。

    ad-hoc 不支持的受限 entitlements（team / application-identifier /
    keychain-access-groups 等）会被剔除，否则系统会在启动时直接 SIGKILL。
    """
    exe = P.executable()
    scrubbed = 0
    for item in sign_targets():
        if item == exe:
            continue
        scrubbed += 1 if _sign_item(item) else 0

    # 官方 provisioning profile 在 ad-hoc 下无效，留着会与 entitlements 校验冲突
    prof = APP / "Contents" / "embedded.provisionprofile"
    if prof.exists():
        prof.unlink()

    scrubbed += 1 if _sign_item(APP) else 0
    if scrubbed:
        print("  · 已剔除 ad-hoc 不支持的受限 entitlements（team / keychain 等）")

    r = subprocess.run(["codesign", "--verify", "--strict", str(APP)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        err = (r.stderr.strip().splitlines() or ["?"])[0][:200]
        return False, err
    subprocess.run(["xattr", "-dr", "com.apple.quarantine", str(APP)],
                   capture_output=True)
    v = subprocess.run(["codesign", "--verify", "--deep", "--strict", str(APP)],
                       capture_output=True, text=True)
    return v.returncode == 0, v.stderr.strip()[:200]


def quit_claude():
    exe_name = P.executable().name
    if subprocess.run(["pgrep", "-x", exe_name], capture_output=True).returncode != 0:
        return False
    print(f"  正在退出 {P.APP_NAME} ...")
    subprocess.run(["osascript", "-e", f'tell application "{P.APP_NAME}" to quit'],
                   capture_output=True)
    time.sleep(2)
    subprocess.run(["pkill", "-9", "-x", exe_name], capture_output=True)
    time.sleep(1)
    return True


def start_claude():
    subprocess.run(["open", str(APP)], capture_output=True)


# ---------------------------------------------------------------- sync

def cmd_sync(args):
    src, kind = ion_dist_source()
    if WORK_TREE.exists():
        shutil.rmtree(WORK_TREE)
    WORK_TREE.mkdir(parents=True)

    if kind == "dir":
        shutil.copytree(src, WORK_TREE, dirs_exist_ok=True)
        print(f"✓ 已复制松散目录 {src} → {WORK_TREE}")
    else:
        a = Asar(src)
        n = 0
        for rel, node in a.list_files():
            if node.get("unpacked"):
                continue
            dest = WORK_TREE / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(a.read(rel))
            n += 1
        print(f"✓ 已解包 {src.name} → {WORK_TREE}（{n} 个文件）")

    # 备份原始 asar / Info.plist（只备份一次，用于还原）
    WORK_BACKUP.mkdir(parents=True, exist_ok=True)
    if kind == "asar" and not WORK_BACKUP.joinpath("ion-dist.asar.orig").exists():
        shutil.copy2(src, WORK_BACKUP / "ion-dist.asar.orig")
        print(f"✓ 原始 asar 已备份 → {WORK_BACKUP / 'ion-dist.asar.orig'}")
    if not (WORK_BACKUP / "Info.plist.orig").exists():
        shutil.copy2(INFO_PLIST, WORK_BACKUP / "Info.plist.orig")
        print(f"✓ 原始 Info.plist 已备份 → {WORK_BACKUP / 'Info.plist.orig'}")

    # 记录备份对应的版本（restore 时用来提示「备份不是当前版本」）
    meta_path = WORK_BACKUP / "meta.json"
    if not meta_path.exists():
        meta = {
            "app": str(APP),
            "app_version": app_version(),
            "layout": kind,
            "asar_sha256": header_sha256(src) if kind == "asar" else None,
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    return 0


# ---------------------------------------------------------------- 翻译部署

def i18n_out_dir() -> Path:
    """部署目标 i18n 目录（松散布局写 app，asar 布局写工作树）。"""
    return (ION_DIR / "i18n") if P.layout() == "dir" else (WORK_TREE / "i18n")


def build_zh_jsons() -> list[str]:
    """以当前版本 en-US 为基座生成 zh-Hans 主/动态两套翻译。"""
    i18n = i18n_out_dir()
    en_path = i18n / "en-US.json"
    dyn_en_path = i18n / "dynamic" / "en-US.json"
    if not en_path.is_file():
        raise SystemExit(f"✗ 找不到 {en_path}（先运行 sync）")

    zh = json.loads(ZH_SRC.read_text(encoding="utf-8"))
    en = json.loads(en_path.read_text(encoding="utf-8"))

    merged = {k: zh.get(k, v) for k, v in en.items()}
    translated = sum(1 for k, v in merged.items() if v != en[k])
    added = [k for k in en if k not in zh]
    dropped = sum(1 for k in zh if k not in en)

    out = i18n / f"{LANG}.json"
    out.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ 主翻译 {out}: {len(merged)} 条 | 已译 {translated} | "
          f"新增待译 {len(added)} | 剔除旧 key {dropped}")

    # 动态文案（模型名/能力说明等，数量少但直接显示在界面上）
    if dyn_en_path.is_file():
        dyn_en = json.loads(dyn_en_path.read_text(encoding="utf-8"))
        dyn_src = PATCH_DIR / "zh-Hans-dynamic.json"
        dyn_zh_src = json.loads(dyn_src.read_text(encoding="utf-8")) if dyn_src.is_file() else {}
        dyn_zh = {k: (zh.get(k) or dyn_zh_src.get(k) or v) for k, v in dyn_en.items()}
        dyn_done = sum(1 for k, v in dyn_zh.items() if v != dyn_en[k])
        dout = i18n / "dynamic" / f"{LANG}.json"
        dout.write_text(json.dumps(dyn_zh, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        print(f"  ✓ 动态翻译 {dout}: {len(dyn_zh)} 条 | 已译 {dyn_done}"
              + (f" | 未译 {len(dyn_zh) - dyn_done}" if dyn_done < len(dyn_zh) else ""))
    return added


# ---------------------------------------------------------------- 注入

def _assets_js():
    base = i18n_out_dir().parent / "assets" / "v1"
    return sorted(base.glob("*.js")) if base.is_dir() else []


def inject_whitelist() -> int:
    """把 zh-Hans 加进语言白名单数组。

    不依赖写死的语言列表：按「以 en-US 开头、≥5 个语言码」的数组字面量识别，
    Claude 增删语言后仍然生效。
    """
    total = 0
    for js in _assets_js():
        text = js.read_text(encoding="utf-8", errors="surrogateescape")
        stats = {"seen": 0, "changed": 0}

        def _sub(m):
            try:
                arr = json.loads(m.group(0))
            except json.JSONDecodeError:
                return m.group(0)
            stats["seen"] += 1
            if LANG in arr:
                return m.group(0)
            arr.append(LANG)
            stats["changed"] += 1
            return json.dumps(arr, separators=(",", ":"))

        new_text = WHITELIST_RE.sub(_sub, text)
        if not stats["seen"]:
            continue
        if stats["changed"]:
            js.write_text(new_text, encoding="utf-8", errors="surrogateescape")
            print(f"  ✓ {js.name}: 已注入 {LANG}（{stats['changed']} 处）")
            total += stats["changed"]
        else:
            print(f"  ✓ {js.name}: 白名单已含 {LANG}")
            total += 1
    if not total:
        print("  ⚠ 未找到语言白名单数组（bundle 结构可能又变了）")
    return total


def _name_map_injected(text: str) -> bool:
    """语言选择器映射表里是否已有 zh-Hans。

    注意：bundle 里还有一个「ISO 语言名 → 本地名」大表（已含 zh-Hans），
    不能拿全局搜索当依据，必须只看选择器映射表这一段。
    """
    m = NAME_MAP_RE.search(text)
    if not m:
        return False
    return f'"{LANG}":{{name:' in text[m.start():m.start() + 900]


def _has_lang_whitelist(text: str) -> bool:
    for m in WHITELIST_RE.finditer(text):
        try:
            if LANG in json.loads(m.group(0)):
                return True
        except json.JSONDecodeError:
            continue
    return False


def inject_name_map() -> int:
    """把 zh-Hans 名称加进语言选择器的名称映射表。"""
    total = 0
    for js in _assets_js():
        text = js.read_text(encoding="utf-8", errors="surrogateescape")
        m = NAME_MAP_RE.search(text)
        if not m:
            continue
        if _name_map_injected(text):
            print(f"  ✓ {js.name}: 名称映射已含 {LANG}")
            total += 1
            continue
        new_text = text[:m.end()] + "," + NAME_MAP_ENTRY + text[m.end():]
        js.write_text(new_text, encoding="utf-8", errors="surrogateescape")
        print(f"  ✓ {js.name}: 已注入语言名称映射")
        total += 1
    if not total:
        print("  ⚠ 未找到语言名称映射（不影响主流程）")
    return total


# ---------------------------------------------------------------- stage / pack

def preflight_write():
    """打包前检查：目标可写 + 磁盘够用（失败时给出可执行的处理方式）。"""
    if not APP.is_dir():
        raise SystemExit(f"✗ 找不到 {APP}\n  用 CLAUDE_ZH_APP 指定 Claude.app 路径")
    if not P.writable([APP, RES]):
        raise SystemExit(
            f"✗ 没有写入权限: {APP}\n"
            "  · 把 Claude 拖到 ~/Applications 后安装，或\n"
            "  · 用管理员权限运行（sudo），或\n"
            "  · 用 CLAUDE_ZH_APP 指向可写的副本。")
    need = 600 * 1024 * 1024
    free = shutil.disk_usage(RES).free
    if free < need:
        raise SystemExit(
            f"✗ 磁盘空间不足: 需要约 {need // 2**20} MB，剩余 {free // 2**20} MB")


def do_stage():
    print("\n[1/2] 部署中文翻译")
    build_zh_jsons()
    print("\n[2/2] 注入语言白名单 / 名称映射")
    inject_whitelist()
    inject_name_map()


def cmd_stage(args):
    print("=" * 60)
    print(" Claude 中文化补丁 —— stage（只改工作树）")
    print(f" 目标: {RES}   版本: {app_version()}   布局: {P.layout()}")
    print("=" * 60)
    if getattr(args, "no_sync", False) and WORK_TREE.is_dir():
        print("· 跳过解包（--no-sync，沿用当前工作树）")
    else:
        cmd_sync(args)
    do_stage()
    return 0


def do_pack():
    src, kind = ion_dist_source()
    if kind == "dir":
        print("\n  ⚠ 当前是松散目录布局，无需打包")
        return
    preflight_write()
    print("\n重新打包 ion-dist.asar ...")
    quit_claude()
    tmp_out = WORK / "ion-dist.asar.new"
    stat = asar_pack(WORK_TREE, tmp_out)
    print(f"  ✓ 已生成 {stat['files']} 个文件 / {stat['bytes'] / 1e6:.1f} MB")

    # 原子替换
    dst = RES / "ion-dist.asar.tmp"
    shutil.copy2(tmp_out, dst)
    os.replace(dst, ION_ASAR)
    prev = set_plist_asar_hash(stat["header_sha256"])
    print(f"  ✓ 已写入 {ION_ASAR}")
    print(f"    头部 SHA256: {str(prev)[:16]}… → {stat['header_sha256'][:16]}…")

    # Electron 44+ 还要同步烧在 Electron Framework 里的整表摘要
    new_digest = integrity_digest(plist_integrity_dict())
    n = patch_framework_digest(new_digest)
    if n:
        cur = read_framework_digest()
        print(f"  ✓ 已更新 Electron Framework 的 integrity 摘要（{n} 处）")
        print(f"    摘要: {new_digest[:16]}…  版本={cur[1] if cur else '?'}")
    else:
        print("  ⚠ 未找到 __asar_integrity 摘要槽（旧版 Electron 无需它）")

    print("\n重新签名（资源/二进制已改动，必须重签）...")
    ok, err = sign_all()
    if ok:
        print("  ✓ 已从内到外完成 ad-hoc 重签，签名校验通过")
        print("    注意: 应用身份已变，钥匙串里的旧条目可能需要重新授权一次")
    else:
        print(f"  ✗ 重签失败：{err}")


def cmd_pack(args):
    do_pack()
    return 0


def cmd_apply(args):
    print("=" * 60)
    print(" Claude 中文化补丁（ion-dist.asar 版）")
    print(f" 目标: {RES}")
    print(f" 版本: {app_version()}   语言: {LANG}   布局: {P.layout()}")
    print("=" * 60)

    cmd_sync(args)
    do_stage()
    do_pack()

    print("\n" + "=" * 60)
    print(" 完成。启动 Claude 后若界面仍是英文：")
    print("   左下角账户 → 「语言」→ 选「中文（简体）」")
    print(" 还原: python3 zh_patch.py restore")
    print("=" * 60)
    return 0


# ---------------------------------------------------------------- verify

def cmd_resign(args):
    print("=" * 60)
    print(" 重新签名（ad-hoc，剔除受限 entitlements）")
    print("=" * 60)
    ok, err = sign_all()
    if ok:
        print("✓ 签名校验通过")
        return 0
    print(f"✗ 重签失败: {err}")
    return 1


def cmd_verify(args):
    print("=" * 60)
    print(" 校验 Claude 中文化补丁")
    print("=" * 60)
    ok = True
    print(f"\n版本: {app_version()}")

    kind = P.layout()
    src = ION_ASAR if kind == "asar" else ION_DIR
    print(f"前端布局: {kind} ({src})")

    if kind != "asar":
        zh_ok = (src / "i18n" / f"{LANG}.json").is_file()
        print(("✓" if zh_ok else "✗") + f" i18n/{LANG}.json " + ("存在" if zh_ok else "缺失"))
        return 0 if zh_ok else 1

    a = Asar(src)
    print(f"asar 文件数: {len(a.list_files())}")

    # 1) 每文件 integrity
    bad = []
    for rel, node in a.list_files():
        integ = node.get("integrity")
        if not integ:
            continue
        calc = file_integrity(a.read(rel))
        if calc["hash"] != integ["hash"] or calc["blocks"] != integ["blocks"]:
            bad.append(rel)
    if bad:
        ok = False
        print(f"✗ integrity 不符 {len(bad)} 个，例如: {bad[:3]}")
    else:
        print("✓ 每个文件的 integrity 均正确")

    # 2) 头部 hash vs Info.plist
    header_sha = header_sha256(src)
    plist_hash = plist_asar_hash()
    if header_sha == plist_hash:
        print(f"✓ Info.plist 头部 hash 匹配 ({header_sha[:16]}…)")
    else:
        ok = False
        print(f"✗ Info.plist hash 不匹配\n   实际 {header_sha}\n   plist {plist_hash}")

    # 3) 整表摘要（Electron 44+）
    want = integrity_digest(plist_integrity_dict())
    cur = read_framework_digest()
    if cur is None:
        print("⚠ 未找到 __asar_integrity 摘要槽（旧版 Electron 无需它）")
    elif cur[0] == 1 and cur[2] == want:
        print(f"✓ integrity 整表摘要匹配 ({want[:16]}…)")
    else:
        ok = False
        print(f"✗ integrity 整表摘要不匹配（used={cur[0]} version={cur[1]}）\n"
              f"   二进制 {cur[2][:32]}\n   应为   {want[:32]}")

    # 4) 中文文件与注入
    zh_ok = a.exists(f"i18n/{LANG}.json")
    print(("✓" if zh_ok else "✗") + f" i18n/{LANG}.json " + ("存在" if zh_ok else "缺失"))
    ok &= zh_ok

    dyn_ok = a.exists(f"i18n/dynamic/{LANG}.json")
    print(("✓" if dyn_ok else "✗") + f" i18n/dynamic/{LANG}.json " +
          ("存在" if dyn_ok else "缺失"))

    wl = nm = 0
    for rel, _ in a.list_files():
        if not rel.startswith("assets/v1/") or not rel.endswith(".js"):
            continue
        try:
            text = a.read(rel).decode("utf-8", "ignore")
        except Exception:
            continue
        if _has_lang_whitelist(text):
            wl += 1
        if _name_map_injected(text):
            nm += 1
    print(("✓" if wl else "✗") + f" 语言白名单注入: {wl} 个文件")
    print(("✓" if nm else "⚠") + f" 语言名称映射注入: {nm} 个文件")

    # 4) 覆盖率
    if zh_ok:
        zh = json.loads(a.read_text(f"i18n/{LANG}.json"))
        en = json.loads(a.read_text("i18n/en-US.json"))
        same = sum(1 for k, v in en.items() if zh.get(k) == v)
        print(f"\n覆盖率: {len(en) - same}/{len(en)} = "
              f"{100 * (len(en) - same) / len(en):.2f}%  (未译 {same})")

    print("\n" + ("✓ 全部通过" if ok else "✗ 存在问题"))
    return 0 if ok else 1


# ---------------------------------------------------------------- restore

def cmd_restore(args):
    kind = P.layout()
    if kind == "dir":
        # 松散目录布局：删掉中文文件即可
        quit_claude()
        removed = 0
        for f in (ION_DIR / "i18n" / f"{LANG}.json",
                  ION_DIR / "i18n" / "dynamic" / f"{LANG}.json"):
            if f.is_file():
                f.unlink()
                removed += 1
                print(f"✓ 已删除 {f}")
        ok, err = sign_all()
        print("✓ 已重新签名" if ok else f"✗ 签名失败: {err}")
        print("还原完成" + ("（没有中文文件可删）" if not removed else ""))
        return 0

    bak = WORK_BACKUP / "ion-dist.asar.orig"
    if not bak.is_file():
        raise SystemExit(
            f"✗ 没有备份文件: {bak}\n"
            "   先运行 python3 zh_patch.py sync（会把当前版本备份下来）")

    # 备份与当前版本不一致时，还原会把旧版资源塞进新版 app，先拦住
    meta_path = WORK_BACKUP / "meta.json"
    if meta_path.is_file() and not getattr(args, "force", False):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
        bak_ver, cur_ver = meta.get("app_version"), app_version()
        if bak_ver and cur_ver != "?" and bak_ver != cur_ver:
            raise SystemExit(
                f"✗ 备份来自 Claude {bak_ver}，当前是 {cur_ver}。\n"
                "  直接还原会把旧版本资源写进新版本 app，可能无法启动。\n"
                "  确认要还原请加 --force。")

    quit_claude()
    shutil.copy2(bak, ION_ASAR)
    print(f"✓ 已还原 {ION_ASAR}")

    plist_bak = WORK_BACKUP / "Info.plist.orig"
    if plist_bak.is_file():
        shutil.copy2(plist_bak, INFO_PLIST)
        print("✓ Info.plist 已还原")
    else:
        orig_sha = header_sha256(bak)
        print(f"✓ Info.plist hash 已恢复 (→ {orig_sha[:16]}…)")
        set_plist_asar_hash(orig_sha)

    # 还原后整表摘要要跟着回到原值，否则 Electron 判定整表无效
    patch_framework_digest(integrity_digest(plist_integrity_dict()))
    print("✓ Electron Framework 的 integrity 摘要已还原")

    ok, err = sign_all()
    print("✓ 已重新签名" if ok else f"✗ 签名失败: {err}")
    print("还原完成，可用 open -a Claude 启动")
    return 0


# ---------------------------------------------------------------- info

def cmd_info(args):
    print("=" * 60)
    print(" 环境信息")
    print("=" * 60)
    print(f"Claude 版本 : {app_version()}")
    try:
        kind = P.layout()
    except SystemExit as e:
        print(e)
        return 1
    src = ION_ASAR if kind == "asar" else ION_DIR
    print(f"前端布局    : {kind} → {src}")
    if kind == "asar":
        print(f"asar 大小   : {src.stat().st_size / 1e6:.1f} MB")
    print(f"Info.plist  : {plist_asar_hash()}")
    print(f"工作树      : {WORK_TREE} ({'存在' if WORK_TREE.exists() else '不存在'})")
    print(f"翻译源      : {ZH_SRC} ({'存在' if ZH_SRC.exists() else '缺失'})")
    if kind == "asar":
        a = Asar(src)
        print(f"asar 条目   : {len(a.list_files())}")
        print(f"zh-Hans     : {'已部署' if a.exists(f'i18n/{LANG}.json') else '未部署'}")
    return 0


# ---------------------------------------------------------------- status / doctor

def _coverage_from_asar(a: Asar) -> dict:
    try:
        en = json.loads(a.read_text("i18n/en-US.json"))
    except Exception:
        return {}
    zh_rel = f"i18n/{LANG}.json"
    if not a.exists(zh_rel):
        return {"total": len(en), "translated": 0,
                "untranslated": len(en), "percent": 0.0}
    try:
        zh = json.loads(a.read_text(zh_rel))
    except Exception:
        return {}
    same = sum(1 for k, v in en.items() if zh.get(k) == v)
    total = len(en) or 1
    return {
        "total": len(en),
        "translated": len(en) - same,
        "untranslated": same,
        "percent": round(100 * (len(en) - same) / total, 2),
    }


def _scan_injections(a: Asar) -> tuple[int, int]:
    """统计语言白名单 / 名称映射已注入的 JS 文件数（较慢，深度检查时才做）。"""
    wl = nm = 0
    for rel, _ in a.list_files():
        if not rel.startswith("assets/v1/") or not rel.endswith(".js"):
            continue
        try:
            text = a.read(rel).decode("utf-8", "ignore")
        except Exception:
            continue
        if _has_lang_whitelist(text):
            wl += 1
        if _name_map_injected(text):
            nm += 1
    return wl, nm


def collect_status(deep: bool = False) -> dict:
    """GUI / doctor 用的结构化状态（只读，不改动 app）。"""
    d = P.describe()
    patch = {
        "applied": False,
        "zh_deployed": False,
        "dynamic_zh_deployed": False,
        "header_hash_ok": None,
        "integrity_digest_ok": None,
        "whitelist_files": None,
        "name_map_files": None,
    }
    d["patch"] = patch

    kind = d.get("layout")
    if not kind:
        d["llm"] = _llm_status()
        return d

    if kind == "asar":
        try:
            a = Asar(ION_ASAR)
            d["asar_files"] = len(a.list_files())
            patch["zh_deployed"] = a.exists(f"i18n/{LANG}.json")
            patch["dynamic_zh_deployed"] = a.exists(f"i18n/dynamic/{LANG}.json")
            patch["applied"] = patch["zh_deployed"]
            patch["header_hash_ok"] = header_sha256(ION_ASAR) == plist_asar_hash()
            want = integrity_digest(plist_integrity_dict())
            cur = read_framework_digest()
            patch["integrity_digest_ok"] = (
                None if cur is None else (cur[0] == 1 and cur[2] == want))
            d["coverage"] = _coverage_from_asar(a)
            if deep:
                wl, nm = _scan_injections(a)
                patch["whitelist_files"], patch["name_map_files"] = wl, nm
        except Exception as e:
            d["error"] = f"{type(e).__name__}: {e}"
    else:
        patch["zh_deployed"] = (ION_DIR / "i18n" / f"{LANG}.json").is_file()
        patch["dynamic_zh_deployed"] = (
            ION_DIR / "i18n" / "dynamic" / f"{LANG}.json").is_file()
        patch["applied"] = patch["zh_deployed"]

    d["llm"] = _llm_status()
    return d


def _llm_status() -> dict:
    try:
        import llm_endpoint
        base, model = llm_endpoint.discover()
        return {"available": True, "base": base, "model": model}
    except Exception as e:
        return {"available": False, "error": str(e)}


def cmd_status(args):
    print(json.dumps(collect_status(deep=args.deep), ensure_ascii=False, indent=2))
    return 0


def doctor_checks() -> list[dict]:
    """环境体检项：每一项给出 ok / 说明 / 修复建议。"""
    status = collect_status(deep=True)
    checks = []

    def add(name, ok, detail="", hint=""):
        checks.append({"name": name, "ok": ok, "detail": detail, "hint": hint})

    add("macOS", status["macos"], f"{sys.platform}")
    add("Python ≥ 3.10", status["python_ok"], status["python"],
        "brew install python@3.13")
    add("codesign", status["codesign"], shutil.which("codesign") or "",
        "xcode-select --install")
    add("Claude.app", status["app_exists"], status["app"],
        "用 CLAUDE_ZH_APP=/path/to/Claude.app 指定")
    add("写入权限", status["writable"], "app 目录可写" if status["writable"] else "不可写",
        "把 Claude 移到 ~/Applications 或用管理员权限运行")
    add("前端布局", bool(status["layout"]), str(status["layout"]),
        "确认 Claude 版本；旧版为 dir，新版为 asar")
    add("翻译源", status["translation_entries"] > 0,
        f"{status['translation_entries']} 条", "检查 zh-Hans-final.json 是否存在")
    add("工作树", status["work_tree"], status["work"],
        "运行 python3 zh_patch.py sync")
    sentinel = read_framework_digest() is not None
    add("Electron integrity 槽", sentinel,
        "已找到" if sentinel else "未找到（旧版 Electron 可能不需要）",
        "版本过新时可用 CLAUDE_ZH_SENTINEL 覆盖哨兵")
    llm = status.get("llm") or {}
    add("本地 LLM 端点", bool(llm.get("available")),
        llm.get("model", "") if llm.get("available") else "未探测到",
        "设置 CLAUDE_ZH_API / CLAUDE_ZH_MODEL 指向任意 OpenAI 兼容端点")
    return checks


def cmd_doctor(args):
    checks = doctor_checks()
    if args.json:
        print(json.dumps({"checks": checks}, ensure_ascii=False, indent=2))
        return 0
    print("=" * 64)
    print(" 环境体检")
    print("=" * 64)
    for c in checks:
        mark = "✓" if c["ok"] else "✗"
        print(f"  {mark} {c['name']:<22} {c['detail']}")
        if not c["ok"] and c["hint"]:
            print(f"    → {c['hint']}")
    hard = [c for c in checks if c["name"] in
            {"macOS", "Python ≥ 3.10", "codesign", "Claude.app", "写入权限", "前端布局"}]
    ok = all(c["ok"] for c in hard)
    print("\n" + ("✓ 环境可用" if ok else "✗ 存在阻塞项，按上面提示处理后重试"))
    return 0 if ok else 1


# ---------------------------------------------------------------- main

def main():
    P.require_env()
    ap = argparse.ArgumentParser(description="Claude Desktop 中文化补丁（asar 版）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sync", help="从 Claude.app 抽取 ion-dist 到工作树")
    s.set_defaults(func=cmd_sync)

    t = sub.add_parser("stage", help="部署翻译并注入（不打包）")
    t.add_argument("--no-sync", action="store_true",
                   help="工作树已是最新时跳过解包")
    t.set_defaults(func=cmd_stage)

    k = sub.add_parser("pack", help="工作树 → asar，并更新 Info.plist")
    k.set_defaults(func=cmd_pack)

    a = sub.add_parser("apply", help="sync + stage + pack（含重签）")
    a.set_defaults(func=cmd_apply)

    sg = sub.add_parser("resign", help="重新 ad-hoc 签名（剔除受限 entitlements）")
    sg.set_defaults(func=cmd_resign)

    v = sub.add_parser("verify", help="校验当前补丁状态")
    v.set_defaults(func=cmd_verify)

    r = sub.add_parser("restore", help="还原官方原版")
    r.add_argument("--force", action="store_true",
                   help="备份版本与当前版本不一致时仍然还原")
    r.set_defaults(func=cmd_restore)

    i = sub.add_parser("info", help="打印环境信息")
    i.set_defaults(func=cmd_info)

    st = sub.add_parser("status", help="输出 JSON 状态（供 GUI 使用）")
    st.add_argument("--deep", action="store_true", help="额外扫描语言注入（较慢）")
    st.set_defaults(func=cmd_status)

    d = sub.add_parser("doctor", help="环境体检")
    d.add_argument("--json", action="store_true")
    d.set_defaults(func=cmd_doctor)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())