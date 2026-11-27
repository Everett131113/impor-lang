"""用 zig 交叉编译 Impor 的完整后端 C 产物，产出 Linux/macOS 真实 ELF/Mach-O。

本机无 Linux 内核，因此这里只证明「生成 C → 交叉编译 → 链接成功 → 产物目标正确」，
不声称运行通过。

用法: python tools/cross_build.py <src.impor> [out_dir]
"""
import json
import os
import subprocess
import sys

WT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZIG = r"D:\ImporWork\toolchains\zig\zig-windows-x86_64-0.13.0\zig.exe"

TARGETS = [
    ("x86_64-linux-musl", "linux64-musl"),
    ("aarch64-linux-musl", "linux-arm64-musl"),
    ("x86_64-macos", "macos-x64"),
    ("aarch64-macos", "macos-arm64"),
]


def elf_info(path):
    with open(path, "rb") as f:
        head = f.read(64)
    info = {"size": os.path.getsize(path)}
    if head[:4] == b"\x7fELF":
        info["format"] = "ELF"
        info["class"] = {1: "32-bit", 2: "64-bit"}.get(head[4], str(head[4]))
        info["endian"] = {1: "LE", 2: "BE"}.get(head[5], "?")
        info["type"] = {2: "EXEC", 3: "DYN(PIE)", 1: "REL"}.get(int.from_bytes(head[16:18], "little"))
        info["machine"] = {62: "x86-64", 183: "aarch64", 3: "i386"}.get(
            int.from_bytes(head[18:20], "little"), str(int.from_bytes(head[18:20], "little")))
        # 静态性：有 .interp 段说明是动态链接
        phoff = int.from_bytes(head[32:40], "little")
        phentsize = int.from_bytes(head[54:56], "little")
        phnum = int.from_bytes(head[56:58], "little")
        has_interp = False
        with open(path, "rb") as f:
            for i in range(phnum):
                f.seek(phoff + i * phentsize)
                ph = f.read(phentsize)
                if not ph:
                    break
                if int.from_bytes(ph[0:4], "little") == 3:  # PT_INTERP
                    has_interp = True
        info["link"] = "dynamic(有 PT_INTERP)" if has_interp else "static(无 PT_INTERP)"
    elif head[:4] == b"\xcf\xfa\xed\xfe":
        info["format"] = "Mach-O 64"
        cputype = int.from_bytes(head[4:8], "little")
        info["machine"] = {0x01000007: "x86-64", 0x0100000C: "arm64"}.get(cputype, str(cputype))
        info["filetype"] = {2: "EXECUTE", 6: "DYLIB"}.get(int.from_bytes(head[12:16], "little"))
    else:
        info["format"] = "unknown"
    return info


def main():
    src = sys.argv[1]
    out_dir = sys.argv[2] if len(sys.argv) > 2 else os.path.join(WT, "dist-cross")
    os.makedirs(out_dir, exist_ok=True)

    sys.path.insert(0, WT)
    from impor.cli import strip_header
    from impor.interpreter import parse_source
    from impor.ctrans import c_transpile

    raw = open(src, encoding="utf-8").read()
    body = strip_header(raw)
    ast = parse_source(body, src)
    stats = {}
    c_code = c_transpile(ast, source_name=src, stats=stats, source_lines=body.splitlines())
    base = os.path.splitext(os.path.basename(src))[0]
    cpath = os.path.join(out_dir, base + ".c")
    with open(cpath, "w", encoding="utf-8") as f:
        f.write(c_code)
    print("C 生成:", cpath, len(c_code), "字节")

    results = {}
    for triple, tag in TARGETS:
        out = os.path.join(out_dir, "%s-%s" % (base, tag))
        cmd = [ZIG, "cc", "-target", triple, "-O2", "-static", cpath, "-o", out, "-lm"]
        p = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=600)
        if p.returncode != 0:
            results[tag] = {"rc": p.returncode, "err": (p.stdout + p.stderr)[-1500:]}
            print("[FAIL] %-18s %s" % (tag, results[tag]["err"].splitlines()[-1][:150] if results[tag]["err"] else ""))
            continue
        info = elf_info(out)
        results[tag] = info
        print("[OK]   %-18s %-14s %-8s %-9s %-16s %10d B  %s" % (
            tag, info.get("format"), info.get("class", ""), info.get("type", ""),
            info.get("machine", ""), info["size"], info.get("link", "")))
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if all("rc" not in v for v in results.values()) else 2


if __name__ == "__main__":
    sys.exit(main())
