# -*- coding: utf-8 -*-
"""校验产物机器类型：PE / ELF / Mach-O。

用法：
    python tools/check_artifact.py --expect win32 out/ci_lib.dll
    python tools/check_artifact.py --expect linux-arm64 out/libci_lib.so
"""
import argparse
import struct
import sys

PE_MACHINES = {"win32": 0x014c, "win64": 0x8664}
ELF_MACHINES = {"linux64": 62, "linux-arm64": 183}
MACHO_CPUS = {"macos-x64": 0x01000007, "macos-arm64": 0x0100000c}


def _read(path, start, size):
    with open(path, "rb") as f:
        f.seek(start)
        return f.read(size)


def pe_machine(path):
    with open(path, "rb") as f:
        head = f.read(64)
        if len(head) < 64 or head[:2] != b"MZ":
            return None
        pe = struct.unpack_from("<I", head, 0x3c)[0]
    sig = _read(path, pe, 4)
    if sig != b"PE\0\0":
        return None
    return struct.unpack("<H", _read(path, pe + 4, 2))[0]


def elf_machine(path):
    ident = _read(path, 0, 20)
    if len(ident) < 20 or ident[:4] != b"\x7fELF":
        return None
    order = "<" if ident[5] == 1 else ">"
    return struct.unpack(order + "H", ident[18:20])[0]


def macho_cpu(path):
    magic = _read(path, 0, 8)
    if len(magic) < 8:
        return None
    # 64 位 Mach-O：0xfeedfacf；32 位：0xfeedface
    if struct.unpack("<I", magic[:4])[0] in (0xfeedfacf, 0xfeedface):
        return struct.unpack("<I", magic[4:8])[0]
    if struct.unpack(">I", magic[:4])[0] in (0xfeedfacf, 0xfeedface):
        return struct.unpack(">I", magic[4:8])[0]
    return None


def check(path, expect):
    if expect in PE_MACHINES:
        got = pe_machine(path)
        want = PE_MACHINES[expect]
        kind = "PE Machine"
    elif expect in ELF_MACHINES:
        got = elf_machine(path)
        want = ELF_MACHINES[expect]
        kind = "ELF e_machine"
    elif expect in MACHO_CPUS:
        got = macho_cpu(path)
        want = MACHO_CPUS[expect]
        kind = "Mach-O cputype"
    else:
        print("未知目标：%s" % expect, file=sys.stderr)
        return 2
    if got is None:
        print("无法识别产物格式：%s" % path, file=sys.stderr)
        return 2
    print("%s = 0x%x（期望 0x%x）" % (kind, got, want))
    if got != want:
        print("机器类型不匹配：%s != %s" % (got, want), file=sys.stderr)
        return 1
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--expect", required=True,
                    choices=sorted(PE_MACHINES) + sorted(ELF_MACHINES)
                    + sorted(MACHO_CPUS))
    ap.add_argument("path")
    args = ap.parse_args()
    return check(args.path, args.expect)


if __name__ == "__main__":
    sys.exit(main())
