# -*- coding: utf-8 -*-
"""Impor 3.2.1 安全内核（本地、可审计、默认拒绝）。

边界说明（诚实声明）：
  - 这是应用层安全模型，不是操作系统沙箱。Impor 仍以当前用户身份运行。
  - 能防住：路径穿越、越界写入、未授权插件写文件/联网/启动子进程、未登记的原生库加载、
    符号执行/半截文件、供应链哈希不符、审计缺失。
  - 防不住：同用户权限下的任意代码执行、内核级攻击、物理访问、ctypes 原生调用绕过
    Python audit hook。需要更强边界时应使用受限账户、AppContainer 或虚拟机。

核心策略：
  1. 默认拒绝：插件写、FFI、网络能力默认关闭，需显式登记。
  2. 路径围栏：所有写路径必须落在工作区（commonpath 校验，解析符号链接）。
  3. 原子写：临时文件 + os.replace，断电不会留下半截源文件。
  4. 供应链：发布物 SHA256 清单，安装/更新前校验。
  5. 审计：关键动作追加 JSONL，失败不静默。
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time

SECURITY_VERSION = "3.2.1"


def _real(path):
    return os.path.realpath(os.path.abspath(str(path)))


def inside(root, path):
    """路径是否在 root 之内（解析符号链接后比较）。"""
    try:
        return os.path.commonpath([_real(root), _real(path)]) == _real(root)
    except ValueError:
        return False


def require_inside(root, path, what="路径"):
    target = _real(path)
    if not inside(root, target):
        raise PermissionError("%s 越界：%s 不在工作区 %s 内" % (what, target, _real(root)))
    return target


def safe_write(path, content, root=None, encoding="utf-8", newline="\n"):
    """原子写：同目录临时文件 + os.replace；可选工作区围栏。"""
    target = _real(path)
    if root is not None:
        require_inside(root, target, "写入目标")
    directory = os.path.dirname(target) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".impor_sec_", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline=newline) as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return target


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            data = f.read(chunk)
            if not data:
                break
            h.update(data)
    return h.hexdigest().upper()


def verify_manifest(path, manifest_path):
    """校验发布物哈希；清单格式 {"files": {"name": "SHA256"}} 或 {"sha256": "..."}。"""
    with open(manifest_path, encoding="utf-8") as f:
        data = json.load(f)
    expected = None
    name = os.path.basename(path)
    files = data.get("files")
    if isinstance(files, dict):
        expected = files.get(name)
    if expected is None:
        expected = data.get("sha256")
    if not expected:
        return False, "清单中没有 %s 的哈希" % name
    actual = sha256_file(path)
    return (actual == str(expected).upper(), "实际 %s / 期望 %s" % (actual, expected))


class AuditLog:
    """追加式审计日志（JSONL）。默认写到 <workspace>/.impor/audit.log。"""

    def __init__(self, root):
        self.root = _real(root or ".")
        self.path = os.path.join(self.root, ".impor", "audit.log")

    def write(self, event, **fields):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            record = {"ts": round(time.time(), 3), "event": str(event),
                      "security": SECURITY_VERSION}
            record.update({str(k): v for k, v in fields.items()})
            with open(self.path, "a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            return True
        except Exception:
            return False

    def tail(self, n=50):
        try:
            with open(self.path, encoding="utf-8", errors="replace") as f:
                return [json.loads(x) for x in f.read().splitlines()[-n:] if x.strip()]
        except Exception:
            return []


def harden_process():
    """尽力而为的进程加固：关闭 core dump、限制递归、清理危险环境变量。"""
    notes = []
    try:
        import resource  # 仅 Unix
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        notes.append("core dump 已关闭")
    except Exception:
        pass
    for key in ("PYTHONINSPECT", "PYTHONSTARTUP", "PYTHONPATH"):
        if key in os.environ:
            os.environ.pop(key, None)
            notes.append("已清理环境变量 " + key)
    return notes


THREAT_MODEL = {
    "assets": ["用户源码", "工作区文件", "编译器与发布物", "插件进程", "更新通道"],
    "trust_boundaries": [
        "编辑器主进程 ↔ 插件子进程（进程隔离 + 代理 API）",
        "工作区 ↔ 系统其余路径（commonpath 围栏）",
        "脚本 ↔ 原生库（FFI 允许清单）",
        "发布物 ↔ 用户机器（SHA256 校验）",
    ],
    "mitigations": [
        "默认拒绝写/FFI/网络/子进程", "原子写", "符号链接解析后校验", "插件权限清单 + audit hook 强制",
        "崩溃自动重启上限", "JSONL 审计", "供应链哈希", "诊断四段式（含安全类）",
    ],
    "residual_risks": [
        "同用户权限下的恶意代码仍可绕过应用层限制",
        "插件 audit hook 只覆盖 Python 级事件，ctypes/原生扩展可绕过",
        "插件子进程未做 OS 级沙箱（AppContainer/Job Object 未启用）",
        "FFI 参数仅支持原始类型，但被允许的库本身可能危险",
        "更新通道依赖 HTTPS 与哈希清单，未做签名验证",
    ],
}
