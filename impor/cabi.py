# -*- coding: utf-8 -*-
"""v3.8.3：Impor `export func` 的 C ABI v2 包装与头文件生成。

边界（未满足即报错，不猜测）：
- 只导出顶层 `export func`；
- 参数与返回值必须显式标注 int / float / bool / str / void / size_t / handle / ptr；
- size_t 映射为 C size_t，handle/ptr 映射为 void *（用于不透明句柄与结构体按指针传递）；
- 字符串返回值由 DLL 分配，调用方用 impor_free_str() 释放；
- 不支持默认参数、变参、泛型、类方法、async 导出。
"""
from .typecheck import normalize_type
from .version import __version__


class CabiError(Exception):
    pass


_CTYPE = {
    "int": "long long",
    "float": "double",
    "bool": "int",
    "str": "const char *",
    "void": "void",
    "size_t": "size_t",
    "handle": "void *",
    "ptr": "void *",
}


def _kind(t):
    t = normalize_type(t)
    if t in _CTYPE:
        return t
    if not t or t == "?":
        raise CabiError(
            "导出函数必须显式标注类型（int/float/bool/str/void/size_t/handle/ptr），当前为空或 ?")
    raise CabiError(
        "C ABI 不支持类型 %r；仅支持 int/float/bool/str/void/size_t/handle/ptr" % t)


def _params(export):
    ptypes = export.get("param_types") or []
    if len(ptypes) != export["arity"]:
        raise CabiError(
            "函数 %s 的参数类型标注数量（%d）与参数数量（%d）不一致"
            % (export["name"], len(ptypes), export["arity"]))
    return [_kind(t) for t in ptypes]


def symbol_name(name):
    """导出符号统一加 impor_ 前缀，避免和 C 关键字/宿主库符号撞名。"""
    return "impor_" + name


def check_exports(exports):
    if not exports:
        raise CabiError(
            "没有可导出的函数。请在源码中使用：export def add(a: int, b: int) -> int { ... }")
    for e in exports:
        _params(e)
        _kind(e.get("ret_type"))


def header_text(module, exports):
    check_exports(exports)
    guard = "IMPOR_%s_H" % "".join(ch if ch.isalnum() else "_" for ch in module).upper()
    lines = [
        "/* 由 Impor (I++) v%s 生成，勿手改。" % __version__,
        " * 调用约定：C；产物位宽由编译器决定（内置 tcc 为 32 位 i386；64 位工程请用 --c-source 以 cl/gcc -m64 编译）。",
        " * 编译 DLL 时定义 IMPOR_BUILD_DLL（本工具自动完成）。",
        " */",
        "#ifndef %s" % guard,
        "#define %s" % guard,
        "",
        "#include <stddef.h>",
        "",
        "#ifdef __cplusplus",
        'extern "C" {',
        "#endif",
        "",
        "#ifndef IMPOR_API",
        "#  if defined(_WIN32) && defined(IMPOR_BUILD_DLL)",
        "#    define IMPOR_API __declspec(dllexport)",
        "#  else",
        "#    define IMPOR_API",
        "#  endif",
        "#endif",
        "",
    ]
    for e in exports:
        types = _params(e)
        ret = _kind(e.get("ret_type"))
        args = ", ".join("%s a%d" % (_CTYPE[ct], i) for i, ct in enumerate(types)) or "void"
        lines.append("IMPOR_API %s %s(%s);" % (_CTYPE[ret], symbol_name(e["name"]), args))
    lines.append("")
    lines.append("/* 释放本 DLL 返回的字符串；传入 NULL 安全。 */")
    lines.append("IMPOR_API void impor_free_str(const char *s);")
    lines.append("/* 显式回收 DLL 内堆数组（可选）。DLL 模式不注册 atexit，")
    lines.append("   宿主在卸载前调用一次可拿到确定性的释放；不调用则由操作系统回收。 */")
    lines.append("IMPOR_API void impor_runtime_shutdown(void);")
    lines += ["", "#ifdef __cplusplus", "}", "#endif", "", "#endif", ""]
    return "\n".join(lines)


def _wrap_one(e):
    types = _params(e)
    ret = _kind(e.get("ret_type"))
    cname = e["cname"]
    sym = symbol_name(e["name"])
    out = []
    args = ", ".join("%s a%d" % (_CTYPE[t], i) for i, t in enumerate(types)) or "void"
    out.append("IMPOR_API %s %s(%s) {" % (_CTYPE[ret], sym, args))
    out.append("    impor_pin_once();")
    for i, t in enumerate(types):
        if t == "int":
            out.append("    Val _a%d = v_int(a%d);" % (i, i))
        elif t == "float":
            out.append("    Val _a%d = v_float(a%d);" % (i, i))
        elif t == "bool":
            out.append("    Val _a%d = v_bool(a%d != 0);" % (i, i))
        elif t == "str":
            out.append("    Val _a%d = v_str(a%d ? a%d : \"\");" % (i, i, i))
        elif t == "size_t":
            out.append("    Val _a%d = v_int((long long)a%d);" % (i, i))
        elif t == "handle" or t == "ptr":
            out.append("    Val _a%d = v_int((long long)(size_t)a%d);" % (i, i))
    call_args = ", ".join("_a%d" % i for i in range(len(types)))
    out.append("    Val _r = %s(%s);" % (cname, call_args))
    if ret == "int":
        out.append("    if (_r.t == V_FLOAT) return (long long)_r.f;")
        out.append("    if (_r.t == V_BOOL || _r.t == V_INT) return _r.i;")
        out.append("    return 0;")
    elif ret == "float":
        out.append("    if (_r.t == V_FLOAT) return _r.f;")
        out.append("    if (_r.t == V_BOOL || _r.t == V_INT) return (double)_r.i;")
        out.append("    return 0.0;")
    elif ret == "bool":
        out.append("    if (_r.t == V_BOOL || _r.t == V_INT) return _r.i != 0;")
        out.append("    return 0;")
    elif ret == "size_t":
        out.append("    if (_r.t == V_INT) return (size_t)_r.i;")
        out.append("    if (_r.t == V_FLOAT) return (size_t)_r.f;")
        out.append("    return 0;")
    elif ret == "handle" or ret == "ptr":
        out.append("    if (_r.t == V_INT) return (void *)(size_t)_r.i;")
        out.append("    return NULL;")
    elif ret == "str":
        # v3.8.3：返回给 C 的字符串始终由 DLL 新分配，调用方统一用 impor_free_str 释放。
        out.append("    return v_str(_r.s).s;")
    else:
        out.append("    (void)_r;")
        out.append("    return;")
    out.append("}")
    return "\n".join(out)


def wrappers_text(exports):
    check_exports(exports)
    lines = [
        "",
        "/* ==== Impor C ABI v2 导出包装（DLL）==== */",
        "#ifndef IMPOR_API",
        "#  ifdef _WIN32",
        "#    define IMPOR_API __declspec(dllexport)",
        "#  else",
        "#    define IMPOR_API __attribute__((visibility(\"default\")))",
        "#  endif",
        "#endif",
        "#ifdef __cplusplus",
        'extern "C" {',
        "#endif",
        "#if defined(_WIN32)",
        "/* v4.1：把本模块钉在进程里（GET_MODULE_HANDLE_EX_FLAG_PIN）。",
        " * tcc 的 DLL CRT 会往 msvcrt 的共享退出表登记堆清理钩子；宿主 FreeLibrary 之后，",
        " * 进程退出仍会调到已卸载的代码 → 0xC0000005。钉住模块后不再卸载，退出码稳定为 0。",
        " * 代价：DLL 映射保留到进程结束（对“逻辑层”插件是可接受的，Python/CPython 扩展也这么做）。 */",
        "typedef long (*pfn_gmhex)(unsigned long, const void *, void **);",
        "static void impor_pin_self(void) {",
        "    HMODULE h = NULL;",
        "    pfn_gmhex f = (pfn_gmhex)GetProcAddress(GetModuleHandleA(\"kernel32.dll\"), \"GetModuleHandleExW\");",
        "    if (f) f(0x00000004ul, (const void *)&impor_pin_self, (void **)&h);",
        "}",
        "#  if defined(__GNUC__) || defined(__TINYC__)",
        "__attribute__((constructor)) static void impor_pin_ctor(void) { impor_pin_self(); }",
        "#  endif",
        "static void impor_pin_once(void) {",
        "    static int done = 0;",
        "    if (done) return;",
        "    done = 1;",
        "    impor_pin_self();",
        "}",
        "#else",
        "static void impor_pin_once(void) { }",
        "#endif",
    ]
    for e in exports:
        lines.append(_wrap_one(e))
        lines.append("")
    lines += [
        "IMPOR_API void impor_free_str(const char *s) {",
        "    if (s) free((void *)s);",
        "}",
        "IMPOR_API void impor_runtime_shutdown(void) {",
        "    rt_shutdown();",
        "}",
        "#ifdef __cplusplus",
        "}",
        "#endif",
        "",
    ]
    return "\n".join(lines)


def def_text(module, exports):
    """给 tcc -impdef / MSVC lib 用的 .def（可选产物）。"""
    check_exports(exports)
    lines = ["LIBRARY %s" % module, "EXPORTS"]
    for e in exports:
        lines.append("    %s" % symbol_name(e["name"]))
    lines.append("    impor_free_str")
    lines.append("    impor_runtime_shutdown")
    return "\n".join(lines) + "\n"
