#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Impor C 运行时跨平台审计（4.7.1）。

本机是 Windows，无法实际编译 Linux/macOS 产物，所以这一层只做**静态审计**：
  - 找出所有「条件里带 _WIN32」的预处理块，标记它有没有 `#else` 分支；
  - 找出不在任何 `_WIN32` 保护块里的 Windows API 直接调用（这些在 Linux 上会直接编译失败）；
  - 按「功能缺失是否致命」分类，输出阻塞项清单。

结论只对源码负责，不对「已在 Linux 上跑通」负责——那一句必须由 CI 实跑来给，
所以本工具的输出明确区分 `verdict`（静态结论）与 `ci_required`（还需 CI 验证）。
"""

import json
import os
import re
import sys

# POSIX 上必须有等价实现的核心能力（出现在无保护位置 = 阻塞发布）
FATAL_APIS = {
    "CreateFileW": "文件打开",
    "CreateFileA": "文件打开",
    "MultiByteToWideChar": "UTF-8 ↔ UTF-16 转换",
    "WideCharToMultiByte": "UTF-16 → UTF-8 转换",
    "InitializeConditionVariable": "条件变量",
    "SleepConditionVariableCS": "条件变量等待",
    "WakeConditionVariable": "条件变量唤醒",
    "WakeAllConditionVariable": "条件变量广播",
    "_beginthreadex": "线程创建",
    "CreateThread": "线程创建",
    "QueryPerformanceCounter": "高精度计时",
    "GetTickCount64": "计时",
    "VirtualAlloc": "大块内存",
    "LoadLibraryA": "动态库加载",
    "LoadLibraryW": "动态库加载",
    "GetProcAddress": "动态库符号",
    "GetModuleFileNameW": "可执行文件路径",
    "SetConsoleOutputCP": "控制台代码页",
    "lstrlenW": "宽字符串长度",
    "WSAStartup": "socket 初始化",
}

# 允许无 POSIX 等价（Windows 专有功能，Linux 上没有也不影响语言语义）
OK_APIS = {
    "windows.h": "Windows 头（被 _WIN32 包住即可）",
    "__declspec": "MSVC 导出标记",
    "__stdcall": "x86 调用约定",
    "IMPOR_BUILD_DLL": "DLL 构建开关",
    "Shell_NotifyIcon": "托盘图标",
    "apply_icon": "PE 图标资源",
    "windres": "PE 资源编译器",
    "MultiByteToWideChar(": "（若在保护块内则免报）",
}

INCLUDE_RE = re.compile(r"#\s*include\s*<([^>]+)>")
IF_RE = re.compile(r"#\s*(if|ifdef|ifndef|elif|else|endif)\b(.*)")


def _strip_comments(lines):
    """去掉 /* */ 与 // 注释（保持行号一一对应）。

    不做这一步会把注释里提到的 API 当成真调用——c_runtime.c 顶部有一段
    「Windows 用 CONDITION_VARIABLE + SleepConditionVariableCS」的说明，
    第一次跑审计就把它报成了致命阻塞项（假阳性）。
    """
    out = []
    in_block = False
    for line in lines:
        res = []
        i = 0
        n = len(line)
        while i < n:
            if in_block:
                j = line.find("*/", i)
                if j == -1:
                    i = n
                    break
                i = j + 2
                in_block = False
                continue
            j = line.find("/*", i)
            k = line.find("//", i)
            if k != -1 and (j == -1 or k < j):
                res.append(line[i:k])
                i = n
                break
            if j == -1:
                res.append(line[i:])
                break
            res.append(line[i:j])
            i = j + 2
            in_block = True
        out.append("".join(res))
    return out


def scan(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    code = _strip_comments(lines)
    stack = []
    blocks = []
    naked = []
    includes_outside = []
    for lineno, line in enumerate(code, 1):
        m = IF_RE.match(line.strip())
        if m:
            kind, rest = m.group(1), m.group(2).strip()
            if kind in ("if", "ifdef", "ifndef"):
                win = ("_WIN32" in rest or "_WIN64" in rest
                       or re.search(r"\bWIN32\b", rest) is not None)
                stack.append({"line": lineno, "cond": rest, "win": win,
                              "has_else": False, "apis": []})
            elif kind == "elif":
                if stack and ("_WIN32" in rest or "_WIN64" in rest):
                    stack[-1]["win"] = True
            elif kind == "else":
                if stack:
                    stack[-1]["has_else"] = True
            elif kind == "endif":
                if stack:
                    blocks.append(stack.pop())
            continue
        inc = INCLUDE_RE.search(line)
        if inc and not any(b["win"] for b in stack):
            includes_outside.append({"line": lineno, "include": inc.group(1),
                                     "text": line.strip()})
        for api, what in FATAL_APIS.items():
            if api in line and not any(b["win"] for b in stack):
                naked.append({"line": lineno, "api": api, "what": what,
                              "text": line.strip()[:110]})
            elif api in line and stack:
                stack[-1]["apis"].append(api)
        for api in OK_APIS:
            if api == "MultiByteToWideChar(":
                continue
            if api in line and not any(b["win"] for b in stack):
                naked.append({"line": lineno, "api": api, "what": "（见 OK_APIS，非致命）",
                              "text": line.strip()[:110]})
    while stack:
        blocks.append(stack.pop())
    win_blocks = [b for b in blocks if b["win"]]
    return {
        "file": path,
        "lines": len(lines),
        "if_blocks": len(blocks),
        "win_blocks": len(win_blocks),
        "win_with_else": sum(1 for b in win_blocks if b["has_else"]),
        "win_without_else": [{"line": b["line"], "cond": b["cond"],
                              "apis": sorted(set(b["apis"]))}
                             for b in win_blocks if not b["has_else"]],
        "naked": naked,
        "includes_outside_guard": includes_outside,
    }


def verdict(rep):
    fatal = [n for n in rep["naked"]
             if n["api"] in FATAL_APIS]
    return {
        "ok": not fatal,
        "fatal_count": len(fatal),
        "fatal": fatal,
        "note": ("静态审计通过：所有致命 Windows API 都在 _WIN32 保护块内"
                 if not fatal else
                 "存在 %d 处无保护的 Windows API 调用，Linux 上会编译失败" % len(fatal)),
        "ci_required": True,
    }


def main(argv):
    targets = argv[1:]
    if not targets:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        targets = [os.path.join(root, "impor", "c_runtime.c")]
    out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs")
    report = {"files": []}
    rc = 0
    for t in targets:
        if not os.path.isfile(t):
            print("跳过（不存在）：%s" % t)
            continue
        rep = scan(t)
        rep["verdict"] = verdict(rep)
        report["files"].append(rep)
        print("== %s ==" % os.path.basename(t))
        print("  行数 %d / 预处理块 %d / 含 _WIN32 的块 %d（其中 %d 有 #else）"
              % (rep["lines"], rep["if_blocks"], rep["win_blocks"], rep["win_with_else"]))
        print("  %s" % rep["verdict"]["note"])
        if rep["naked"]:
            print("  无保护调用（%d）：" % len(rep["naked"]))
            for n in rep["naked"][:12]:
                print("    第 %d 行 %s（%s）" % (n["line"], n["api"], n["what"]))
        if rep["win_without_else"]:
            print("  仅 Windows 实现的块（%d，逐个确认是否 POSIX 必需）："
                  % len(rep["win_without_else"]))
            for b in rep["win_without_else"][:12]:
                print("    第 %d 行 %s → %s"
                      % (b["line"], b["cond"] or "(无条件)",
                         ", ".join(b["apis"]) or "（无致命 API）"))
        if rep["includes_outside_guard"]:
            print("  未受保护的 include（%d）：%s"
                  % (len(rep["includes_outside_guard"]),
                     ", ".join(i["include"] for i in rep["includes_outside_guard"])[:120]))
        if not rep["verdict"]["ok"]:
            rc = 1
    try:
        os.makedirs(out_dir, exist_ok=True)
        p = os.path.join(out_dir, "posix-audit.json")
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print("报告：%s" % p)
    except OSError as exc:
        print("报告写入失败：%s" % exc)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
