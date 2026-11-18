# -*- coding: utf-8 -*-
"""Impor 系统编程能力审计。

只报告可验证的事实，不把“有 C 后端”当成“系统编程可用”。
"""
import json
os_path = __import__("os").path
import sys
import struct


def collect(root=None):
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    root = root or os.path.dirname(here)
    checks = []

    def add(name, status, detail):
        checks.append({"name": name, "status": status, "detail": detail})

    add("C 后端", "OK" if os.path.isfile(os.path.join(here, "tcc", "win32", "tcc.exe")) else "MISSING",
        "TinyCC 转译，可出独立 exe")
    add("解释器位宽", "OK", "%d 位（%s）" % (8 * struct.calcsize("P"), sys.platform))
    add("自举编译器", "OK" if os.path.isfile(os.path.join(here, "imporc.exe")) else "MISSING",
        "imporc.exe")
    add("FFI 基础调用", "OK",
        "解释器 + C 后端已支持（int/float/bool/cstr，最多 8 参数；C 后端需字面量 argtypes）")
    add("FFI 指针", "PARTIAL", "解释器支持受控缓冲区 token；C 后端拒绝任意指针；不接受裸地址")
    add("FFI 结构体/回调", "BLOCKED", "无结构体布局、无回调")
    add("稳定 C ABI", "PARTIAL", "已有头文件/.def/导出符号；内置 tcc 产物为 32 位 i386")
    add("产物位宽", "PARTIAL", "内置 tcc 产出 32 位(i386)；64 位工程请用 --c-source 以 cl / gcc -m64 编译")
    add("自动内存管理", "PARTIAL", "运行时自动回收；语言层不暴露 malloc/free；热路径分配规则未冻结")
    add("线程/原子/锁", "BLOCKED", "无运行时原语")
    add("直接 syscall/mmap", "BLOCKED", "无系统调用层")
    add("freestanding", "BLOCKED", "仍依赖 TinyCC/运行时")
    add("LLVM/Cranelift 后端", "BLOCKED", "未实现")
    ready = all(c["status"] == "OK" for c in checks if c["name"] in
                ("FFI 指针", "FFI 结构体/回调", "稳定 C ABI", "自动内存管理",
                 "线程/原子/锁", "直接 syscall/mmap", "freestanding"))
    from impor.version import __version__
    return {"version": __version__, "root": root, "systems_ready": ready, "checks": checks}


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="impor sys-check")
    ap.add_argument("--json", default="")
    ap.add_argument("--strict", action="store_true", help="未就绪时退出码 2")
    args = ap.parse_args(argv or [])
    report = collect()
    print("Impor 系统编程能力审计")
    for c in report["checks"]:
        print("  [%s] %-18s %s" % (c["status"], c["name"], c["detail"]))
    print("系统编程就绪：%s" % ("YES" if report["systems_ready"] else "NO"))
    if args.json:
        import io
        with io.open(args.json, "w", encoding="utf-8", newline="\n") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print("报告：" + args.json)
    if args.strict and not report["systems_ready"]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
