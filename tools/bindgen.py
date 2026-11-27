# -*- coding: utf-8 -*-
"""C 头 → Impor FFI 绑定生成器（4.7.1，实验性）。

现状：`impor/std` 里的 http / image / sqlite3 / zlib 绑定是**手写**的，每个库
都要人肉抄一遍函数签名。本工具用「预处理 + 轻量原型抽取」把这件事自动化，
目标是一晚上打通一批库（libcurl / SDL / openssl / libpng 的标量子集）。

它**不是**完整的 C 解析器，能力和 Impor 的 FFI 边界严格对齐（docs/语言规范.md 14.3）：

  支持：int / long / size_t / uintN_t / char* / const char* / double / float / void
  跳过：指针参数（含 void*、struct*）、结构体按值传递、函数指针/回调、
        可变参数（...）、超过 8 个参数、位域、union

也就是说：生成的绑定只覆盖「标量 + 字符串」的调用形态，其余整条跳过并记账，
不会生成一个运行期才崩的假绑定。

用法：
    py -3.11 tools/bindgen.py --header path/to/zlib.h --module zlib \
                              --dll zlib1.dll --out impor/std/zlib.impor
    py -3.11 tools/bindgen.py --header zlib.h --module zlib --json   # 只看统计
    py -3.11 tools/bindgen.py --header zlib.h --module zlib --prefix inflate,deflate,crc32
"""

import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys

# C 类型 → (Impor ffi 类型, 是否指针)
INT_TYPES = {
    "int", "unsigned", "unsigned int", "short", "unsigned short", "long",
    "unsigned long", "long long", "unsigned long long", "char", "signed char",
    "unsigned char", "size_t", "ssize_t", "int8_t", "int16_t", "int32_t", "int64_t",
    "uint8_t", "uint16_t", "uint32_t", "uint64_t", "bool", "_Bool",
    "uInt", "Bytef", "uLong", "uLongf", "z_size_t", "intptr_t", "uintptr_t",
}
FLOAT_TYPES = {"double", "float", "long double"}
VOID_TYPES = {"void"}
CSTR_TYPES = {"char *", "const char *", "char*", "const char*"}


def norm_type(t):
    t = re.sub(r"\s+", " ", t.strip())
    t = t.replace("const ", "").replace("volatile ", "")
    t = re.sub(r"\s*\*\s*", " *", t)
    return t.strip()


def map_type(t):
    """返回 (ffi 类型, 失败原因或 None)。"""
    t = norm_type(t)
    if t.startswith("struct ") or t.startswith("union ") or t.startswith("enum "):
        return None, "结构体/联合/枚举"
    if "*" in t:
        if t in CSTR_TYPES:
            return "cstr", None
        return None, "指针参数（FFI 不支持 ptr）"
    if t in VOID_TYPES:
        return "void", None
    if t in FLOAT_TYPES:
        return "float", None
    if t in INT_TYPES:
        return "int", None
    if re.match(r"^u?int(8|16|32|64)_t$", t):
        return "int", None
    if re.match(r"^u?long( long)?$", t):
        return "int", None
    # 宽松兜底：typedef 出来的标量（`_off64_t`、`uInt`、`z_size_t`…）
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*_t$", t) and t not in ("va_list",):
        return "int", None
    return None, "未映射类型 %r" % t


PROTO = re.compile(
    r"(?P<ret>[A-Za-z_][A-Za-z0-9_ \t\*]*?)\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*"
    r"\((?P<args>[^;{}()]*)\)\s*;", re.S)

PROTO_ONE = re.compile(
    r"^(?P<ret>[A-Za-z_][A-Za-z0-9_ \t\*]*?)\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*"
    r"\((?P<args>[^()]*)\)$", re.S)

MODIFIERS = ("_CRTIMP", "__cdecl", "__stdcall", "__fastcall", "WINAPI", "APIENTRY",
             "EXPORT", "__restrict", "restrict", "inline", "extern", "static",
             "_Noreturn", "__inline", "_inline", "__forceinline", "IMPORT")

KEYWORDS = {"if", "while", "for", "switch", "return", "sizeof", "typedef", "struct",
            "union", "enum", "do", "else", "static", "extern", "inline", "case",
            "defined", "typedef"}


def strip_decls(text):
    """去掉 __attribute__ / __declspec / 调用约定宏 / inline 等修饰。"""
    text = re.sub(r"__attribute__\s*\(\([^()]*\)\)", " ", text)
    text = re.sub(r"__declspec\s*\(\([^()]*\)\)", " ", text)
    for tok in MODIFIERS:
        text = re.sub(r"\b%s\b" % re.escape(tok), " ", text)
    return text


def strip_bodies(text):
    """删掉所有 {...}（函数体/结构体体），只保留顶层声明。"""
    out = []
    depth = 0
    for ch in text:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(ch)
        elif ch == "\n":
            out.append("\n")
    return "".join(out)


def preprocess(header):
    """优先 gcc -E -P（去注释、展开宏）；没有 gcc 就读原文。"""
    gcc = os.environ.get("IMPOR_GCC") or shutil.which("gcc")
    if gcc:
        try:
            r = subprocess.run([gcc, "-E", "-P", header], capture_output=True, text=True,
                               timeout=120)
            if r.returncode == 0 and r.stdout:
                return r.stdout, "gcc -E -P"
        except Exception:
            pass
    with io.open(header, "r", encoding="utf-8", errors="replace") as f:
        return f.read(), "原文（未预处理）"


def split_args(argtext):
    if not argtext.strip() or argtext.strip() == "void":
        return []
    args = []
    depth = 0
    cur = ""
    for ch in argtext:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            args.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        args.append(cur)
    return args


TYPE_WORDS = (INT_TYPES | FLOAT_TYPES | VOID_TYPES |
              {"const", "struct", "union", "enum", "signed", "unsigned", "short", "long"})


def strip_param_name(at):
    """把 `const char *s` 变成 `const char *`、`unsigned long x` 变成 `unsigned long`。

    直接 `parts[:-1]` 会把 `const char *p` 变成 `const char`（星号连名字一起切掉），
    于是 `char` 命中整型表，`puts` 的参数被错标成 int —— cstr 参数传字符串进去
    就报 `int('hello')`。这是 4.7.1 用生成的绑定实跑时抓到的生成器 bug。
    """
    at = at.strip()
    m = re.match(r"^(.*?)(\*+)\s*[A-Za-z_][A-Za-z0-9_]*$", at, re.S)
    if m:
        return (m.group(1).rstrip() + m.group(2)).strip()
    parts = at.split()
    if len(parts) >= 2:
        lastw = parts[-1]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", lastw) and lastw not in TYPE_WORDS \
                and not re.match(r"^[A-Za-z_][A-Za-z0-9_]*_t$", lastw):
            return " ".join(parts[:-1])
    return at


def parse_header(text, prefixes, skip_names):
    """返回 (条目列表, 跳过明细)。按 `;` 分条解析，避免跨语句误配。"""
    text = re.sub(r"^[ \t]*#[^\n]*$", " ", text, flags=re.M)  # 去掉 #include/#define 等指令行
    text = strip_bodies(strip_decls(text))
    ok = []
    skipped = {}
    seen = set()
    for chunk in text.split(";"):
        src = " ".join(chunk.split())
        if not src or "(" not in src:
            continue
        m = PROTO_ONE.match(src)
        if not m:
            continue
        ret, name, argtext = m.group("ret"), m.group("name"), m.group("args")
        if name in KEYWORDS or name in seen:
            continue
        ret_n = norm_type(ret)
        if any(k in ret_n for k in ("typedef", "static", "extern", "inline")) or ret_n == "":
            continue
        if prefixes and not any(name.startswith(p) for p in prefixes):
            continue
        if skip_names and name in skip_names:
            continue
        if "..." in argtext:
            skipped["可变参数"] = skipped.get("可变参数", 0) + 1
            continue
        rtype, why = map_type(ret)
        if rtype is None:
            skipped[why] = skipped.get(why, 0) + 1
            continue
        args = []
        bad = None
        for a in split_args(argtext):
            at = " ".join(a.strip().split())
            if not at:
                continue
            if "(" in at:
                bad = "函数指针/回调"
                break
            at = strip_param_name(at)
            atype, why2 = map_type(at)
            if atype is None or atype == "void":
                bad = why2 or "void 参数"
                break
            args.append(atype)
        if bad:
            skipped[bad] = skipped.get(bad, 0) + 1
            continue
        if len(args) > 8:
            skipped["参数超过 8 个"] = skipped.get("参数超过 8 个", 0) + 1
            continue
        seen.add(name)
        ok.append({"name": name, "ret": rtype, "args": args})
    return ok, skipped


HEADER_TMPL = """# -*- coding: utf-8 -*-
# 由 tools/bindgen.py 自动生成（Impor {ver}），勿手改。
# 源头文件：{header}（{mode}）
# 目标库：{dll}
# 统计：抽取 {total} 条原型，生成 {generated} 个包装，跳过 {skipped} 条（{why}）
#
# 只覆盖 Impor FFI 支持的形态（int/float/cstr/void，≤8 参数）。指针、结构体、
# 回调、可变参数一律整条跳过——这是能力边界，不是 bug。需要那些形态的库，
# 请写 C shim（参考 impor/bindings/http/impor_bind_http.c 的做法）后手写绑定。

var __dll = "{dll}"
var __lib = nil
var __ready = false
var __err = ""

def _open() {{
    if __ready {{
        return true
    }}
    var lib = nil
    try {{
        set lib = ffi_open(__dll)
    }} catch e {{
        set __err = to_str(e)
        return false
    }}
    if lib == nil {{
        set __err = "ffi_open 返回空句柄"
        return false
    }}
    set __lib = lib
    set __ready = true
    return true
}}

export def ready() {{
    return __ready
}}

export def last_error() {{
    return __err
}}

export def load(path) {{
    set __dll = to_str(path)
    set __ready = false
    set __err = ""
    return _open()
}}
"""


def gen_module(entries, header, dll, mode, total, skipped, version, prefix=""):
    why = "、".join("%s %d" % (k, v) for k, v in sorted(skipped.items())) or "无"
    head = HEADER_TMPL.format(ver=version, header=os.path.basename(header), mode=mode,
                              dll=dll, total=total, generated=len(entries), skipped=total - len(entries),
                              why=why)
    body = []
    for e in entries:
        args = ", ".join("a%d" % i for i in range(len(e["args"])))
        call_args = ", ".join("a%d" % i for i in range(len(e["args"])))
        typelist = ", ".join('"%s"' % t for t in e["args"])
        body.append("")
        body.append("export def %s%s(%s) {" % (prefix, e["name"], args))
        body.append("    if !_open() {")
        body.append('        return nil')
        body.append("    }")
        if e["ret"] == "void":
            body.append('    ffi_call(__lib, "%s", [%s], "void", [%s])'
                        % (e["name"], call_args, typelist))
            body.append("    return nil")
        else:
            body.append('    return ffi_call(__lib, "%s", [%s], "%s", [%s])'
                        % (e["name"], call_args, e["ret"], typelist))
        body.append("}")
    return head + "\n".join(body) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="C 头 → Impor FFI 绑定生成器")
    ap.add_argument("--header", required=True)
    ap.add_argument("--module", default=None, help="模块名（默认取头文件名）")
    ap.add_argument("--dll", default=None, help="目标 DLL/so 名")
    ap.add_argument("--out", default=None, help="输出 .impor 路径")
    ap.add_argument("--prefix", default=None, help="只保留这些前缀（逗号分隔）")
    ap.add_argument("--skip", default=None, help="跳过的函数名（逗号分隔）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if not os.path.isfile(args.header):
        print("找不到头文件：%s" % args.header)
        return 2
    mod = args.module or os.path.splitext(os.path.basename(args.header))[0]
    dll = args.dll or (mod + ".dll")
    prefixes = [p.strip() for p in (args.prefix or "").split(",") if p.strip()]
    skips = set(p.strip() for p in (args.skip or "").split(",") if p.strip())

    text, mode = preprocess(args.header)
    entries, skipped = parse_header(text, prefixes, skips)

    import sys as _sys
    ver = "4.7.1"
    for cand in (os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "VERSION"),):
        if os.path.isfile(cand):
            with io.open(cand, encoding="utf-8") as f:
                ver = f.read().strip()

    stats = {"header": args.header, "mode": mode, "module": mod, "dll": dll,
             "generated": len(entries), "skipped": sum(skipped.values()),
             "skip_reasons": skipped, "functions": [e["name"] for e in entries]}
    if args.json or not args.out:
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    if args.out:
        content = gen_module(entries, args.header, dll, mode, len(entries) + sum(skipped.values()),
                             skipped, ver)
        with io.open(args.out, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        if not args.json:
            print("写出 %s（%d 个包装，跳过 %d 条）" % (args.out, len(entries), sum(skipped.values())))
    return 0 if entries else 1


if __name__ == "__main__":
    sys.exit(main())
