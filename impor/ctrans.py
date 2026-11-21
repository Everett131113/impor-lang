# -*- coding: utf-8 -*-
import json
import os
"""
Impor (I++) C 后端（v2.13）：Impor AST → 原生 C 源码。

设计目标：让 Impor 脚本脱离 Python 宿主，编译成独立原生可执行文件。
- 复用现有 lexer/parser（中文语法自动支持）；
- 值模型：动态类型 Val（int/float/str/bool/null），脚本短生命周期使用
  strdup/malloc 泄漏式内存管理（简单可靠，脚本退出即回收）；
- 第一版支持子集：字面量/变量/算术/比较/逻辑/字符串拼接/插值/
  if-elif-else/while/for-range/函数(无闭包)/print/input/inputstr/
  to_num/to_str/len/type/trim/upper/lower/randi/seed/clamp/sign/
  wait/sleep/now/clock/断言/exit/清屏；
- 不支持的语法抛 UnsupportedFeature，提示用 --backend=py（Python 转译后端）。

双后端策略：AST 解释器（教学）+ Python 转译器（兼容）保留；C 后端用于
`impor build` 产出真正独立的原生可执行文件。
"""

import re


class UnsupportedFeature(Exception):
    """C 后端暂不支持的 Impor 特性（带源码位置）。"""

    def __init__(self, msg, lineno=0, col=0):
        super().__init__(msg)
        self.msg = msg
        self.lineno = lineno
        self.col = col


# ---------------- 内建函数映射（名字 -> C 运行库函数） ----------------
BUILTINS = {
    "input": "rt_input",
    "inputstr": "rt_inputstr",
    "to_num": "rt_to_num",
    "to_str": "rt_tostr",
    "str": "rt_tostr",
    "int": "rt_to_int",
    "len": "rt_len",
    "expect": "rt_expect",
    "type": "rt_type",
    "type_of": "rt_type",
    "trim": "rt_trim",
    "upper": "rt_upper",
    "lower": "rt_lower",
    "randi": "rt_randi",
    "seed": "rt_seed",
    "clamp": "rt_clamp",
    "sign": "rt_sign",
    "min": "rt_min2",
    "max": "rt_max2",
    "wait": "rt_sleep",
    "sleep": "rt_sleep",
    "now": "rt_now",
    "clock": "rt_clock",
    "randstr": "rt_randstr",
    "abs": "rt_abs",
    "div": "rt_idiv",
    "arrfill": "rt_arrfill",
    "arrpop": "rt_arrpop",
    "arrappend": "rt_arrappend",
    "arrfind": "rt_arrfind",
    "arrdup": "rt_arrdup",
    "arrrev": "rt_arrrev",
    "shuffle": "rt_shuffle",
    "max_arr": "rt_maxarr",
    "min_arr": "rt_minarr",
    "sum_arr": "rt_sumarr",
    "strpad": "rt_strpad",
    "strcut": "rt_strcut",
    "strjoin": "rt_strjoin",
    "strlines": "rt_strlines",
    "strreplace": "rt_strreplace",
    "filelines": "rt_filelines",
    "fileread": "rt_fileread",
    "filewrite": "rt_filewrite",
    "filewritebytes": "rt_filewritebytes",
    "fileexists": "rt_fileexists",
    "filelist": "rt_filelist",
    "filemkdir": "rt_filemkdir",
    "filesize": "rt_filesize",
    "filemv": "rt_filemv",
    "system": "rt_system",
    "exit": "rt_exit",
    "mem": "rt_mem",
    "gc": "rt_gc",
    "json_encode": "rt_json_encode",
    "json_decode": "rt_json_decode",
    "regex_match": "rt_regex_match",
    "regex_find": "rt_regex_find",
    "regex_replace": "rt_regex_replace",
    "strfind": "rt_strfind",
    "run_tasks": "rt_run_tasks",
    "task_status": "rt_task_status",
    "args": "rt_args",
    "exedir": "rt_exedir",
    "ffi_open": "rt_ffi_open",
    "ffi_call": "rt_ffi_call",
    "ffi_alloc": "rt_ffi_alloc",
    "ffi_read_i64": "rt_ffi_read_i64",
    "ffi_read_str_at": "rt_ffi_read_str_at",
    "__module__": "rt_module_new",
}

# print 特殊处理；range 仅在 for 中；其余内建一律不支持（编译报错）
UNSUPPORTED_BUILTINS_HINT = (
    "lerp", "remap", "distance", "angle",
    "strjoin", "hash",
    "vec2", "rgb", "frame", "fps", "pause", "watch", "snapshot",
    "breakpoint", "import_path", "reload", "open", "readln", "writeln",
    "filecat",
)

# 仍未支持的 AST 节点类型名（给出友好提示）
_UNSUP_NODE = {
    "Defer": "defer 延迟块（C 后端暂不支持；请用 impor run / impor compile）",
    "ExceptionDef": "自定义异常 异常",
}


# ---------------- C 运行库（内嵌到每个编译产物） ----------------
def _load_runtime_c():
    """优先读取随包分发的 impor/c_runtime.c（与 C 编译器 imporc 共享同一运行时），
    文件缺失时回退内嵌副本。"""
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "c_runtime.c"), "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return _RUNTIME_C_EMBEDDED


_RUNTIME_C_EMBEDDED = r"""
/* Impor C 运行库 —— 由 impor build 自动内嵌，勿手改 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <time.h>
#include <stdarg.h>
#ifdef _WIN32
#include <windows.h>
#else
#include <unistd.h>
#endif

typedef enum { V_NULL, V_INT, V_FLOAT, V_STR, V_BOOL, V_ARR } VType;
typedef struct Arr Arr;
typedef struct { VType t; long long i; double f; char *s; Arr *a; } Val;
struct Arr { Val *items; long long n, cap; };

static void rt_abort(const char *msg) {
    fprintf(stderr, "ImporRuntimeError: %s\n", msg);
    exit(1);
}

static Val v_null(void) { Val v; v.t = V_NULL; v.i = 0; v.f = 0; v.s = NULL; v.a = NULL; return v; }
static Val v_int(long long x) { Val v; v.t = V_INT; v.i = x; v.f = 0; v.s = NULL; v.a = NULL; return v; }
static Val v_float(double x) { Val v; v.t = V_FLOAT; v.i = 0; v.f = x; v.s = NULL; v.a = NULL; return v; }
static Val v_bool(int b) { Val v; v.t = V_BOOL; v.i = b ? 1 : 0; v.f = 0; v.s = NULL; v.a = NULL; return v; }
static Val v_str(const char *s) { Val v; v.t = V_STR; v.i = 0; v.f = 0; v.s = strdup(s ? s : ""); v.a = NULL; return v; }
static Val v_arr(void) { Val v; v.t = V_ARR; v.i = 0; v.f = 0; v.s = NULL; v.a = NULL; return v; }

static int rt_truth(Val v) {
    switch (v.t) {
        case V_NULL: return 0;
        case V_INT: return v.i != 0;
        case V_FLOAT: return v.f != 0.0;
        case V_STR: return v.s != NULL && v.s[0] != '\0';
        case V_BOOL: return v.i != 0;
        case V_ARR: return v.a != NULL && v.a->n > 0;
    }
    return 0;
}

static int rt_isnum(Val v) { return v.t == V_INT || v.t == V_FLOAT; }
static double rt_num(Val v) {
    if (v.t == V_INT) return (double)v.i;
    if (v.t == V_FLOAT) return v.f;
    if (v.t == V_STR && v.s) return atof(v.s);
    return 0;
}
static long long rt_int(Val v) {
    if (v.t == V_INT) return v.i;
    if (v.t == V_FLOAT) return (long long)v.f;
    if (v.t == V_STR && v.s) return atoll(v.s);
    return 0;
}
static const char *rt_str_of(Val v) { return v.t == V_STR && v.s ? v.s : ""; }

/* 数值格式化（对齐 Impor str()：整数 float 显示 .0） */
static void rt_fmt_num(Val v, char *buf, size_t n) {
    if (v.t == V_FLOAT) {
        snprintf(buf, n, "%.12g", v.f);
        if (!strchr(buf, '.') && !strchr(buf, 'e') && !strchr(buf, 'E')) {
            size_t m = strlen(buf);
            if (m + 2 < n) { buf[m] = '.'; buf[m + 1] = '0'; buf[m + 2] = '\0'; }
        }
    } else {
        snprintf(buf, n, "%lld", v.i);
    }
}

static Val rt_tostr(Val v) {
    char buf[80];
    switch (v.t) {
        case V_NULL: return v_str("None");
        case V_INT: snprintf(buf, sizeof(buf), "%lld", v.i); return v_str(buf);
        case V_FLOAT: rt_fmt_num(v, buf, sizeof(buf)); return v_str(buf);
        case V_STR: return v_str(v.s ? v.s : "");
        case V_BOOL: return v_str(v.i ? "True" : "False");
        case V_ARR: {
            size_t cap = (v.a ? (size_t)v.a->n : 0) * 8 + 16, n = 0;
            char *b = (char *)malloc(cap + 1);
            if (!b) rt_abort("内存不足");
            b[n++] = '[';
            if (v.a) {
                for (long long k = 0; k < v.a->n; k++) {
                    if (k) { b[n++] = ','; b[n++] = ' '; }
                    Val e = rt_tostr(v.a->items[k]);
                    size_t el = strlen(e.s ? e.s : "");
                    if (n + el + 2 > cap) {
                        cap = (cap + el + 16) * 2;
                        b = (char *)realloc(b, cap + 1);
                        if (!b) rt_abort("内存不足");
                    }
                    memcpy(b + n, e.s ? e.s : "", el); n += el;
                    free(e.s);
                }
            }
            b[n++] = ']';
            b[n] = '\0';
            Val r = v_str(b);
            free(b);
            return r;
        }
    }
    return v_str("");
}

static Val rt_add(Val a, Val b) {
    if (a.t == V_STR || b.t == V_STR) {
        Val sa = rt_tostr(a), sb = rt_tostr(b);
        size_t na = strlen(sa.s ? sa.s : ""), nb = strlen(sb.s ? sb.s : "");
        size_t n = na + nb + 1;
        char *buf = (char *)malloc(n);
        if (!buf) rt_abort("内存不足");
        memcpy(buf, sa.s ? sa.s : "", na);
        memcpy(buf + na, sb.s ? sb.s : "", nb + 1);
        free(sa.s); free(sb.s);
        Val r; r.t = V_STR; r.i = 0; r.f = 0; r.a = NULL; r.s = buf;
        return r;
    }
    if (a.t == V_FLOAT || b.t == V_FLOAT) return v_float(rt_num(a) + rt_num(b));
    return v_int(rt_int(a) + rt_int(b));
}

/* ================= 数组（v2.17） ================= */
static Arr *arr_new(long long cap) {
    Arr *a = (Arr *)malloc(sizeof(Arr));
    if (!a) rt_abort("内存不足");
    a->n = 0;
    a->cap = cap > 0 ? cap : 8;
    a->items = (Val *)malloc(sizeof(Val) * (size_t)a->cap);
    if (!a->items) rt_abort("内存不足");
    return a;
}
static void arr_push(Arr *a, Val v) {
    if (a->n >= a->cap) {
        a->cap *= 2;
        a->items = (Val *)realloc(a->items, sizeof(Val) * (size_t)a->cap);
        if (!a->items) rt_abort("内存不足");
    }
    a->items[a->n++] = v;
}
static void rt_val_free(Val v) {
    if (v.t == V_STR && v.s) { free(v.s); v.s = NULL; }
    else if (v.t == V_ARR && v.a) {
        for (long long k = 0; k < v.a->n; k++) rt_val_free(v.a->items[k]);
        free(v.a->items); free(v.a); v.a = NULL;
    }
}
static Val rt_val_dup(Val v) {
    if (v.t == V_STR && v.s) return v_str(v.s);
    if (v.t == V_ARR && v.a) {
        Val r = v_arr();
        r.a = arr_new(v.a->n);
        for (long long k = 0; k < v.a->n; k++)
            arr_push(r.a, rt_val_dup(v.a->items[k]));
        return r;
    }
    return v;
}
static Val rt_arr_of(long long n, ...) {
    va_list ap; va_start(ap, n);
    Val r = v_arr();
    r.a = arr_new(n);
    for (long long k = 0; k < n; k++) arr_push(r.a, rt_val_dup(va_arg(ap, Val)));
    va_end(ap);
    return r;
}
static Val rt_arr_get(Val a, Val b) {
    if (a.t != V_ARR || !a.a) rt_abort("下标访问需要数组");
    long long i = rt_int(b);
    if (i < 0 || i >= a.a->n) rt_abort("数组下标越界");
    return a.a->items[i];
}
static Val rt_arr_set(Val a, Val b, Val c) {
    if (a.t != V_ARR || !a.a) rt_abort("下标赋值需要数组");
    long long i = rt_int(b);
    if (i < 0 || i >= a.a->n) rt_abort("数组下标越界");
    rt_val_free(a.a->items[i]);
    a.a->items[i] = rt_val_dup(c);
    return c;
}
static Val rt_arrfill(Val n, Val v) {
    long long sz = rt_int(n);
    if (sz < 0) rt_abort("arrfill 大小不能为负");
    Val r = v_arr();
    r.a = arr_new(sz);
    for (long long k = 0; k < sz; k++) arr_push(r.a, rt_val_dup(v));
    return r;
}
static Val rt_arrpop(Val a) {
    if (a.t != V_ARR || !a.a) rt_abort("arrpop 需要数组");
    if (a.a->n == 0) return v_null();
    return a.a->items[--a.a->n];
}
static Val rt_arrappend(Val a, Val v) {
    if (a.t != V_ARR || !a.a) rt_abort("arrappend 需要数组");
    arr_push(a.a, rt_val_dup(v));
    return a;
}
static Val rt_arrfind(Val a, Val v) {
    if (a.t != V_ARR || !a.a) rt_abort("arrfind 需要数组");
    for (long long k = 0; k < a.a->n; k++) {
        Val x = a.a->items[k];
        if ((x.t == V_INT && v.t == V_INT && x.i == v.i) ||
            (x.t == V_STR && v.t == V_STR && x.s && v.s && strcmp(x.s, v.s) == 0) ||
            (rt_isnum(x) && rt_isnum(v) && rt_num(x) == rt_num(v)))
            return v_int(k);
    }
    return v_int(-1);
}
static Val rt_arrdup(Val a) {
    if (a.t != V_ARR || !a.a) rt_abort("arrdup 需要数组");
    Val r = v_arr();
    r.a = arr_new(a.a->n);
    for (long long k = 0; k < a.a->n; k++) arr_push(r.a, rt_val_dup(a.a->items[k]));
    return r;
}
static Val rt_arrrev(Val a) {
    if (a.t != V_ARR || !a.a) rt_abort("arrrev 需要数组");
    Val r = v_arr();
    r.a = arr_new(a.a->n);
    for (long long k = a.a->n - 1; k >= 0; k--) arr_push(r.a, rt_val_dup(a.a->items[k]));
    return r;
}
static Val rt_shuffle(Val a) {
    if (a.t != V_ARR || !a.a) rt_abort("shuffle 需要数组");
    for (long long k = a.a->n - 1; k > 0; k--) {
        long long j = (long long)(rand() % (unsigned long long)(k + 1));
        Val t = a.a->items[k]; a.a->items[k] = a.a->items[j]; a.a->items[j] = t;
    }
    return a;
}
static Val rt_maxarr(Val a) {
    if (a.t != V_ARR || !a.a || a.a->n == 0) rt_abort("max_arr 需要非空数组");
    Val m = a.a->items[0];
    for (long long k = 1; k < a.a->n; k++)
        if (rt_num(a.a->items[k]) > rt_num(m)) m = a.a->items[k];
    return m;
}
static Val rt_minarr(Val a) {
    if (a.t != V_ARR || !a.a || a.a->n == 0) rt_abort("min_arr 需要非空数组");
    Val m = a.a->items[0];
    for (long long k = 1; k < a.a->n; k++)
        if (rt_num(a.a->items[k]) < rt_num(m)) m = a.a->items[k];
    return m;
}
static Val rt_sumarr(Val a) {
    if (a.t != V_ARR || !a.a) rt_abort("sum_arr 需要数组");
    double s = 0;
    for (long long k = 0; k < a.a->n; k++) s += rt_num(a.a->items[k]);
    return (s == (long long)s) ? v_int((long long)s) : v_float(s);
}

static Val rt_len(Val v) {
    if (v.t == V_STR) return v_int(v.s ? (long long)strlen(v.s) : 0);
    if (v.t == V_ARR) return v_int(v.a ? v.a->n : 0);
    rt_abort("len 需要字符串或数组");
    return v_int(0);
}

static Val rt_sub(Val a, Val b) {
    if (a.t == V_FLOAT || b.t == V_FLOAT) return v_float(rt_num(a) - rt_num(b));
    return v_int(rt_int(a) - rt_int(b));
}
static Val rt_mul(Val a, Val b) {
    if (a.t == V_FLOAT || b.t == V_FLOAT) return v_float(rt_num(a) * rt_num(b));
    return v_int(rt_int(a) * rt_int(b));
}
static Val rt_div(Val a, Val b) {
    double d = rt_num(b);
    if (d == 0) rt_abort("除数为零");
    return v_float(rt_num(a) / d);
}
static Val rt_mod(Val a, Val b) {
    if (a.t == V_FLOAT || b.t == V_FLOAT) {
        double d = rt_num(b);
        if (d == 0) rt_abort("取模除数为零");
        return v_float(fmod(rt_num(a), d));
    }
    long long d = rt_int(b);
    if (d == 0) rt_abort("取模除数为零");
    long long r = rt_int(a) % d;
    if (r != 0 && ((r < 0) != (d < 0))) r += d;  /* Python 取正模语义 */
    return v_int(r);
}
static Val rt_neg(Val a) {
    if (a.t == V_FLOAT) return v_float(-a.f);
    return v_int(-rt_int(a));
}
static Val rt_not(Val a) { return v_bool(!rt_truth(a)); }
static Val rt_and(Val a, Val b) { return rt_truth(a) ? b : a; }
static Val rt_or(Val a, Val b) { return rt_truth(a) ? a : b; }

static int rt_cmp_i(Val a, Val b) {
    /* -2 表示不可比较；-1/0/1 为比较结果 */
    if (a.t == V_STR && b.t == V_STR) {
        int c = strcmp(a.s ? a.s : "", b.s ? b.s : "");
        return c < 0 ? -1 : (c > 0 ? 1 : 0);
    }
    if (rt_isnum(a) && rt_isnum(b)) {
        double x = rt_num(a), y = rt_num(b);
        return x < y ? -1 : (x > y ? 1 : 0);
    }
    if (a.t == V_BOOL && b.t == V_BOOL) {
        return a.i < b.i ? -1 : (a.i > b.i ? 1 : 0);
    }
    if (a.t == V_NULL && b.t == V_NULL) return 0;
    return -2;
}
static Val rt_cmp(Val a, Val b, const char *op) {
    if (strcmp(op, "==") == 0) {
        if (a.t == V_NULL && b.t == V_NULL) return v_bool(1);
        if (a.t == V_STR && b.t == V_STR) return v_bool(strcmp(a.s, b.s) == 0);
        if (a.t == V_BOOL && b.t == V_BOOL) return v_bool(a.i == b.i);
        if (rt_isnum(a) && rt_isnum(b)) return v_bool(rt_num(a) == rt_num(b));
        return v_bool(0);
    }
    if (strcmp(op, "!=") == 0) {
        Val e = rt_cmp(a, b, "==");
        return v_bool(!rt_truth(e));
    }
    int c = rt_cmp_i(a, b);
    if (c == -2) rt_abort("类型不可比较");
    if (strcmp(op, "<") == 0) return v_bool(c < 0);
    if (strcmp(op, ">") == 0) return v_bool(c > 0);
    if (strcmp(op, "<=") == 0) return v_bool(c <= 0);
    if (strcmp(op, ">=") == 0) return v_bool(c >= 0);
    rt_abort("未知比较运算符");
    return v_bool(0);
}

/* ---------- 打印 ---------- */
static void rt_pstr(const char *s) { printf("%s", s ? s : ""); fflush(stdout); }
static void rt_print_val(Val v) { Val s = rt_tostr(v); printf("%s", s.s); fflush(stdout); }
static void rt_print_nl(void) { printf("\n"); fflush(stdout); }
static void rt_print_sep(void) { printf(" "); fflush(stdout); }

/* ---------- 输入 ---------- */
static Val rt_input(Val prompt) {
    const char *p = rt_str_of(prompt);
    if (p[0]) { printf("%s", p); fflush(stdout); }
    static char buf[4096];
    if (!fgets(buf, sizeof(buf), stdin)) return v_str("");
    buf[strcspn(buf, "\r\n")] = '\0';
    return v_str(buf);
}
static Val rt_inputstr(Val prompt) {
    const char *p = rt_str_of(prompt);
    if (p[0]) { printf("%s", p); fflush(stdout); }
    static char buf[4096];
    if (!fgets(buf, sizeof(buf), stdin)) return v_str("\n");
    buf[strcspn(buf, "\r\n")] = '\0';
    size_t n = strlen(buf);
    char *nb = (char *)malloc(n + 2);
    if (!nb) rt_abort("内存不足");
    memcpy(nb, buf, n);
    nb[n] = '\n';
    nb[n + 1] = '\0';
    return v_str(nb);
}

/* ---------- 转换 ---------- */
static Val rt_to_num(Val v) {
    if (rt_isnum(v)) return v;
    const char *s = v.t == V_STR ? (v.s ? v.s : "") : "";
    while (*s == ' ' || *s == '\t' || *s == '\n') s++;
    if (!*s) return v_int(0);
    if (strchr(s, '.') || strchr(s, 'e') || strchr(s, 'E')) {
        return v_float(strtod(s, NULL));
    }
    return v_int(atoll(s));
}
static Val rt_type(Val v) {
    switch (v.t) {
        case V_NULL: return v_str("null");
        case V_INT: return v_str("int");
        case V_FLOAT: return v_str("float");
        case V_STR: return v_str("str");
        case V_BOOL: return v_str("bool");
        case V_ARR: return v_str("list");
    }
    return v_str("unknown");
}

/* ---------- 字符串工具（ASCII 版；中文原样保留） ---------- */
static Val rt_trim(Val v) {
    const char *s = v.t == V_STR && v.s ? v.s : rt_str_of(v);
    while (*s == ' ' || *s == '\t' || *s == '\n' || *s == '\r') s++;
    size_t n = strlen(s);
    while (n > 0 && (s[n - 1] == ' ' || s[n - 1] == '\t' || s[n - 1] == '\n' || s[n - 1] == '\r')) n--;
    char *buf = (char *)malloc(n + 1);
    if (!buf) rt_abort("内存不足");
    memcpy(buf, s, n);
    buf[n] = '\0';
    return v_str(buf);
}
static Val rt_upper(Val v) {
    const char *s = rt_str_of(v);
    char *buf = strdup(s);
    if (!buf) rt_abort("内存不足");
    for (char *p = buf; *p; p++)
        if (*p >= 'a' && *p <= 'z') *p -= 32;
    return v_str(buf);
}
static Val rt_lower(Val v) {
    const char *s = rt_str_of(v);
    char *buf = strdup(s);
    if (!buf) rt_abort("内存不足");
    for (char *p = buf; *p; p++)
        if (*p >= 'A' && *p <= 'Z') *p += 32;
    return v_str(buf);
}
static Val rt_strpad(Val v, Val w, Val ch) {
    const char *s = rt_str_of(v);
    long long width = rt_int(w);
    const char *pad = rt_str_of(ch);
    size_t n = strlen(s);
    if (width <= 0 || (long long)n >= width) return v_str(s);
    long long cnt = width - (long long)n;
    char *buf = (char *)malloc((size_t)width + 1);
    if (!buf) rt_abort("内存不足");
    for (long long k = 0; k < cnt; k++) buf[k] = pad[0];
    memcpy(buf + cnt, s, n);
    buf[width] = '\0';
    return v_str(buf);
}
static Val rt_strcut(Val v, Val st, Val ed) {
    const char *s = rt_str_of(v);
    size_t n = strlen(s);
    long long a = rt_int(st), b = rt_int(ed);
    if (a < 0) a = 0;
    if (b > (long long)n) b = (long long)n;
    if (a >= b) return v_str("");
    char *buf = (char *)malloc((size_t)(b - a) + 1);
    if (!buf) rt_abort("内存不足");
    memcpy(buf, s + a, (size_t)(b - a));
    buf[b - a] = '\0';
    return v_str(buf);
}

/* ---------- 数学 ---------- */
static Val rt_clamp(Val v, Val lo, Val hi) {
    double x = rt_num(v), l = rt_num(lo), h = rt_num(hi);
    if (x < l) x = l;
    if (x > h) x = h;
    /* 对齐解释器：全整数输入保持整数结果；有浮点参与才返回浮点 */
    if (v.t != V_FLOAT && lo.t != V_FLOAT && hi.t != V_FLOAT) return v_int((long long)x);
    return v_float(x);
}
static Val rt_sign(Val v) {
    double x = rt_num(v);
    return v_int(x < 0 ? -1 : (x > 0 ? 1 : 0));
}
static Val rt_min2(Val a, Val b) {
    if (rt_cmp_i(a, b) == -2) rt_abort("min 类型不可比较");
    return rt_cmp_i(a, b) <= 0 ? a : b;
}
static Val rt_max2(Val a, Val b) {
    if (rt_cmp_i(a, b) == -2) rt_abort("max 类型不可比较");
    return rt_cmp_i(a, b) >= 0 ? a : b;
}
static Val rt_abs(Val v) {
    if (v.t == V_FLOAT) return v_float(fabs(v.f));
    return v_int(v.i < 0 ? -v.i : v.i);
}
static Val rt_idiv(Val a, Val b) {
    long long x = rt_int(a), y = rt_int(b);
    if (y == 0) rt_abort("div 除数为 0");
    long long q = x / y;
    if ((x % y != 0) && ((x < 0) != (y < 0))) q -= 1;  /* 向下取整，与解释器一致 */
    return v_int(q);
}
static Val rt_sleep(Val v) {
    double sec = rt_num(v);
    if (sec > 0) {
#ifdef _WIN32
        Sleep((DWORD)(sec * 1000.0));
#else
        usleep((useconds_t)(sec * 1000000.0));
#endif
    }
    return v_null();
}
static Val rt_now(void) {
    time_t t = time(NULL);
    struct tm *lt = localtime(&t);
    char buf[64];
    if (lt) {
        strftime(buf, sizeof(buf), "%a %b %d %H:%M:%S %Y", lt);
        return v_str(buf);
    }
    return v_str("");
}
static Val rt_clock(void) { return v_float((double)clock() / (double)CLOCKS_PER_SEC); }

/* ---------- 随机 ---------- */
static int _rt_rand_seeded = 0;
static void rt_seed(Val v) { srand((unsigned)rt_int(v)); _rt_rand_seeded = 1; }
static Val rt_randi(Val lo, Val hi) {
    if (!_rt_rand_seeded) { srand((unsigned)time(NULL)); _rt_rand_seeded = 1; }
    long long a = rt_int(lo), b = rt_int(hi);
    if (a > b) { long long t = a; a = b; b = t; }
    long long range = b - a + 1;
    return v_int(a + (long long)(rand() % (unsigned long)range));
}
static Val rt_randstr(Val n) {
    static const char pool[] = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
    long long cnt = rt_int(n);
    if (!_rt_rand_seeded) { srand((unsigned)time(NULL)); _rt_rand_seeded = 1; }
    char *buf = (char *)malloc((size_t)cnt + 1);
    if (!buf) rt_abort("内存不足");
    for (long long k = 0; k < cnt; k++) buf[k] = pool[rand() % (sizeof(pool) - 1)];
    buf[cnt] = '\0';
    return v_str(buf);
}

/* ---------- 杂项 ---------- */
static Val rt_exit_script(void) { exit(0); return v_null(); }
static Val rt_exit(Val n) { exit(rt_int(n)); return v_null(); }
static void rt_cls(void) {
#ifdef _WIN32
    system("cls");
#else
    printf("\033[2J\033[H");
#endif
}
static void rt_assert(Val v, const char *msg) {
    if (!rt_truth(v)) {
        fprintf(stderr, "ImporAssertionError: %s\n", msg ? msg : "断言失败");
        exit(1);
    }
}

/* ---------- v2.14 自举工具链：文件读写 / 外部命令 ---------- */
static Val rt_fileread(Val path) {
    FILE *fp = fopen(rt_str_of(path), "rb");
    if (!fp) rt_abort("fileread: 无法打开文件");
    fseek(fp, 0, SEEK_END);
    long n = ftell(fp);
    fseek(fp, 0, SEEK_SET);
    char *buf = (char *)malloc((size_t)n + 1);
    if (!buf) { fclose(fp); rt_abort("fileread: 内存不足"); }
    size_t got = fread(buf, 1, (size_t)n, fp);
    buf[got] = '\0';
    fclose(fp);
    return v_str(buf);
}
static Val rt_filewrite(Val path, Val content) {
    FILE *fp = fopen(rt_str_of(path), "wb");
    if (!fp) rt_abort("filewrite: 无法写入文件");
    Val cs = rt_tostr(content);
    fwrite(cs.s, 1, strlen(cs.s), fp);
    fclose(fp);
    return v_bool(1);
}
static Val rt_system(Val cmd) {
#ifdef _WIN32
    STARTUPINFOA si;
    PROCESS_INFORMATION pi;
    ZeroMemory(&si, sizeof(si));
    si.cb = sizeof(si);
    ZeroMemory(&pi, sizeof(pi));
    char cl[8192];
    snprintf(cl, sizeof(cl), "%s", rt_str_of(cmd));
    if (!CreateProcessA(NULL, cl, NULL, NULL, FALSE, 0, NULL, NULL, &si, &pi))
        return v_int(-1);
    WaitForSingleObject(pi.hProcess, INFINITE);
    DWORD code = 0;
    GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);
    return v_int((long long)code);
#else
    return v_int(system(rt_str_of(cmd)));
#endif
}
"""

RUNTIME_C = _load_runtime_c()



# ---------------- C 字符串字面量转义 ----------------
def c_escape_str(text):
    """Impor 字符串值 → C 字符串字面量（UTF-8 中文直接保留）。"""
    out = ['"']
    for ch in text:
        o = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif o < 32:
            out.append("\\x%02x" % o)
        elif o == 0x7F:
            out.append("\\x7f")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


# ---------------- AST → C 转译器 ----------------
class CGen:
    def __init__(self):
        self.lines = []
        self.globals_map = {}      # 顶层变量名 -> gN
        self.funcs = []            # [(name, cname, params, body)]
        self.funcs_map = {}        # name -> cname
        self.exports = []          # v3.8.2：export def → C ABI 元数据
        self._fn_writes = {}       # fn名 -> 函数内写集合（局部变量名）
        self.classes = []          # [ClassDef 节点]（按源码顺序，基类在前）
        self.class_methods = {}    # 类名 -> [(方法名, params, body)]
        self.class_fields = {}     # 类名 -> [(字段名, 初值 AST)]
        self.method_cnames = {}    # (类名, 方法名) -> fnN
        self._cur_cls = None       # 当前正在生成的方法所属类
        self._cur_method_self = None  # 当前方法内 self 的 C 表达式（lself）
        self._gcnt = 0
        self._fcnt = 0
        self._lcnt = 0
        self._cur_fn = None        # 当前函数 (name, {locals_map})
        self._loop_depth = 0
        self._indent = 0
        self._arg_seq = 0
        self._match_seq = 0
        self._try_seq = 0
        self.exceptions = set()     # v4.1：exception 声明的类型名（catch 过滤用）
        self.closures = []          # 嵌套函数（闭包）定义
        self._closure_map = {}      # (外层函数名, 嵌套函数名) -> cloN
        self._boxed_by_fn = {}      # 函数名 -> 被闭包捕获、需要装箱的局部名
        self._fn_caps = {}          # cloN -> 捕获顺序 [(名, 外层函数名)]
        self._cur_closure = None
        self.lambdas = []           # 3.2.1：lambda 表达式 → 静态函数
        self._lambda_map = {}       # id(Lambda AST) -> lamN
        self.source_lines = None   # v4.3：源码行，用于独立 exe 的运行时片段
        self.source_name = None     # v3.9：用于编译期读取 .impor/ffi_allow.json
        self.ffi_protos = []        # v3.9：FFI 强类型调用点原型
        self.ffi_sites = []         # v3.9：FFI 强类型调用点定义
        self._ffi_site_cnt = 0
        self.ffi_allow = set()
        self.emit_main = True

    # ---- 实参数组 ----
    def _arg_array(self, args):
        """把调用实参装进临时 Val 数组，返回 (数组名, 元素个数)。

        不能直接用 C99 复合字面量 `(Val[]){...}`：tcc 在元素由函数调用初始化时
        会报 "index too large"（2 个及以上必现），因此改在语句表达式里先建数组。
        """
        self._arg_seq += 1
        arr = "_ia%d" % self._arg_seq
        return arr, len(args)

    # ---- 命名 ----
    def _gname(self, name):
        if name not in self.globals_map:
            self.globals_map[name] = "g%d" % self._gcnt
            self._gcnt += 1
        return self.globals_map[name]

    def _local_base(self, name):
        loc = self._cur_fn[1]
        if name not in loc:
            loc[name] = "l%d" % self._lcnt
            self._lcnt += 1
        return loc[name]

    def _is_boxed(self, name):
        if not self._cur_fn:
            return False
        return name in self._boxed_by_fn.get(self._cur_fn[0], set())

    def _lname(self, name):
        base = self._local_base(name)
        return "(*%s)" % base if self._is_boxed(name) else base

    def _cname(self, name):
        if name in self.funcs_map:
            return self.funcs_map[name]
        raise UnsupportedFeature("未定义函数：%s" % name, 0, 0)

    # ---- 收集阶段（第一遍）----
    def _collect_writes(self, stmts):
        """收集函数体内被写入的变量名（局部变量）。"""
        from impor.ast_nodes import (Assign, CompoundAssign, For, If, While, Block,
                                     MatchNode, BindingPattern, TryCatch)
        w = set()

        def walk(s):
            if isinstance(s, Assign):
                if s.scope == "local":
                    w.add(s.name)  # var 声明 → 局部；set（update）就近更新，不占局部
            elif isinstance(s, CompoundAssign):
                w.add(s.name)
            elif isinstance(s, For):
                w.add(s.var)
                for x in s.body:  # v3.4：块/循环体内声明也要递归收集
                    walk(x)
            elif isinstance(s, MatchNode):
                for pat, body in s.cases:
                    if isinstance(pat, BindingPattern) and pat.name:
                        w.add(pat.name)
                    for x in body:
                        walk(x)
                if s.elseb:
                    for x in s.elseb:
                        walk(x)
            elif isinstance(s, Block):
                for x in s.stmts:
                    walk(x)
            elif isinstance(s, If):
                for b in [s.body] + [cb for _, cb in s.elseifs]:
                    for x in b:
                        walk(x)
                if s.elseb:
                    for x in s.elseb:
                        walk(x)
            elif isinstance(s, While):
                for x in s.body:
                    walk(x)
            elif isinstance(s, TryCatch):
                for x in s.body:
                    walk(x)
                for x in s.catch_body:
                    walk(x)
                for x in s.finally_body:
                    walk(x)

        for s in stmts:
            walk(s)
        return w

    # ---- v4.2：嵌套函数 / 闭包收集 ----
    def _iter_nested_defs(self, stmts):
        """遍历函数体内的 def（不进入嵌套 def 自身）。"""
        from impor.ast_nodes import Def, If, While, For, Block, MatchNode, TryCatch
        for s in stmts:
            if isinstance(s, Def):
                yield s
            elif isinstance(s, Block):
                for x in self._iter_nested_defs(s.stmts):
                    yield x
            elif isinstance(s, If):
                for b in [s.body] + [cb for _, cb in s.elseifs]:
                    for x in self._iter_nested_defs(b):
                        yield x
                if s.elseb:
                    for x in self._iter_nested_defs(s.elseb):
                        yield x
            elif isinstance(s, While):
                for x in self._iter_nested_defs(s.body):
                    yield x
            elif isinstance(s, For):
                for x in self._iter_nested_defs(s.body):
                    yield x
            elif isinstance(s, TryCatch):
                for b in (s.body, s.catch_body, s.finally_body):
                    for x in self._iter_nested_defs(b):
                        yield x
            elif isinstance(s, MatchNode):
                for _, b in s.cases:
                    for x in self._iter_nested_defs(b):
                        yield x
                if s.elseb:
                    for x in self._iter_nested_defs(s.elseb):
                        yield x

    def _scan_names(self, node, used, declared, globals_decl):
        """收集嵌套函数体中读取 / 声明的名字。"""
        from impor.ast_nodes import (Var, Def, Assign, CompoundAssign, For,
                                     BindingPattern)
        if isinstance(node, Def):
            return
        if isinstance(node, (list, tuple)):
            for x in node:
                self._scan_names(x, used, declared, globals_decl)
            return
        if not hasattr(node, "__dict__"):
            return
        if isinstance(node, Var):
            used.add(node.name)
            return
        if isinstance(node, Assign):
            if node.scope == "global":
                globals_decl.add(node.name)
            elif node.scope != "local":
                used.add(node.name)
            else:
                declared.add(node.name)
        elif isinstance(node, CompoundAssign):
            if node.scope == "global":
                globals_decl.add(node.name)
            else:
                used.add(node.name)
        elif isinstance(node, For):
            declared.add(node.var)
        elif isinstance(node, BindingPattern) and node.name:
            declared.add(node.name)
        for k, v in node.__dict__.items():
            if k.startswith("_") or k in ("lineno", "col"):
                continue
            self._scan_names(v, used, declared, globals_decl)

    def _collect_closures_for(self, fn):
        from impor.ast_nodes import Def
        defs = list(self._iter_nested_defs(fn.body))
        if not defs:
            return
        outer = fn.name
        outer_avail = set(fn.params) | set(self._fn_writes.get(outer, set()))
        outer_avail |= {d.name for d in defs}
        for d in defs:
            deeper = list(self._iter_nested_defs(d.body))
            if deeper:
                raise UnsupportedFeature(
                    "C 后端暂不支持两层以上嵌套函数：%s" % deeper[0].name,
                    deeper[0].lineno, deeper[0].col)
            used, declared, globals_decl = set(), set(), set()
            self._scan_names(d.body, used, declared, globals_decl)
            caps = sorted((used - declared - globals_decl) & outer_avail
                          - set(d.params) - set(self.funcs_map))
            if d.name in caps:
                raise UnsupportedFeature(
                    "C 后端暂不支持嵌套函数自递归：%s" % d.name, d.lineno, d.col)
            cname = "clo%d" % self._fcnt
            self._fcnt += 1
            self._closure_map[(outer, d.name)] = cname
            self.closures.append({
                "owner": outer, "cname": cname, "name": d.name,
                "params": d.params, "body": d.body, "caps": caps,
                "lineno": d.lineno,
            })
            self._boxed_by_fn.setdefault(outer, set()).update(caps)
            self._fn_writes.setdefault(outer, set()).add(d.name)
            inner = self._collect_writes(d.body)
            inner -= set(caps) | globals_decl | set(d.params)
            self._fn_writes[cname] = inner
            self._fn_caps[cname] = caps
            # 嵌套函数体内：捕获名按装箱指针访问
            self._boxed_by_fn.setdefault(cname, set()).update(caps)

    def _collect_defs(self, node, in_func=False):
        """遍历顶层收集函数定义；嵌套函数 / 不支持节点提前报错。"""
        stmts = node.stmts if hasattr(node, "stmts") else node
        for s in stmts:
            from impor.ast_nodes import Def, ClassDef, ExceptionDef, Block
            if isinstance(s, Block):
                self._collect_defs(s, in_func=in_func)
                continue
            if isinstance(s, Def):
                if in_func:
                    raise UnsupportedFeature(
                        "C 后端暂不支持嵌套函数（闭包）", s.lineno, s.col)
                if s.name in self.funcs_map:
                    raise UnsupportedFeature("函数重名：%s" % s.name, s.lineno, s.col)
                cname = "fn%d" % self._fcnt
                self._fcnt += 1
                self.funcs_map[s.name] = cname
                self.funcs.append([s.name, cname, s.params, s.body])
                if getattr(s, "exported", False):
                    self.exports.append({
                        "name": s.name, "cname": cname, "arity": len(s.params),
                        "param_types": list(getattr(s, "param_types", []) or []),
                        "ret_type": getattr(s, "ret_type", "") or "",
                    })
                w = self._collect_writes(s.body)
                w.update(s.params)
                self._fn_writes[s.name] = w
                self._collect_closures_for(s)
            elif isinstance(s, ClassDef):
                # 类：收集方法（作为带 (Obj*, Val*) 签名的方法函数）与字段初始化
                if in_func:
                    raise UnsupportedFeature("C 后端暂不支持嵌套类", s.lineno, s.col)
                self.classes.append(s)
                self.class_methods[s.cls_name] = []
                self.class_fields[s.cls_name] = []
                for st in s.body:
                    from impor.ast_nodes import Def as _Def, Assign as _Assign
                    if isinstance(st, _Def):
                        fn = "fn%d" % self._fcnt
                        self._fcnt += 1
                        self.method_cnames[(s.cls_name, st.name)] = fn
                        self.class_methods[s.cls_name].append((st.name, st.params, st.body))
                        w = self._collect_writes(st.body)
                        w.update(st.params)
                        self._fn_writes["%s.%s" % (s.cls_name, st.name)] = w
                    elif isinstance(st, _Assign):
                        if st.scope == "global":
                            raise UnsupportedFeature(
                                "类字段不能声明为 global：%s" % st.name, st.lineno, st.col)
                        self.class_fields[s.cls_name].append((st.name, st.expr))
                    else:
                        raise UnsupportedFeature(
                            "类体只支持方法（def）与字段赋值（var 名 = 初值）",
                            getattr(st, "lineno", 0), getattr(st, "col", 0))
            elif isinstance(s, ExceptionDef):
                # v4.1：exception 声明本身不产生代码，只登记类型名，
                # 供 raise / catch 与解释器同名对齐。
                self.exceptions.add(s.exc_name)

    def _collect_globals(self, node):
        """收集顶层赋值名 → 全局变量（含顶层 while/if/块 体内的声明）。"""
        from impor.ast_nodes import (Assign, CompoundAssign, For, If, While, Block,
                                     MatchNode, BindingPattern, TryCatch)

        def walk(s):
            if isinstance(s, (Assign, CompoundAssign)):
                self._gname(s.name)
            elif isinstance(s, For):
                self._gname(s.var)
                for x in s.body:  # v3.4：循环体/块体内的声明也递归收集
                    walk(x)
            elif isinstance(s, MatchNode):
                for pat, body in s.cases:
                    if isinstance(pat, BindingPattern) and pat.name:
                        self._gname(pat.name)
                    for x in body:
                        walk(x)
                if s.elseb:
                    for x in s.elseb:
                        walk(x)
            elif isinstance(s, Block):
                for x in s.stmts:
                    walk(x)
            elif isinstance(s, If):
                for b in [s.body] + [cb for _, cb in s.elseifs]:
                    for x in b:
                        walk(x)
                if s.elseb:
                    for x in s.elseb:
                        walk(x)
            elif isinstance(s, While):
                for x in s.body:
                    walk(x)
            elif isinstance(s, TryCatch):
                for x in s.body:
                    walk(x)
                for x in s.catch_body:
                    walk(x)
                for x in s.finally_body:
                    walk(x)

        for s in node.stmts:
            walk(s)

    # ---- 生成阶段（第二遍）----
    def _source_block(self):
        """v4.3：把源码行嵌入 C 产物，使独立 exe 的运行时错误也能显示片段。"""
        lines = list(self.source_lines or [])
        parts = ['/* 源码行（运行时诊断用） */',
                 '#define IMPOR_HAS_SRC 1',
                 'const char *g_src_lines[] = {']
        if not lines:
            parts.append('    "",')
        else:
            for src in lines:
                parts.append('    %s,' % c_escape_str(src))
        parts.append('};')
        parts.append('int g_src_n = %d;' % len(lines))
        return "\n".join(parts)

    def _ffi_allow_block(self):
        """v3.9：把 FFI 允许清单编译期烘焙进 C，运行时不可扩展。

        来源与解释器一致：`.impor/ffi_allow.json` 的 allow 数组 +
        IMPOR_FFI_ALLOW（逗号或分号分隔）。只保留文件名，比较时忽略大小写。
        """
        vals = []
        seen = set()

        def put(item):
            s = str(item).strip().lower()
            if s and s not in seen:
                seen.add(s)
                vals.append(s)

        base = os.path.dirname(os.path.abspath(self.source_name or "."))
        for _ in range(6):
            cfg = os.path.join(base, ".impor", "ffi_allow.json")
            if os.path.isfile(cfg):
                try:
                    with open(cfg, encoding="utf-8") as f:
                        data = json.load(f)
                    for item in (data.get("allow") or []):
                        put(item)
                except Exception:
                    pass
                break
            parent = os.path.dirname(base)
            if parent == base:
                break
            base = parent
        for item in re.split(r"[,;]", os.environ.get("IMPOR_FFI_ALLOW", "")):
            put(item)

        parts = ["/* FFI 允许清单（编译期烘焙；运行时不可扩展） */",
                 "#define IMPOR_FFI_ALLOW_BAKED 1",
                 "static const char *g_ffi_allow[] = {"]
        if vals:
            for v in vals:
                parts.append("    %s," % c_escape_str(v))
        else:
            parts.append("    NULL,")
        parts.append("};")
        parts.append("static int g_ffi_allow_n = %d;" % len(vals))
        self.ffi_allow = set(vals)
        return "\n".join(parts)

    def _collect_lambdas(self, node):
        """3.2.1：收集所有 Lambda 表达式，生成独立的静态函数。"""
        from impor.ast_nodes import Lambda
        if isinstance(node, Lambda):
            if id(node) not in self._lambda_map:
                cname = "lam%d" % self._fcnt
                self._fcnt += 1
                self._lambda_map[id(node)] = cname
                self.lambdas.append((cname, node))
            return
        if isinstance(node, (list, tuple)):
            for x in node:
                self._collect_lambdas(x)
            return
        if not hasattr(node, "__dict__"):
            return
        for k, v in node.__dict__.items():
            if k.startswith("_") or k in ("lineno", "col"):
                continue
            self._collect_lambdas(v)

    def _gen_lambda(self, cname, lam):
        out = ["", "/* Impor lambda */", "static Val %s(void *_env, Val *_args) {" % cname,
               "    (void)_env;"]
        self._cur_fn = (cname, {})
        self._lcnt = 0
        for i, p in enumerate(lam.params):
            out.append("    Val %s = _args[%d];" % (self._lname(p), i))
        expr = self._gen_expr(lam.expr)
        out.append("    return %s;" % expr)
        out.append("}")
        out.append('static Fn fob_%s = {%s, NULL, 0, %d, "<lambda>"};' %
                   (cname, cname, len(lam.params)))
        return out

    def gen_program(self, node):
        self._collect_defs(node)
        self._collect_globals(node)
        self._collect_lambdas(node)
        out = []
        out.append("/* 由 Impor (I++) 编译器生成，勿手改 */")
        if not self.emit_main:
            # v4.1：共享库（DLL）模式。运行时据此跳过 atexit 注册——
            # tcc 的 DLL CRT 在 FreeLibrary/进程退出时会重复走退出钩子，
            # 之前会以 0xC0000005 崩溃。DLL 里改由宿主显式调用 impor_runtime_shutdown()。
            out.append("#define IMPOR_BUILD_DLL 1")
        out.append("#include <stdio.h>")
        out.append("#include <stdlib.h>")
        out.append("#include <string.h>")
        out.append("#include <math.h>")
        out.append(self._source_block())
        out.append(self._ffi_allow_block())
        out.append(RUNTIME_C.rstrip("\n"))
        # 函数原型
        if self.funcs:
            out.append("")
            out.append("/* 函数原型 */")
            for name, cname, params, body in self.funcs:
                out.append("static Val %s(%s);" % (cname, self._c_params(params)))
        if self.closures:
            out.append("")
            out.append("/* 闭包原型 */")
            for c in self.closures:
                out.append("static Val %s(void *_env, Val *_args);" % c["cname"])
        out.append("/*__FFI_PROTOS__*/")
        if self.lambdas:
            out.append("")
            out.append("/* lambda 原型 */")
            for cname, _lam in self.lambdas:
                out.append("static Val %s(void *_env, Val *_args);" % cname)
                out.append("static Fn fob_%s;" % cname)
        # 全局变量
        if self.globals_map:
            out.append("")
            out.append("/* 全局变量 */")
            for orig, cname in sorted(self.globals_map.items(), key=lambda kv: kv[1]):
                out.append("static Val %s;" % cname)
        # 函数定义
        for name, cname, params, body in self.funcs:
            out.append("")
            out.append("/* Impor 函数：%s */" % name)
            out.append("static Val %s(%s) {" % (cname, self._c_params(params)))
            self._cur_fn = (name, {})
            self._lcnt = 0
            writes = self._fn_writes.get(name, set())
            boxed = self._boxed_by_fn.get(name, set())
            param_set = set(params)
            for i, p in enumerate(params):
                base = self._local_base(p)
                if p in boxed:
                    out.append("    Val *%s = rt_box_new(p%d);" % (base, i))
                else:
                    out.append("    Val %s = p%d;" % (base, i))
            for p in sorted(writes):
                if p not in param_set:
                    base = self._local_base(p)
                    if p in boxed:
                        out.append("    Val *%s = rt_box_new(v_null());" % base)
                    else:
                        out.append("    Val %s = v_null();" % base)
            self._gen_block(out, body, indent=1)
            out.append("    return v_null();")
            out.append("}")
        # ---- 类段：方法函数 / 方法表 / 字段初始化器 / ClassInfo ----
        for cls in self.classes:
            for mname, params, body in self.class_methods[cls.cls_name]:
                out.append("")
                out.append("/* Impor 方法：%s.%s */" % (cls.cls_name, mname))
                out.extend(self._gen_method(cls.cls_name, mname, params, body))
        for cls in self.classes:
            cn = cls.cls_name
            out.append("")
            out.append("static const Method mt_%s[] = {" % cn)
            for mname, params, body in self.class_methods[cn]:
                out.append('    {"%s", %s},' % (mname, self.method_cnames[(cn, mname)]))
            out.append("    {NULL, NULL},")
            out.append("};")
            if self.class_fields[cn]:
                out.append("static void fi_%s(Obj *o) {" % cn)
                self._cur_fn = None
                self._cur_method_self = None
                for fname, fexpr in self.class_fields[cn]:
                    out.append('    rt_objset(o, "%s", %s);' % (fname, self._gen_expr(fexpr)))
                out.append("}")
            else:
                out.append("static void fi_%s(Obj *o) { (void)o; }" % cn)
            base = '"%s"' % cls.base_name if cls.base_name else "NULL"
            disp = getattr(cls, "orig_name", cn)
            out.append("static ClassInfo ci_%s = {%s, %s, %s, mt_%s, fi_%s};" %
                       (cn, '"%s"' % cn, '"%s"' % disp, base, cn, cn))
        # ---- v4.2：顶层函数作为一等值（包装成统一 (env, args) 签名） ----
        if self.funcs:
            out.append("")
            out.append("/* 函数值包装 */")
            for name, cname, params, body in self.funcs:
                call = "%s(%s)" % (cname, ", ".join("args[%d]" % i for i in range(len(params))))
                out.append("static Val wrap_%s(void *env, Val *args) { (void)env; (void)args; return %s; }" % (cname, call))
                out.append('static Fn fob_%s = {wrap_%s, NULL, 0, %d, "%s"};' %
                           (cname, cname, len(params), name.replace('"', '\\"')))
        # ---- v4.2：嵌套函数（闭包） ----
        for c in self.closures:
            out.extend(self._gen_closure(c))
        # ---- 3.2.1：lambda 表达式 ----
        for cname, lam in self.lambdas:
            out.extend(self._gen_lambda(cname, lam))
        # main（--c-source 的库源码不生成 main）
        if self.emit_main:
            out.append("")
            out.append("int main(int argc, char **argv) {")
            out.append("    rt_args_init(argc, argv);")
            self._cur_fn = None
            self._gen_block(out, node.stmts, indent=1)
            out.append("    return 0;")
            out.append("}")
        # ---- v3.9：FFI 强类型调用点（main/函数体生成后才完整） ----
        if self.ffi_sites:
            out.append("")
            out.append("/* FFI 强类型调用点 */")
            out.extend(self.ffi_sites)
        if self.ffi_protos:
            block = "\n".join(["/* FFI 强类型调用点原型 */"] + self.ffi_protos)
        else:
            block = "/* 无 FFI 调用点 */"
        for i, s in enumerate(out):
            if s == "/*__FFI_PROTOS__*/":
                out[i] = block
                break
        return "\n".join(out) + "\n"

    def _gen_closure(self, c):
        cname = c["cname"]
        caps = c["caps"]
        params = c["params"]
        out = ["", "/* Impor 闭包：%s（外层 %s） */" % (c["name"], c["owner"]),
               "static Val %s(void *_env, Val *_args) {" % cname,
               "    (void)_env; (void)_args;"]
        self._cur_fn = (cname, {})
        self._lcnt = 0
        self._boxed_by_fn.setdefault(cname, set())
        for i, cap in enumerate(caps):
            base = self._local_base(cap)
            out.append("    Val *%s = ((Val **)_env)[%d];" % (base, i))
        for i, p in enumerate(params):
            base = self._local_base(p)
            out.append("    Val %s = _args[%d];" % (base, i))
        for w in sorted(self._fn_writes.get(cname, set())):
            if w not in set(params) and w not in set(caps):
                out.append("    Val %s = v_null();" % self._local_base(w))
        self._gen_block(out, c["body"], indent=1)
        out.append("    return v_null();")
        out.append("}")
        self._cur_fn = None
        return out

    def _gen_method(self, cls, name, params, body):
        """生成类方法函数：static Val fnN(Obj *self, Val *args)。
        self → Val lself（v_obj(self)）；参数 → Val l0=args[0]…；局部按需分配。"""
        self._lcnt = 0
        self._cur_fn = (name, {})
        self._cur_cls = cls
        self._cur_method_self = "lself"
        fn = self.method_cnames[(cls, name)]
        out = ["static Val %s(Obj *self, Val *args) {" % fn]
        out.append("    Val lself = v_obj(self);")
        for i, p in enumerate(params):
            out.append("    Val l%d = args[%d];" % (i, i))
            self._cur_fn[1][p] = "l%d" % i
            self._lcnt += 1
        writes = self._fn_writes.get("%s.%s" % (cls, name), set())
        for w in sorted(writes):
            if w not in set(params):
                out.append("    Val %s = v_null();" % self._lname(w))
        self._gen_block(out, body, indent=1)
        out.append("}")
        self._cur_method_self = None
        self._cur_cls = None
        return out

    def _c_params(self, params):
        if not params:
            return "void"
        return ", ".join("Val p%d" % i for i in range(len(params)))

    def _ind(self, s, n):
        pad = "    " * n
        return pad + s if s else s

    # ---- 语句生成 ----
    def _fast_int_loop(self, stmts, start):
        """v3.7.0：顶层整数紧循环快速路径，直接生成 long long 循环。

        只接受形状明确的纯整数循环：若干 int 常量初始化 + while(比较) +
        对同一组变量的 + - * 赋值。任何不支持的结构都返回 None，走通用 Val 路径。
        """
        from impor.ast_nodes import Assign, CompoundAssign, While, BinOp, Num, Var
        if start + 2 >= len(stmts):
            return None
        vars_map = {}
        i = start
        while i < len(stmts) and type(stmts[i]) is Assign and stmts[i].scope in ("local", "update"):
            a = stmts[i]
            if type(a.expr) is not Num or not isinstance(a.expr.val, int):
                return None
            vars_map[a.name] = "long long"
            i += 1
        if len(vars_map) < 2 or i >= len(stmts) or type(stmts[i]) is not While:
            return None
        loop = stmts[i]
        if type(loop.cond) is not BinOp or loop.cond.op not in ("<", "<=", ">", ">=", "==", "!="):
            return None
        cnames = {}
        for idx, name in enumerate(vars_map):
            cnames[name] = "_fi%d" % idx

        def iexpr(e):
            if type(e) is Num and isinstance(e.val, int):
                return str(e.val)
            if type(e) is Var and e.name in cnames:
                return cnames[e.name]
            if type(e) is BinOp and e.op in ("+", "-", "*"):
                left, right = iexpr(e.left), iexpr(e.right)
                if left is not None and right is not None:
                    return "(%s %s %s)" % (left, e.op, right)
            return None

        cond = iexpr(loop.cond.left)
        rhs = iexpr(loop.cond.right)
        if cond is None or rhs is None:
            return None
        body = []
        for s in loop.body:
            if type(s) is Assign and s.name in cnames:
                val = iexpr(s.expr)
                if val is None:
                    return None
                body.append("%s = %s;" % (cnames[s.name], val))
            elif type(s) is CompoundAssign and s.name in cnames:
                op = {"+=": "+", "-=": "-", "*=": "*"}.get(s.op)
                val = iexpr(s.expr)
                if op is None or val is None:
                    return None
                body.append("%s = %s %s %s;" % (cnames[s.name], cnames[s.name], op, val))
            else:
                return None
        if not body:
            return None
        init = []
        for name, cname in cnames.items():
            decl = next(x for x in stmts[start:i] if x.name == name)
            init.append("%s = %s" % (cname, iexpr(decl.expr)))
        lines = ["long long %s;" % ", ".join(init) + " /* fast-int loop */"]
        lines.append("while (%s %s %s) {" % (cond, loop.cond.op, rhs))
        for b in body:
            lines.append("    " + b)
        lines.append("}")
        for name, cname in cnames.items():
            lines.append("%s = v_int(%s);" % (self._gname(name), cname))
        return lines, i + 1

    def _gen_block(self, out, stmts, indent):
        idx = 0
        while idx < len(stmts):
            s = stmts[idx]
            if indent == 1 and self._cur_fn is None:
                fast = self._fast_int_loop(stmts, idx)
                if fast:
                    lines, nxt = fast
                    out.extend(self._ind(x, indent) for x in lines)
                    idx = nxt
                    continue
            body = self._gen_stmt(s)
            if body:
                # v3.3：语句首行前置 Impor 行号标记，供 tcc 编译错误反查源行
                ln0 = getattr(s, "lineno", 0) or 0
                if ln0:
                    out.append(self._ind("/*@L%d*/ rt_set_line(%d); %s" % (ln0, ln0, body[0]), indent))
                else:
                    out.append(self._ind(body[0], indent))
                for ln in body[1:]:
                    out.append(self._ind(ln, indent))
            idx += 1

    def _gen_stmt(self, n):
        from impor.ast_nodes import (
            Assign, CompoundAssign, Print, If, While, For, Def, Return,
            Break, Continue, ExitScript, ClsOut, AssertNode, ExprStmt,
            ClassDef, ExceptionDef, Call, IndexSet, AttrSet, Block,
            MatchNode, BindingPattern, TryCatch, Raise,
        )
        t = type(n)
        if t is MatchNode:
            return self._gen_match(n)
        if t is TryCatch:
            return self._gen_try(n)
        if t is Block:
            # v3.4：语句块（顶层 { } / do-until 降级后的 Block）逐条生成
            lines = []
            for s in n.stmts:
                lines.extend(self._gen_stmt(s))
            return lines
        if t is Assign:
            if self._cur_fn and n.scope == "update" and n.name not in self._fn_writes.get(self._cur_fn[0], set()):
                c = self._gname(n.name)  # set 就近更新：函数内无该局部 → 写全局
            else:
                c = self._lname(n.name) if self._cur_fn and n.scope != "global" else self._gname(n.name)
            return ["%s = %s;" % (c, self._gen_expr(n.expr))]
        if t is IndexSet:
            return ["rt_arr_set(%s, %s, %s);" % (self._gen_expr(n.container),
                                                 self._gen_expr(n.idx),
                                                 self._gen_expr(n.val))]
        if t is CompoundAssign:
            if n.scope == "global":
                c = self._gname(n.name)
            elif self._cur_fn:
                # 函数内：若名字在本函数写集合中则局部，否则尝试全局
                if n.scope == "update" and n.name not in self._fn_writes.get(self._cur_fn[0], set()):
                    c = self._gname(n.name)
                else:
                    c = self._lname(n.name)
            else:
                c = self._gname(n.name)
            opmap = {"+=": "rt_add", "-=": "rt_sub", "*=": "rt_mul",
                     "/=": "rt_div", "%=": "rt_mod"}
            fn = opmap.get(n.op)
            if fn is None:
                raise UnsupportedFeature("不支持的复合赋值：%s" % n.op, n.lineno, n.col)
            return ["%s = %s(%s, %s);" % (c, fn, c, self._gen_expr(n.expr))]
        if t is Print:
            return self._gen_print(n)
        if t is Call:
            from impor.ast_nodes import Var as _Var
            if isinstance(n.callee, _Var) and n.callee.name == "print":
                # parser 把顶层 print(...) 解析为 Call；按 Print 语义生成
                from impor.ast_nodes import Print as _Print
                return self._gen_print(_Print(n.args, n.lineno, n.col))
            return ["%s;" % self._gen_expr(n)]
        if t is If:
            return self._gen_if(n)
        if t is While:
            return self._gen_while(n)
        if t is For:
            return self._gen_for(n)
        if t is Def:
            # 顶层函数已在函数区生成；嵌套函数在这里创建闭包值
            if not self._cur_fn:
                return []
            owner = self._cur_fn[0]
            if owner not in set(c["owner"] for c in self.closures):
                return []
            cname = self._closure_map.get((owner, n.name))
            if not cname:
                return []
            caps = self._fn_caps.get(cname, [])
            base = self._local_base(n.name)
            lhs = "(*%s)" % base if self._is_boxed(n.name) else base
            rhs = 'rt_fn_new(%s, ' % cname
            lines = []
            if caps:
                self._lcnt += 1
                tmp = "_cap%d" % (self._lcnt - 1)
                lines.append("Val *%s[%d];" % (tmp, len(caps)))
                for i, cap in enumerate(caps):
                    raw = self._local_base(cap)
                    ptr = raw if self._is_boxed(cap) else ("&" + raw)
                    lines.append("%s[%d] = %s;" % (tmp, i, ptr))
                rhs += "%s, %d" % (tmp, len(caps))
            else:
                rhs += "NULL, 0"
            rhs += ', "%s", %d)' % (n.name.replace('"', '\\"'), len(n.params))
            lines.append("%s = %s;" % (lhs, rhs))
            return lines
        if t is Return:
            e = "v_null()" if n.expr is None else self._gen_expr(n.expr)
            return ["return %s;" % e]
        if t is Break:
            if not self._loop_depth:
                raise UnsupportedFeature("break 不在循环内", n.lineno, n.col)
            return ["break;"]
        if t is Continue:
            if not self._loop_depth:
                raise UnsupportedFeature("continue 不在循环内", n.lineno, n.col)
            return ["continue;"]
        if t is ExitScript:
            return ["rt_exit_script();"]
        if t is ClsOut:
            return ["rt_cls();"]
        if t is AssertNode:
            return ["rt_assert(%s, \"断言失败\");" % self._gen_expr(n.cond)]
        if t is ExprStmt:
            return ["%s;" % self._gen_expr(n.expr)]
        if t is AttrSet:
            return ["rt_objset(%s.o, %s, %s);" % (self._gen_expr(n.obj_expr),
                                                   c_escape_str(n.attr_name),
                                                   self._gen_expr(n.value))]
        if t is ClassDef:
            return ["reg_class(&ci_%s);" % n.cls_name]
        if t is ExceptionDef:
            return []  # v4.1：声明本身无代码，名字已在收集阶段登记
        if t is Raise:
            return [self._gen_raise(n)]
        raise UnsupportedFeature(
            "C 后端暂不支持：%s" % _UNSUP_NODE.get(t.__name__, t.__name__),
            getattr(n, "lineno", 0), getattr(n, "col", 0))

    def _gen_print(self, n):
        from impor.ast_nodes import Str, FStr
        out = []
        items = n.items
        for i, e in enumerate(items):
            if isinstance(e, Str):
                out.append("rt_pstr(%s);" % c_escape_str(e.val))
            elif isinstance(e, FStr):
                out.extend(self._gen_fstr_parts(e))
            else:
                out.append("rt_print_val(%s);" % self._gen_expr(e))
            if i != len(items) - 1:
                out.append("rt_print_sep();")
        if not items:
            out.append("rt_print_nl();")
        else:
            out.append("rt_print_nl();")
        return out

    def _gen_fstr_parts(self, n):
        """插值字符串打印：文本段 rt_pstr，表达式段 rt_print_val（expr 段用 n.exprs 对齐）。"""
        out = []
        ei = 0
        for kind, val in n.parts:
            if kind == "text":
                out.append("rt_pstr(%s);" % c_escape_str(val))
            else:
                out.append("rt_print_val(%s);" % self._gen_expr(n.exprs[ei]))
                ei += 1
        return out

    def _gen_if(self, n):
        out = ["if (rt_truth(%s)) {" % self._gen_expr(n.cond)]
        self._gen_block(out, n.body, indent=1)
        for cond, body in n.elseifs:
            out.append("} else if (rt_truth(%s)) {" % self._gen_expr(cond))
            self._gen_block(out, body, indent=1)
        if n.elseb is not None:
            out.append("} else {")
            self._gen_block(out, n.elseb, indent=1)
        out.append("}")
        return out

    def _gen_match(self, n):
        """match/switch → 单次求值 + if 链；绑定模式只在分支内绑定。"""
        from impor.ast_nodes import BindingPattern, AdtPattern
        self._match_seq += 1
        mid = self._match_seq
        subj = "_m%d" % mid
        hit = "_mh%d" % mid
        out = ["{", "    Val %s = %s;" % (subj, self._gen_expr(n.subject)),
               "    int %s = 0;" % hit]
        for pat, body in n.cases:
            if isinstance(pat, AdtPattern):
                cond = "rt_truth(rt_cmp(rt_dict_get(%s, v_str(\"__adt\")), v_str(\"%s\"), \"==\"))" % (
                    subj, pat.tag.replace('"', '\\"'))
            elif isinstance(pat, BindingPattern):
                cond = "1"  # 绑定模式匹配任意值
            else:
                cond = "rt_truth(rt_cmp(%s, %s, \"==\"))" % (
                    subj, self._gen_expr(pat))
            out.append("    if (!%s && %s) {" % (hit, cond))
            out.append("        %s = 1;" % hit)
            if isinstance(pat, AdtPattern):
                for bind, fname in zip(pat.fields, pat.field_names):
                    if not bind:
                        continue
                    val = "rt_dict_get(%s, v_str(\"%s\"))" % (subj, fname.replace('"', '\\"'))
                    if self._cur_fn:
                        out.append("        Val %s = %s;" % (self._lname(bind), val))
                    else:
                        out.append("        %s = %s;" % (self._gname(bind), val))
            elif isinstance(pat, BindingPattern) and pat.name:
                if self._cur_fn:
                    out.append("        Val %s = %s;" % (self._lname(pat.name), subj))
                else:
                    out.append("        %s = %s;" % (self._gname(pat.name), subj))
            self._gen_block(out, body, indent=2)
            out.append("    }")
        if n.elseb is not None:
            out.append("    if (!%s) {" % hit)
            self._gen_block(out, n.elseb, indent=2)
            out.append("    }")
        out.append("}")
        return out

    def _gen_raise(self, n):
        """v4.1：raise Foo("消息") → rt_throw("Foo", "消息")。

        exception 声明的类型名在收集阶段登记，但这里不强制查表：
        与解释器一致，raise 后面的名字本身就是类型名。
        """
        tname = n.exc_type_name or "Error"
        msg = ('rt_str_of(rt_tostr(%s))' % self._gen_expr(n.msg_expr)
               if n.msg_expr is not None else '""')
        return 'rt_throw(%s, %s);' % (c_escape_str(tname), msg)

    def _gen_try(self, n):
        """try/catch/finally → setjmp 跳转栈。

        v3.8.4：运行时已有 g_jmp_stack / g_err_type / g_err_msg，这里补上
        push/pop 与 catch 变量绑定。异常由 rt_throw() longjmp 回最近一层。

        v4.1：按类型过滤 catch（catch Foo e），类型不匹配时先跑 finally，
        再把异常抛给外一层 try —— 与解释器的捕获顺序一致。
        """
        self._try_seq += 1
        tid = self._try_seq
        tj = "_tj%d" % tid
        rh = "_rh%d" % tid
        out = ["{", "    int %s = g_jmp_n++;" % tj,
               "    int %s = 0;" % rh,
               "    if (setjmp(g_jmp_stack[%s]) == 0) {" % tj]
        self._gen_block(out, n.body, indent=2)
        out.append("        g_jmp_n = %s;" % tj)
        out.append("    } else {")
        out.append("        g_jmp_n = %s;" % tj)
        if n.catch_body:
            ev = n.err_var or "__err"
            target = self._lname(ev) if self._cur_fn else self._gname(ev)
            out.append("        Val %s = rt_err_obj();" % target)
            if n.err_type:
                out.append("        if (rt_err_matches(%s)) {" % c_escape_str(n.err_type))
                self._gen_block(out, n.catch_body, indent=3)
                out.append("        } else {")
                out.append("            %s = 1;" % rh)
                out.append("        }")
            else:
                self._gen_block(out, n.catch_body, indent=2)
        else:
            out.append("        %s = 1;" % rh)
        out.append("    }")
        if n.finally_body:
            self._gen_block(out, n.finally_body, indent=1)
        out.append("    if (%s) rt_rethrow(%s);" % (rh, tj))
        out.append("}")
        return out

    def _gen_while(self, n):
        self._loop_depth += 1
        out = ["while (rt_truth(%s)) {" % self._gen_expr(n.cond)]
        self._gen_block(out, n.body, indent=1)
        out.append("}")
        self._loop_depth -= 1
        return out

    def _gen_for(self, n):
        """for x in range(...) → C for 循环；for x in 数组 → 数组迭代。"""
        from impor.ast_nodes import Call, Var, Num
        it = n.iter_expr
        varc = self._lname(n.var) if self._cur_fn else self._gname(n.var)
        if (isinstance(it, Call) and isinstance(it.callee, Var)
                and it.callee.name == "range"):
            args = it.args
            if len(args) not in (1, 2, 3):
                raise UnsupportedFeature("range 参数数量错误", n.lineno, n.col)
            self._loop_depth += 1
            out = []
            if len(args) == 1:
                out.append("for (Val %s = v_int(0); rt_int(%s) < rt_int(%s); "
                           "%s = v_int(rt_int(%s) + 1)) {" %
                           (varc, varc, self._gen_expr(args[0]), varc, varc))
            elif len(args) == 2:
                out.append("for (Val %s = %s; rt_int(%s) < rt_int(%s); "
                           "%s = v_int(rt_int(%s) + 1)) {" %
                           (varc, self._gen_expr(args[0]), varc,
                            self._gen_expr(args[1]), varc, varc))
            else:
                st = self._gen_expr(args[0])
                en = self._gen_expr(args[1])
                sp = self._gen_expr(args[2])
                out.append("for (Val %s = %s; (rt_int(%s) >= 0 ? "
                           "rt_int(%s) < rt_int(%s) : rt_int(%s) > rt_int(%s)); "
                           "%s = v_int(rt_int(%s) + rt_int(%s))) {" %
                           (varc, st, sp, varc, en, varc, en, varc, varc, sp))
            self._gen_block(out, n.body, indent=1)
            out.append("}")
            self._loop_depth -= 1
            return out
        # 数组迭代：{ Val _fa; for (k < n) { varc = rt_arr_get(...); 块 } }
        uid = id(n)
        self._loop_depth += 1
        out = []
        out.append("{ Val _fa%d = %s; Val _fk%d = (_fa%d.t == V_DICT) ? "
                   "rt_dict_keys(_fa%d) : _fa%d; for (long long _fi%d = 0; _fi%d < "
                   "(_fk%d.t == V_ARR && _fk%d.a ? _fk%d.a->n : 0); _fi%d++) {" %
                   (uid, self._gen_expr(it), uid, uid, uid, uid, uid, uid, uid,
                    uid, uid, uid))
        out.append("  %s = rt_arr_get(_fk%d, v_int(_fi%d));" % (varc, uid, uid))
        self._gen_block(out, n.body, indent=2)
        out.append("} }")
        self._loop_depth -= 1
        return out

    # ---- 表达式生成 ----
    def _gen_expr(self, n):
        from impor.ast_nodes import (
            Num, Str, FStr, Bool, Null, Var, BinOp, UnaryOp, TernaryExpr, Call,
            ListLit, DictLit, IndexGet, Slice, AttrGet, Pipe, NewInstance, SuperCall,
            Lambda, Await,
        )
        t = type(n)
        if t is Num:
            if isinstance(n.val, float):
                return "v_float(%.17g)" % n.val
            return "v_int(%d)" % n.val
        if t is Str:
            return "v_str_lit(%s)" % c_escape_str(n.val)
        if t is Bool:
            return "v_bool(%d)" % (1 if n.val else 0)
        if t is Null:
            return "v_null()"
        if t is Var:
            return self._gen_var(n)
        if t is BinOp:
            return self._gen_binop(n)
        if t is UnaryOp:
            if n.op == "-":
                return "rt_neg(%s)" % self._gen_expr(n.child)
            if n.op == "not":
                return "rt_not(%s)" % self._gen_expr(n.child)
            raise UnsupportedFeature("不支持的运算符：%s" % n.op, n.lineno, n.col)
        if t is TernaryExpr:
            return "(%s ? %s : %s)" % (self._gen_expr(n.cond),
                                       self._gen_expr(n.true_expr),
                                       self._gen_expr(n.false_expr))
        if t is Call:
            return self._gen_call(n)
        if t is Lambda:
            cname = self._lambda_map.get(id(n))
            if not cname:
                raise UnsupportedFeature("lambda 收集失败", n.lineno, n.col)
            return "v_fn(&fob_%s)" % cname
        if t is Await:
            return self._gen_expr(n.expr)
        if t is FStr:
            return self._gen_fstr_expr(n)
        if t is ListLit:
            items = ", ".join(self._gen_expr(x) for x in n.items)
            return "rt_arr_of(%d%s%s)" % (len(n.items), ", " if items else "", items)
        if t is DictLit:
            pairs = []
            for k, v in n.kv:
                pairs.append(self._gen_expr(k))
                pairs.append(self._gen_expr(v))
            return "rt_dict_of(%d%s%s)" % (len(n.kv), ", " if pairs else "", ", ".join(pairs))
        if t is Slice:
            start = "v_null()" if n.start is None else self._gen_expr(n.start)
            end = "v_null()" if n.end is None else self._gen_expr(n.end)
            step = "v_null()" if n.step is None else self._gen_expr(n.step)
            return "rt_slice(%s, %s, %s, %s)" % (
                self._gen_expr(n.container), start, end, step)
        if t is IndexGet:
            return "rt_index(%s, %s)" % (self._gen_expr(n.container),
                                        self._gen_expr(n.idx))
        if t is AttrGet:
            if isinstance(n.obj_expr, SuperCall):
                raise UnsupportedFeature(
                    "super() 只能用于方法调用 super().方法()", n.lineno, n.col)
            return "rt_getattr(%s, %s)" % (self._gen_expr(n.obj_expr),
                                           c_escape_str(n.attr_name))
        if t is NewInstance:
            args = [self._gen_expr(a) for a in n.args]
            if not args:
                return "rt_new(%s, NULL, 0)" % c_escape_str(n.cls_name)
            arr, cnt = self._arg_array(args)
            return ("({ Val %s[%d] = {%s}; rt_new(%s, %s, %d); })" %
                    (arr, cnt, ", ".join(args), c_escape_str(n.cls_name), arr, cnt))
        if t is SuperCall:
            raise UnsupportedFeature(
                "super() 只能用于方法调用 super().方法()", n.lineno, n.col)
        if t is Pipe:
            # v3.7.8：C 后端把管道降级为普通调用：x |> f(a) → f(x, a)，x |> f → f(x)。
            rhs = n.right
            if isinstance(rhs, Call):
                return self._gen_call(Call(rhs.callee, [n.left] + list(rhs.args),
                                           n.lineno, n.col))
            return self._gen_call(Call(rhs, [n.left], n.lineno, n.col))
        raise UnsupportedFeature(
            "C 后端暂不支持：%s" % _UNSUP_NODE.get(t.__name__, t.__name__),
            getattr(n, "lineno", 0), getattr(n, "col", 0))

    def _gen_var(self, n):
        if self._cur_method_self and n.name == "self":
            return self._cur_method_self  # 方法内 self → lself（Val 含 .o）
        if self._cur_fn:
            loc = self._cur_fn[1]  # 参数 + 局部（写集合）均已分配
            if n.name in loc:
                return self._lname(n.name)
            if n.name in self.globals_map:
                return self._gname(n.name)
        else:
            if n.name in self.globals_map:
                return self._gname(n.name)
        if n.name in self.funcs_map:
            return "v_fn(&fob_%s)" % self.funcs_map[n.name]
        raise UnsupportedFeature("未定义变量：%s" % n.name, n.lineno, n.col)

    def _gen_binop(self, n):
        from impor.ast_nodes import Num, Str
        op = n.op
        l = self._gen_expr(n.left)
        r = self._gen_expr(n.right)
        if op in ("+", "-", "*", "/", "%"):
            fn = {"+": "rt_add", "-": "rt_sub", "*": "rt_mul",
                  "/": "rt_div", "%": "rt_mod"}[op]
            return "%s(%s, %s)" % (fn, l, r)
        if op in ("==", "!=", "<", ">", "<=", ">="):
            return "rt_cmp(%s, %s, \"%s\")" % (l, r, op)
        if op == "and":
            # C 三元惰性求值，且返回操作数本身（Python 语义）
            return "(rt_truth(%s) ? %s : %s)" % (l, r, l)
        if op == "or":
            return "(rt_truth(%s) ? %s : %s)" % (l, l, r)
        raise UnsupportedFeature("不支持的运算符：%s" % op, n.lineno, n.col)

    def _ffi_check_literal(self, node):
        from impor.ast_nodes import Str
        if not isinstance(node, Str):
            return
        base = node.val.replace("\\", "/").split("/")[-1].lower()
        if base not in self.ffi_allow:
            raise UnsupportedFeature(
                "原生库 %s 未登记在 .impor/ffi_allow.json 的 allow 中"
                "（或 IMPOR_FFI_ALLOW）；C 后端在编译期拒绝未登记库" % base,
                node.lineno, node.col)

    def _gen_ffi_call(self, n):
        """v3.9：ffi_call → 生成强类型静态包装，避免 C 变参 ABI 猜类型。

        只接受字面量 argtypes / ret。动态签名无法在 C 编译期生成正确调用，
        这里直接拒绝，而不是生成一个会在运行时崩溃的弱类型调用。
        """
        from impor.ast_nodes import Str, ListLit, Null
        if len(n.args) < 2 or len(n.args) > 6:
            raise UnsupportedFeature("ffi_call 需要 2–6 个参数", n.lineno, n.col)
        self._ffi_check_literal(n.args[0])
        calling = ""
        if len(n.args) >= 6:
            cnode = n.args[5]
            if isinstance(cnode, Str):
                calling = cnode.val.lower()
            elif not isinstance(cnode, Null):
                raise UnsupportedFeature(
                    "C 后端 ffi_call 的 calling 必须是字符串字面量", n.lineno, n.col)
            if calling not in ("", "stdcall", "cdecl"):
                raise UnsupportedFeature("calling 只支持 stdcall / cdecl",
                                         n.lineno, n.col)
        args_node = n.args[2] if len(n.args) >= 3 else None
        if args_node is None or isinstance(args_node, Null):
            args_count = 0
            args_expr = "v_null()"
        elif isinstance(args_node, ListLit):
            args_count = len(args_node.items)
            args_expr = self._gen_expr(args_node)
        else:
            raise UnsupportedFeature(
                "C 后端 ffi_call 的参数数组必须是字面量列表", n.lineno, n.col)
        if args_count > 8:
            raise UnsupportedFeature("ffi_call 最多支持 8 个参数", n.lineno, n.col)

        ret_node = n.args[3] if len(n.args) >= 4 else None
        ret = ret_node.val.lower() if isinstance(ret_node, Str) else "int"

        types = []
        at_node = n.args[4] if len(n.args) >= 5 else None
        if at_node is not None and not isinstance(at_node, Null):
            if not isinstance(at_node, ListLit) or not all(
                    isinstance(x, Str) for x in at_node.items):
                raise UnsupportedFeature(
                    "C 后端 ffi_call 的 argtypes 必须是字符串字面量列表",
                    n.lineno, n.col)
            types = [x.val.lower() for x in at_node.items]
        if len(types) != args_count:
            raise UnsupportedFeature(
                "ffi_call 的 argtypes 数量必须与参数个数一致（%d != %d）" %
                (len(types), args_count), n.lineno, n.col)

        carg = []
        arg_exprs = []
        for i, t in enumerate(types):
            if t in ("int", "int64", "i64"):
                carg.append("long long")
                arg_exprs.append("(long long)_args.a->items[%d].i" % i)
            elif t in ("i32", "int32"):
                carg.append("int")
                arg_exprs.append("(int)_args.a->items[%d].i" % i)
            elif t == "bool":
                carg.append("int")
                arg_exprs.append("(int)_args.a->items[%d].i" % i)
            elif t == "float":
                carg.append("double")
                arg_exprs.append("_args.a->items[%d].f" % i)
            elif t in ("cstr", "str", "string"):
                carg.append("const char *")
                arg_exprs.append(
                    "(_args.a->items[%d].s ? _args.a->items[%d].s : \"\")" % (i, i))
            elif t in ("addr", "address", "uintptr"):
                carg.append("void *")
                arg_exprs.append(
                    "(void *)(intptr_t)_args.a->items[%d].i" % i)
            elif t in ("ptr", "pointer", "void*"):
                raise UnsupportedFeature(
                    "C 后端不接收任意指针参数（遵守 FFI 只做调用的硬约束）",
                    n.lineno, n.col)
            else:
                raise UnsupportedFeature("C 后端不认识的 FFI 参数类型：%s" % t,
                                         n.lineno, n.col)

        if ret in ("int", "int64"):
            rtyp = "int"
            rexpr = "v_int((long long)r)"
        elif ret in ("float", "double", "f64"):
            rtyp = "double"
            rexpr = "v_float(r)"
        elif ret == "bool":
            rtyp = "int"
            rexpr = "v_bool(r)"
        elif ret in ("cstr", "str", "string"):
            rtyp = "const char *"
            rexpr = 'v_str(r ? r : "")'
        elif ret in ("ptr", "pointer", "void*"):
            rtyp = "void *"
            rexpr = "v_int((long long)(intptr_t)r)"
        elif ret in ("void", "none", "null"):
            rtyp = "void"
            rexpr = "v_null()"
        else:
            raise UnsupportedFeature("C 后端不认识的 FFI 返回类型：%s" % ret,
                                     n.lineno, n.col)

        site = "ffi_site%d" % self._ffi_site_cnt
        self._ffi_site_cnt += 1
        self.ffi_protos.append(
            "static Val %s(Val _lib, Val _func, Val _args);" % site)
        conv = "IMPOR_STDCALL" if calling == "stdcall" else ""
        lines = [
            "static Val %s(Val _lib, Val _func, Val _args) {" % site,
            "    typedef %s (%s *FN)(%s);" % (
                rtyp, conv, ", ".join(carg) if carg else "void"),
            "    FN fn = (FN)rt_ffi_sym(_lib, _func);",
        ]
        if args_count:
            lines.append(
                "    if (_args.t != V_ARR || !_args.a || _args.a->n != %d) "
                'rt_throw("FFIError", "FFI 参数个数与 argtypes 不一致");' % args_count)
        if rtyp == "void":
            lines.append("    fn(%s);" % ", ".join(arg_exprs))
            lines.append("    if (_args.t == V_ARR && _args.a) { rt_arr_unreg(_args.a);"
                         " if (_args.a->items) free(_args.a->items);"
                         " free(_args.a); _args.a = NULL; }")
            lines.append("    return v_null();")
        else:
            lines.append("    %s r = fn(%s);" % (rtyp, ", ".join(arg_exprs)))
            lines.append("    Val _ret = %s;" % rexpr)
            lines.append("    if (_args.t == V_ARR && _args.a) { rt_arr_unreg(_args.a);"
                         " if (_args.a->items) free(_args.a->items);"
                         " free(_args.a); _args.a = NULL; }")
            lines.append("    return _ret;")
        lines.append("}")
        self.ffi_sites.append("\n".join(lines))
        return "%s(%s, %s, %s)" % (
            site, self._gen_expr(n.args[0]), self._gen_expr(n.args[1]), args_expr)

    def _gen_call(self, n):
        from impor.ast_nodes import Var, AttrGet, SuperCall
        cal = n.callee
        if isinstance(cal, AttrGet):
            args = [self._gen_expr(a) for a in n.args]
            if isinstance(cal.obj_expr, SuperCall):
                # super().方法(实参) → 从当前类基类起沿继承链找方法
                if not self._cur_method_self or not self._cur_cls:
                    raise UnsupportedFeature(
                        "super() 只能在类方法内使用", n.lineno, n.col)
                if not args:
                    return "rt_supercall(%s.o, %s, %s, NULL, 0)" % (
                        self._cur_method_self, c_escape_str(self._cur_cls),
                        c_escape_str(cal.attr_name))
                arr, cnt = self._arg_array(args)
                return ("({ Val %s[%d] = {%s}; rt_supercall(%s.o, %s, %s, %s, %d); })" % (
                    arr, cnt, ", ".join(args), self._cur_method_self,
                    c_escape_str(self._cur_cls), c_escape_str(cal.attr_name), arr, cnt))
            obj = self._gen_expr(cal.obj_expr)
            if not args:
                return "rt_method(%s, %s, NULL, 0)" % (obj, c_escape_str(cal.attr_name))
            arr, cnt = self._arg_array(args)
            return ("({ Val %s[%d] = {%s}; rt_method(%s, %s, %s, %d); })" % (
                arr, cnt, ", ".join(args), obj, c_escape_str(cal.attr_name), arr, cnt))
        if not isinstance(cal, Var):
            raise UnsupportedFeature("仅支持直接函数调用（不支持属性/索引调用）",
                                     n.lineno, n.col)
        name = cal.name
        if name == "print":
            raise UnsupportedFeature("print 是语句，不能作为表达式", n.lineno, n.col)
        if name == "list":
            # list() 空数组；list(n) 新建 n 个 0（与解释器一致）
            if not n.args:
                return "rt_arr_of(0)"
            if len(n.args) == 1:
                return "rt_arrfill(%s, v_int(0))" % self._gen_expr(n.args[0])
            raise UnsupportedFeature("list() 最多 1 个参数", n.lineno, n.col)
        if name == "ffi_open":
            if len(n.args) != 1:
                raise UnsupportedFeature("ffi_open 需要 1 个参数（库路径）",
                                         n.lineno, n.col)
            self._ffi_check_literal(n.args[0])
            return "rt_ffi_open(%s)" % self._gen_expr(n.args[0])
        if name == "ffi_call":
            return self._gen_ffi_call(n)
        if name in self.funcs_map:
            cname = self.funcs_map[name]
            params = [f[2] for f in self.funcs if f[1] == cname][0]
            if len(n.args) != len(params):
                raise UnsupportedFeature(
                    "%s 需要 %d 个参数，给了 %d 个" %
                    (name, len(params), len(n.args)), n.lineno, n.col)
            args = [self._gen_expr(a) for a in n.args]
            return ("({ rt_trace_push(%s, %d); Val _tr = %s(%s); rt_trace_pop(); _tr; })" %
                    (c_escape_str(name), n.lineno, cname, ", ".join(args)))
        if name in BUILTINS:
            fn = BUILTINS[name]
            args = [self._gen_expr(a) for a in n.args]
            if fn in ("rt_now", "rt_clock"):
                if args:
                    raise UnsupportedFeature("%s() 不需要参数" % name, n.lineno, n.col)
                return "%s()" % fn
            if fn in ("rt_input", "rt_inputstr") and not args:
                args = ['v_str("")']
            if fn == "rt_expect":
                if len(args) not in (2, 3):
                    raise UnsupportedFeature("expect 需要 2 或 3 个参数", n.lineno, n.col)
                if len(args) == 2:
                    args.append('v_str("")')
            return "%s(%s)" % (fn, ", ".join(args))
        if name in UNSUPPORTED_BUILTINS_HINT:
            raise UnsupportedFeature(
                "内建函数 %s() 在 C 后端暂不支持（请用 --backend=py）" % name,
                n.lineno, n.col)
        # v4.2：局部 / 全局变量里存放的函数值（闭包、函数作参数）
        if (self._cur_fn and name in self._cur_fn[1]) or (
                (not self._cur_fn) and name in self.globals_map):
            target = self._lname(name) if self._cur_fn else self._gname(name)
            args = [self._gen_expr(a) for a in n.args]
            if not args:
                return "rt_call(%s, NULL, 0)" % target
            arr, cnt = self._arg_array(args)
            return ("({ Val %s[%d] = {%s}; rt_call(%s, %s, %d); })" %
                    (arr, cnt, ", ".join(args), target, arr, cnt))
        raise UnsupportedFeature("未定义函数：%s" % name, n.lineno, n.col)

    def _gen_fstr_expr(self, n):
        """插值字符串作为表达式：rt_cat 链拼接（expr 段用 n.exprs 对齐）。"""
        segs = []
        ei = 0
        for kind, val in n.parts:
            if kind == "text":
                segs.append("v_str(%s)" % c_escape_str(val))
            else:
                segs.append("rt_tostr(%s)" % self._gen_expr(n.exprs[ei]))
                ei += 1
        if not segs:
            return "v_str(\"\")"
        expr = segs[0]
        for s in segs[1:]:
            expr = "rt_add(%s, %s)" % (expr, s)
        return expr


def _reject_const_mutation(root):
    """C 后端不跟踪 const 环境：任何对 const 名字的再次赋值都必须在构建期拒绝。

    这不是“降级”，而是把 C 后端无法兑现的只读承诺明确挡住；读取 const 仍然允许。
    """
    from impor.ast_nodes import Assign, CompoundAssign

    consts = set()

    def collect(node):
        if isinstance(node, (list, tuple)):
            for x in node:
                collect(x)
            return
        if not hasattr(node, "__dict__"):
            return
        if isinstance(node, Assign) and getattr(node, "scope", "") == "const":
            consts.add(node.name)
        for v in node.__dict__.values():
            if isinstance(v, (list, tuple)) or hasattr(v, "__dict__"):
                collect(v)

    collect(root)
    if not consts:
        return

    def scan(node):
        if isinstance(node, (list, tuple)):
            for x in node:
                scan(x)
            return
        if not hasattr(node, "__dict__"):
            return
        if isinstance(node, Assign):
            if getattr(node, "scope", "") != "const" and node.name in consts:
                raise UnsupportedFeature(
                    "常量 %s 不可修改（const 只读）；C 后端不跟踪 const 环境，"
                    "请改用 var 或删除重新赋值" % node.name,
                    getattr(node, "lineno", 0), getattr(node, "col", 0))
        elif isinstance(node, CompoundAssign) and node.name in consts:
            raise UnsupportedFeature(
                "常量 %s 不可修改（const 只读）；C 后端不跟踪 const 环境，"
                "请改用 var 或删除重新赋值" % node.name,
                getattr(node, "lineno", 0), getattr(node, "col", 0))
        for k, v in node.__dict__.items():
            if k.startswith("_"):
                continue
            if isinstance(v, (list, tuple)) or hasattr(v, "__dict__"):
                scan(v)

    scan(root)


def c_transpile(ast_node, source_name=None, stats=None, source_lines=None,
                emit_main=True):
    """Impor AST → C 源码字符串。抛 UnsupportedFeature 表示需用 Python 后端。

    stats：可选 dict，回填 "modules"（本次链接进来的模块名列表）。
    source_lines：可选源码行，用于把源码片段嵌入 C 运行时，使独立 exe 出错时也能显示。
    """
    from .clink import link_modules, LinkError
    try:
        ast_node = link_modules(ast_node, source_name=source_name,
                                stats=stats)
    except LinkError as e:
        raise UnsupportedFeature(str(e), 0, 0)
    _reject_const_mutation(ast_node)
    gen = CGen()
    gen.source_lines = source_lines
    gen.source_name = source_name
    gen.emit_main = emit_main
    c = gen.gen_program(ast_node)
    if stats is not None:
        stats["exports"] = list(gen.exports)
    return c
