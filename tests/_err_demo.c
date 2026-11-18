/* 由 Impor (I++) 编译器生成，勿手改 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
/* 源码行（运行时诊断用） */
const char *g_src_lines[] = {
    "important<<DAY<<english(\"English\"):",
    "func f(x)",
    "    return 10 / x",
    "print(f(0))",
};
int g_src_n = 4;

/* Impor C 运行库 —— 由 impor build 自动内嵌，勿手改 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <time.h>
#include <stdarg.h>
#include <setjmp.h>
#include <limits.h>
#ifdef _WIN32
#include <windows.h>
#else
#include <unistd.h>
#endif

typedef enum { V_NULL, V_INT, V_FLOAT, V_STR, V_BOOL, V_ARR, V_OBJ, V_DICT, V_FUNC, V_MODULE } VType;
typedef struct Arr Arr;
typedef struct Obj Obj;
typedef struct Fn Fn;
typedef struct { VType t; long long i; double f; char *s; Arr *a; Obj *o; Fn *fn; } Val;
struct Arr { Val *items; long long n, cap; struct Arr *rp_prev, *rp_next; };
/* v4.2：一等函数 / 闭包。code 统一为 (env, args)，env 是捕获变量指针数组 */
typedef Val (*FnCode)(void *env, Val *args);
struct Fn { FnCode code; void *env; int ncap; int arity; const char *name; };
/* v4.2：模块对象（导出名 -> 值），o 字段按指针复用 */
typedef struct Mod { const char *name; Val attrs; } Mod;

/* 对象系统类型定义（前向声明区；函数实现见文件后部） */
typedef struct { char *name; Val v; } Field;
struct Obj { int cls_id; int n; int cap; Field *fs; };
typedef Val (*Mfn)(Obj *, Val *);
typedef struct { const char *name; Mfn fn; } Method;
typedef struct ClassInfo { const char *name; const char *disp; const char *base; const Method *methods; void (*init_fields)(Obj *); } ClassInfo;
static ClassInfo *g_cls_tab[64];
static int g_cls_n = 0;

static void rt_report_abort(const char *msg);
static void rt_abort(const char *msg) {
    rt_report_abort(msg);
    exit(1);
}

/* ================= v3.2 自动内存管理（C 侧） =================
   策略：所有堆数组登记到双链表，进程退出（atexit）统一释放；
   数值/字符串字面量走驻留池（相同常量复用同一块内存）；
   全程统计分配次数 / 字节 / 峰值，供 mem() 查询。
   安全约定：退出时只释放数组结构与 items，不释放字符串（可能被共享，
   进程结束由操作系统回收），因此不存在 double free。 */
static long long g_rt_allocs = 0;
static long long g_rt_bytes = 0;
static long long g_rt_peak = 0;
static long long g_rt_frees = 0;
static Arr *g_rt_arr_head = NULL;
static long long g_rt_arr_n = 0;
static int g_rt_shutdown_ready = 0;
static void rt_shutdown(void);
static void rt_note_alloc(long long bytes) {
    g_rt_allocs++;
    g_rt_bytes += bytes;
    if (g_rt_bytes > g_rt_peak) g_rt_peak = g_rt_bytes;
    if (!g_rt_shutdown_ready) {
        g_rt_shutdown_ready = 1;
        atexit(rt_shutdown);
    }
}
static void rt_note_free(long long bytes) {
    g_rt_frees++;
    g_rt_bytes -= bytes;
    if (g_rt_bytes < 0) g_rt_bytes = 0;
}
static void rt_arr_reg(Arr *a) {
    a->rp_prev = NULL;
    a->rp_next = g_rt_arr_head;
    if (g_rt_arr_head) g_rt_arr_head->rp_prev = a;
    g_rt_arr_head = a;
    g_rt_arr_n++;
}
static void rt_arr_unreg(Arr *a) {
    if (!a) return;
    if (a->rp_prev) a->rp_prev->rp_next = a->rp_next;
    else if (g_rt_arr_head == a) g_rt_arr_head = a->rp_next;
    if (a->rp_next) a->rp_next->rp_prev = a->rp_prev;
    a->rp_prev = NULL;
    a->rp_next = NULL;
    if (g_rt_arr_n > 0) g_rt_arr_n--;
}
static void rt_shutdown(void) {
    Arr *p = g_rt_arr_head;
    while (p) {
        Arr *nx = p->rp_next;
        if (p->items) free(p->items);
        free(p);
        p = nx;
    }
    g_rt_arr_head = NULL;
    g_rt_arr_n = 0;
}

/* ---- 字符串常量驻留池（仅字面量 v_str_lit 使用；普通 v_str 仍为独立分配） ---- */
#define RT_INTERN_BUCKETS 128
#define RT_INTERN_MAX 4096
#define RT_INTERN_MAXLEN 64
typedef struct RtStrNode { char *s; unsigned int h; struct RtStrNode *next; } RtStrNode;
static RtStrNode *g_rt_intern[RT_INTERN_BUCKETS];
static long long g_rt_intern_n = 0;
static char *rt_intern(const char *s) {
    unsigned int h = 2166136261u;
    const char *p;
    RtStrNode *node;
    size_t n;
    if (!s) s = "";
    n = strlen(s);
    for (p = s; *p; p++) h = (h ^ (unsigned int)(unsigned char)*p) * 16777619u;
    if (n <= RT_INTERN_MAXLEN && g_rt_intern_n < RT_INTERN_MAX) {
        RtStrNode *b = g_rt_intern[h % RT_INTERN_BUCKETS];
        while (b) {
            if (b->h == h && strcmp(b->s, s) == 0) return b->s;
            b = b->next;
        }
        node = (RtStrNode *)malloc(sizeof(RtStrNode));
        if (node) {
            node->s = (char *)malloc(n + 1);
            if (node->s) {
                memcpy(node->s, s, n + 1);
                node->h = h;
                node->next = g_rt_intern[h % RT_INTERN_BUCKETS];
                g_rt_intern[h % RT_INTERN_BUCKETS] = node;
                g_rt_intern_n++;
                rt_note_alloc((long long)n + 1);
                return node->s;
            }
            free(node);
        }
    }
    {
        char *d = (char *)malloc(n + 1);
        if (!d) rt_abort("内存不足");
        memcpy(d, s, n + 1);
        rt_note_alloc((long long)n + 1);
        return d;
    }
}

/* ---- mem()/gc()：由 ctrans 内建映射调用（实现见 v_str 之后的函数区） ---- */


/* ---- 异常运行时（try / raise）：setjmp 跳转栈，由编译器生成的 try 语句使用 ---- */
static jmp_buf g_jmp_stack[64];
static int g_jmp_n = 0;
static char g_err_type[128] = "";
static char g_err_msg[512] = "";

/* ================= v4.2：统一错误码 + 调用栈 ================= */
static const char *g_trace[128];
static int g_trace_line[128];
static int g_trace_n = 0;

/* v4.3：独立 exe 也显示源码片段（g_src_lines 由代码生成器写入） */
extern const char *g_src_lines[];
extern int g_src_n;
static int g_cur_line = 0;
static void rt_set_line(int n) { g_cur_line = n; }
static void rt_print_snippet(void) {
    int ln = g_cur_line;
    printf("DBG ln=%d n=%d\n", ln, g_src_n);
    if (ln < 1 || ln > g_src_n) return;
    printf("第%d行 | %s\n", ln, g_src_lines[ln - 1]);
    printf("        ^\n");
}

static void rt_trace_push(const char *name, int line) {
    if (g_trace_n < 128) {
        g_trace[g_trace_n] = name ? name : "?";
        g_trace_line[g_trace_n] = line;
        g_trace_n++;
    }
}
static void rt_trace_pop(void) {
    if (g_trace_n > 0) g_trace_n--;
}
static void rt_trace_print(void) {
    int i;
    printf("调用栈（自顶向下）:\n");
    if (g_trace_n == 0) { printf("  <main>\n"); return; }
    for (i = g_trace_n - 1; i >= 0; i--) {
        if (g_trace_line[i] > 0)
            printf("%s %s（第%d行）\n", i == g_trace_n - 1 ? " ->" : "   ",
                   g_trace[i], g_trace_line[i]);
        else
            printf("%s %s\n", i == g_trace_n - 1 ? " ->" : "   ", g_trace[i]);
    }
}
static const char *rt_err_code(const char *t) {
    if (!t) return "E1099";
    if (strcmp(t, "ZeroDivisionError") == 0) return "E1001";
    if (strcmp(t, "TypeError") == 0) return "E1002";
    if (strcmp(t, "IndexError") == 0) return "E1003";
    if (strcmp(t, "KeyError") == 0) return "E1004";
    if (strcmp(t, "ValueError") == 0) return "E1005";
    if (strcmp(t, "AttributeError") == 0) return "E1006";
    if (strcmp(t, "AssertionError") == 0) return "E1007";
    if (strcmp(t, "NameError") == 0) return "E1008";
    if (strcmp(t, "ConstError") == 0) return "E1009";
    if (strcmp(t, "ImportError") == 0) return "E1010";
    if (strcmp(t, "RecursionError") == 0) return "E1011";
    if (strcmp(t, "OverflowError") == 0) return "E1012";
    if (strcmp(t, "MemoryError") == 0) return "E1013";
    return "E1099";
}
static void rt_report_error(void) {
    printf("%s: %s [%s]\n", g_err_type[0] ? g_err_type : "Error",
           g_err_msg, rt_err_code(g_err_type));
    rt_print_snippet();
    rt_trace_print();
}
static const char *rt_abort_type(const char *m) {
    if (!m) return "Error";
    if (strstr(m, "除数为零") || strstr(m, "除数为 0") || strstr(m, "除数为0") || strstr(m, "by zero"))
        return "ZeroDivisionError";
    if (strstr(m, "越界") || strstr(m, "out of range")) return "IndexError";
    if (strstr(m, "溢出")) return "OverflowError";
    if (strstr(m, "无属性") || strstr(m, "无此方法") || strstr(m, "空对象"))
        return "AttributeError";
    if (strstr(m, "TypeError") || strstr(m, "需要数组") || strstr(m, "需要字符串") ||
        strstr(m, "必须是整数数组") || strstr(m, "不能为负"))
        return "TypeError";
    if (strstr(m, "内存不足")) return "MemoryError";
    return "RuntimeError";
}
static void rt_report_abort(const char *msg) {
    const char *t = rt_abort_type(msg);
    printf("%s: %s [%s]\n", t, msg ? msg : "", rt_err_code(t));
    rt_trace_print();
}

static void rt_throw(const char *t, const char *m) {
    int i = 0;
    const char *tt = t ? t : "Error";
    const char *mm = m ? m : "";
    while (tt[i] && i < 127) { g_err_type[i] = tt[i]; i++; }
    g_err_type[i] = '\0';
    i = 0;
    while (mm[i] && i < 511) { g_err_msg[i] = mm[i]; i++; }
    g_err_msg[i] = '\0';
    if (g_jmp_n > 0)
        longjmp(g_jmp_stack[g_jmp_n - 1], 1);
    rt_report_error();
    exit(1);
}

static void rt_throw_again(void) {
    if (g_jmp_n > 0)
        longjmp(g_jmp_stack[g_jmp_n - 1], 1);
    rt_report_error();
    exit(1);
}

static Val v_null(void) { Val v; v.t = V_NULL; v.i = 0; v.f = 0; v.s = NULL; v.a = NULL; v.o = NULL; v.fn = NULL; return v; }
static Val v_int(long long x) { Val v; v.t = V_INT; v.i = x; v.f = 0; v.s = NULL; v.a = NULL; v.o = NULL; v.fn = NULL; return v; }
static Val v_float(double x) { Val v; v.t = V_FLOAT; v.i = 0; v.f = x; v.s = NULL; v.a = NULL; v.o = NULL; v.fn = NULL; return v; }
static Val v_bool(int b) { Val v; v.t = V_BOOL; v.i = b ? 1 : 0; v.f = 0; v.s = NULL; v.a = NULL; v.o = NULL; v.fn = NULL; return v; }
static Val v_str(const char *s) { Val v; v.t = V_STR; v.i = 0; v.f = 0; v.s = strdup(s ? s : ""); v.a = NULL; v.o = NULL; v.fn = NULL; return v; }
/* v3.2：字符串字面量走驻留池（相同常量复用同一块内存；该指针不会被 rt_val_free 释放） */
static Val v_str_lit(const char *s) { Val v; v.t = V_STR; v.i = 0; v.f = 0; v.s = rt_intern(s); v.a = NULL; v.o = NULL; v.fn = NULL; return v; }

/* v3.2：mem() / gc() —— C 侧内存统计与托管状态（ctrans 内建映射） */
static Val rt_mem(void) {
    char b[320];
    snprintf(b, sizeof(b),
             "C 运行时内存: 分配 %lld 次 / 累计 %lld 字节 / 峰值 %lld 字节 / 托管数组 %lld 个 / 驻留常量 %lld 条",
             g_rt_allocs, g_rt_bytes, g_rt_peak, g_rt_arr_n, g_rt_intern_n);
    printf("%s\n", b);
    return v_str(b);
}
static Val rt_gc(void) {
    printf("自动内存回收：C 后端由运行时托管（数组登记，退出统一释放；常量驻留），无需手动回收\n");
    return v_int(0);
}
static Val v_arr(void) { Val v; v.t = V_ARR; v.i = 0; v.f = 0; v.s = NULL; v.a = NULL; v.o = NULL; v.fn = NULL; return v; }
static Val v_obj(Obj *o) { Val v; v.t = V_OBJ; v.i = 0; v.f = 0; v.s = NULL; v.a = NULL; v.o = o; v.fn = NULL; return v; }
/* v4.2：字典复用 Arr 存储，items 按 [key0, val0, key1, val1, ...] 成对排列 */
static Val v_dict(void) { Val v; v.t = V_DICT; v.i = 0; v.f = 0; v.s = NULL; v.a = NULL; v.o = NULL; v.fn = NULL; return v; }
/* v4.2：一等函数值（闭包） */
static Val v_fn(Fn *f) { Val v; v.t = V_FUNC; v.i = 0; v.f = 0; v.s = NULL; v.a = NULL; v.o = NULL; v.fn = f; return v; }
static Val *rt_box_new(Val v) {
    Val *b = (Val *)malloc(sizeof(Val));
    if (!b) rt_abort("内存不足");
    *b = v;
    return b;
}
static Fn *rt_fn_alloc(FnCode code, void *env, int ncap, const char *name, int arity) {
    Fn *f = (Fn *)malloc(sizeof(Fn));
    if (!f) rt_abort("内存不足");
    f->code = code; f->env = env; f->ncap = ncap;
    f->name = name ? name : "<函数>"; f->arity = arity;
    return f;
}
static Val rt_fn_new(FnCode code, Val **caps, int ncap, const char *name, int arity) {
    Val **env = NULL;
    int i;
    if (ncap > 0) {
        env = (Val **)malloc(sizeof(Val *) * (size_t)ncap);
        if (!env) rt_abort("内存不足");
        for (i = 0; i < ncap; i++) env[i] = caps[i];
    }
    return v_fn(rt_fn_alloc(code, env, ncap, name, arity));
}
static Val rt_call(Val fv, Val *args, int n) {
    if (fv.t != V_FUNC || !fv.fn)
        rt_throw("TypeError", "这个值不是函数，不能调用");
    if (n != fv.fn->arity)
        rt_throw("TypeError", "函数参数个数不匹配");
    return fv.fn->code(fv.fn->env, args);
}

static int rt_truth(Val v) {
    switch (v.t) {
        case V_NULL: return 0;
        case V_INT: return v.i != 0;
        case V_FLOAT: return v.f != 0.0;
        case V_STR: return v.s != NULL && v.s[0] != '\0';
        case V_BOOL: return v.i != 0;
        case V_ARR: return v.a != NULL && v.a->n > 0;
        case V_OBJ: return v.o != NULL;
        case V_DICT: return v.a != NULL && v.a->n > 0;
        case V_FUNC: return v.fn != NULL;
        case V_MODULE: return v.o != NULL;
    }
    return 0;
}

static int rt_isnum(Val v) { return v.t == V_INT || v.t == V_FLOAT || v.t == V_BOOL; }
static double rt_num(Val v) {
    if (v.t == V_INT) return (double)v.i;
    if (v.t == V_FLOAT) return v.f;
    if (v.t == V_BOOL) return (double)v.i;
    if (v.t == V_STR && v.s) return atof(v.s);
    return 0;
}
static long long rt_int(Val v) {
    if (v.t == V_INT) return v.i;
    if (v.t == V_FLOAT) return (long long)v.f;
    if (v.t == V_BOOL) return v.i;
    if (v.t == V_STR && v.s) return atoll(v.s);
    return 0;
}
static const char *rt_str_of(Val v) { return v.t == V_STR && v.s ? v.s : ""; }

/* ================= v3.3：UTF-8 字符语义 + int64 溢出检测 ================ */
/* 字符数：按 UTF-8 编码单元计数，与解释器 len("你好")==2 对齐 */
static long long rt_u8len(const char *s) {
    long long n = 0;
    const unsigned char *p;
    if (!s) return 0;
    for (p = (const unsigned char *)s; *p; p++) {
        if ((*p & 0xC0) != 0x80) n++;
    }
    return n;
}

/* 字符串前缀的字符数（find 返回字符下标而非字节偏移） */
static long long rt_u8len_prefix(const char *s, const char *end) {
    long long n = 0;
    const unsigned char *p;
    if (!s || !end || end < s) return 0;
    for (p = (const unsigned char *)s; p < (const unsigned char *)end; p++)
        if ((*p & 0xC0) != 0x80) n++;
    return n;
}

static const char *rt_u8_at(const char *s, long long idx, size_t *clen) {
    long long n = 0;
    const unsigned char *p;
    *clen = 0;
    if (!s || idx < 0) return NULL;
    p = (const unsigned char *)s;
    while (*p && n < idx) {
        /* 跳过本字符所有续字节 */
        unsigned char c = *p;
        long long w = 1;
        if (c >= 0xF0) w = 4; else if (c >= 0xE0) w = 3; else if (c >= 0xC0) w = 2;
        p += w;
        n++;
    }
    if (!*p) return NULL;
    {
        unsigned char c = *p;
        size_t w = 1;
        if (c >= 0xF0) w = 4; else if (c >= 0xE0) w = 3; else if (c >= 0xC0) w = 2;
        while (w > 1 && (p[w - 1] & 0xC0) != 0x80) w--;
        *clen = w;
    }
    return (const char *)p;
}

/* 64 位有符号整数溢出检测（与解释器 Python 任意整数不同：C 侧溢出时报错） */
static long long rt_ck_add(long long a, long long b) {
    if (b > 0 ? a > LLONG_MAX - b : a < LLONG_MIN - b)
        rt_abort("整数溢出：超出 64 位有符号整数范围");
    return a + b;
}
static long long rt_ck_sub(long long a, long long b) {
    if (b < 0 ? a > LLONG_MAX + b : a < LLONG_MIN + b)
        rt_abort("整数溢出：超出 64 位有符号整数范围");
    return a - b;
}
static long long rt_ck_mul(long long a, long long b) {
    if (a == 0 || b == 0) return 0;
    if (a == -1 && b == LLONG_MIN) rt_abort("整数溢出：超出 64 位有符号整数范围");
    if (b == -1 && a == LLONG_MIN) rt_abort("整数溢出：超出 64 位有符号整数范围");
    if (a > 0) {
        if (b > 0) { if (a > LLONG_MAX / b) rt_abort("整数溢出：超出 64 位有符号整数范围"); }
        else { if (b < LLONG_MIN / a) rt_abort("整数溢出：超出 64 位有符号整数范围"); }
    } else {
        if (b > 0) { if (a < LLONG_MIN / b) rt_abort("整数溢出：超出 64 位有符号整数范围"); }
        else { if (b < LLONG_MAX / a) rt_abort("整数溢出：超出 64 位有符号整数范围"); }
    }
    return a * b;
}

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

/* 嵌套容器里的字符串需要 repr（带引号），顶层 print 仍用 tostr */
static Val rt_repr(Val v);

static Val rt_tostr(Val v) {
    char buf[80];    switch (v.t) {
        case V_NULL: return v_str("None");
        case V_INT: snprintf(buf, sizeof(buf), "%lld", v.i); return v_str(buf);
        case V_FLOAT: rt_fmt_num(v, buf, sizeof(buf)); return v_str(buf);
        case V_STR: return v_str(v.s ? v.s : "");
        case V_BOOL: return v_str(v.i ? "True" : "False");
        case V_FUNC: return v_str(v.fn && v.fn->name ? v.fn->name : "<函数>");
        case V_MODULE: {
            Mod *m = (Mod *)v.o;
            char buf[160];
            snprintf(buf, sizeof(buf), "<Impor 模块 %s>", m && m->name ? m->name : "?");
            return v_str(buf);
        }
        case V_OBJ: {
            if (v.o && v.o->cls_id >= 0 && v.o->cls_id < g_cls_n && g_cls_tab[v.o->cls_id]) {
                char buf[160];
                snprintf(buf, sizeof(buf), "<%s 实例>", g_cls_tab[v.o->cls_id]->name);
                return v_str(buf);
            }
            return v_str("<对象>");
        }
        case V_ARR: {
            size_t cap = (v.a ? (size_t)v.a->n : 0) * 8 + 16, n = 0;
            char *b = (char *)malloc(cap + 1);
            if (!b) rt_abort("内存不足");
            b[n++] = '[';
            if (v.a) {
                for (long long k = 0; k < v.a->n; k++) {
                    if (k) { b[n++] = ','; b[n++] = ' '; }
                    Val e = rt_repr(v.a->items[k]);
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
        case V_DICT: {
            size_t cap = (v.a ? (size_t)(v.a->n + 1) / 2 : 0) * 12 + 16, n = 0;
            char *b = (char *)malloc(cap + 1);
            if (!b) rt_abort("内存不足");
            b[n++] = '{';
            if (v.a) {
                for (long long k = 0; k + 1 < v.a->n; k += 2) {
                    Val ks = rt_repr(v.a->items[k]);
                    Val vs = rt_repr(v.a->items[k + 1]);
                    size_t kl = strlen(ks.s ? ks.s : ""), vl = strlen(vs.s ? vs.s : "");
                    if (n + kl + vl + 8 > cap) {
                        cap = (cap + kl + vl + 16) * 2;
                        b = (char *)realloc(b, cap + 1);
                        if (!b) rt_abort("内存不足");
                    }
                    if (k) { b[n++] = ','; b[n++] = ' '; }
                    memcpy(b + n, ks.s ? ks.s : "", kl); n += kl;
                    b[n++] = ':'; b[n++] = ' ';
                    memcpy(b + n, vs.s ? vs.s : "", vl); n += vl;
                    free(ks.s); free(vs.s);
                }
            }
            b[n++] = '}';
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
        Val sa, sb;
        size_t na, nb, n;
        char *buf;
        Val r;
        /* 对齐解释器：只有 字符串 + 字符串 合法；字符串与数字相加报 TypeError */
        if (a.t != V_STR || b.t != V_STR)
            rt_abort("TypeError: 不能将字符串与数字相加（请用 to_str() 显式转换）");
        sa = rt_tostr(a); sb = rt_tostr(b);
        na = strlen(sa.s ? sa.s : ""); nb = strlen(sb.s ? sb.s : "");
        n = na + nb + 1;
        buf = (char *)malloc(n);
        if (!buf) rt_abort("内存不足");
        memcpy(buf, sa.s ? sa.s : "", na);
        memcpy(buf + na, sb.s ? sb.s : "", nb + 1);
        free(sa.s); free(sb.s);
        r.t = V_STR; r.i = 0; r.f = 0; r.a = NULL; r.s = buf; r.o = NULL; r.fn = NULL;
        return r;
    }
    if (a.t == V_FLOAT || b.t == V_FLOAT) return v_float(rt_num(a) + rt_num(b));
    return v_int(rt_ck_add(rt_int(a), rt_int(b)));
}

/* ================= 数组（v2.17） ================= */
static Val rt_dict_set(Val d, Val k, Val v);
static Arr *arr_new(long long cap) {
    Arr *a = (Arr *)malloc(sizeof(Arr));
    if (!a) rt_abort("内存不足");
    a->n = 0;
    a->cap = cap > 0 ? cap : 8;
    a->items = (Val *)malloc(sizeof(Val) * (size_t)a->cap);
    if (!a->items) rt_abort("内存不足");
    rt_arr_reg(a);
    rt_note_alloc((long long)(sizeof(Arr) + sizeof(Val) * (size_t)a->cap));
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
    else if ((v.t == V_ARR || v.t == V_DICT) && v.a) {
        for (long long k = 0; k < v.a->n; k++) rt_val_free(v.a->items[k]);
        rt_arr_unreg(v.a);
        rt_note_free((long long)(sizeof(Arr) + sizeof(Val) * (size_t)v.a->cap));
        free(v.a->items); free(v.a); v.a = NULL;
    }
    /* V_OBJ：对象生命周期由运行时持有，不在此释放（本轮无 GC，进程退出回收） */
}
static Val rt_val_dup(Val v) {
    if (v.t == V_STR && v.s) return v_str(v.s);
    if ((v.t == V_ARR || v.t == V_DICT) && v.a) {
        Val r = v.t == V_DICT ? v_dict() : v_arr();
        r.a = arr_new(v.a->n);
        for (long long k = 0; k < v.a->n; k++)
            arr_push(r.a, rt_val_dup(v.a->items[k]));
        return r;
    }
    if (v.t == V_OBJ) return v;  /* 对象引用语义 */
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
static Val rt_range(Val n) {
    long long cnt = rt_int(n);
    Val r = v_arr();
    r.a = arr_new(cnt);
    for (long long k = 0; k < cnt; k++) arr_push(r.a, v_int(k));
    return r;
}
static Val rt_arr_get(Val a, Val b) {
    if (a.t != V_ARR || !a.a) rt_abort("下标访问需要数组");
    long long i = rt_int(b);
    if (i < 0 || i >= a.a->n) rt_abort("list index out of range");
    return a.a->items[i];
}
static Val rt_arr_set(Val a, Val b, Val c) {
    if (a.t == V_DICT) return rt_dict_set(a, b, c);
    if (a.t != V_ARR || !a.a) rt_abort("下标赋值需要数组");
    long long i = rt_int(b);
    if (i < 0 || i >= a.a->n) rt_abort("list index out of range");
    rt_val_free(a.a->items[i]);
    a.a->items[i] = rt_val_dup(c);
    return c;
}

/* ================= v4.2 字典 =================
   存储：Arr->items 成对排列，n 为元素个数（2 倍键值对数）。
   键比较：数字（含 bool）按数值，字符串按内容，其余按类型 + 指针。 */
static int rt_key_eq(Val a, Val b) {
    if (rt_isnum(a) && rt_isnum(b)) return rt_num(a) == rt_num(b);
    if (a.t == V_STR && b.t == V_STR)
        return strcmp(a.s ? a.s : "", b.s ? b.s : "") == 0;
    if (a.t == V_NULL && b.t == V_NULL) return 1;
    if (a.t == V_OBJ && b.t == V_OBJ) return a.o == b.o;
    if (a.t == V_ARR && b.t == V_ARR) return a.a == b.a;
    if (a.t == V_DICT && b.t == V_DICT) return a.a == b.a;
    if (a.t == V_FUNC && b.t == V_FUNC) return a.fn == b.fn;
    return 0;
}
static Val rt_dict_new(void) {
    Val r = v_dict();
    r.a = arr_new(8);
    return r;
}
static Val rt_dict_set(Val d, Val k, Val v) {
    if (d.t != V_DICT || !d.a) rt_throw("TypeError", "字典赋值需要字典对象");
    for (long long i = 0; i + 1 < d.a->n; i += 2) {
        if (rt_key_eq(d.a->items[i], k)) {
            rt_val_free(d.a->items[i + 1]);
            d.a->items[i + 1] = rt_val_dup(v);
            return v;
        }
    }
    arr_push(d.a, rt_val_dup(k));
    arr_push(d.a, rt_val_dup(v));
    return v;
}
static Val rt_dict_get(Val d, Val k) {
    if (d.t != V_DICT || !d.a) rt_throw("TypeError", "下标访问需要字典");
    for (long long i = 0; i + 1 < d.a->n; i += 2)
        if (rt_key_eq(d.a->items[i], k)) return d.a->items[i + 1];
    rt_throw("KeyError", "字典键不存在");
    return v_null();
}
static Val rt_dict_of(long long n, ...) {
    va_list ap;
    Val r = rt_dict_new();
    va_start(ap, n);
    for (long long k = 0; k < n; k++) {
        Val key = va_arg(ap, Val);
        Val val = va_arg(ap, Val);
        rt_dict_set(r, key, val);
    }
    va_end(ap);
    return r;
}
static Val rt_dict_len(Val d) { return v_int(d.t == V_DICT && d.a ? d.a->n / 2 : 0); }
static Val rt_dict_keys(Val d) {
    Val r = v_arr();
    r.a = arr_new(d.t == V_DICT && d.a ? d.a->n / 2 : 0);
    if (d.t == V_DICT && d.a)
        for (long long i = 0; i + 1 < d.a->n; i += 2) arr_push(r.a, rt_val_dup(d.a->items[i]));
    return r;
}
static Val rt_dict_values(Val d) {
    Val r = v_arr();
    r.a = arr_new(d.t == V_DICT && d.a ? d.a->n / 2 : 0);
    if (d.t == V_DICT && d.a)
        for (long long i = 1; i < d.a->n; i += 2) arr_push(r.a, rt_val_dup(d.a->items[i]));
    return r;
}
static Val rt_dict_get_or(Val d, Val k, Val fallback) {
    if (d.t != V_DICT || !d.a) rt_throw("TypeError", "get 需要字典");
    for (long long i = 0; i + 1 < d.a->n; i += 2)
        if (rt_key_eq(d.a->items[i], k)) return d.a->items[i + 1];
    return fallback;
}
static Val rt_len(Val v);
static Val rt_repr(Val v) {
    if (v.t == V_STR && v.s) {
        size_t cap = strlen(v.s) * 2 + 4, used = 0;
        char *b = (char *)malloc(cap);
        const char *p;
        if (!b) rt_abort("内存不足");
        b[used++] = '\'';
        for (p = v.s; *p; p++) {
            if (used + 4 > cap) { cap = (cap + 8) * 2; b = (char *)realloc(b, cap); if (!b) rt_abort("内存不足"); }
            if (*p == '\\' || *p == '\'') { b[used++] = '\\'; b[used++] = *p; }
            else if (*p == '\n') { b[used++] = '\\'; b[used++] = 'n'; }
            else if (*p == '\r') { b[used++] = '\\'; b[used++] = 'r'; }
            else if (*p == '\t') { b[used++] = '\\'; b[used++] = 't'; }
            else b[used++] = *p;
        }
        if (used + 2 > cap) { cap = used + 4; b = (char *)realloc(b, cap); if (!b) rt_abort("内存不足"); }
        b[used++] = '\''; b[used] = '\0';
        {
            Val r = v_str(b);
            free(b);
            return r;
        }
    }
    return rt_tostr(v);
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

/* ================= 对象系统（v2.19：class / new / super / this） ================= */
static int cls_id_of(const char *name) {
    if (!name) return -1;
    for (int k = 0; k < g_cls_n; k++)
        if (g_cls_tab[k] && strcmp(g_cls_tab[k]->name, name) == 0) return k;
    return -1;
}
static int reg_class(ClassInfo *ci) {
    if (g_cls_n >= 64) rt_abort("类数量超出上限");
    g_cls_tab[g_cls_n] = ci;
    return g_cls_n++;
}
static Obj *obj_new(int cls_id) {
    Obj *o = (Obj *)malloc(sizeof(Obj));
    if (!o) rt_abort("内存不足");
    o->cls_id = cls_id;
    o->n = 0; o->cap = 4;
    o->fs = (Field *)malloc(sizeof(Field) * (size_t)o->cap);
    if (!o->fs) rt_abort("内存不足");
    return o;
}
static int obj_find(Obj *o, const char *name) {
    for (int k = 0; k < o->n; k++)
        if (o->fs[k].name && strcmp(o->fs[k].name, name) == 0) return k;
    return -1;
}
static Val rt_objget(Obj *o, const char *name) {
    if (!o) rt_abort("空对象访问属性");
    int k = obj_find(o, name);
    if (k >= 0) return o->fs[k].v;
    rt_abort("对象无属性");
    return v_null();
}
static Val rt_objset(Obj *o, const char *name, Val v) {
    if (!o) rt_abort("空对象写属性");
    int k = obj_find(o, name);
    if (k >= 0) {
        rt_val_free(o->fs[k].v);
        o->fs[k].v = rt_val_dup(v);
        return v;
    }
    if (o->n >= o->cap) {
        o->cap *= 2;
        o->fs = (Field *)realloc(o->fs, sizeof(Field) * (size_t)o->cap);
        if (!o->fs) rt_abort("内存不足");
    }
    o->fs[o->n].name = strdup(name);
    o->fs[o->n].v = rt_val_dup(v);
    o->n++;
    return v;
}

/* v4.2：模块对象。属性表是一个普通字典（导出名 -> 值） */
static Val v_mod(Mod *m) {
    Val v; v.t = V_MODULE; v.i = 0; v.f = 0; v.s = NULL; v.a = NULL;
    v.o = (Obj *)m; v.fn = NULL;
    return v;
}
static Val rt_module_new(Val namev, Val attrs) {
    Mod *m = (Mod *)malloc(sizeof(Mod));
    if (!m) rt_abort("内存不足");
    m->name = (namev.t == V_STR && namev.s) ? namev.s : "?";
    m->attrs = attrs;
    return v_mod(m);
}
static int mod_find(Mod *m, const char *name, Val *out) {
    if (!m || m->attrs.t != V_DICT || !m->attrs.a) return 0;
    for (long long i = 0; i + 1 < m->attrs.a->n; i += 2) {
        if (m->attrs.a->items[i].t == V_STR &&
            strcmp(m->attrs.a->items[i].s ? m->attrs.a->items[i].s : "", name ? name : "") == 0) {
            if (out) *out = m->attrs.a->items[i + 1];
            return 1;
        }
    }
    return 0;
}
static Val rt_getattr(Val self, const char *name) {
    if (self.t == V_OBJ) return rt_objget(self.o, name);
    if (self.t == V_DICT) return rt_dict_get(self, v_str(name));
    if (self.t == V_MODULE) {
        Val out;
        if (mod_find((Mod *)self.o, name, &out)) return out;
        char buf[256];
        snprintf(buf, sizeof(buf), "模块没有属性 %s", name ? name : "");
        rt_abort(buf);
        return v_null();
    }
    rt_abort("这个值没有属性");
    return v_null();
}
static const Method *cls_methods(int cls_id) {
    if (cls_id >= 0 && cls_id < g_cls_n && g_cls_tab[cls_id])
        return g_cls_tab[cls_id]->methods;
    return NULL;
}
/* 构造当前捕获的异常对象（含 name / msg 字段，供 catch 变量使用） */
static Val rt_err_obj(void) {
    Obj *o = obj_new(0);
    rt_objset(o, "name", v_str(g_err_type));
    rt_objset(o, "msg", v_str(g_err_msg));
    return v_obj(o);
}
static Val obj_call(Obj *o, const char *name, Val *args, int n) {
    (void)n;
    if (!o) rt_abort("空对象调用方法");
    int cid = o->cls_id;
    while (cid >= 0) {
        const Method *ms = cls_methods(cid);
        for (const Method *m = ms; m && m->name; m++)
            if (strcmp(m->name, name) == 0) {
                Val r;
                rt_trace_push(m->name, 0);
                r = m->fn(o, args);
                rt_trace_pop();
                return r;
            }
        cid = g_cls_tab[cid]->base ? cls_id_of(g_cls_tab[cid]->base) : -1;
    }
    rt_abort("对象无此方法");
    return v_null();
}

/* v4.2：统一方法分派入口（类实例 / 字典 / 字符串 / 数组） */
static Val rt_method(Val self, const char *name, Val *args, int n) {
    const char *m = name ? name : "";
    if (self.t == V_OBJ) return obj_call(self.o, m, args, n);
    if (self.t == V_MODULE) {
        Val fn;
        if (mod_find((Mod *)self.o, m, &fn)) {
            if (fn.t == V_FUNC) return rt_call(fn, args, n);
            rt_throw("TypeError", "模块属性不是函数，不能调用");
            return v_null();
        }
        char buf[256];
        snprintf(buf, sizeof(buf), "模块没有属性 %s", m);
        rt_abort(buf);
        return v_null();
    }
    if (self.t == V_DICT) {
        if (strcmp(m, "keys") == 0) return rt_dict_keys(self);
        if (strcmp(m, "values") == 0) return rt_dict_values(self);
        if (strcmp(m, "items") == 0) {
            Val r = v_arr();
            r.a = arr_new(self.a ? self.a->n / 2 : 0);
            if (self.a)
                for (long long i = 0; i + 1 < self.a->n; i += 2)
                    arr_push(r.a, rt_arr_of(2, self.a->items[i], self.a->items[i + 1]));
            return r;
        }
        if (strcmp(m, "get") == 0) {
            if (n < 1) rt_throw("TypeError", "dict.get 至少需要 1 个参数");
            return rt_dict_get_or(self, args[0], n >= 2 ? args[1] : v_null());
        }
        if (strcmp(m, "has") == 0 || strcmp(m, "contains") == 0) {
            if (n < 1) rt_throw("TypeError", "dict.has 需要 1 个参数");
            for (long long i = 0; self.a && i + 1 < self.a->n; i += 2)
                if (rt_key_eq(self.a->items[i], args[0])) return v_bool(1);
            return v_bool(0);
        }
        rt_throw("AttributeError", "字典无此方法");
        return v_null();
    }
    if (self.t == V_STR) {
        const char *s = self.s ? self.s : "";
        if (strcmp(m, "upper") == 0) {
            char *b = strdup(s);
            for (char *p = b; *p; p++) if (*p >= 'a' && *p <= 'z') *p = (char)(*p - 32);
            Val r = v_str(b); free(b); return r;
        }
        if (strcmp(m, "lower") == 0) {
            char *b = strdup(s);
            for (char *p = b; *p; p++) if (*p >= 'A' && *p <= 'Z') *p = (char)(*p + 32);
            Val r = v_str(b); free(b); return r;
        }
        if (strcmp(m, "trim") == 0 || strcmp(m, "strip") == 0) {
            const char *a = s; const char *e = s + strlen(s);
            while (*a == ' ' || *a == '\t' || *a == '\r' || *a == '\n') a++;
            while (e > a && (e[-1] == ' ' || e[-1] == '\t' || e[-1] == '\r' || e[-1] == '\n')) e--;
            {
                size_t len = (size_t)(e - a);
                char *b = (char *)malloc(len + 1);
                if (!b) rt_abort("内存不足");
                memcpy(b, a, len); b[len] = '\0';
                Val r = v_str(b); free(b); return r;
            }
        }
        if (strcmp(m, "replace") == 0) {
            if (n < 2) rt_throw("TypeError", "replace 需要 2 个参数");
            const char *from = rt_str_of(args[0]);
            const char *to = rt_str_of(args[1]);
            size_t fl = strlen(from), tl = strlen(to), cap = strlen(s) * 2 + 8, used = 0;
            char *b = (char *)malloc(cap);
            const char *p = s;
            if (!b) rt_abort("内存不足");
            while (*p) {
                const char *hit = fl ? strstr(p, from) : NULL;
                if (!hit || hit > p + strlen(p)) {
                    size_t rest = strlen(p);
                    if (used + rest + 1 > cap) { cap = (used + rest + 8) * 2; b = (char *)realloc(b, cap); if (!b) rt_abort("内存不足"); }
                    memcpy(b + used, p, rest); used += rest; b[used] = '\0'; break;
                }
                size_t seg = (size_t)(hit - p);
                if (used + seg + tl + 1 > cap) { cap = (used + seg + tl + 8) * 2; b = (char *)realloc(b, cap); if (!b) rt_abort("内存不足"); }
                memcpy(b + used, p, seg); used += seg;
                memcpy(b + used, to, tl); used += tl; b[used] = '\0';
                p = hit + fl;
            }
            if (!*s) { b[0] = '\0'; }
            {
                Val r = v_str(b); free(b); return r;
            }
        }
        if (strcmp(m, "starts_with") == 0 || strcmp(m, "startsWith") == 0) {
            if (n < 1) return v_bool(0);
            return v_bool(strncmp(s, rt_str_of(args[0]), strlen(rt_str_of(args[0]))) == 0);
        }
        if (strcmp(m, "ends_with") == 0 || strcmp(m, "endsWith") == 0) {
            size_t sl, al;
            if (n < 1) return v_bool(0);
            sl = strlen(s); al = strlen(rt_str_of(args[0]));
            return v_bool(sl >= al && strcmp(s + sl - al, rt_str_of(args[0])) == 0);
        }
        if (strcmp(m, "find") == 0) {
            const char *hit;
            if (n < 1) return v_int(-1);
            hit = strstr(s, rt_str_of(args[0]));
            return v_int(hit ? rt_u8len_prefix(s, hit) : -1);
        }
        rt_throw("AttributeError", "字符串无此方法");
        return v_null();
    }
    if (self.t == V_ARR) {
        if (strcmp(m, "append") == 0 || strcmp(m, "push") == 0) {
            if (n < 1) rt_throw("TypeError", "append 需要 1 个参数");
            return rt_arrappend(self, args[0]);
        }
        if (strcmp(m, "pop") == 0) return rt_arrpop(self);
        if (strcmp(m, "len") == 0 || strcmp(m, "size") == 0) return rt_len(self);
        rt_throw("AttributeError", "数组无此方法");
        return v_null();
    }
    rt_throw("AttributeError", "该类型不支持方法调用");
    return v_null();
}
static Val rt_supercall(Obj *o, const char *from_cls, const char *name, Val *args, int n) {
    (void)n;
    if (!o) rt_abort("空对象 super");
    int cid = from_cls ? cls_id_of(from_cls) : -1;
    if (cid >= 0 && g_cls_tab[cid]->base)
        cid = cls_id_of(g_cls_tab[cid]->base);
    else
        cid = -1;
    while (cid >= 0) {
        const Method *ms = cls_methods(cid);
        for (const Method *m = ms; m && m->name; m++)
            if (strcmp(m->name, name) == 0) return m->fn(o, args);
        cid = g_cls_tab[cid]->base ? cls_id_of(g_cls_tab[cid]->base) : -1;
    }
    rt_abort("super 未找到方法");
    return v_null();
}
static Val rt_new(const char *clsname, Val *args, int n) {
    int cid = cls_id_of(clsname);
    if (cid < 0) rt_abort("未定义的类");
    Obj *o = obj_new(cid);
    if (g_cls_tab[cid]->init_fields) g_cls_tab[cid]->init_fields(o);
    const Method *found = NULL;
    int ic = cid;
    while (ic >= 0 && !found) {
        const Method *ms = cls_methods(ic);
        for (const Method *m = ms; m && m->name; m++)
            if (strcmp(m->name, "__init__") == 0) { found = m; break; }
        ic = g_cls_tab[ic]->base ? cls_id_of(g_cls_tab[ic]->base) : -1;
    }
    if (found) found->fn(o, args);
    return v_obj(o);
}

/* ---------- 命令行参数（imporc 生成的 main 传入） ---------- */
static int g_argc = 0;
static char **g_argv = NULL;
static Val rt_args(void) {
    Val r = v_arr();
    r.a = arr_new(g_argc > 1 ? g_argc - 1 : 0);
    for (int k = 1; k < g_argc; k++) arr_push(r.a, v_str(g_argv[k]));
    return r;
}

static Val rt_len(Val v) {
    if (v.t == V_STR) return v_int(rt_u8len(v.s));   /* v3.3：按字符计数 */
    if (v.t == V_ARR) return v_int(v.a ? v.a->n : 0);
    if (v.t == V_DICT) return v_int(v.a ? v.a->n / 2 : 0);
    rt_abort("len 需要字符串、数组或字典");
    return v_int(0);
}

/* v3.3：统一下标访问——字符串取字符（UTF-8），数组取元素 */
static Val rt_index(Val c, Val i) {
    if (c.t == V_DICT) return rt_dict_get(c, i);
    if (c.t == V_STR) {
        const char *s = c.s ? c.s : "";
        long long k = rt_int(i);
        long long n = rt_u8len(s);
        const char *p;
        size_t w;
        char *buf;
        Val r;
        if (k < 0) k += n;
        if (k < 0 || k >= n) rt_abort("string index out of range");
        p = rt_u8_at(s, k, &w);
        if (!p) rt_abort("string index out of range");
        buf = (char *)malloc(w + 1);
        if (!buf) rt_abort("内存不足");
        memcpy(buf, p, w);
        buf[w] = '\0';
        r.t = V_STR; r.i = 0; r.f = 0; r.a = NULL; r.s = buf; r.o = NULL; r.fn = NULL; r.fn = NULL; r.o = NULL;
        return r;
    }
    return rt_arr_get(c, i);
}

/* ================= v4.2 切片 =================
   Python 语义：start/end 省略用 v_null() 表示，step 省略等价 1。
   负索引按“从末尾数”解释，越界自动裁剪。 */
static long long rt_slice_start(long long n, Val v, long long step) {
    long long x;
    if (v.t == V_NULL) return step > 0 ? 0 : n - 1;
    x = rt_int(v);
    if (x < 0) x += n;
    if (step > 0) { if (x < 0) x = 0; if (x > n) x = n; }
    else { if (x < -1) x = -1; if (x > n - 1) x = n - 1; }
    return x;
}
static long long rt_slice_end(long long n, Val v, long long step) {
    long long x;
    if (v.t == V_NULL) return step > 0 ? n : -1;
    x = rt_int(v);
    if (x < 0) x += n;
    if (step > 0) { if (x < 0) x = 0; if (x > n) x = n; }
    else { if (x < -1) x = -1; if (x > n - 1) x = n - 1; }
    return x;
}
static Val rt_slice(Val c, Val start, Val end, Val stepv) {
    long long n, step, from, to, i;
    if (c.t == V_DICT) rt_throw("TypeError", "字典不支持切片");
    if (c.t != V_STR && (c.t != V_ARR || !c.a)) rt_throw("TypeError", "切片需要字符串或数组");
    step = stepv.t == V_NULL ? 1 : rt_int(stepv);
    if (step == 0) rt_throw("ValueError", "切片 step 不能为 0");
    n = c.t == V_STR ? rt_u8len(c.s) : c.a->n;
    from = rt_slice_start(n, start, step);
    to = rt_slice_end(n, end, step);
    if (c.t == V_ARR) {
        Val r = v_arr();
        long long cnt = 0;
        if (step > 0) for (i = from; i < to; i += step) cnt++;
        else for (i = from; i > to; i += step) cnt++;
        r.a = arr_new(cnt);
        if (step > 0) for (i = from; i < to; i += step) arr_push(r.a, rt_val_dup(c.a->items[i]));
        else for (i = from; i > to; i += step) arr_push(r.a, rt_val_dup(c.a->items[i]));
        return r;
    } else {
        const char *s = c.s ? c.s : "";
        size_t cap = 8, used = 0;
        char *buf = (char *)malloc(cap);
        if (!buf) rt_abort("内存不足");
        buf[0] = '\0';
        if (step > 0) {
            for (i = from; i < to; i += step) {
                size_t w; const char *p = rt_u8_at(s, i, &w);
                if (!p) break;
                if (used + w + 1 > cap) { while (used + w + 1 > cap) cap *= 2; buf = (char *)realloc(buf, cap); if (!buf) rt_abort("内存不足"); }
                memcpy(buf + used, p, w); used += w; buf[used] = '\0';
            }
        } else {
            for (i = from; i > to; i += step) {
                size_t w; const char *p = rt_u8_at(s, i, &w);
                if (!p) break;
                if (used + w + 1 > cap) { while (used + w + 1 > cap) cap *= 2; buf = (char *)realloc(buf, cap); if (!buf) rt_abort("内存不足"); }
                memcpy(buf + used, p, w); used += w; buf[used] = '\0';
            }
        }
        {
            Val r = v_str(buf);
            free(buf);
            return r;
        }
    }
}

static Val rt_str_repeat(const char *s, long long n) {
    size_t len = s ? strlen(s) : 0;
    char *buf, *p;
    long long k;
    Val r;
    if (n <= 0 || len == 0) return v_str("");
    if ((double)len * (double)n > 200000000.0) rt_abort("内存不足：字符串重复结果过大");
    buf = (char *)malloc(len * (size_t)n + 1);
    if (!buf) rt_abort("内存不足");
    p = buf;
    for (k = 0; k < n; k++) { memcpy(p, s, len); p += len; }
    *p = '\0';
    r.t = V_STR; r.i = 0; r.f = 0; r.a = NULL; r.s = buf;
    return r;
}
static Val rt_sub(Val a, Val b) {
    if (a.t == V_FLOAT || b.t == V_FLOAT) return v_float(rt_num(a) - rt_num(b));
    return v_int(rt_ck_sub(rt_int(a), rt_int(b)));
}
static Val rt_mul(Val a, Val b) {
    /* 对齐解释器：字符串 * 整数 = 重复；字符串 * 浮点报 TypeError */
    if (a.t == V_STR) {
        if (b.t == V_FLOAT) rt_abort("TypeError: 字符串只能与整数相乘");
        return rt_str_repeat(a.s, rt_int(b));
    }
    if (b.t == V_STR) {
        if (a.t == V_FLOAT) rt_abort("TypeError: 字符串只能与整数相乘");
        return rt_str_repeat(b.s, rt_int(a));
    }
    if (a.t == V_FLOAT || b.t == V_FLOAT) return v_float(rt_num(a) * rt_num(b));
    return v_int(rt_ck_mul(rt_int(a), rt_int(b)));
}
static Val rt_div(Val a, Val b) {
    double d = rt_num(b);
    if (d == 0) rt_abort("division by zero");
    return v_float(rt_num(a) / d);
}
static Val rt_mod(Val a, Val b) {
    if (a.t == V_FLOAT || b.t == V_FLOAT) {
        double d = rt_num(b);
        if (d == 0) rt_abort("integer division or modulo by zero");
        return v_float(fmod(rt_num(a), d));
    }
    long long d = rt_int(b);
    if (d == 0) rt_abort("integer modulo by zero");
    long long r = rt_int(a) % d;
    if (r != 0 && ((r < 0) != (d < 0))) r += d;  /* Python 取正模语义 */
    return v_int(r);
}
static Val rt_neg(Val a) {
    if (a.t == V_FLOAT) return v_float(-a.f);
    if (rt_int(a) == LLONG_MIN) rt_abort("整数溢出：超出 64 位有符号整数范围");
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
static int rt_val_eq(Val a, Val b) {
    if (rt_isnum(a) && rt_isnum(b)) return rt_num(a) == rt_num(b);
    if (a.t != b.t) return 0;
    if (a.t == V_NULL) return 1;
    if (a.t == V_STR) return strcmp(a.s ? a.s : "", b.s ? b.s : "") == 0;
    if (a.t == V_BOOL) return a.i == b.i;
    if (a.t == V_OBJ) return a.o == b.o;
    if (a.t == V_FUNC) return a.fn == b.fn;
    if (a.t == V_MODULE) {
        Mod *ma = (Mod *)a.o, *mb = (Mod *)b.o;
        if (!ma || !mb) return ma == mb;
        return strcmp(ma->name ? ma->name : "", mb->name ? mb->name : "") == 0;
    }
    if (a.t == V_ARR) {
        if (!a.a || !b.a) return a.a == b.a;
        if (a.a->n != b.a->n) return 0;
        for (long long i = 0; i < a.a->n; i++)
            if (!rt_val_eq(a.a->items[i], b.a->items[i])) return 0;
        return 1;
    }
    if (a.t == V_DICT) {
        if (!a.a || !b.a) return a.a == b.a;
        if (a.a->n != b.a->n) return 0;
        for (long long i = 0; i + 1 < a.a->n; i += 2) {
            int found = 0;
            for (long long j = 0; j + 1 < b.a->n; j += 2) {
                if (rt_key_eq(a.a->items[i], b.a->items[j])) {
                    found = 1;
                    if (!rt_val_eq(a.a->items[i + 1], b.a->items[j + 1])) return 0;
                    break;
                }
            }
            if (!found) return 0;
        }
        return 1;
    }
    return a.i == b.i;
}
static Val rt_expect(Val actual, Val expected, Val label) {
    if (rt_val_eq(actual, expected)) return actual;
    {
        char msg[640];
        Val a = rt_repr(actual), e = rt_repr(expected);
        snprintf(msg, sizeof(msg), "%s：期望 %s，实际 %s",
                 label.s && *label.s ? label.s : "expect",
                 e.s ? e.s : "", a.s ? a.s : "");
        rt_throw("AssertionError", msg);
    }
    return v_null();
}
static Val rt_cmp(Val a, Val b, const char *op) {
    if (strcmp(op, "==") == 0) {
        if (a.t == V_NULL && b.t == V_NULL) return v_bool(1);
        if (a.t == V_STR && b.t == V_STR) return v_bool(strcmp(a.s, b.s) == 0);
        if (a.t == V_BOOL && b.t == V_BOOL) return v_bool(a.i == b.i);
        if (rt_isnum(a) && rt_isnum(b)) return v_bool(rt_num(a) == rt_num(b));
        return v_bool(rt_val_eq(a, b));
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
        case V_DICT: return v_str("dict");
        case V_FUNC: return v_str("function");
        case V_MODULE: return v_str("module");
        case V_OBJ:
            if (v.o && v.o->cls_id >= 0 && v.o->cls_id < g_cls_n && g_cls_tab[v.o->cls_id])
                return v_str(g_cls_tab[v.o->cls_id]->disp ? g_cls_tab[v.o->cls_id]->disp
                                                          : g_cls_tab[v.o->cls_id]->name);
            return v_str("obj");
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
    long long n = rt_u8len(s);          /* v3.3：按字符计 */
    long long a = rt_int(st), b = rt_int(ed);
    size_t boff, eoff, w;
    const char *p;
    char *buf;
    Val r;
    if (a < 0) a = 0;
    if (b > n) b = n;
    if (a >= b) return v_str("");
    p = rt_u8_at(s, a, &w);
    if (!p) return v_str("");
    boff = (size_t)(p - s);
    p = rt_u8_at(s, b, &w);
    eoff = p ? (size_t)(p - s) : strlen(s);
    buf = (char *)malloc(eoff - boff + 1);
    if (!buf) rt_abort("内存不足");
    memcpy(buf, s + boff, eoff - boff);
    buf[eoff - boff] = '\0';
    r.t = V_STR; r.i = 0; r.f = 0; r.a = NULL; r.s = buf;
    return r;
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
    if (y == 0) rt_abort("division by zero");
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
static Val rt_filewritebytes(Val path, Val data) {
    /* v3.8 原生工具链：把 0..255 的整数数组按字节写入文件 */
    if (data.t != V_ARR) rt_abort("filewritebytes: 第二个参数必须是整数数组");
    FILE *fp = fopen(rt_str_of(path), "wb");
    if (!fp) rt_abort("filewritebytes: 无法写入文件");
    long long n = data.a ? data.a->n : 0;
    for (long long i = 0; i < n; i++) {
        Val v = data.a->items[i];
        long long x = v.t == V_INT ? v.i : (long long)v.f;
        if (x < 0 || x > 255) { fclose(fp); rt_abort("filewritebytes: 字节值必须在 0..255"); }
        fputc((unsigned char)x, fp);
    }
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

/* 函数原型 */
static Val fn0(Val p0);

/* Impor 函数：f */
static Val fn0(Val p0) {
    Val l0 = p0;
    /*@L3*/ rt_set_line(3); return rt_div(v_int(10), l0);
    return v_null();
}

/* 函数值包装 */
static Val wrap_fn0(void *env, Val *args) { (void)env; (void)args; return fn0(args[0]); }
static Fn fob_fn0 = {wrap_fn0, NULL, 0, 1, "f"};

int main(int argc, char **argv) {
    (void)argc; (void)argv;
    /*@L4*/ rt_set_line(4); rt_print_val(({ rt_trace_push("f", 4); Val _tr = fn0(v_int(0)); rt_trace_pop(); _tr; }));
    rt_print_nl();
    return 0;
}
