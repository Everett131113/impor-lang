# -*- coding: utf-8 -*-
"""Impor 自有的 PE32+ 链接器（零外部工具链）。

把 x64asm 编出的机器码、字符串字面量与 Windows 导入表组装成一个可直接运行的
64 位 PE（.exe）：自己构造 DOS/PE/可选头、节区头与导入描述符，自己回填 IAT 与
数据符号的 RIP 相对引用，不调用任何汇编器、链接器，也不产生重定位表（固定
ImageBase，exe 标准基址通常可直接加载）。

仅覆盖 Impor 本后端所需，导入 kernel32.dll 的控制台/进程函数。
"""
import struct

IMAGE_BASE = 0x0000000140000000
SECTION_ALIGN = 0x1000
FILE_ALIGN = 0x200

TEXT_RVA = 0x1000

# IMAGE_FILE_HEADER.Characteristics
F_EXEC = 0x0002
F_LARGE = 0x0020
# IMAGE_OPTIONAL_HEADER64.DllCharacteristics
DLL_NX = 0x0100
DLL_TERM = 0x8000

# 节区标志
SCN_CNT_CODE = 0x00000020
SCN_MEM_EXEC = 0x20000000
SCN_MEM_READ = 0x40000000
SCN_CNT_INIT = 0x00000040
SCN_MEM_WRITE = 0x80000000


def _align(v, n):
    return (v + n - 1) & ~(n - 1)


class _RData:
    """累积 .rdata 字节，按对齐分配并记录节内偏移。"""

    def __init__(self):
        self.buf = bytearray()

    def align(self, n):
        while len(self.buf) % n:
            self.buf.append(0)

    def add(self, data):
        off = len(self.buf)
        self.buf += bytes(data)
        return off


def _dos_header(pe_off):
    h = bytearray(64)
    h[0:2] = b"MZ"
    struct.pack_into("<I", h, 0x3C, pe_off)
    return bytes(h)


def _coff_header(num_sections, opt_size):
    return struct.pack(
        "<HHIIIHH",
        0x8664,                 # Machine = AMD64
        num_sections,
        0,                      # TimeDateStamp
        0, 0,                   # 符号表
        opt_size,
        F_EXEC | F_LARGE,
    )


def _section_header(name, vsize, rva, raw_size, raw_ptr, flags):
    nm = name.encode("ascii")[:8].ljust(8, b"\0")
    # VirtualSize/Address/RawSize/RawPtr/RelocPtr/LinePtr, RelocNum/LineNum, Characteristics
    return nm + struct.pack("<IIIIIIHHI",
                           vsize, rva, raw_size, raw_ptr, 0, 0, 0, 0, flags)


def link_exe(asm, strings, imports, entry_symbol):
    """组装 PE 字节。

    asm: 已构造好的 x64asm.Asm（无需先 finish，本函数统一处理）
    strings: {符号: bytes} 字符串字面量
    imports: {dll名: [函数名, ...]}
    entry_symbol: 入口标签（asm 内）
    """
    # 1) 先解析内部标签，得到含外部占位（仍为0）的代码
    code = bytearray(asm.finish())

    # 2) 布局 .text
    text_raw = _align(len(code), FILE_ALIGN)
    text_mem = _align(len(code), SECTION_ALIGN)

    # 3) 构建 .rdata
    rd = _RData()
    rdata_rva = TEXT_RVA + text_mem
    symbol_rva = {}

    # 3.1 字符串字面量（2 字节对齐）
    for sym, data in strings.items():
        rd.align(2)
        off = rd.add(data)
        symbol_rva[sym] = rdata_rva + off

    # 3.2 IMAGE_IMPORT_BY_NAME（每个函数一个）
    by_name_rva = {}
    for dll, funcs in imports.items():
        for fn in funcs:
            rd.align(2)
            off = rd.add(struct.pack("<H", 0) + fn.encode("ascii") + b"\0")
            by_name_rva[fn] = rdata_rva + off

    # 3.3 DLL 名
    dll_name_rva = {}
    for dll in imports:
        off = rd.add(dll.encode("ascii") + b"\0")
        dll_name_rva[dll] = rdata_rva + off

    # 3.4 ILT（每个 DLL，8 字节项 + 末尾 0）
    ilt_rva = {}
    for dll, funcs in imports.items():
        rd.align(8)
        ilt_rva[dll] = rdata_rva + len(rd.buf)
        for fn in funcs:
            rd.add(struct.pack("<Q", by_name_rva[fn]))
        rd.add(struct.pack("<Q", 0))

    # 3.5 IAT（布局同 ILT；每个函数项以函数名登记为符号）
    iat_start = None
    for dll, funcs in imports.items():
        rd.align(8)
        table_rva = rdata_rva + len(rd.buf)
        if iat_start is None:
            iat_start = table_rva
        for fn in funcs:
            symbol_rva[fn] = rdata_rva + len(rd.buf)   # IAT 项 RVA
            rd.add(struct.pack("<Q", by_name_rva[fn]))
        rd.add(struct.pack("<Q", 0))
    iat_total = (rdata_rva + len(rd.buf)) - iat_start

    # 3.6 IID 数组（每个 DLL 20 字节 + 末尾全 0）
    rd.align(8)
    iid_rva = rdata_rva + len(rd.buf)
    for dll, funcs in imports.items():
        rd.add(struct.pack("<IIIII",
                           ilt_rva[dll], 0, 0,
                           dll_name_rva[dll],
                           _iat_first(symbol_rva, funcs)))
    rd.add(bytes(20))
    iid_size = len(imports) * 20 + 20

    rdata_raw = _align(len(rd.buf), FILE_ALIGN)
    rdata_mem = _align(len(rd.buf), SECTION_ALIGN)

    # 4) 回填外部 fixup（IAT 与字符串），RIP 相对
    for at, target, length, start in asm.extern_fixups():
        if target not in symbol_rva:
            raise ValueError("链接期未解析外部符号: %s" % target)
        tgt = symbol_rva[target]
        disp = tgt - (TEXT_RVA + at + length)
        struct.pack_into("<i", code, at, disp)

    # 5) 入口 RVA
    if entry_symbol not in asm.labels:
        raise ValueError("入口符号未定义: %s" % entry_symbol)
    entry_rva = TEXT_RVA + asm.labels[entry_symbol]

    # 6) 组装头部
    num_sections = 2
    opt_size = 240
    headers_size = _align(64 + 4 + 20 + opt_size + num_sections * 40, FILE_ALIGN)
    size_of_image = rdata_rva + rdata_mem

    data_dir = [(0, 0)] * 16
    data_dir[1] = (iid_rva, iid_size)          # Import
    data_dir[12] = (iat_start, iat_total)      # IAT
    dd = b"".join(struct.pack("<II", a, b) for a, b in data_dir)

    optional = _optional_header(
        entry_rva, text_raw, rdata_raw, size_of_image, headers_size, dd)

    pe_off = 64
    headers = _dos_header(pe_off)
    headers += b"PE\0\0"
    headers += _coff_header(num_sections, opt_size)
    headers += optional
    headers += _section_header(".text", text_mem, TEXT_RVA, text_raw,
                               headers_size,
                               SCN_CNT_CODE | SCN_MEM_EXEC | SCN_MEM_READ)
    headers += _section_header(".rdata", rdata_mem, rdata_rva, rdata_raw,
                               headers_size + text_raw,
                               SCN_CNT_INIT | SCN_MEM_READ)
    headers = bytes(headers)
    if len(headers) < headers_size:
        headers += bytes(headers_size - len(headers))

    out = bytearray(headers)
    out += bytes(code) + bytes(text_raw - len(code))
    out += bytes(rd.buf) + bytes(rdata_raw - len(rd.buf))
    return bytes(out)


def _iat_first(symbol_rva, funcs):
    return symbol_rva[funcs[0]]


def _optional_header(entry_rva, text_raw, rdata_raw, image_size, headers_size, dd):
    # 逐字段构造 IMAGE_OPTIONAL_HEADER64（240 字节）
    head = struct.pack(
        "<HBB IIII I",
        0x20B, 14, 0,                 # Magic, LinkerMajor/Minor
        text_raw, rdata_raw, 0,       # SizeOfCode/InitData/UninitData
        entry_rva,                    # AddressOfEntryPoint
        TEXT_RVA,                     # BaseOfCode
    )
    head += struct.pack("<Q", IMAGE_BASE)
    head += struct.pack("<II", SECTION_ALIGN, FILE_ALIGN)
    head += struct.pack("<HHHHHH", 6, 0, 0, 0, 6, 0)   # OS/Image/Subsys 版本
    head += struct.pack("<I", 0)                        # Win32VersionValue
    head += struct.pack("<II", image_size, headers_size)
    head += struct.pack("<I", 0)                        # CheckSum
    head += struct.pack("<HH", 3, DLL_NX | DLL_TERM)    # Subsystem CUI, DllCharacteristics
    head += struct.pack("<QQQQ", 0x100000, 0x1000, 0x100000, 0x1000)  # Stack/Heap reserve/commit
    head += struct.pack("<II", 0, 16)                   # LoaderFlags, NumberOfRvaAndSizes
    head += dd
    assert len(head) == 240, len(head)
    return head
