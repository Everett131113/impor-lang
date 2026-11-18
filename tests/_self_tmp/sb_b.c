/* by imporc.impor (self-hosted) */

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
static Val c_strfind(Val, Val);
static Val c_is_digit(Val);
static Val c_is_alpha(Val);
static Val c_top_ind();
static Val c_pop_ind();
static Val c_push_ind(Val);
static Val c_top_sw();
static Val c_push_sw(Val);
static Val c_pop_sw();
static Val c_add_func(Val, Val);
static Val c_is_func(Val);
static Val c_param_idx(Val);
static Val c_out(Val);
static Val c_cap_index(Val);
static Val c_env_ptrs(Val);
static Val c_env_count(Val);
static Val c_call_expr(Val, Val, Val);
static Val c_var_ref(Val);
static Val c_list_at(Val, Val);
static Val c_scan();
static Val c_expect_op(Val);
static Val c_esc_str(Val);
static Val c_parse_primary();
static Val c_parse_unary();
static Val c_parse_mul();
static Val c_parse_add();
static Val c_parse_cmp();
static Val c_parse_and();
static Val c_parse_or();
static Val c_parse_expr();
static Val c_add_var(Val);
static Val c_gen_block();
static Val c_gen_block2();
static Val c_gen_stmt();
static Val c_next_arg();
static Val c_mi_is_word(Val);
static Val c_mi_replace_word(Val, Val, Val);
static Val c_mi_trim(Val);
static Val c_mi_dir_of(Val);
static Val c_mi_sanitize(Val);
static Val c_mi_replace_dots(Val);
static Val c_mi_strip_header(Val);
static Val c_mi_strip_export(Val);
static Val c_mi_export_symbols(Val);
static Val c_mi_decl_symbols(Val);
static Val c_mi_mangle_symbols(Val, Val, Val);
static Val c_mi_attrs_of(Val, Val);
static Val c_mi_split_import(Val);
static Val c_mi_expand_imports(Val, Val);
static Fn fob_c_strfind;
static Fn fob_c_is_digit;
static Fn fob_c_is_alpha;
static Fn fob_c_top_ind;
static Fn fob_c_pop_ind;
static Fn fob_c_push_ind;
static Fn fob_c_top_sw;
static Fn fob_c_push_sw;
static Fn fob_c_pop_sw;
static Fn fob_c_add_func;
static Fn fob_c_is_func;
static Fn fob_c_param_idx;
static Fn fob_c_out;
static Fn fob_c_cap_index;
static Fn fob_c_env_ptrs;
static Fn fob_c_env_count;
static Fn fob_c_call_expr;
static Fn fob_c_var_ref;
static Fn fob_c_list_at;
static Fn fob_c_scan;
static Fn fob_c_expect_op;
static Fn fob_c_esc_str;
static Fn fob_c_parse_primary;
static Fn fob_c_parse_unary;
static Fn fob_c_parse_mul;
static Fn fob_c_parse_add;
static Fn fob_c_parse_cmp;
static Fn fob_c_parse_and;
static Fn fob_c_parse_or;
static Fn fob_c_parse_expr;
static Fn fob_c_add_var;
static Fn fob_c_gen_block;
static Fn fob_c_gen_block2;
static Fn fob_c_gen_stmt;
static Fn fob_c_next_arg;
static Fn fob_c_mi_is_word;
static Fn fob_c_mi_replace_word;
static Fn fob_c_mi_trim;
static Fn fob_c_mi_dir_of;
static Fn fob_c_mi_sanitize;
static Fn fob_c_mi_replace_dots;
static Fn fob_c_mi_strip_header;
static Fn fob_c_mi_strip_export;
static Fn fob_c_mi_export_symbols;
static Fn fob_c_mi_decl_symbols;
static Fn fob_c_mi_mangle_symbols;
static Fn fob_c_mi_attrs_of;
static Fn fob_c_mi_split_import;
static Fn fob_c_mi_expand_imports;
Val g_gsrc;
Val g_gpos;
Val g_gtok;
Val g_gtype;
Val g_gvars;
Val g_gout;
Val g_gfuncs;
Val g_gfuncs2;
Val g_ginfunc;
Val g_glocals;
Val g_gindstack;
Val g_g_swinds;
Val g_gprev_nl;
Val g_gfail;
Val g_gded_pending;
Val g_gded_target;
Val g_gcur_cls;
Val g_gsuper_pending;
Val g_gassign_kind;
Val g_gassign_name;
Val g_gassign_base;
Val g_gargseq;
Val g_gassign_prop;
Val g_gassign_idx;
Val g_gparams;
Val g_gmethod;
Val g_gassign_raw;
Val g_genum_types;
Val g_gclosure;
Val g_gdecls;
Val g_gcaps;
Val g_gclos_seq;
Val g_gclosure_depth;
Val g_gargs;
Val g_gt2;
Val g_gp2;
Val g_gm;
Val g_gtmp;
Val g_gmode;
Val g_gscript;
Val g_goutname;
Val g_src;
Val g_nlpos;
Val g_cldepth;
Val g_fname;
Val g_np;
Val g_rt;
Val g_ccode;
Val g_proto;
Val g_i2;
Val g_f1;
Val g_fcnt;
Val g_curname;
Val g_curnp;
Val g_c2;
Val g_ps;
Val g_j2;
Val g_i;
Val g_vn;
Val g_c;
Val g_rc;
Val g_rc2;
static Val c_strfind(Val p0, Val p1) {
Val *l_hay = rt_box_new(p0);
Val *l_needle = rt_box_new(p1);
Val *l_hl = rt_box_new(rt_len((*l_hay)));
Val *l_nl = rt_box_new(rt_len((*l_needle)));
Val *l_i = rt_box_new(v_int(0));
while (rt_truth(rt_cmp((*l_i), (*l_hl), "<"))) {
if (rt_truth(rt_cmp(rt_strcut((*l_hay), (*l_i), rt_add((*l_i), (*l_nl))), (*l_needle), "=="))) {
return (*l_i);
}
(*l_i) = rt_add((*l_i), v_int(1));
}
return rt_neg(v_int(1));
return v_null();
}
static Val wrap_c_strfind(void *env, Val *args) { (void)env; (void)args; return c_strfind(args[0], args[1]); }
static Fn fob_c_strfind = {wrap_c_strfind, 0, 0, 2, "strfind"};
static Val c_is_digit(Val p0) {
Val *l_c = rt_box_new(p0);
if (rt_truth(rt_and(rt_cmp((*l_c), v_str_lit("0"), ">="), rt_cmp((*l_c), v_str_lit("9"), "<=")))) {
return v_bool(1);
}
return v_bool(0);
return v_null();
}
static Val wrap_c_is_digit(void *env, Val *args) { (void)env; (void)args; return c_is_digit(args[0]); }
static Fn fob_c_is_digit = {wrap_c_is_digit, 0, 0, 1, "is_digit"};
static Val c_is_alpha(Val p0) {
Val *l_c = rt_box_new(p0);
if (rt_truth(rt_and(rt_cmp((*l_c), v_str_lit("a"), ">="), rt_cmp((*l_c), v_str_lit("z"), "<=")))) {
return v_bool(1);
}
if (rt_truth(rt_and(rt_cmp((*l_c), v_str_lit("A"), ">="), rt_cmp((*l_c), v_str_lit("Z"), "<=")))) {
return v_bool(1);
}
if (rt_truth(rt_cmp((*l_c), v_str_lit("_"), "=="))) {
return v_bool(1);
}
return v_bool(0);
return v_null();
}
static Val wrap_c_is_alpha(void *env, Val *args) { (void)env; (void)args; return c_is_alpha(args[0]); }
static Fn fob_c_is_alpha = {wrap_c_is_alpha, 0, 0, 1, "is_alpha"};
static Val c_top_ind() {
Val *l_s = rt_box_new(g_gindstack);
Val *l_last = rt_box_new(rt_neg(v_int(1)));
Val *l_prev = rt_box_new(rt_neg(v_int(1)));
Val *l_i = rt_box_new(v_int(0));
while (rt_truth(rt_cmp((*l_i), rt_len((*l_s)), "<"))) {
if (rt_truth(rt_cmp(rt_strcut((*l_s), (*l_i), rt_add((*l_i), v_int(1))), v_str_lit("|"), "=="))) {
(*l_prev) = (*l_last);
(*l_last) = (*l_i);
}
(*l_i) = rt_add((*l_i), v_int(1));
}
if (rt_truth(rt_cmp((*l_prev), v_int(0), "<"))) {
return v_int(0);
}
return rt_to_num(rt_strcut((*l_s), rt_add((*l_prev), v_int(1)), (*l_last)));
return v_null();
}
static Val wrap_c_top_ind(void *env, Val *args) { (void)env; (void)args; return c_top_ind(); }
static Fn fob_c_top_ind = {wrap_c_top_ind, 0, 0, 0, "top_ind"};
static Val c_pop_ind() {
Val *l_s = rt_box_new(g_gindstack);
Val *l_last = rt_box_new(rt_neg(v_int(1)));
Val *l_prev = rt_box_new(rt_neg(v_int(1)));
Val *l_i = rt_box_new(v_int(0));
while (rt_truth(rt_cmp((*l_i), rt_len((*l_s)), "<"))) {
if (rt_truth(rt_cmp(rt_strcut((*l_s), (*l_i), rt_add((*l_i), v_int(1))), v_str_lit("|"), "=="))) {
(*l_prev) = (*l_last);
(*l_last) = (*l_i);
}
(*l_i) = rt_add((*l_i), v_int(1));
}
if (rt_truth(rt_cmp((*l_last), v_int(0), "<"))) {
g_gindstack = v_str_lit("");
}
else {
if (rt_truth(rt_cmp((*l_prev), v_int(0), "<"))) {
g_gindstack = v_str_lit("");
}
else {
g_gindstack = rt_strcut((*l_s), v_int(0), rt_add((*l_prev), v_int(1)));
}
}
return v_bool(1);
return v_null();
}
static Val wrap_c_pop_ind(void *env, Val *args) { (void)env; (void)args; return c_pop_ind(); }
static Fn fob_c_pop_ind = {wrap_c_pop_ind, 0, 0, 0, "pop_ind"};
static Val c_push_ind(Val p0) {
Val *l_ind = rt_box_new(p0);
g_gindstack = rt_add(rt_add(g_gindstack, rt_tostr((*l_ind))), v_str_lit("|"));
return v_bool(1);
return v_null();
}
static Val wrap_c_push_ind(void *env, Val *args) { (void)env; (void)args; return c_push_ind(args[0]); }
static Fn fob_c_push_ind = {wrap_c_push_ind, 0, 0, 1, "push_ind"};
static Val c_top_sw() {
Val *l_s = rt_box_new(g_g_swinds);
Val *l_last = rt_box_new(rt_neg(v_int(1)));
Val *l_prev = rt_box_new(rt_neg(v_int(1)));
Val *l_i = rt_box_new(v_int(0));
while (rt_truth(rt_cmp((*l_i), rt_len((*l_s)), "<"))) {
if (rt_truth(rt_cmp(rt_strcut((*l_s), (*l_i), rt_add((*l_i), v_int(1))), v_str_lit("|"), "=="))) {
(*l_prev) = (*l_last);
(*l_last) = (*l_i);
}
(*l_i) = rt_add((*l_i), v_int(1));
}
if (rt_truth(rt_cmp((*l_last), v_int(0), "<"))) {
return v_int(0);
}
if (rt_truth(rt_cmp((*l_prev), v_int(0), "<"))) {
return rt_to_num(rt_strcut((*l_s), v_int(0), (*l_last)));
}
return rt_to_num(rt_strcut((*l_s), rt_add((*l_prev), v_int(1)), (*l_last)));
return v_null();
}
static Val wrap_c_top_sw(void *env, Val *args) { (void)env; (void)args; return c_top_sw(); }
static Fn fob_c_top_sw = {wrap_c_top_sw, 0, 0, 0, "top_sw"};
static Val c_push_sw(Val p0) {
Val *l_ind = rt_box_new(p0);
g_g_swinds = rt_add(rt_add(g_g_swinds, rt_tostr((*l_ind))), v_str_lit("|"));
return v_bool(1);
return v_null();
}
static Val wrap_c_push_sw(void *env, Val *args) { (void)env; (void)args; return c_push_sw(args[0]); }
static Fn fob_c_push_sw = {wrap_c_push_sw, 0, 0, 1, "push_sw"};
static Val c_pop_sw() {
Val *l_v = rt_box_new(({ rt_trace_push("top_sw", 0); Val _tr = c_top_sw(); rt_trace_pop(); _tr; }));
Val *l_s = rt_box_new(g_g_swinds);
Val *l_last = rt_box_new(rt_neg(v_int(1)));
Val *l_prev = rt_box_new(rt_neg(v_int(1)));
Val *l_i = rt_box_new(v_int(0));
while (rt_truth(rt_cmp((*l_i), rt_len((*l_s)), "<"))) {
if (rt_truth(rt_cmp(rt_strcut((*l_s), (*l_i), rt_add((*l_i), v_int(1))), v_str_lit("|"), "=="))) {
(*l_prev) = (*l_last);
(*l_last) = (*l_i);
}
(*l_i) = rt_add((*l_i), v_int(1));
}
if (rt_truth(rt_cmp((*l_prev), v_int(0), "<"))) {
g_g_swinds = v_str_lit("");
}
else {
g_g_swinds = rt_strcut((*l_s), v_int(0), rt_add((*l_prev), v_int(1)));
}
return (*l_v);
return v_null();
}
static Val wrap_c_pop_sw(void *env, Val *args) { (void)env; (void)args; return c_pop_sw(); }
static Fn fob_c_pop_sw = {wrap_c_pop_sw, 0, 0, 0, "pop_sw"};
static Val c_add_func(Val p0, Val p1) {
Val *l_name = rt_box_new(p0);
Val *l_np = rt_box_new(p1);
if (rt_truth(rt_cmp(({ rt_trace_push("strfind", 0); Val _tr = c_strfind(rt_add(v_str_lit(";"), g_gfuncs2), rt_add(rt_add(v_str_lit(";"), (*l_name)), v_str_lit(";"))); rt_trace_pop(); _tr; }), v_int(0), "<"))) {
g_gfuncs2 = rt_add(rt_add(rt_add(rt_add(g_gfuncs2, (*l_name)), v_str_lit(";")), rt_tostr((*l_np))), v_str_lit(";"));
}
return v_bool(1);
return v_null();
}
static Val wrap_c_add_func(void *env, Val *args) { (void)env; (void)args; return c_add_func(args[0], args[1]); }
static Fn fob_c_add_func = {wrap_c_add_func, 0, 0, 2, "add_func"};
static Val c_is_func(Val p0) {
Val *l_name = rt_box_new(p0);
if (rt_truth(rt_cmp(({ rt_trace_push("strfind", 0); Val _tr = c_strfind(rt_add(v_str_lit(";"), g_gfuncs2), rt_add(rt_add(v_str_lit(";"), (*l_name)), v_str_lit(";"))); rt_trace_pop(); _tr; }), v_int(0), ">="))) {
return v_bool(1);
}
return v_bool(0);
return v_null();
}
static Val wrap_c_is_func(void *env, Val *args) { (void)env; (void)args; return c_is_func(args[0]); }
static Fn fob_c_is_func = {wrap_c_is_func, 0, 0, 1, "is_func"};
static Val c_param_idx(Val p0) {
Val *l_t = rt_box_new(p0);
Val *l_s = rt_box_new(g_gparams);
Val *l_k = rt_box_new(v_int(0));
Val *l_cur = rt_box_new(v_str_lit(""));
Val *l_i = rt_box_new(v_int(0));
while (rt_truth(rt_cmp((*l_i), rt_len((*l_s)), "<"))) {
if (rt_truth(rt_cmp(rt_strcut((*l_s), (*l_i), rt_add((*l_i), v_int(1))), v_str_lit(";"), "=="))) {
if (rt_truth(rt_cmp((*l_cur), (*l_t), "=="))) {
return (*l_k);
}
(*l_k) = rt_add((*l_k), v_int(1));
(*l_cur) = v_str_lit("");
}
else {
(*l_cur) = rt_add((*l_cur), rt_strcut((*l_s), (*l_i), rt_add((*l_i), v_int(1))));
}
(*l_i) = rt_add((*l_i), v_int(1));
}
return rt_neg(v_int(1));
return v_null();
}
static Val wrap_c_param_idx(void *env, Val *args) { (void)env; (void)args; return c_param_idx(args[0]); }
static Fn fob_c_param_idx = {wrap_c_param_idx, 0, 0, 1, "param_idx"};
static Val c_out(Val p0) {
Val *l_s = rt_box_new(p0);
if (rt_truth(g_ginfunc)) {
g_gfuncs = rt_add(g_gfuncs, (*l_s));
}
else {
g_gout = rt_add(g_gout, (*l_s));
}
return v_bool(1);
return v_null();
}
static Val wrap_c_out(void *env, Val *args) { (void)env; (void)args; return c_out(args[0]); }
static Fn fob_c_out = {wrap_c_out, 0, 0, 1, "out"};
static Val c_cap_index(Val p0) {
Val *l_name = rt_box_new(p0);
Val *l_idx = rt_box_new(v_int(0));
Val *l_cur = rt_box_new(v_str_lit(""));
Val *l_i = rt_box_new(v_int(0));
while (rt_truth(rt_cmp((*l_i), rt_len(g_gcaps), "<"))) {
if (rt_truth(rt_cmp(rt_strcut(g_gcaps, (*l_i), rt_add((*l_i), v_int(1))), v_str_lit("|"), "=="))) {
if (rt_truth(rt_cmp((*l_cur), (*l_name), "=="))) {
return (*l_idx);
}
(*l_idx) = rt_add((*l_idx), v_int(1));
(*l_cur) = v_str_lit("");
}
else {
(*l_cur) = rt_add((*l_cur), rt_strcut(g_gcaps, (*l_i), rt_add((*l_i), v_int(1))));
}
(*l_i) = rt_add((*l_i), v_int(1));
}
return rt_neg(v_int(1));
return v_null();
}
static Val wrap_c_cap_index(void *env, Val *args) { (void)env; (void)args; return c_cap_index(args[0]); }
static Fn fob_c_cap_index = {wrap_c_cap_index, 0, 0, 1, "cap_index"};
static Val c_env_ptrs(Val p0) {
Val *l_gl = rt_box_new(p0);
Val *l_s = rt_box_new(v_str_lit(""));
Val *l_cur = rt_box_new(v_str_lit(""));
Val *l_i = rt_box_new(v_int(0));
while (rt_truth(rt_cmp((*l_i), rt_len((*l_gl)), "<"))) {
if (rt_truth(rt_cmp(rt_strcut((*l_gl), (*l_i), rt_add((*l_i), v_int(1))), v_str_lit("|"), "=="))) {
if (rt_truth(rt_cmp(rt_len((*l_cur)), v_int(0), ">"))) {
(*l_s) = rt_add(rt_add(rt_add((*l_s), v_str_lit("l_")), (*l_cur)), v_str_lit(", "));
}
(*l_cur) = v_str_lit("");
}
else {
(*l_cur) = rt_add((*l_cur), rt_strcut((*l_gl), (*l_i), rt_add((*l_i), v_int(1))));
}
(*l_i) = rt_add((*l_i), v_int(1));
}
return (*l_s);
return v_null();
}
static Val wrap_c_env_ptrs(void *env, Val *args) { (void)env; (void)args; return c_env_ptrs(args[0]); }
static Fn fob_c_env_ptrs = {wrap_c_env_ptrs, 0, 0, 1, "env_ptrs"};
static Val c_env_count(Val p0) {
Val *l_gl = rt_box_new(p0);
Val *l_n = rt_box_new(v_int(0));
Val *l_cur = rt_box_new(v_str_lit(""));
Val *l_i = rt_box_new(v_int(0));
while (rt_truth(rt_cmp((*l_i), rt_len((*l_gl)), "<"))) {
if (rt_truth(rt_cmp(rt_strcut((*l_gl), (*l_i), rt_add((*l_i), v_int(1))), v_str_lit("|"), "=="))) {
if (rt_truth(rt_cmp(rt_len((*l_cur)), v_int(0), ">"))) {
(*l_n) = rt_add((*l_n), v_int(1));
}
(*l_cur) = v_str_lit("");
}
else {
(*l_cur) = rt_add((*l_cur), rt_strcut((*l_gl), (*l_i), rt_add((*l_i), v_int(1))));
}
(*l_i) = rt_add((*l_i), v_int(1));
}
return (*l_n);
return v_null();
}
static Val wrap_c_env_count(void *env, Val *args) { (void)env; (void)args; return c_env_count(args[0]); }
static Fn fob_c_env_count = {wrap_c_env_count, 0, 0, 1, "env_count"};
static Val c_call_expr(Val p0, Val p1, Val p2) {
Val *l_target = rt_box_new(p0);
Val *l_args = rt_box_new(p1);
Val *l_narg = rt_box_new(p2);
if (rt_truth(rt_cmp((*l_narg), v_int(0), ">"))) {
g_gargseq = rt_add(g_gargseq, v_int(1));
Val *l_an = rt_box_new(rt_add(v_str_lit("_ia"), rt_tostr(g_gargseq)));
return rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(v_str_lit("({ Val "), (*l_an)), v_str_lit("[")), rt_tostr((*l_narg))), v_str_lit("] = {")), (*l_args)), v_str_lit("}; rt_call(")), (*l_target)), v_str_lit(", ")), (*l_an)), v_str_lit(", ")), rt_tostr((*l_narg))), v_str_lit("); })"));
}
return rt_add(rt_add(v_str_lit("rt_call("), (*l_target)), v_str_lit(", 0, 0)"));
return v_null();
}
static Val wrap_c_call_expr(void *env, Val *args) { (void)env; (void)args; return c_call_expr(args[0], args[1], args[2]); }
static Fn fob_c_call_expr = {wrap_c_call_expr, 0, 0, 3, "call_expr"};
static Val c_var_ref(Val p0) {
Val *l_name = rt_box_new(p0);
if (rt_truth(rt_and(g_ginfunc, rt_cmp(({ rt_trace_push("strfind", 0); Val _tr = c_strfind(rt_add(v_str_lit("|"), g_glocals), rt_add(rt_add(v_str_lit("|"), (*l_name)), v_str_lit("|"))); rt_trace_pop(); _tr; }), v_int(0), ">=")))) {
return rt_add(rt_add(v_str_lit("(*l_"), (*l_name)), v_str_lit(")"));
}
return rt_add(v_str_lit("g_"), (*l_name));
return v_null();
}
static Val wrap_c_var_ref(void *env, Val *args) { (void)env; (void)args; return c_var_ref(args[0]); }
static Fn fob_c_var_ref = {wrap_c_var_ref, 0, 0, 1, "var_ref"};
static Val c_list_at(Val p0, Val p1) {
Val *l_s = rt_box_new(p0);
Val *l_idx = rt_box_new(p1);
Val *l_k = rt_box_new(v_int(0));
Val *l_cur = rt_box_new(v_str_lit(""));
Val *l_i = rt_box_new(v_int(0));
while (rt_truth(rt_cmp((*l_i), rt_len((*l_s)), "<"))) {
if (rt_truth(rt_cmp(rt_strcut((*l_s), (*l_i), rt_add((*l_i), v_int(1))), v_str_lit("|"), "=="))) {
if (rt_truth(rt_cmp((*l_k), (*l_idx), "=="))) {
return (*l_cur);
}
(*l_k) = rt_add((*l_k), v_int(1));
(*l_cur) = v_str_lit("");
}
else {
(*l_cur) = rt_add((*l_cur), rt_strcut((*l_s), (*l_i), rt_add((*l_i), v_int(1))));
}
(*l_i) = rt_add((*l_i), v_int(1));
}
return v_str_lit("");
return v_null();
}
static Val wrap_c_list_at(void *env, Val *args) { (void)env; (void)args; return c_list_at(args[0], args[1]); }
static Fn fob_c_list_at = {wrap_c_list_at, 0, 0, 2, "list_at"};
static Val c_scan() {
if (rt_truth(g_gded_pending)) {
({ rt_trace_push("pop_ind", 0); Val _tr = c_pop_ind(); rt_trace_pop(); _tr; });
g_gtype = v_int(7);
g_gtok = v_str_lit("");
g_gprev_nl = v_bool(0);
if (rt_truth(rt_cmp(g_gded_target, ({ rt_trace_push("top_ind", 0); Val _tr = c_top_ind(); rt_trace_pop(); _tr; }), "<"))) {
return v_null();
}
g_gded_pending = v_bool(0);
return v_null();
}
if (rt_truth(g_gprev_nl)) {
Val *l_n0 = rt_box_new(rt_len(g_gsrc));
Val *l_more = rt_box_new(v_bool(1));
while (rt_truth(rt_and(rt_cmp(g_gpos, (*l_n0), "<"), (*l_more)))) {
Val *l_c0 = rt_box_new(rt_strcut(g_gsrc, g_gpos, rt_add(g_gpos, v_int(1))));
if (rt_truth(rt_or(rt_cmp((*l_c0), v_str_lit("\n"), "=="), rt_cmp((*l_c0), v_str_lit("\r"), "==")))) {
g_gpos = rt_add(g_gpos, v_int(1));
}
else {
(*l_more) = v_bool(0);
}
}
Val *l_ind = rt_box_new(v_int(0));
while (rt_truth(rt_cmp(g_gpos, (*l_n0), "<"))) {
Val *l_c1 = rt_box_new(rt_strcut(g_gsrc, g_gpos, rt_add(g_gpos, v_int(1))));
if (rt_truth(rt_cmp((*l_c1), v_str_lit(" "), "=="))) {
(*l_ind) = rt_add((*l_ind), v_int(1));
g_gpos = rt_add(g_gpos, v_int(1));
}
else {
if (rt_truth(rt_cmp((*l_c1), v_str_lit("\t"), "=="))) {
(*l_ind) = rt_add((*l_ind), v_int(4));
g_gpos = rt_add(g_gpos, v_int(1));
}
else {
break;
}
}
}
if (rt_truth(rt_cmp(g_gpos, (*l_n0), ">="))) {
g_gtype = v_int(5);
g_gtok = v_str_lit("");
return v_null();
}
Val *l_c2 = rt_box_new(rt_strcut(g_gsrc, g_gpos, rt_add(g_gpos, v_int(1))));
if (rt_truth(rt_cmp((*l_c2), v_str_lit("#"), "=="))) {
while (rt_truth(rt_and(rt_cmp(g_gpos, (*l_n0), "<"), rt_cmp(rt_strcut(g_gsrc, g_gpos, rt_add(g_gpos, v_int(1))), v_str_lit("\n"), "!=")))) {
g_gpos = rt_add(g_gpos, v_int(1));
}
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
return v_null();
}
if (rt_truth(rt_and(rt_and(rt_cmp((*l_c2), v_str_lit("/"), "=="), rt_cmp(rt_add(g_gpos, v_int(1)), (*l_n0), "<")), rt_cmp(rt_strcut(g_gsrc, rt_add(g_gpos, v_int(1)), rt_add(g_gpos, v_int(2))), v_str_lit("/"), "==")))) {
while (rt_truth(rt_and(rt_cmp(g_gpos, (*l_n0), "<"), rt_cmp(rt_strcut(g_gsrc, g_gpos, rt_add(g_gpos, v_int(1))), v_str_lit("\n"), "!=")))) {
g_gpos = rt_add(g_gpos, v_int(1));
}
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
return v_null();
}
if (rt_truth(rt_cmp((*l_ind), ({ rt_trace_push("top_ind", 0); Val _tr = c_top_ind(); rt_trace_pop(); _tr; }), ">"))) {
({ rt_trace_push("push_ind", 0); Val _tr = c_push_ind((*l_ind)); rt_trace_pop(); _tr; });
g_gtype = v_int(6);
g_gtok = v_str_lit("");
g_gprev_nl = v_bool(0);
return v_null();
}
if (rt_truth(rt_cmp((*l_ind), ({ rt_trace_push("top_ind", 0); Val _tr = c_top_ind(); rt_trace_pop(); _tr; }), "<"))) {
({ rt_trace_push("pop_ind", 0); Val _tr = c_pop_ind(); rt_trace_pop(); _tr; });
g_gtype = v_int(7);
g_gtok = v_str_lit("");
g_gprev_nl = v_bool(0);
if (rt_truth(rt_cmp((*l_ind), ({ rt_trace_push("top_ind", 0); Val _tr = c_top_ind(); rt_trace_pop(); _tr; }), "<"))) {
g_gded_pending = v_bool(1);
g_gded_target = (*l_ind);
}
return v_null();
}
}
Val *l_n = rt_box_new(rt_len(g_gsrc));
while (rt_truth(rt_cmp(g_gpos, (*l_n), "<"))) {
Val *l_c = rt_box_new(rt_strcut(g_gsrc, g_gpos, rt_add(g_gpos, v_int(1))));
if (rt_truth(rt_or(rt_cmp((*l_c), v_str_lit(" "), "=="), rt_cmp((*l_c), v_str_lit("\t"), "==")))) {
g_gpos = rt_add(g_gpos, v_int(1));
}
else {
if (rt_truth(rt_or(rt_cmp((*l_c), v_str_lit("\n"), "=="), rt_cmp((*l_c), v_str_lit("\r"), "==")))) {
g_gpos = rt_add(g_gpos, v_int(1));
g_gprev_nl = v_bool(1);
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
return v_null();
}
else {
break;
}
}
}
if (rt_truth(rt_cmp(g_gpos, (*l_n), ">="))) {
g_gtype = v_int(5);
g_gtok = v_str_lit("");
return v_null();
}
g_gprev_nl = v_bool(0);
Val *l_c = rt_box_new(rt_strcut(g_gsrc, g_gpos, rt_add(g_gpos, v_int(1))));
if (rt_truth(rt_cmp((*l_c), v_str_lit("#"), "=="))) {
while (rt_truth(rt_and(rt_cmp(g_gpos, (*l_n), "<"), rt_cmp(rt_strcut(g_gsrc, g_gpos, rt_add(g_gpos, v_int(1))), v_str_lit("\n"), "!=")))) {
g_gpos = rt_add(g_gpos, v_int(1));
}
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
return v_null();
}
if (rt_truth(rt_and(rt_and(rt_cmp((*l_c), v_str_lit("/"), "=="), rt_cmp(rt_add(g_gpos, v_int(1)), (*l_n), "<")), rt_cmp(rt_strcut(g_gsrc, rt_add(g_gpos, v_int(1)), rt_add(g_gpos, v_int(2))), v_str_lit("/"), "==")))) {
while (rt_truth(rt_and(rt_cmp(g_gpos, (*l_n), "<"), rt_cmp(rt_strcut(g_gsrc, g_gpos, rt_add(g_gpos, v_int(1))), v_str_lit("\n"), "!=")))) {
g_gpos = rt_add(g_gpos, v_int(1));
}
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
return v_null();
}
if (rt_truth(({ rt_trace_push("is_digit", 0); Val _tr = c_is_digit((*l_c)); rt_trace_pop(); _tr; }))) {
Val *l_st = rt_box_new(g_gpos);
while (rt_truth(rt_cmp(g_gpos, (*l_n), "<"))) {
Val *l_d = rt_box_new(rt_strcut(g_gsrc, g_gpos, rt_add(g_gpos, v_int(1))));
if (rt_truth(rt_or(({ rt_trace_push("is_digit", 0); Val _tr = c_is_digit((*l_d)); rt_trace_pop(); _tr; }), rt_cmp((*l_d), v_str_lit("."), "==")))) {
g_gpos = rt_add(g_gpos, v_int(1));
}
else {
break;
}
}
g_gtok = rt_strcut(g_gsrc, (*l_st), g_gpos);
g_gtype = v_int(2);
return v_null();
}
if (rt_truth(({ rt_trace_push("is_alpha", 0); Val _tr = c_is_alpha((*l_c)); rt_trace_pop(); _tr; }))) {
Val *l_st = rt_box_new(g_gpos);
while (rt_truth(rt_cmp(g_gpos, (*l_n), "<"))) {
Val *l_d = rt_box_new(rt_strcut(g_gsrc, g_gpos, rt_add(g_gpos, v_int(1))));
if (rt_truth(rt_or(({ rt_trace_push("is_alpha", 0); Val _tr = c_is_alpha((*l_d)); rt_trace_pop(); _tr; }), ({ rt_trace_push("is_digit", 0); Val _tr = c_is_digit((*l_d)); rt_trace_pop(); _tr; })))) {
g_gpos = rt_add(g_gpos, v_int(1));
}
else {
break;
}
}
g_gtok = rt_strcut(g_gsrc, (*l_st), g_gpos);
g_gtype = v_int(1);
return v_null();
}
if (rt_truth(rt_or(rt_cmp((*l_c), v_str_lit("\""), "=="), rt_cmp((*l_c), v_str_lit("'"), "==")))) {
Val *l_q = rt_box_new((*l_c));
Val *l_st = rt_box_new(g_gpos);
g_gpos = rt_add(g_gpos, v_int(1));
while (rt_truth(rt_cmp(g_gpos, (*l_n), "<"))) {
Val *l_d = rt_box_new(rt_strcut(g_gsrc, g_gpos, rt_add(g_gpos, v_int(1))));
if (rt_truth(rt_cmp((*l_d), v_str_lit("\\"), "=="))) {
g_gpos = rt_add(g_gpos, v_int(2));
}
else {
if (rt_truth(rt_cmp((*l_d), (*l_q), "=="))) {
g_gpos = rt_add(g_gpos, v_int(1));
break;
}
g_gpos = rt_add(g_gpos, v_int(1));
}
}
g_gtok = rt_strcut(g_gsrc, (*l_st), g_gpos);
g_gtype = v_int(3);
return v_null();
}
Val *l_nx = rt_box_new(rt_add(g_gpos, v_int(1)));
if (rt_truth(rt_and(rt_and(rt_cmp((*l_c), v_str_lit("="), "=="), rt_cmp((*l_nx), (*l_n), "<")), rt_cmp(rt_strcut(g_gsrc, (*l_nx), rt_add((*l_nx), v_int(1))), v_str_lit("="), "==")))) {
g_gtok = v_str_lit("==");
g_gtype = v_int(4);
g_gpos = rt_add(g_gpos, v_int(2));
return v_null();
}
if (rt_truth(rt_and(rt_and(rt_cmp((*l_c), v_str_lit("!"), "=="), rt_cmp((*l_nx), (*l_n), "<")), rt_cmp(rt_strcut(g_gsrc, (*l_nx), rt_add((*l_nx), v_int(1))), v_str_lit("="), "==")))) {
g_gtok = v_str_lit("!=");
g_gtype = v_int(4);
g_gpos = rt_add(g_gpos, v_int(2));
return v_null();
}
if (rt_truth(rt_and(rt_and(rt_cmp((*l_c), v_str_lit("<"), "=="), rt_cmp((*l_nx), (*l_n), "<")), rt_cmp(rt_strcut(g_gsrc, (*l_nx), rt_add((*l_nx), v_int(1))), v_str_lit("="), "==")))) {
g_gtok = v_str_lit("<=");
g_gtype = v_int(4);
g_gpos = rt_add(g_gpos, v_int(2));
return v_null();
}
if (rt_truth(rt_and(rt_and(rt_cmp((*l_c), v_str_lit(">"), "=="), rt_cmp((*l_nx), (*l_n), "<")), rt_cmp(rt_strcut(g_gsrc, (*l_nx), rt_add((*l_nx), v_int(1))), v_str_lit("="), "==")))) {
g_gtok = v_str_lit(">=");
g_gtype = v_int(4);
g_gpos = rt_add(g_gpos, v_int(2));
return v_null();
}
g_gtok = (*l_c);
g_gtype = v_int(4);
g_gpos = rt_add(g_gpos, v_int(1));
return v_null();
return v_null();
}
static Val wrap_c_scan(void *env, Val *args) { (void)env; (void)args; return c_scan(); }
static Fn fob_c_scan = {wrap_c_scan, 0, 0, 0, "scan"};
static Val c_expect_op(Val p0) {
Val *l_op = rt_box_new(p0);
if (rt_truth(rt_cmp(g_gtok, (*l_op), "!="))) {
rt_print_val(v_str_lit("imporc.impor: 期望 ")); rt_print_sep(); rt_print_val((*l_op)); rt_print_sep(); rt_print_val(v_str_lit(" 实际 ")); rt_print_sep(); rt_print_val(g_gtok); rt_print_nl();
g_gfail = v_bool(1);
}
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
return v_null();
}
static Val wrap_c_expect_op(void *env, Val *args) { (void)env; (void)args; return c_expect_op(args[0]); }
static Fn fob_c_expect_op = {wrap_c_expect_op, 0, 0, 1, "expect_op"};
static Val c_esc_str(Val p0) {
Val *l_s = rt_box_new(p0);
Val *l_n = rt_box_new(rt_len((*l_s)));
Val *l_inner = rt_box_new(rt_strcut((*l_s), v_int(1), rt_sub((*l_n), v_int(1))));
Val *l_out = rt_box_new(v_str_lit(""));
Val *l_i = rt_box_new(v_int(0));
while (rt_truth(rt_cmp((*l_i), rt_len((*l_inner)), "<"))) {
Val *l_c = rt_box_new(rt_strcut((*l_inner), (*l_i), rt_add((*l_i), v_int(1))));
if (rt_truth(rt_cmp((*l_c), v_str_lit("\\"), "=="))) {
if (rt_truth(rt_cmp(rt_add((*l_i), v_int(1)), rt_len((*l_inner)), "<"))) {
Val *l_nxt = rt_box_new(rt_strcut((*l_inner), rt_add((*l_i), v_int(1)), rt_add((*l_i), v_int(2))));
(*l_out) = rt_add(rt_add((*l_out), v_str_lit("\\")), (*l_nxt));
(*l_i) = rt_add((*l_i), v_int(2));
}
else {
(*l_out) = rt_add((*l_out), v_str_lit("\\\\"));
(*l_i) = rt_add((*l_i), v_int(1));
}
}
else {
if (rt_truth(rt_cmp((*l_c), v_str_lit("\""), "=="))) {
(*l_out) = rt_add((*l_out), v_str_lit("\\\""));
}
else {
(*l_out) = rt_add((*l_out), (*l_c));
}
(*l_i) = rt_add((*l_i), v_int(1));
}
}
return (*l_out);
return v_null();
}
static Val wrap_c_esc_str(void *env, Val *args) { (void)env; (void)args; return c_esc_str(args[0]); }
static Fn fob_c_esc_str = {wrap_c_esc_str, 0, 0, 1, "esc_str"};
static Val c_parse_primary() {
Val *l_t = rt_box_new(g_gtok);
if (rt_truth(rt_cmp(g_gtok, v_str_lit("{"), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
Val *l_ditems = rt_box_new(v_str_lit(""));
Val *l_dcnt = rt_box_new(v_int(0));
if (rt_truth(rt_cmp(g_gtok, v_str_lit("}"), "!="))) {
while (rt_truth(v_int(1))) {
if (rt_truth(rt_cmp((*l_dcnt), v_int(0), ">"))) {
(*l_ditems) = rt_add((*l_ditems), v_str_lit(", "));
}
(*l_ditems) = rt_add((*l_ditems), ({ rt_trace_push("parse_expr", 0); Val _tr = c_parse_expr(); rt_trace_pop(); _tr; }));
({ rt_trace_push("expect_op", 0); Val _tr = c_expect_op(v_str_lit(":")); rt_trace_pop(); _tr; });
(*l_ditems) = rt_add(rt_add((*l_ditems), v_str_lit(", ")), ({ rt_trace_push("parse_expr", 0); Val _tr = c_parse_expr(); rt_trace_pop(); _tr; }));
(*l_dcnt) = rt_add((*l_dcnt), v_int(1));
if (rt_truth(rt_cmp(g_gtok, v_str_lit(","), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
}
else {
break;
}
}
}
({ rt_trace_push("expect_op", 0); Val _tr = c_expect_op(v_str_lit("}")); rt_trace_pop(); _tr; });
if (rt_truth(rt_cmp((*l_dcnt), v_int(0), ">"))) {
return rt_add(rt_add(rt_add(rt_add(v_str_lit("rt_dict_of("), rt_tostr((*l_dcnt))), v_str_lit(", ")), (*l_ditems)), v_str_lit(")"));
}
return v_str_lit("rt_dict_of(0)");
}
if (rt_truth(rt_cmp(g_gtok, v_str_lit("["), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
Val *l_items = rt_box_new(v_str_lit(""));
Val *l_cnt = rt_box_new(v_int(0));
if (rt_truth(rt_cmp(g_gtok, v_str_lit("]"), "!="))) {
(*l_items) = ({ rt_trace_push("parse_expr", 0); Val _tr = c_parse_expr(); rt_trace_pop(); _tr; });
(*l_cnt) = v_int(1);
while (rt_truth(rt_cmp(g_gtok, v_str_lit(","), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
(*l_items) = rt_add(rt_add((*l_items), v_str_lit(", ")), ({ rt_trace_push("parse_expr", 0); Val _tr = c_parse_expr(); rt_trace_pop(); _tr; }));
(*l_cnt) = rt_add((*l_cnt), v_int(1));
}
}
if (rt_truth(rt_cmp(g_gtok, v_str_lit("]"), "!="))) {
rt_print_val(v_str_lit("imporc.impor: 数组字面量需要 ]")); rt_print_nl();
g_gfail = v_bool(1);
return v_str_lit("0");
}
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
if (rt_truth(rt_cmp((*l_cnt), v_int(0), ">"))) {
return rt_add(rt_add(rt_add(rt_add(v_str_lit("rt_arr_of("), rt_tostr((*l_cnt))), v_str_lit(", ")), (*l_items)), v_str_lit(")"));
}
return v_str_lit("rt_arr_of(0)");
}
if (rt_truth(rt_cmp(g_gtype, v_int(2), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
if (rt_truth(rt_cmp(({ rt_trace_push("strfind", 0); Val _tr = c_strfind((*l_t), v_str_lit(".")); rt_trace_pop(); _tr; }), v_int(0), ">="))) {
return rt_add(rt_add(v_str_lit("v_float("), (*l_t)), v_str_lit(")"));
}
return rt_add(rt_add(v_str_lit("v_int("), (*l_t)), v_str_lit(")"));
}
if (rt_truth(rt_cmp(g_gtype, v_int(3), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
return rt_add(rt_add(v_str_lit("v_str_lit(\""), ({ rt_trace_push("esc_str", 0); Val _tr = c_esc_str((*l_t)); rt_trace_pop(); _tr; })), v_str_lit("\")"));
}
if (rt_truth(rt_cmp(g_gtype, v_int(1), "=="))) {
if (rt_truth(rt_cmp((*l_t), v_str_lit("range"), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
({ rt_trace_push("expect_op", 0); Val _tr = c_expect_op(v_str_lit("(")); rt_trace_pop(); _tr; });
Val *l_rn = rt_box_new(({ rt_trace_push("parse_expr", 0); Val _tr = c_parse_expr(); rt_trace_pop(); _tr; }));
({ rt_trace_push("expect_op", 0); Val _tr = c_expect_op(v_str_lit(")")); rt_trace_pop(); _tr; });
return rt_add(rt_add(v_str_lit("rt_range("), (*l_rn)), v_str_lit(")"));
}
if (rt_truth(rt_cmp((*l_t), v_str_lit("true"), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
return v_str_lit("v_bool(1)");
}
if (rt_truth(rt_cmp((*l_t), v_str_lit("false"), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
return v_str_lit("v_bool(0)");
}
if (rt_truth(rt_cmp((*l_t), v_str_lit("not"), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
return rt_add(rt_add(v_str_lit("rt_not("), ({ rt_trace_push("parse_unary", 0); Val _tr = c_parse_unary(); rt_trace_pop(); _tr; })), v_str_lit(")"));
}
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
Val *l_base = rt_box_new(v_str_lit(""));
g_gassign_kind = v_str_lit("");
if (rt_truth(rt_and(rt_cmp(g_gtok, v_str_lit("."), "=="), rt_cmp(({ rt_trace_push("strfind", 0); Val _tr = c_strfind(rt_add(v_str_lit("|"), g_genum_types), rt_add(rt_add(v_str_lit("|"), (*l_t)), v_str_lit("|"))); rt_trace_pop(); _tr; }), v_int(0), ">=")))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
Val *l_emem = rt_box_new(g_gtok);
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
(*l_t) = rt_add(rt_add(rt_add(v_str_lit("__enum_"), (*l_t)), v_str_lit("_")), (*l_emem));
}
if (rt_truth(rt_cmp((*l_t), v_str_lit("new"), "=="))) {
Val *l_clsname = rt_box_new(g_gtok);
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
({ rt_trace_push("expect_op", 0); Val _tr = c_expect_op(v_str_lit("(")); rt_trace_pop(); _tr; });
Val *l_cargs = rt_box_new(v_str_lit(""));
Val *l_cn = rt_box_new(v_int(0));
if (rt_truth(rt_cmp(g_gtok, v_str_lit(")"), "!="))) {
while (rt_truth(v_int(1))) {
if (rt_truth(rt_cmp((*l_cn), v_int(0), ">"))) {
(*l_cargs) = rt_add((*l_cargs), v_str_lit(", "));
}
(*l_cargs) = rt_add((*l_cargs), ({ rt_trace_push("parse_expr", 0); Val _tr = c_parse_expr(); rt_trace_pop(); _tr; }));
(*l_cn) = rt_add((*l_cn), v_int(1));
if (rt_truth(rt_cmp(g_gtok, v_str_lit(","), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
}
else {
break;
}
}
}
({ rt_trace_push("expect_op", 0); Val _tr = c_expect_op(v_str_lit(")")); rt_trace_pop(); _tr; });
if (rt_truth(rt_cmp((*l_cn), v_int(0), ">"))) {
g_gargseq = rt_add(g_gargseq, v_int(1));
(*l_base) = rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(v_str_lit("({ Val _ia"), rt_tostr(g_gargseq)), v_str_lit("[")), rt_tostr((*l_cn))), v_str_lit("] = {")), (*l_cargs)), v_str_lit("}; rt_new(\"")), (*l_clsname)), v_str_lit("\", _ia")), rt_tostr(g_gargseq)), v_str_lit(", ")), rt_tostr((*l_cn))), v_str_lit("); })"));
}
else {
(*l_base) = rt_add(rt_add(v_str_lit("rt_new(\""), (*l_clsname)), v_str_lit("\", 0, 0)"));
}
}
else {
if (rt_truth(rt_or(rt_cmp((*l_t), v_str_lit("self"), "=="), rt_cmp((*l_t), v_str_lit("this"), "==")))) {
(*l_base) = v_str_lit("v_obj(self)");
}
else {
if (rt_truth(rt_cmp((*l_t), v_str_lit("super"), "=="))) {
({ rt_trace_push("expect_op", 0); Val _tr = c_expect_op(v_str_lit("(")); rt_trace_pop(); _tr; });
({ rt_trace_push("expect_op", 0); Val _tr = c_expect_op(v_str_lit(")")); rt_trace_pop(); _tr; });
g_gsuper_pending = v_bool(1);
(*l_base) = v_str_lit("self");
}
else {
if (rt_truth(rt_cmp(g_gtok, v_str_lit("("), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
Val *l_args = rt_box_new(v_str_lit(""));
Val *l_narg = rt_box_new(v_int(0));
if (rt_truth(rt_cmp(g_gtok, v_str_lit(")"), "!="))) {
while (rt_truth(v_int(1))) {
if (rt_truth(rt_cmp((*l_narg), v_int(0), ">"))) {
(*l_args) = rt_add((*l_args), v_str_lit(", "));
}
(*l_args) = rt_add((*l_args), ({ rt_trace_push("parse_expr", 0); Val _tr = c_parse_expr(); rt_trace_pop(); _tr; }));
(*l_narg) = rt_add((*l_narg), v_int(1));
if (rt_truth(rt_cmp(g_gtok, v_str_lit(","), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
}
else {
break;
}
}
}
({ rt_trace_push("expect_op", 0); Val _tr = c_expect_op(v_str_lit(")")); rt_trace_pop(); _tr; });
if (rt_truth(rt_cmp((*l_t), v_str_lit("len"), "=="))) {
(*l_base) = rt_add(rt_add(v_str_lit("rt_len("), (*l_args)), v_str_lit(")"));
}
else {
if (rt_truth(rt_cmp((*l_t), v_str_lit("strcut"), "=="))) {
(*l_base) = rt_add(rt_add(v_str_lit("rt_strcut("), (*l_args)), v_str_lit(")"));
}
else {
if (rt_truth(rt_cmp((*l_t), v_str_lit("strpad"), "=="))) {
(*l_base) = rt_add(rt_add(v_str_lit("rt_strpad("), (*l_args)), v_str_lit(")"));
}
else {
if (rt_truth(rt_cmp((*l_t), v_str_lit("to_num"), "=="))) {
(*l_base) = rt_add(rt_add(v_str_lit("rt_to_num("), (*l_args)), v_str_lit(")"));
}
else {
if (rt_truth(rt_cmp((*l_t), v_str_lit("to_str"), "=="))) {
(*l_base) = rt_add(rt_add(v_str_lit("rt_tostr("), (*l_args)), v_str_lit(")"));
}
else {
if (rt_truth(rt_cmp((*l_t), v_str_lit("upper"), "=="))) {
(*l_base) = rt_add(rt_add(v_str_lit("rt_upper("), (*l_args)), v_str_lit(")"));
}
else {
if (rt_truth(rt_cmp((*l_t), v_str_lit("lower"), "=="))) {
(*l_base) = rt_add(rt_add(v_str_lit("rt_lower("), (*l_args)), v_str_lit(")"));
}
else {
if (rt_truth(rt_cmp((*l_t), v_str_lit("trim"), "=="))) {
(*l_base) = rt_add(rt_add(v_str_lit("rtmi_trim("), (*l_args)), v_str_lit(")"));
}
else {
if (rt_truth(rt_cmp((*l_t), v_str_lit("abs"), "=="))) {
(*l_base) = rt_add(rt_add(v_str_lit("rt_abs("), (*l_args)), v_str_lit(")"));
}
else {
if (rt_truth(rt_cmp((*l_t), v_str_lit("min"), "=="))) {
(*l_base) = rt_add(rt_add(v_str_lit("rt_min2("), (*l_args)), v_str_lit(")"));
}
else {
if (rt_truth(rt_cmp((*l_t), v_str_lit("max"), "=="))) {
(*l_base) = rt_add(rt_add(v_str_lit("rt_max2("), (*l_args)), v_str_lit(")"));
}
else {
if (rt_truth(rt_cmp((*l_t), v_str_lit("sign"), "=="))) {
(*l_base) = rt_add(rt_add(v_str_lit("rt_sign("), (*l_args)), v_str_lit(")"));
}
else {
if (rt_truth(rt_cmp((*l_t), v_str_lit("clamp"), "=="))) {
(*l_base) = rt_add(rt_add(v_str_lit("rt_clamp("), (*l_args)), v_str_lit(")"));
}
else {
if (rt_truth(rt_cmp((*l_t), v_str_lit("fileread"), "=="))) {
(*l_base) = rt_add(rt_add(v_str_lit("rt_fileread("), (*l_args)), v_str_lit(")"));
}
else {
if (rt_truth(rt_cmp((*l_t), v_str_lit("filewrite"), "=="))) {
(*l_base) = rt_add(rt_add(v_str_lit("rt_filewrite("), (*l_args)), v_str_lit(")"));
}
else {
if (rt_truth(rt_cmp((*l_t), v_str_lit("system"), "=="))) {
(*l_base) = rt_add(rt_add(v_str_lit("rt_system("), (*l_args)), v_str_lit(")"));
}
else {
if (rt_truth(rt_cmp((*l_t), v_str_lit("exit"), "=="))) {
(*l_base) = rt_add(rt_add(v_str_lit("rt_exit("), (*l_args)), v_str_lit(")"));
}
else {
if (rt_truth(rt_cmp((*l_t), v_str_lit("mem"), "=="))) {
(*l_base) = v_str_lit("rt_mem()");
}
else {
if (rt_truth(rt_cmp((*l_t), v_str_lit("gc"), "=="))) {
(*l_base) = v_str_lit("rt_gc()");
}
else {
if (rt_truth(rt_cmp((*l_t), v_str_lit("sleep"), "=="))) {
(*l_base) = rt_add(rt_add(v_str_lit("rt_sleep("), (*l_args)), v_str_lit(")"));
}
else {
if (rt_truth(rt_cmp((*l_t), v_str_lit("div"), "=="))) {
(*l_base) = rt_add(rt_add(v_str_lit("rt_idiv("), (*l_args)), v_str_lit(")"));
}
else {
if (rt_truth(rt_or(rt_cmp((*l_t), v_str_lit("type"), "=="), rt_cmp((*l_t), v_str_lit("type_of"), "==")))) {
(*l_base) = rt_add(rt_add(v_str_lit("rt_type("), (*l_args)), v_str_lit(")"));
}
else {
if (rt_truth(rt_cmp((*l_t), v_str_lit("args"), "=="))) {
(*l_base) = v_str_lit("rt_args()");
}
else {
if (rt_truth(rt_and(g_ginfunc, rt_cmp(({ rt_trace_push("strfind", 0); Val _tr = c_strfind(rt_add(v_str_lit("|"), g_glocals), rt_add(rt_add(v_str_lit("|"), (*l_t)), v_str_lit("|"))); rt_trace_pop(); _tr; }), v_int(0), ">=")))) {
(*l_base) = ({ rt_trace_push("call_expr", 0); Val _tr = c_call_expr(({ rt_trace_push("var_ref", 0); Val _tr = c_var_ref((*l_t)); rt_trace_pop(); _tr; }), (*l_args), (*l_narg)); rt_trace_pop(); _tr; });
}
else {
if (rt_truth(rt_and(g_ginfunc, rt_cmp(({ rt_trace_push("cap_index", 0); Val _tr = c_cap_index((*l_t)); rt_trace_pop(); _tr; }), v_int(0), ">=")))) {
(*l_base) = ({ rt_trace_push("call_expr", 0); Val _tr = c_call_expr(rt_add(rt_add(v_str_lit("(*c_"), (*l_t)), v_str_lit(")")), (*l_args), (*l_narg)); rt_trace_pop(); _tr; });
}
else {
if (rt_truth(({ rt_trace_push("is_func", 0); Val _tr = c_is_func((*l_t)); rt_trace_pop(); _tr; }))) {
(*l_base) = rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(v_str_lit("({ rt_trace_push(\""), (*l_t)), v_str_lit("\", 0); Val _tr = c_")), (*l_t)), v_str_lit("(")), (*l_args)), v_str_lit("); rt_trace_pop(); _tr; })"));
}
else {
if (rt_truth(rt_cmp((*l_t), v_str_lit("expect"), "=="))) {
if (rt_truth(rt_cmp((*l_narg), v_int(2), "=="))) {
(*l_base) = rt_add(rt_add(v_str_lit("rt_expect("), (*l_args)), v_str_lit(", v_str(\"\"))"));
}
else {
(*l_base) = rt_add(rt_add(v_str_lit("rt_expect("), (*l_args)), v_str_lit(")"));
}
}
else {
if (rt_truth(rt_cmp((*l_t), v_str_lit("imporc_module"), "=="))) {
(*l_base) = rt_add(rt_add(v_str_lit("rt_module_new("), (*l_args)), v_str_lit(")"));
}
else {
(*l_base) = ({ rt_trace_push("call_expr", 0); Val _tr = c_call_expr(rt_add(v_str_lit("g_"), (*l_t)), (*l_args), (*l_narg)); rt_trace_pop(); _tr; });
}
}
}
}
}
}
}
}
}
}
}
}
}
}
}
}
}
}
}
}
}
}
}
}
}
}
}
}
}
else {
if (rt_truth(g_gmethod)) {
Val *l_pidx = rt_box_new(({ rt_trace_push("param_idx", 0); Val _tr = c_param_idx((*l_t)); rt_trace_pop(); _tr; }));
if (rt_truth(rt_cmp((*l_pidx), v_int(0), ">="))) {
(*l_base) = rt_add(rt_add(v_str_lit("args["), rt_tostr((*l_pidx))), v_str_lit("]"));
}
else {
if (rt_truth(rt_and(g_ginfunc, rt_cmp(({ rt_trace_push("strfind", 0); Val _tr = c_strfind(rt_add(v_str_lit("|"), g_glocals), rt_add(rt_add(v_str_lit("|"), (*l_t)), v_str_lit("|"))); rt_trace_pop(); _tr; }), v_int(0), ">=")))) {
(*l_base) = ({ rt_trace_push("var_ref", 0); Val _tr = c_var_ref((*l_t)); rt_trace_pop(); _tr; });
}
else {
if (rt_truth(rt_and(g_ginfunc, rt_cmp(({ rt_trace_push("cap_index", 0); Val _tr = c_cap_index((*l_t)); rt_trace_pop(); _tr; }), v_int(0), ">=")))) {
(*l_base) = rt_add(rt_add(v_str_lit("(*c_"), (*l_t)), v_str_lit(")"));
}
else {
(*l_base) = rt_add(v_str_lit("g_"), (*l_t));
}
}
}
}
else {
if (rt_truth(rt_and(g_ginfunc, rt_cmp(({ rt_trace_push("strfind", 0); Val _tr = c_strfind(rt_add(v_str_lit("|"), g_glocals), rt_add(rt_add(v_str_lit("|"), (*l_t)), v_str_lit("|"))); rt_trace_pop(); _tr; }), v_int(0), ">=")))) {
(*l_base) = ({ rt_trace_push("var_ref", 0); Val _tr = c_var_ref((*l_t)); rt_trace_pop(); _tr; });
}
else {
if (rt_truth(rt_and(g_ginfunc, rt_cmp(({ rt_trace_push("cap_index", 0); Val _tr = c_cap_index((*l_t)); rt_trace_pop(); _tr; }), v_int(0), ">=")))) {
(*l_base) = rt_add(rt_add(v_str_lit("(*c_"), (*l_t)), v_str_lit(")"));
}
else {
if (rt_truth(rt_and(rt_cmp(g_ginfunc, v_bool(0), "=="), ({ rt_trace_push("is_func", 0); Val _tr = c_is_func((*l_t)); rt_trace_pop(); _tr; })))) {
(*l_base) = rt_add(rt_add(v_str_lit("v_fn(&fob_c_"), (*l_t)), v_str_lit(")"));
}
else {
(*l_base) = rt_add(v_str_lit("g_"), (*l_t));
}
}
}
}
g_gassign_kind = v_str_lit("var");
g_gassign_name = (*l_base);
g_gassign_raw = (*l_t);
}
}
}
}
while (rt_truth(rt_or(rt_cmp(g_gtok, v_str_lit("."), "=="), rt_cmp(g_gtok, v_str_lit("["), "==")))) {
if (rt_truth(rt_cmp(g_gtok, v_str_lit("."), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
Val *l_mname = rt_box_new(g_gtok);
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
if (rt_truth(rt_cmp(g_gtok, v_str_lit("("), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
Val *l_margs = rt_box_new(v_str_lit(""));
Val *l_mn = rt_box_new(v_int(0));
if (rt_truth(rt_cmp(g_gtok, v_str_lit(")"), "!="))) {
while (rt_truth(v_int(1))) {
if (rt_truth(rt_cmp((*l_mn), v_int(0), ">"))) {
(*l_margs) = rt_add((*l_margs), v_str_lit(", "));
}
(*l_margs) = rt_add((*l_margs), ({ rt_trace_push("parse_expr", 0); Val _tr = c_parse_expr(); rt_trace_pop(); _tr; }));
(*l_mn) = rt_add((*l_mn), v_int(1));
if (rt_truth(rt_cmp(g_gtok, v_str_lit(","), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
}
else {
break;
}
}
}
({ rt_trace_push("expect_op", 0); Val _tr = c_expect_op(v_str_lit(")")); rt_trace_pop(); _tr; });
g_gassign_kind = v_str_lit("");
if (rt_truth(g_gsuper_pending)) {
if (rt_truth(rt_cmp((*l_mn), v_int(0), ">"))) {
g_gargseq = rt_add(g_gargseq, v_int(1));
(*l_base) = rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(v_str_lit("({ Val _ia"), rt_tostr(g_gargseq)), v_str_lit("[")), rt_tostr((*l_mn))), v_str_lit("] = {")), (*l_margs)), v_str_lit("}; rt_supercall(")), (*l_base)), v_str_lit(", \"")), g_gcur_cls), v_str_lit("\", \"")), (*l_mname)), v_str_lit("\", _ia")), rt_tostr(g_gargseq)), v_str_lit(", ")), rt_tostr((*l_mn))), v_str_lit("); })"));
}
else {
(*l_base) = rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(v_str_lit("rt_supercall("), (*l_base)), v_str_lit(", \"")), g_gcur_cls), v_str_lit("\", \"")), (*l_mname)), v_str_lit("\", 0, 0)"));
}
g_gsuper_pending = v_bool(0);
}
else {
if (rt_truth(rt_cmp((*l_mn), v_int(0), ">"))) {
g_gargseq = rt_add(g_gargseq, v_int(1));
(*l_base) = rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(v_str_lit("({ Val _ia"), rt_tostr(g_gargseq)), v_str_lit("[")), rt_tostr((*l_mn))), v_str_lit("] = {")), (*l_margs)), v_str_lit("}; rt_method(")), (*l_base)), v_str_lit(", \"")), (*l_mname)), v_str_lit("\", _ia")), rt_tostr(g_gargseq)), v_str_lit(", ")), rt_tostr((*l_mn))), v_str_lit("); })"));
}
else {
(*l_base) = rt_add(rt_add(rt_add(rt_add(v_str_lit("rt_method("), (*l_base)), v_str_lit(", \"")), (*l_mname)), v_str_lit("\", 0, 0)"));
}
}
}
else {
if (rt_truth(g_gsuper_pending)) {
rt_print_val(v_str_lit("imporc.impor: super 后必须是方法调用")); rt_print_nl();
g_gfail = v_bool(1);
return v_str_lit("");
}
g_gassign_kind = v_str_lit("prop");
g_gassign_base = (*l_base);
g_gassign_prop = (*l_mname);
(*l_base) = rt_add(rt_add(rt_add(rt_add(v_str_lit("rt_getattr("), (*l_base)), v_str_lit(", \"")), (*l_mname)), v_str_lit("\")"));
}
}
else {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
Val *l_iold = rt_box_new((*l_base));
Val *l_start = rt_box_new(v_str_lit("v_null()"));
if (rt_truth(rt_and(rt_cmp(g_gtok, v_str_lit(":"), "!="), rt_cmp(g_gtok, v_str_lit("]"), "!=")))) {
(*l_start) = ({ rt_trace_push("parse_expr", 0); Val _tr = c_parse_expr(); rt_trace_pop(); _tr; });
}
if (rt_truth(rt_cmp(g_gtok, v_str_lit(":"), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
Val *l_send = rt_box_new(v_str_lit("v_null()"));
if (rt_truth(rt_and(rt_cmp(g_gtok, v_str_lit(":"), "!="), rt_cmp(g_gtok, v_str_lit("]"), "!=")))) {
(*l_send) = ({ rt_trace_push("parse_expr", 0); Val _tr = c_parse_expr(); rt_trace_pop(); _tr; });
}
Val *l_sstep = rt_box_new(v_str_lit("v_null()"));
if (rt_truth(rt_cmp(g_gtok, v_str_lit(":"), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
if (rt_truth(rt_cmp(g_gtok, v_str_lit("]"), "!="))) {
(*l_sstep) = ({ rt_trace_push("parse_expr", 0); Val _tr = c_parse_expr(); rt_trace_pop(); _tr; });
}
}
({ rt_trace_push("expect_op", 0); Val _tr = c_expect_op(v_str_lit("]")); rt_trace_pop(); _tr; });
(*l_base) = rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(v_str_lit("rt_slice("), (*l_iold)), v_str_lit(", ")), (*l_start)), v_str_lit(", ")), (*l_send)), v_str_lit(", ")), (*l_sstep)), v_str_lit(")"));
g_gassign_kind = v_str_lit("");
}
else {
({ rt_trace_push("expect_op", 0); Val _tr = c_expect_op(v_str_lit("]")); rt_trace_pop(); _tr; });
(*l_base) = rt_add(rt_add(rt_add(rt_add(v_str_lit("rt_index("), (*l_iold)), v_str_lit(", ")), (*l_start)), v_str_lit(")"));
g_gassign_kind = v_str_lit("idx");
g_gassign_base = (*l_iold);
g_gassign_idx = (*l_start);
}
}
}
return (*l_base);
}
if (rt_truth(rt_cmp(g_gtok, v_str_lit("("), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
Val *l_e = rt_box_new(({ rt_trace_push("parse_expr", 0); Val _tr = c_parse_expr(); rt_trace_pop(); _tr; }));
({ rt_trace_push("expect_op", 0); Val _tr = c_expect_op(v_str_lit(")")); rt_trace_pop(); _tr; });
return (*l_e);
}
rt_print_val(v_str_lit("imporc.impor: 表达式错误 token=")); rt_print_sep(); rt_print_val((*l_t)); rt_print_nl();
g_gfail = v_bool(1);
return v_str_lit("");
return v_null();
}
static Val wrap_c_parse_primary(void *env, Val *args) { (void)env; (void)args; return c_parse_primary(); }
static Fn fob_c_parse_primary = {wrap_c_parse_primary, 0, 0, 0, "parse_primary"};
static Val c_parse_unary() {
if (rt_truth(rt_cmp(g_gtok, v_str_lit("-"), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
return rt_add(rt_add(v_str_lit("rt_neg("), ({ rt_trace_push("parse_unary", 0); Val _tr = c_parse_unary(); rt_trace_pop(); _tr; })), v_str_lit(")"));
}
return ({ rt_trace_push("parse_primary", 0); Val _tr = c_parse_primary(); rt_trace_pop(); _tr; });
return v_null();
}
static Val wrap_c_parse_unary(void *env, Val *args) { (void)env; (void)args; return c_parse_unary(); }
static Fn fob_c_parse_unary = {wrap_c_parse_unary, 0, 0, 0, "parse_unary"};
static Val c_parse_mul() {
Val *l_a = rt_box_new(({ rt_trace_push("parse_unary", 0); Val _tr = c_parse_unary(); rt_trace_pop(); _tr; }));
while (rt_truth(v_int(1))) {
if (rt_truth(rt_cmp(g_gtok, v_str_lit("*"), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
(*l_a) = rt_add(rt_add(rt_add(rt_add(v_str_lit("rt_mul("), (*l_a)), v_str_lit(", ")), ({ rt_trace_push("parse_unary", 0); Val _tr = c_parse_unary(); rt_trace_pop(); _tr; })), v_str_lit(")"));
}
else {
if (rt_truth(rt_cmp(g_gtok, v_str_lit("/"), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
(*l_a) = rt_add(rt_add(rt_add(rt_add(v_str_lit("rt_div("), (*l_a)), v_str_lit(", ")), ({ rt_trace_push("parse_unary", 0); Val _tr = c_parse_unary(); rt_trace_pop(); _tr; })), v_str_lit(")"));
}
else {
if (rt_truth(rt_cmp(g_gtok, v_str_lit("%"), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
(*l_a) = rt_add(rt_add(rt_add(rt_add(v_str_lit("rt_mod("), (*l_a)), v_str_lit(", ")), ({ rt_trace_push("parse_unary", 0); Val _tr = c_parse_unary(); rt_trace_pop(); _tr; })), v_str_lit(")"));
}
else {
break;
}
}
}
}
return (*l_a);
return v_null();
}
static Val wrap_c_parse_mul(void *env, Val *args) { (void)env; (void)args; return c_parse_mul(); }
static Fn fob_c_parse_mul = {wrap_c_parse_mul, 0, 0, 0, "parse_mul"};
static Val c_parse_add() {
Val *l_a = rt_box_new(({ rt_trace_push("parse_mul", 0); Val _tr = c_parse_mul(); rt_trace_pop(); _tr; }));
while (rt_truth(v_int(1))) {
if (rt_truth(rt_cmp(g_gtok, v_str_lit("+"), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
(*l_a) = rt_add(rt_add(rt_add(rt_add(v_str_lit("rt_add("), (*l_a)), v_str_lit(", ")), ({ rt_trace_push("parse_mul", 0); Val _tr = c_parse_mul(); rt_trace_pop(); _tr; })), v_str_lit(")"));
}
else {
if (rt_truth(rt_cmp(g_gtok, v_str_lit("-"), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
(*l_a) = rt_add(rt_add(rt_add(rt_add(v_str_lit("rt_sub("), (*l_a)), v_str_lit(", ")), ({ rt_trace_push("parse_mul", 0); Val _tr = c_parse_mul(); rt_trace_pop(); _tr; })), v_str_lit(")"));
}
else {
break;
}
}
}
return (*l_a);
return v_null();
}
static Val wrap_c_parse_add(void *env, Val *args) { (void)env; (void)args; return c_parse_add(); }
static Fn fob_c_parse_add = {wrap_c_parse_add, 0, 0, 0, "parse_add"};
static Val c_parse_cmp() {
Val *l_a = rt_box_new(({ rt_trace_push("parse_add", 0); Val _tr = c_parse_add(); rt_trace_pop(); _tr; }));
while (rt_truth(v_int(1))) {
if (rt_truth(rt_or(rt_or(rt_or(rt_or(rt_or(rt_cmp(g_gtok, v_str_lit("=="), "=="), rt_cmp(g_gtok, v_str_lit("!="), "==")), rt_cmp(g_gtok, v_str_lit("<"), "==")), rt_cmp(g_gtok, v_str_lit(">"), "==")), rt_cmp(g_gtok, v_str_lit("<="), "==")), rt_cmp(g_gtok, v_str_lit(">="), "==")))) {
Val *l_op = rt_box_new(g_gtok);
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
(*l_a) = rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(v_str_lit("rt_cmp("), (*l_a)), v_str_lit(", ")), ({ rt_trace_push("parse_add", 0); Val _tr = c_parse_add(); rt_trace_pop(); _tr; })), v_str_lit(", \"")), (*l_op)), v_str_lit("\")"));
}
else {
break;
}
}
return (*l_a);
return v_null();
}
static Val wrap_c_parse_cmp(void *env, Val *args) { (void)env; (void)args; return c_parse_cmp(); }
static Fn fob_c_parse_cmp = {wrap_c_parse_cmp, 0, 0, 0, "parse_cmp"};
static Val c_parse_and() {
Val *l_a = rt_box_new(({ rt_trace_push("parse_cmp", 0); Val _tr = c_parse_cmp(); rt_trace_pop(); _tr; }));
while (rt_truth(rt_cmp(g_gtok, v_str_lit("and"), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
(*l_a) = rt_add(rt_add(rt_add(rt_add(v_str_lit("rt_and("), (*l_a)), v_str_lit(", ")), ({ rt_trace_push("parse_cmp", 0); Val _tr = c_parse_cmp(); rt_trace_pop(); _tr; })), v_str_lit(")"));
}
return (*l_a);
return v_null();
}
static Val wrap_c_parse_and(void *env, Val *args) { (void)env; (void)args; return c_parse_and(); }
static Fn fob_c_parse_and = {wrap_c_parse_and, 0, 0, 0, "parse_and"};
static Val c_parse_or() {
Val *l_a = rt_box_new(({ rt_trace_push("parse_and", 0); Val _tr = c_parse_and(); rt_trace_pop(); _tr; }));
while (rt_truth(rt_cmp(g_gtok, v_str_lit("or"), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
(*l_a) = rt_add(rt_add(rt_add(rt_add(v_str_lit("rt_or("), (*l_a)), v_str_lit(", ")), ({ rt_trace_push("parse_and", 0); Val _tr = c_parse_and(); rt_trace_pop(); _tr; })), v_str_lit(")"));
}
return (*l_a);
return v_null();
}
static Val wrap_c_parse_or(void *env, Val *args) { (void)env; (void)args; return c_parse_or(); }
static Fn fob_c_parse_or = {wrap_c_parse_or, 0, 0, 0, "parse_or"};
static Val c_parse_expr() {
return ({ rt_trace_push("parse_or", 0); Val _tr = c_parse_or(); rt_trace_pop(); _tr; });
return v_null();
}
static Val wrap_c_parse_expr(void *env, Val *args) { (void)env; (void)args; return c_parse_expr(); }
static Fn fob_c_parse_expr = {wrap_c_parse_expr, 0, 0, 0, "parse_expr"};
static Val c_add_var(Val p0) {
Val *l_name = rt_box_new(p0);
if (rt_truth(rt_cmp(({ rt_trace_push("strfind", 0); Val _tr = c_strfind(rt_add(v_str_lit("|"), g_gvars), rt_add(rt_add(v_str_lit("|"), (*l_name)), v_str_lit("|"))); rt_trace_pop(); _tr; }), v_int(0), "<"))) {
g_gvars = rt_add(rt_add(g_gvars, (*l_name)), v_str_lit("|"));
}
return v_bool(1);
return v_null();
}
static Val wrap_c_add_var(void *env, Val *args) { (void)env; (void)args; return c_add_var(args[0]); }
static Fn fob_c_add_var = {wrap_c_add_var, 0, 0, 1, "add_var"};
static Val c_gen_block() {
if (rt_truth(rt_cmp(g_gtype, v_int(6), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
}
while (rt_truth(rt_and(rt_cmp(g_gtype, v_int(5), "!="), rt_not(g_gfail)))) {
if (rt_truth(rt_cmp(g_gtype, v_int(7), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
return v_bool(1);
}
({ rt_trace_push("gen_stmt", 0); Val _tr = c_gen_stmt(); rt_trace_pop(); _tr; });
}
return v_bool(1);
return v_null();
}
static Val wrap_c_gen_block(void *env, Val *args) { (void)env; (void)args; return c_gen_block(); }
static Fn fob_c_gen_block = {wrap_c_gen_block, 0, 0, 0, "gen_block"};
static Val c_gen_block2() {
if (rt_truth(rt_and(rt_cmp(g_gtok, v_str_lit("{"), "=="), rt_cmp(g_gtype, v_int(4), "==")))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
while (rt_truth(rt_and(rt_and(rt_cmp(g_gtype, v_int(5), "!="), rt_not(g_gfail)), rt_not(rt_and(rt_cmp(g_gtok, v_str_lit("}"), "=="), rt_cmp(g_gtype, v_int(4), "==")))))) {
if (rt_truth(rt_or(rt_cmp(g_gtype, v_int(6), "=="), rt_cmp(g_gtype, v_int(7), "==")))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
}
({ rt_trace_push("gen_stmt", 0); Val _tr = c_gen_stmt(); rt_trace_pop(); _tr; });
}
if (rt_truth(rt_and(rt_cmp(g_gtok, v_str_lit("}"), "=="), rt_cmp(g_gtype, v_int(4), "==")))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
}
}
else {
({ rt_trace_push("gen_block", 0); Val _tr = c_gen_block(); rt_trace_pop(); _tr; });
}
return v_bool(1);
return v_null();
}
static Val wrap_c_gen_block2(void *env, Val *args) { (void)env; (void)args; return c_gen_block2(); }
static Fn fob_c_gen_block2 = {wrap_c_gen_block2, 0, 0, 0, "gen_block2"};
static Val c_gen_stmt() {
Val *l_t = rt_box_new(g_gtok);
if (rt_truth(rt_cmp(g_gtype, v_int(6), "=="))) {
rt_print_val(v_str_lit("imporc.impor: 意外的缩进开始 pos=")); rt_print_sep(); rt_print_val(g_gpos); rt_print_sep(); rt_print_val(v_str_lit(" tok=[")); rt_print_sep(); rt_print_val(g_gtok); rt_print_sep(); rt_print_val(v_str_lit("] type=")); rt_print_sep(); rt_print_val(g_gtype); rt_print_sep(); rt_print_val(v_str_lit(" ind=")); rt_print_sep(); rt_print_val(g_gindstack); rt_print_nl();
g_gfail = v_bool(1);
return v_bool(0);
}
if (rt_truth(rt_cmp(g_gtype, v_int(7), "=="))) {
rt_print_val(v_str_lit("imporc.impor: 意外的缩进结束 pos=")); rt_print_sep(); rt_print_val(g_gpos); rt_print_sep(); rt_print_val(v_str_lit(" tok=[")); rt_print_sep(); rt_print_val(g_gtok); rt_print_sep(); rt_print_val(v_str_lit("] type=")); rt_print_sep(); rt_print_val(g_gtype); rt_print_nl();
g_gfail = v_bool(1);
return v_bool(0);
}
if (rt_truth(rt_cmp((*l_t), v_str_lit("var"), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
Val *l_name = rt_box_new(g_gtok);
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
Val *l_val = rt_box_new(v_str_lit("v_null()"));
if (rt_truth(rt_cmp(g_gtok, v_str_lit("="), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
(*l_val) = ({ rt_trace_push("parse_expr", 0); Val _tr = c_parse_expr(); rt_trace_pop(); _tr; });
}
if (rt_truth(g_ginfunc)) {
g_glocals = rt_add(rt_add(g_glocals, (*l_name)), v_str_lit("|"));
({ rt_trace_push("out", 0); Val _tr = c_out(rt_add(rt_add(rt_add(rt_add(v_str_lit("Val *l_"), (*l_name)), v_str_lit(" = rt_box_new(")), (*l_val)), v_str_lit(");\n"))); rt_trace_pop(); _tr; });
}
else {
({ rt_trace_push("add_var", 0); Val _tr = c_add_var((*l_name)); rt_trace_pop(); _tr; });
({ rt_trace_push("out", 0); Val _tr = c_out(rt_add(rt_add(rt_add(rt_add(v_str_lit("g_"), (*l_name)), v_str_lit(" = ")), (*l_val)), v_str_lit(";\n"))); rt_trace_pop(); _tr; });
}
return v_bool(1);
}
if (rt_truth(rt_cmp((*l_t), v_str_lit("set"), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
Val *l_name = rt_box_new(g_gtok);
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
if (rt_truth(rt_cmp(g_gtok, v_str_lit("["), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
Val *l_sidx = rt_box_new(({ rt_trace_push("parse_expr", 0); Val _tr = c_parse_expr(); rt_trace_pop(); _tr; }));
({ rt_trace_push("expect_op", 0); Val _tr = c_expect_op(v_str_lit("]")); rt_trace_pop(); _tr; });
({ rt_trace_push("expect_op", 0); Val _tr = c_expect_op(v_str_lit("=")); rt_trace_pop(); _tr; });
Val *l_sval = rt_box_new(({ rt_trace_push("parse_expr", 0); Val _tr = c_parse_expr(); rt_trace_pop(); _tr; }));
Val *l_sexpr = rt_box_new(v_str_lit(""));
if (rt_truth(rt_or(rt_cmp((*l_name), v_str_lit("self"), "=="), rt_cmp((*l_name), v_str_lit("this"), "==")))) {
(*l_sexpr) = v_str_lit("self");
}
else {
if (rt_truth(rt_and(g_ginfunc, rt_cmp(({ rt_trace_push("strfind", 0); Val _tr = c_strfind(rt_add(v_str_lit("|"), g_glocals), rt_add(rt_add(v_str_lit("|"), (*l_name)), v_str_lit("|"))); rt_trace_pop(); _tr; }), v_int(0), ">=")))) {
(*l_sexpr) = ({ rt_trace_push("var_ref", 0); Val _tr = c_var_ref((*l_name)); rt_trace_pop(); _tr; });
}
else {
if (rt_truth(rt_not(g_ginfunc))) {
({ rt_trace_push("add_var", 0); Val _tr = c_add_var((*l_name)); rt_trace_pop(); _tr; });
}
(*l_sexpr) = rt_add(v_str_lit("g_"), (*l_name));
}
}
({ rt_trace_push("out", 0); Val _tr = c_out(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(v_str_lit("rt_arr_set("), (*l_sexpr)), v_str_lit(", ")), (*l_sidx)), v_str_lit(", ")), (*l_sval)), v_str_lit(");\n"))); rt_trace_pop(); _tr; });
}
else {
if (rt_truth(rt_cmp(g_gtok, v_str_lit("."), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
Val *l_prop = rt_box_new(g_gtok);
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
({ rt_trace_push("expect_op", 0); Val _tr = c_expect_op(v_str_lit("=")); rt_trace_pop(); _tr; });
Val *l_pval = rt_box_new(({ rt_trace_push("parse_expr", 0); Val _tr = c_parse_expr(); rt_trace_pop(); _tr; }));
Val *l_oexpr = rt_box_new(v_str_lit(""));
if (rt_truth(rt_or(rt_cmp((*l_name), v_str_lit("self"), "=="), rt_cmp((*l_name), v_str_lit("this"), "==")))) {
(*l_oexpr) = v_str_lit("self");
}
else {
if (rt_truth(rt_and(g_ginfunc, rt_cmp(({ rt_trace_push("strfind", 0); Val _tr = c_strfind(rt_add(v_str_lit("|"), g_glocals), rt_add(rt_add(v_str_lit("|"), (*l_name)), v_str_lit("|"))); rt_trace_pop(); _tr; }), v_int(0), ">=")))) {
(*l_oexpr) = rt_add(({ rt_trace_push("var_ref", 0); Val _tr = c_var_ref((*l_name)); rt_trace_pop(); _tr; }), v_str_lit(".o"));
}
else {
if (rt_truth(rt_not(g_ginfunc))) {
({ rt_trace_push("add_var", 0); Val _tr = c_add_var((*l_name)); rt_trace_pop(); _tr; });
}
(*l_oexpr) = rt_add(rt_add(v_str_lit("g_"), (*l_name)), v_str_lit(".o"));
}
}
({ rt_trace_push("out", 0); Val _tr = c_out(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(v_str_lit("rt_objset("), (*l_oexpr)), v_str_lit(", \"")), (*l_prop)), v_str_lit("\", ")), (*l_pval)), v_str_lit(");\n"))); rt_trace_pop(); _tr; });
}
else {
if (rt_truth(rt_cmp(g_gtok, v_str_lit("="), "!="))) {
rt_print_val(v_str_lit("imporc.impor: set 缺 =")); rt_print_nl();
rt_exit(v_int(1));
}
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
Val *l_val = rt_box_new(({ rt_trace_push("parse_expr", 0); Val _tr = c_parse_expr(); rt_trace_pop(); _tr; }));
if (rt_truth(rt_and(g_ginfunc, rt_cmp(({ rt_trace_push("strfind", 0); Val _tr = c_strfind(rt_add(v_str_lit("|"), g_glocals), rt_add(rt_add(v_str_lit("|"), (*l_name)), v_str_lit("|"))); rt_trace_pop(); _tr; }), v_int(0), ">=")))) {
({ rt_trace_push("out", 0); Val _tr = c_out(rt_add(rt_add(rt_add(({ rt_trace_push("var_ref", 0); Val _tr = c_var_ref((*l_name)); rt_trace_pop(); _tr; }), v_str_lit(" = ")), (*l_val)), v_str_lit(";\n"))); rt_trace_pop(); _tr; });
}
else {
if (rt_truth(rt_not(g_ginfunc))) {
({ rt_trace_push("add_var", 0); Val _tr = c_add_var((*l_name)); rt_trace_pop(); _tr; });
}
({ rt_trace_push("out", 0); Val _tr = c_out(rt_add(rt_add(rt_add(rt_add(v_str_lit("g_"), (*l_name)), v_str_lit(" = ")), (*l_val)), v_str_lit(";\n"))); rt_trace_pop(); _tr; });
}
}
}
return v_bool(1);
}
if (rt_truth(rt_cmp((*l_t), v_str_lit("print"), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
({ rt_trace_push("expect_op", 0); Val _tr = c_expect_op(v_str_lit("(")); rt_trace_pop(); _tr; });
Val *l_narg = rt_box_new(v_int(0));
if (rt_truth(rt_cmp(g_gtok, v_str_lit(")"), "!="))) {
while (rt_truth(v_int(1))) {
Val *l_e = rt_box_new(({ rt_trace_push("parse_expr", 0); Val _tr = c_parse_expr(); rt_trace_pop(); _tr; }));
if (rt_truth(rt_cmp((*l_narg), v_int(0), ">"))) {
({ rt_trace_push("out", 0); Val _tr = c_out(v_str_lit("rt_print_sep(); ")); rt_trace_pop(); _tr; });
}
({ rt_trace_push("out", 0); Val _tr = c_out(rt_add(rt_add(v_str_lit("rt_print_val("), (*l_e)), v_str_lit("); "))); rt_trace_pop(); _tr; });
(*l_narg) = rt_add((*l_narg), v_int(1));
if (rt_truth(rt_cmp(g_gtok, v_str_lit(","), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
}
else {
break;
}
}
}
({ rt_trace_push("expect_op", 0); Val _tr = c_expect_op(v_str_lit(")")); rt_trace_pop(); _tr; });
({ rt_trace_push("out", 0); Val _tr = c_out(v_str_lit("rt_print_nl();\n")); rt_trace_pop(); _tr; });
return v_bool(1);
}
if (rt_truth(rt_cmp((*l_t), v_str_lit("enum"), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
Val *l_ename = rt_box_new(g_gtok);
if (rt_truth(rt_cmp(g_gtype, v_int(1), "!="))) {
rt_print_val(v_str_lit("imporc.impor: enum 后需要类型名")); rt_print_nl();
g_gfail = v_bool(1);
return v_bool(0);
}
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
if (rt_truth(rt_cmp(g_gtok, v_str_lit(":"), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
}
Val *l_ebrace = rt_box_new(v_bool(0));
if (rt_truth(rt_cmp(g_gtok, v_str_lit("{"), "=="))) {
(*l_ebrace) = v_bool(1);
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
}
else if (rt_truth(rt_cmp(g_gtype, v_int(6), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
}
else {
rt_print_val(v_str_lit("imporc.impor: enum 需要缩进块或大括号块")); rt_print_nl();
g_gfail = v_bool(1);
return v_bool(0);
}
Val *l_edefault = rt_box_new(v_int(0));
Val *l_ecount = rt_box_new(v_int(0));
while (rt_truth(rt_not(g_gfail))) {
if (rt_truth(rt_and((*l_ebrace), rt_cmp(g_gtok, v_str_lit("}"), "==")))) {
break;
}
if (rt_truth(rt_and(rt_not((*l_ebrace)), rt_or(rt_cmp(g_gtype, v_int(7), "=="), rt_cmp(g_gtype, v_int(5), "=="))))) {
break;
}
if (rt_truth(rt_cmp(g_gtype, v_int(6), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
}
else {
if (rt_truth(rt_or(rt_cmp(g_gtok, v_str_lit(","), "=="), rt_cmp(g_gtok, v_str_lit(";"), "==")))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
}
else {
if (rt_truth(rt_cmp(g_gtype, v_int(1), "!="))) {
rt_print_val(v_str_lit("imporc.impor: enum 成员名应为标识符")); rt_print_nl();
g_gfail = v_bool(1);
return v_bool(0);
}
Val *l_emem = rt_box_new(g_gtok);
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
Val *l_ev = rt_box_new((*l_edefault));
if (rt_truth(rt_cmp(g_gtok, v_str_lit("="), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
Val *l_eneg = rt_box_new(v_bool(0));
if (rt_truth(rt_cmp(g_gtok, v_str_lit("-"), "=="))) {
(*l_eneg) = v_bool(1);
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
}
if (rt_truth(rt_cmp(g_gtype, v_int(2), "!="))) {
rt_print_val(v_str_lit("imporc.impor: enum 成员值必须是整数字面量")); rt_print_nl();
g_gfail = v_bool(1);
return v_bool(0);
}
(*l_ev) = rt_to_num(g_gtok);
if (rt_truth((*l_eneg))) {
(*l_ev) = rt_sub(v_int(0), (*l_ev));
}
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
}
Val *l_evname = rt_box_new(rt_add(rt_add(rt_add(v_str_lit("__enum_"), (*l_ename)), v_str_lit("_")), (*l_emem)));
if (rt_truth(g_ginfunc)) {
g_glocals = rt_add(rt_add(g_glocals, (*l_evname)), v_str_lit("|"));
({ rt_trace_push("out", 0); Val _tr = c_out(rt_add(rt_add(rt_add(rt_add(v_str_lit("Val *l_"), (*l_evname)), v_str_lit(" = rt_box_new(v_int(")), rt_tostr((*l_ev))), v_str_lit("));\n"))); rt_trace_pop(); _tr; });
}
else {
({ rt_trace_push("add_var", 0); Val _tr = c_add_var((*l_evname)); rt_trace_pop(); _tr; });
({ rt_trace_push("out", 0); Val _tr = c_out(rt_add(rt_add(rt_add(rt_add(v_str_lit("g_"), (*l_evname)), v_str_lit(" = v_int(")), rt_tostr((*l_ev))), v_str_lit(");\n"))); rt_trace_pop(); _tr; });
}
(*l_edefault) = rt_add((*l_ev), v_int(1));
(*l_ecount) = rt_add((*l_ecount), v_int(1));
}
}
}
if (rt_truth(rt_cmp((*l_ecount), v_int(0), "=="))) {
rt_print_val(v_str_lit("imporc.impor: enum 至少需要一个成员")); rt_print_nl();
g_gfail = v_bool(1);
return v_bool(0);
}
g_genum_types = rt_add(rt_add(g_genum_types, (*l_ename)), v_str_lit("|"));
if (rt_truth((*l_ebrace))) {
if (rt_truth(rt_cmp(g_gtok, v_str_lit("}"), "!="))) {
rt_print_val(v_str_lit("imporc.impor: enum 缺少 } 闭合")); rt_print_nl();
g_gfail = v_bool(1);
return v_bool(0);
}
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
}
return v_bool(1);
}
if (rt_truth(rt_cmp((*l_t), v_str_lit("struct"), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
Val *l_stname = rt_box_new(g_gtok);
if (rt_truth(rt_cmp(g_gtype, v_int(1), "!="))) {
rt_print_val(v_str_lit("imporc.impor: struct 后需要结构体名")); rt_print_nl();
g_gfail = v_bool(1);
return v_bool(0);
}
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
Val *l_sbrace = rt_box_new(v_bool(0));
if (rt_truth(rt_cmp(g_gtok, v_str_lit("{"), "=="))) {
(*l_sbrace) = v_bool(1);
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
}
else if (rt_truth(rt_cmp(g_gtype, v_int(6), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
}
else {
rt_print_val(v_str_lit("imporc.impor: struct 需要缩进块或大括号块")); rt_print_nl();
g_gfail = v_bool(1);
return v_bool(0);
}
Val *l_sfiedefs = rt_box_new(v_str_lit(""));
Val *l_sinit = rt_box_new(v_str_lit(""));
Val *l_snf = rt_box_new(v_int(0));
while (rt_truth(rt_not(g_gfail))) {
if (rt_truth(rt_and((*l_sbrace), rt_cmp(g_gtok, v_str_lit("}"), "==")))) {
break;
}
if (rt_truth(rt_and(rt_not((*l_sbrace)), rt_or(rt_cmp(g_gtype, v_int(7), "=="), rt_cmp(g_gtype, v_int(5), "=="))))) {
break;
}
if (rt_truth(rt_cmp(g_gtok, v_str_lit(";"), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
}
else {
if (rt_truth(rt_cmp(g_gtok, v_str_lit("var"), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
Val *l_sfname = rt_box_new(g_gtok);
if (rt_truth(rt_cmp(g_gtype, v_int(1), "!="))) {
rt_print_val(v_str_lit("imporc.impor: struct 字段名应为标识符")); rt_print_nl();
g_gfail = v_bool(1);
return v_bool(0);
}
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
Val *l_sfval = rt_box_new(v_str_lit("v_null()"));
if (rt_truth(rt_cmp(g_gtok, v_str_lit("="), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
(*l_sfval) = ({ rt_trace_push("parse_expr", 0); Val _tr = c_parse_expr(); rt_trace_pop(); _tr; });
}
(*l_sfiedefs) = rt_add(rt_add(rt_add(rt_add(rt_add((*l_sfiedefs), v_str_lit("rt_objset(o, \"")), (*l_sfname)), v_str_lit("\", ")), (*l_sfval)), v_str_lit(");\n"));
(*l_sinit) = rt_add(rt_add(rt_add(rt_add(rt_add((*l_sinit), v_str_lit("rt_objset(self, \"")), (*l_sfname)), v_str_lit("\", args[")), rt_tostr((*l_snf))), v_str_lit("]);\n"));
(*l_snf) = rt_add((*l_snf), v_int(1));
}
else {
rt_print_val(v_str_lit("imporc.impor: struct 体内只支持 var 字段 [")); rt_print_sep(); rt_print_val(g_gtok); rt_print_sep(); rt_print_val(v_str_lit("]")); rt_print_nl();
g_gfail = v_bool(1);
return v_bool(0);
}
}
}
if (rt_truth((*l_sbrace))) {
({ rt_trace_push("expect_op", 0); Val _tr = c_expect_op(v_str_lit("}")); rt_trace_pop(); _tr; });
}
else {
if (rt_truth(rt_cmp(g_gtype, v_int(7), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
}
}
if (rt_truth(rt_cmp((*l_snf), v_int(0), "=="))) {
rt_print_val(v_str_lit("imporc.impor: struct 至少需要一个字段")); rt_print_nl();
g_gfail = v_bool(1);
return v_bool(0);
}
g_gfuncs = rt_add(rt_add(rt_add(rt_add(rt_add(g_gfuncs, v_str_lit("static Val m_")), (*l_stname)), v_str_lit("___init__(Obj *self, Val *args) {\n")), (*l_sinit)), v_str_lit("return v_null();\n}\n"));
Val *l_sfip = rt_box_new(v_str_lit("0"));
if (rt_truth(rt_cmp(rt_len((*l_sfiedefs)), v_int(0), ">"))) {
g_gfuncs = rt_add(rt_add(rt_add(rt_add(rt_add(g_gfuncs, v_str_lit("static void fi_")), (*l_stname)), v_str_lit("(Obj *o) {\n")), (*l_sfiedefs)), v_str_lit("}\n"));
(*l_sfip) = rt_add(v_str_lit("fi_"), (*l_stname));
}
g_gfuncs = rt_add(rt_add(rt_add(rt_add(rt_add(g_gfuncs, v_str_lit("static const Method mt_")), (*l_stname)), v_str_lit("[] = {{\"__init__\", m_")), (*l_stname)), v_str_lit("___init__}, {0, 0}};\n"));
g_gfuncs = rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(g_gfuncs, v_str_lit("static ClassInfo ci_")), (*l_stname)), v_str_lit(" = {\"")), (*l_stname)), v_str_lit("\", \"")), (*l_stname)), v_str_lit("\", \"\", mt_")), (*l_stname)), v_str_lit(", ")), (*l_sfip)), v_str_lit("};\n"));
({ rt_trace_push("out", 0); Val _tr = c_out(rt_add(rt_add(v_str_lit("reg_class(&ci_"), (*l_stname)), v_str_lit(");\n"))); rt_trace_pop(); _tr; });
return v_bool(1);
}
if (rt_truth(rt_cmp((*l_t), v_str_lit("class"), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
Val *l_clsname = rt_box_new(g_gtok);
if (rt_truth(rt_cmp(g_gtype, v_int(1), "!="))) {
rt_print_val(v_str_lit("imporc.impor: class 后需要类名")); rt_print_nl();
g_gfail = v_bool(1);
return v_bool(0);
}
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
Val *l_basename = rt_box_new(v_str_lit(""));
if (rt_truth(rt_cmp(g_gtok, v_str_lit("("), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
(*l_basename) = g_gtok;
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
({ rt_trace_push("expect_op", 0); Val _tr = c_expect_op(v_str_lit(")")); rt_trace_pop(); _tr; });
}
if (rt_truth(rt_cmp(g_gtype, v_int(6), "!="))) {
rt_print_val(v_str_lit("imporc.impor: class 需要缩进块")); rt_print_nl();
g_gfail = v_bool(1);
return v_bool(0);
}
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
Val *l_mtlist = rt_box_new(v_str_lit(""));
Val *l_fiedefs = rt_box_new(v_str_lit(""));
g_gcur_cls = (*l_clsname);
while (rt_truth(rt_and(rt_and(rt_cmp(g_gtype, v_int(7), "!="), rt_cmp(g_gtype, v_int(5), "!=")), rt_not(g_gfail)))) {
if (rt_truth(rt_or(rt_cmp(g_gtok, v_str_lit("func"), "=="), rt_cmp(g_gtok, v_str_lit("def"), "==")))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
Val *l_mname = rt_box_new(g_gtok);
if (rt_truth(rt_cmp(g_gtype, v_int(1), "!="))) {
rt_print_val(v_str_lit("imporc.impor: 方法名应为标识符")); rt_print_nl();
g_gfail = v_bool(1);
return v_bool(0);
}
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
({ rt_trace_push("expect_op", 0); Val _tr = c_expect_op(v_str_lit("(")); rt_trace_pop(); _tr; });
Val *l_mplocals = rt_box_new(v_str_lit("self|"));
g_gparams = v_str_lit("");
Val *l_nparam = rt_box_new(v_int(0));
if (rt_truth(rt_cmp(g_gtok, v_str_lit(")"), "!="))) {
while (rt_truth(v_int(1))) {
Val *l_pname = rt_box_new(g_gtok);
if (rt_truth(rt_cmp(g_gtype, v_int(1), "!="))) {
rt_print_val(v_str_lit("imporc.impor: 参数名应为标识符")); rt_print_nl();
g_gfail = v_bool(1);
return v_bool(0);
}
g_gparams = rt_add(rt_add(g_gparams, (*l_pname)), v_str_lit(";"));
(*l_mplocals) = rt_add(rt_add((*l_mplocals), (*l_pname)), v_str_lit("|"));
(*l_nparam) = rt_add((*l_nparam), v_int(1));
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
if (rt_truth(rt_cmp(g_gtok, v_str_lit(","), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
}
else {
break;
}
}
}
({ rt_trace_push("expect_op", 0); Val _tr = c_expect_op(v_str_lit(")")); rt_trace_pop(); _tr; });
g_glocals = (*l_mplocals);
g_gfuncs = rt_add(rt_add(rt_add(rt_add(rt_add(g_gfuncs, v_str_lit("static Val m_")), (*l_clsname)), v_str_lit("_")), (*l_mname)), v_str_lit("(Obj *self, Val *args) {\n"));
g_gmethod = v_bool(1);
g_ginfunc = v_bool(1);
({ rt_trace_push("gen_block", 0); Val _tr = c_gen_block(); rt_trace_pop(); _tr; });
g_ginfunc = v_bool(0);
g_gmethod = v_bool(0);
g_gfuncs = rt_add(g_gfuncs, v_str_lit("return v_null();\n}\n"));
(*l_mtlist) = rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add((*l_mtlist), v_str_lit("{\"")), (*l_mname)), v_str_lit("\", m_")), (*l_clsname)), v_str_lit("_")), (*l_mname)), v_str_lit("}, "));
}
else {
if (rt_truth(rt_cmp(g_gtok, v_str_lit("var"), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
Val *l_fname2 = rt_box_new(g_gtok);
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
Val *l_fval = rt_box_new(v_str_lit("v_null()"));
if (rt_truth(rt_cmp(g_gtok, v_str_lit("="), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
(*l_fval) = ({ rt_trace_push("parse_expr", 0); Val _tr = c_parse_expr(); rt_trace_pop(); _tr; });
}
(*l_fiedefs) = rt_add(rt_add(rt_add(rt_add(rt_add((*l_fiedefs), v_str_lit("rt_objset(o, \"")), (*l_fname2)), v_str_lit("\", ")), (*l_fval)), v_str_lit(");\n"));
}
else {
rt_print_val(v_str_lit("imporc.impor: class 体内未知语句 [")); rt_print_sep(); rt_print_val(g_gtok); rt_print_sep(); rt_print_val(v_str_lit("]")); rt_print_nl();
g_gfail = v_bool(1);
return v_bool(0);
}
}
}
if (rt_truth(rt_cmp(g_gtype, v_int(7), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
}
Val *l_fip = rt_box_new(v_str_lit("0"));
if (rt_truth(rt_cmp(rt_len((*l_fiedefs)), v_int(0), ">"))) {
g_gfuncs = rt_add(rt_add(rt_add(rt_add(rt_add(g_gfuncs, v_str_lit("static void fi_")), (*l_clsname)), v_str_lit("(Obj *o) {\n")), (*l_fiedefs)), v_str_lit("}\n"));
(*l_fip) = rt_add(v_str_lit("fi_"), (*l_clsname));
}
g_gfuncs = rt_add(rt_add(rt_add(rt_add(rt_add(g_gfuncs, v_str_lit("static const Method mt_")), (*l_clsname)), v_str_lit("[] = {")), (*l_mtlist)), v_str_lit("{0, 0}};\n"));
g_gfuncs = rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(g_gfuncs, v_str_lit("static ClassInfo ci_")), (*l_clsname)), v_str_lit(" = {\"")), (*l_clsname)), v_str_lit("\", \"")), (*l_clsname)), v_str_lit("\", \"")), (*l_basename)), v_str_lit("\", mt_")), (*l_clsname)), v_str_lit(", ")), (*l_fip)), v_str_lit("};\n"));
({ rt_trace_push("out", 0); Val _tr = c_out(rt_add(rt_add(v_str_lit("reg_class(&ci_"), (*l_clsname)), v_str_lit(");\n"))); rt_trace_pop(); _tr; });
return v_bool(1);
}
if (rt_truth(rt_and(rt_or(rt_cmp((*l_t), v_str_lit("func"), "=="), rt_cmp((*l_t), v_str_lit("def"), "==")), g_ginfunc))) {
if (rt_truth(g_gmethod)) {
rt_print_val(v_str_lit("imporc.impor: 方法内暂不支持嵌套函数")); rt_print_nl();
g_gfail = v_bool(1);
return v_bool(0);
}
if (rt_truth(rt_cmp(g_gclosure_depth, v_int(0), ">"))) {
rt_print_val(v_str_lit("imporc.impor: 暂不支持两层以上嵌套函数")); rt_print_nl();
g_gfail = v_bool(1);
return v_bool(0);
}
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
Val *l_cfn = rt_box_new(g_gtok);
if (rt_truth(rt_cmp(g_gtype, v_int(1), "!="))) {
rt_print_val(v_str_lit("imporc.impor: def 后需要函数名")); rt_print_nl();
g_gfail = v_bool(1);
return v_bool(0);
}
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
({ rt_trace_push("expect_op", 0); Val _tr = c_expect_op(v_str_lit("(")); rt_trace_pop(); _tr; });
Val *l_cparams = rt_box_new(v_str_lit(""));
Val *l_cplist = rt_box_new(v_str_lit(""));
Val *l_cnp = rt_box_new(v_int(0));
Val *l_outer_locals = rt_box_new(g_glocals);
g_glocals = v_str_lit("");
if (rt_truth(rt_cmp(g_gtok, v_str_lit(")"), "!="))) {
while (rt_truth(v_int(1))) {
Val *l_cp = rt_box_new(g_gtok);
if (rt_truth(rt_cmp(g_gtype, v_int(1), "!="))) {
rt_print_val(v_str_lit("imporc.impor: 参数名应为标识符")); rt_print_nl();
g_gfail = v_bool(1);
return v_bool(0);
}
if (rt_truth(rt_cmp((*l_cnp), v_int(0), ">"))) {
(*l_cparams) = rt_add((*l_cparams), v_str_lit(", "));
}
(*l_cparams) = rt_add(rt_add((*l_cparams), v_str_lit("Val l_")), (*l_cp));
(*l_cplist) = rt_add(rt_add((*l_cplist), (*l_cp)), v_str_lit("|"));
g_glocals = rt_add(rt_add(g_glocals, (*l_cp)), v_str_lit("|"));
(*l_cnp) = rt_add((*l_cnp), v_int(1));
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
if (rt_truth(rt_cmp(g_gtok, v_str_lit(","), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
}
else {
break;
}
}
}
({ rt_trace_push("expect_op", 0); Val _tr = c_expect_op(v_str_lit(")")); rt_trace_pop(); _tr; });
Val *l_caps = rt_box_new((*l_outer_locals));
Val *l_capn = rt_box_new(({ rt_trace_push("env_count", 0); Val _tr = c_env_count((*l_caps)); rt_trace_pop(); _tr; }));
g_gclos_seq = rt_add(g_gclos_seq, v_int(1));
Val *l_cname = rt_box_new(rt_add(v_str_lit("clo"), rt_tostr(g_gclos_seq)));
g_gdecls = rt_add(rt_add(rt_add(g_gdecls, v_str_lit("static Val ")), (*l_cname)), v_str_lit("(void *_env, Val *_args);\n"));
Val *l_saved_funcs = rt_box_new(g_gfuncs);
Val *l_saved_caps = rt_box_new(g_gcaps);
Val *l_saved_method = rt_box_new(g_gmethod);
g_gfuncs = v_str_lit("");
g_gcaps = (*l_caps);
g_gmethod = v_bool(0);
g_gclosure_depth = rt_add(g_gclosure_depth, v_int(1));
g_gfuncs = rt_add(rt_add(rt_add(g_gfuncs, v_str_lit("/* Impor 闭包：")), (*l_cfn)), v_str_lit(" */\n"));
g_gfuncs = rt_add(rt_add(rt_add(g_gfuncs, v_str_lit("static Val ")), (*l_cname)), v_str_lit("(void *_env, Val *_args) {\n"));
Val *l_ci = rt_box_new(v_int(0));
Val *l_ccur = rt_box_new(v_str_lit(""));
Val *l_k = rt_box_new(v_int(0));
while (rt_truth(rt_cmp((*l_k), rt_len((*l_caps)), "<"))) {
if (rt_truth(rt_cmp(rt_strcut((*l_caps), (*l_k), rt_add((*l_k), v_int(1))), v_str_lit("|"), "=="))) {
if (rt_truth(rt_cmp(rt_len((*l_ccur)), v_int(0), ">"))) {
g_gfuncs = rt_add(rt_add(rt_add(rt_add(rt_add(g_gfuncs, v_str_lit("Val *c_")), (*l_ccur)), v_str_lit(" = ((Val **)_env)[")), rt_tostr((*l_ci))), v_str_lit("];\n"));
(*l_ci) = rt_add((*l_ci), v_int(1));
}
(*l_ccur) = v_str_lit("");
}
else {
(*l_ccur) = rt_add((*l_ccur), rt_strcut((*l_caps), (*l_k), rt_add((*l_k), v_int(1))));
}
(*l_k) = rt_add((*l_k), v_int(1));
}
Val *l_pi = rt_box_new(v_int(0));
while (rt_truth(rt_cmp((*l_pi), (*l_cnp), "<"))) {
g_gfuncs = rt_add(rt_add(rt_add(rt_add(rt_add(g_gfuncs, v_str_lit("Val *l_")), ({ rt_trace_push("list_at", 0); Val _tr = c_list_at((*l_cplist), (*l_pi)); rt_trace_pop(); _tr; })), v_str_lit(" = rt_box_new(_args[")), rt_tostr((*l_pi))), v_str_lit("]);\n"));
(*l_pi) = rt_add((*l_pi), v_int(1));
}
({ rt_trace_push("gen_block", 0); Val _tr = c_gen_block(); rt_trace_pop(); _tr; });
g_gfuncs = rt_add(g_gfuncs, v_str_lit("return v_null();\n}\n"));
g_gclosure = rt_add(g_gclosure, g_gfuncs);
g_gfuncs = (*l_saved_funcs);
g_gcaps = (*l_saved_caps);
g_gmethod = (*l_saved_method);
g_gclosure_depth = rt_sub(g_gclosure_depth, v_int(1));
g_glocals = rt_add(rt_add((*l_outer_locals), (*l_cfn)), v_str_lit("|"));
if (rt_truth(rt_cmp((*l_capn), v_int(0), ">"))) {
g_gfuncs = rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(g_gfuncs, v_str_lit("Val *_env")), rt_tostr(g_gclos_seq)), v_str_lit("[")), rt_tostr((*l_capn))), v_str_lit("] = {")), ({ rt_trace_push("env_ptrs", 0); Val _tr = c_env_ptrs((*l_caps)); rt_trace_pop(); _tr; })), v_str_lit("};\n"));
g_gfuncs = rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(g_gfuncs, v_str_lit("Val l_")), (*l_cfn)), v_str_lit("_v = rt_fn_new(")), (*l_cname)), v_str_lit(", _env")), rt_tostr(g_gclos_seq)), v_str_lit(", ")), rt_tostr((*l_capn))), v_str_lit(", \"")), (*l_cfn)), v_str_lit("\", ")), rt_tostr((*l_cnp))), v_str_lit(");\n"));
}
else {
g_gfuncs = rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(g_gfuncs, v_str_lit("Val l_")), (*l_cfn)), v_str_lit("_v = rt_fn_new(")), (*l_cname)), v_str_lit(", 0, 0, \"")), (*l_cfn)), v_str_lit("\", ")), rt_tostr((*l_cnp))), v_str_lit(");\n"));
}
g_gfuncs = rt_add(rt_add(rt_add(rt_add(rt_add(g_gfuncs, v_str_lit("Val *l_")), (*l_cfn)), v_str_lit(" = rt_box_new(l_")), (*l_cfn)), v_str_lit("_v);\n"));
return v_bool(1);
}
if (rt_truth(rt_cmp((*l_t), v_str_lit("nonlocal"), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
if (rt_truth(rt_cmp(g_gtok, v_str_lit("set"), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
}
Val *l_nlname = rt_box_new(g_gtok);
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
if (rt_truth(rt_cmp(g_gtok, v_str_lit("="), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
Val *l_nlval = rt_box_new(({ rt_trace_push("parse_expr", 0); Val _tr = c_parse_expr(); rt_trace_pop(); _tr; }));
if (rt_truth(rt_cmp(({ rt_trace_push("cap_index", 0); Val _tr = c_cap_index((*l_nlname)); rt_trace_pop(); _tr; }), v_int(0), ">="))) {
({ rt_trace_push("out", 0); Val _tr = c_out(rt_add(rt_add(rt_add(rt_add(v_str_lit("(*c_"), (*l_nlname)), v_str_lit(") = ")), (*l_nlval)), v_str_lit(";\n"))); rt_trace_pop(); _tr; });
}
else {
rt_print_val(v_str_lit("imporc.impor: nonlocal 只能指向外层局部：")); rt_print_sep(); rt_print_val((*l_nlname)); rt_print_nl();
g_gfail = v_bool(1);
return v_bool(0);
}
}
return v_bool(1);
}
if (rt_truth(rt_cmp((*l_t), v_str_lit("global"), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
if (rt_truth(rt_cmp(g_gtok, v_str_lit("set"), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
}
Val *l_glname = rt_box_new(g_gtok);
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
if (rt_truth(rt_cmp(g_gtok, v_str_lit("="), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
Val *l_glval = rt_box_new(({ rt_trace_push("parse_expr", 0); Val _tr = c_parse_expr(); rt_trace_pop(); _tr; }));
({ rt_trace_push("add_var", 0); Val _tr = c_add_var((*l_glname)); rt_trace_pop(); _tr; });
({ rt_trace_push("out", 0); Val _tr = c_out(rt_add(rt_add(rt_add(rt_add(v_str_lit("g_"), (*l_glname)), v_str_lit(" = ")), (*l_glval)), v_str_lit(";\n"))); rt_trace_pop(); _tr; });
}
return v_bool(1);
}
if (rt_truth(rt_or(rt_cmp((*l_t), v_str_lit("func"), "=="), rt_cmp((*l_t), v_str_lit("def"), "==")))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
Val *l_fname = rt_box_new(g_gtok);
if (rt_truth(rt_cmp(g_gtype, v_int(1), "!="))) {
rt_print_val(v_str_lit("imporc.impor: func 后需要函数名")); rt_print_nl();
g_gfail = v_bool(1);
return v_bool(0);
}
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
({ rt_trace_push("expect_op", 0); Val _tr = c_expect_op(v_str_lit("(")); rt_trace_pop(); _tr; });
Val *l_params = rt_box_new(v_str_lit(""));
Val *l_fplist = rt_box_new(v_str_lit(""));
Val *l_nparam = rt_box_new(v_int(0));
g_glocals = v_str_lit("");
if (rt_truth(rt_cmp(g_gtok, v_str_lit(")"), "!="))) {
while (rt_truth(v_int(1))) {
Val *l_pname = rt_box_new(g_gtok);
if (rt_truth(rt_cmp(g_gtype, v_int(1), "!="))) {
rt_print_val(v_str_lit("imporc.impor: 参数名应为标识符")); rt_print_nl();
g_gfail = v_bool(1);
return v_bool(0);
}
if (rt_truth(rt_cmp((*l_nparam), v_int(0), ">"))) {
(*l_params) = rt_add((*l_params), v_str_lit(", "));
}
(*l_params) = rt_add(rt_add((*l_params), v_str_lit("Val p")), rt_tostr((*l_nparam)));
(*l_fplist) = rt_add(rt_add((*l_fplist), (*l_pname)), v_str_lit("|"));
g_glocals = rt_add(rt_add(g_glocals, (*l_pname)), v_str_lit("|"));
(*l_nparam) = rt_add((*l_nparam), v_int(1));
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
if (rt_truth(rt_cmp(g_gtok, v_str_lit(","), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
}
else {
break;
}
}
}
({ rt_trace_push("expect_op", 0); Val _tr = c_expect_op(v_str_lit(")")); rt_trace_pop(); _tr; });
g_gfuncs = rt_add(rt_add(rt_add(rt_add(rt_add(g_gfuncs, v_str_lit("static Val c_")), (*l_fname)), v_str_lit("(")), (*l_params)), v_str_lit(") {\n"));
Val *l_fbi = rt_box_new(v_int(0));
while (rt_truth(rt_cmp((*l_fbi), (*l_nparam), "<"))) {
g_gfuncs = rt_add(rt_add(rt_add(rt_add(rt_add(g_gfuncs, v_str_lit("Val *l_")), ({ rt_trace_push("list_at", 0); Val _tr = c_list_at((*l_fplist), (*l_fbi)); rt_trace_pop(); _tr; })), v_str_lit(" = rt_box_new(p")), rt_tostr((*l_fbi))), v_str_lit(");\n"));
(*l_fbi) = rt_add((*l_fbi), v_int(1));
}
g_ginfunc = v_bool(1);
({ rt_trace_push("gen_block", 0); Val _tr = c_gen_block(); rt_trace_pop(); _tr; });
g_ginfunc = v_bool(0);
g_gfuncs = rt_add(g_gfuncs, v_str_lit("return v_null();\n}\n"));
g_gdecls = rt_add(rt_add(rt_add(g_gdecls, v_str_lit("static Fn fob_c_")), (*l_fname)), v_str_lit(";\n"));
Val *l_argrefs = rt_box_new(v_str_lit(""));
Val *l_ai = rt_box_new(v_int(0));
while (rt_truth(rt_cmp((*l_ai), (*l_nparam), "<"))) {
if (rt_truth(rt_cmp((*l_ai), v_int(0), ">"))) {
(*l_argrefs) = rt_add((*l_argrefs), v_str_lit(", "));
}
(*l_argrefs) = rt_add(rt_add(rt_add((*l_argrefs), v_str_lit("args[")), rt_tostr((*l_ai))), v_str_lit("]"));
(*l_ai) = rt_add((*l_ai), v_int(1));
}
g_gfuncs = rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(g_gfuncs, v_str_lit("static Val wrap_c_")), (*l_fname)), v_str_lit("(void *env, Val *args) { (void)env; (void)args; return c_")), (*l_fname)), v_str_lit("(")), (*l_argrefs)), v_str_lit("); }\n"));
g_gfuncs = rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(g_gfuncs, v_str_lit("static Fn fob_c_")), (*l_fname)), v_str_lit(" = {wrap_c_")), (*l_fname)), v_str_lit(", 0, 0, ")), rt_tostr((*l_nparam))), v_str_lit(", \"")), (*l_fname)), v_str_lit("\"};\n"));
return v_bool(1);
}
if (rt_truth(rt_cmp((*l_t), v_str_lit("if"), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
Val *l_cond = rt_box_new(({ rt_trace_push("parse_expr", 0); Val _tr = c_parse_expr(); rt_trace_pop(); _tr; }));
({ rt_trace_push("out", 0); Val _tr = c_out(rt_add(rt_add(v_str_lit("if (rt_truth("), (*l_cond)), v_str_lit(")) {\n"))); rt_trace_pop(); _tr; });
({ rt_trace_push("gen_block", 0); Val _tr = c_gen_block(); rt_trace_pop(); _tr; });
({ rt_trace_push("out", 0); Val _tr = c_out(v_str_lit("}\n")); rt_trace_pop(); _tr; });
while (rt_truth(v_int(1))) {
if (rt_truth(rt_or(rt_cmp(g_gtok, v_str_lit("elif"), "=="), rt_cmp(g_gtok, v_str_lit("elseif"), "==")))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
Val *l_c2 = rt_box_new(({ rt_trace_push("parse_expr", 0); Val _tr = c_parse_expr(); rt_trace_pop(); _tr; }));
({ rt_trace_push("out", 0); Val _tr = c_out(rt_add(rt_add(v_str_lit("else if (rt_truth("), (*l_c2)), v_str_lit(")) {\n"))); rt_trace_pop(); _tr; });
({ rt_trace_push("gen_block", 0); Val _tr = c_gen_block(); rt_trace_pop(); _tr; });
({ rt_trace_push("out", 0); Val _tr = c_out(v_str_lit("}\n")); rt_trace_pop(); _tr; });
}
else {
if (rt_truth(rt_cmp(g_gtok, v_str_lit("else"), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
({ rt_trace_push("out", 0); Val _tr = c_out(v_str_lit("else {\n")); rt_trace_pop(); _tr; });
({ rt_trace_push("gen_block", 0); Val _tr = c_gen_block(); rt_trace_pop(); _tr; });
({ rt_trace_push("out", 0); Val _tr = c_out(v_str_lit("}\n")); rt_trace_pop(); _tr; });
}
else {
break;
}
}
}
return v_bool(1);
}
if (rt_truth(rt_cmp((*l_t), v_str_lit("while"), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
Val *l_cond = rt_box_new(({ rt_trace_push("parse_expr", 0); Val _tr = c_parse_expr(); rt_trace_pop(); _tr; }));
({ rt_trace_push("out", 0); Val _tr = c_out(rt_add(rt_add(v_str_lit("while (rt_truth("), (*l_cond)), v_str_lit(")) {\n"))); rt_trace_pop(); _tr; });
({ rt_trace_push("gen_block", 0); Val _tr = c_gen_block(); rt_trace_pop(); _tr; });
({ rt_trace_push("out", 0); Val _tr = c_out(v_str_lit("}\n")); rt_trace_pop(); _tr; });
return v_bool(1);
}
if (rt_truth(rt_cmp((*l_t), v_str_lit("return"), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
if (rt_truth(rt_or(rt_or(rt_cmp(g_gtype, v_int(5), "=="), rt_cmp(g_gtype, v_int(6), "==")), rt_cmp(g_gtype, v_int(7), "==")))) {
({ rt_trace_push("out", 0); Val _tr = c_out(v_str_lit("return v_null();\n")); rt_trace_pop(); _tr; });
}
else {
({ rt_trace_push("out", 0); Val _tr = c_out(rt_add(rt_add(v_str_lit("return "), ({ rt_trace_push("parse_expr", 0); Val _tr = c_parse_expr(); rt_trace_pop(); _tr; })), v_str_lit(";\n"))); rt_trace_pop(); _tr; });
}
return v_bool(1);
}
if (rt_truth(rt_or(rt_or(rt_cmp((*l_t), v_str_lit("elif"), "=="), rt_cmp((*l_t), v_str_lit("elseif"), "==")), rt_cmp((*l_t), v_str_lit("else"), "==")))) {
rt_print_val(v_str_lit("imporc.impor: ")); rt_print_sep(); rt_print_val((*l_t)); rt_print_sep(); rt_print_val(v_str_lit(" 需要紧跟在 if 块后")); rt_print_nl();
g_gfail = v_bool(1);
return v_bool(0);
}
if (rt_truth(rt_cmp((*l_t), v_str_lit("break"), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
({ rt_trace_push("out", 0); Val _tr = c_out(v_str_lit("break;\n")); rt_trace_pop(); _tr; });
return v_bool(1);
}
if (rt_truth(rt_cmp((*l_t), v_str_lit("continue"), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
({ rt_trace_push("out", 0); Val _tr = c_out(v_str_lit("continue;\n")); rt_trace_pop(); _tr; });
return v_bool(1);
}
if (rt_truth(rt_or(rt_cmp((*l_t), v_str_lit("for"), "=="), rt_cmp((*l_t), v_str_lit("each"), "==")))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
Val *l_iv = rt_box_new(g_gtok);
if (rt_truth(rt_cmp(g_gtype, v_int(1), "!="))) {
rt_print_val(v_str_lit("imporc.impor: for/each 后需要迭代变量")); rt_print_nl();
g_gfail = v_bool(1);
return v_bool(0);
}
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
if (rt_truth(rt_cmp(g_gtok, v_str_lit("in"), "!="))) {
rt_print_val(v_str_lit("imporc.impor: for/each 需要 in")); rt_print_nl();
g_gfail = v_bool(1);
return v_bool(0);
}
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
Val *l_ite = rt_box_new(v_str_lit(""));
Val *l_is_range = rt_box_new(v_bool(0));
if (rt_truth(rt_cmp(g_gtok, v_str_lit("range"), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
if (rt_truth(rt_cmp(g_gtok, v_str_lit("("), "!="))) {
rt_print_val(v_str_lit("imporc.impor: range 需要 (")); rt_print_nl();
g_gfail = v_bool(1);
return v_bool(0);
}
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
(*l_ite) = ({ rt_trace_push("parse_expr", 0); Val _tr = c_parse_expr(); rt_trace_pop(); _tr; });
if (rt_truth(rt_cmp(g_gtok, v_str_lit(")"), "!="))) {
rt_print_val(v_str_lit("imporc.impor: range 需要 )")); rt_print_nl();
g_gfail = v_bool(1);
return v_bool(0);
}
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
(*l_is_range) = v_bool(1);
}
else {
(*l_ite) = ({ rt_trace_push("parse_expr", 0); Val _tr = c_parse_expr(); rt_trace_pop(); _tr; });
}
g_gtmp = rt_add(g_gtmp, v_int(1));
Val *l_tag = rt_box_new(g_gtmp);
if (rt_truth(g_ginfunc)) {
g_glocals = rt_add(rt_add(g_glocals, (*l_iv)), v_str_lit("|"));
({ rt_trace_push("out", 0); Val _tr = c_out(rt_add(rt_add(v_str_lit("Val *l_"), (*l_iv)), v_str_lit(" = rt_box_new(v_null());\n"))); rt_trace_pop(); _tr; });
}
else {
({ rt_trace_push("add_var", 0); Val _tr = c_add_var((*l_iv)); rt_trace_pop(); _tr; });
}
if (rt_truth((*l_is_range))) {
({ rt_trace_push("out", 0); Val _tr = c_out(rt_add(rt_add(rt_add(rt_add(v_str_lit("Val _t"), rt_tostr((*l_tag))), v_str_lit("_n = ")), (*l_ite)), v_str_lit(";\n"))); rt_trace_pop(); _tr; });
({ rt_trace_push("out", 0); Val _tr = c_out(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(v_str_lit("for (long long _t"), rt_tostr((*l_tag))), v_str_lit("_i = 0; _t")), rt_tostr((*l_tag))), v_str_lit("_i < _t")), rt_tostr((*l_tag))), v_str_lit("_n.i; _t")), rt_tostr((*l_tag))), v_str_lit("_i++) {\n"))); rt_trace_pop(); _tr; });
if (rt_truth(g_ginfunc)) {
({ rt_trace_push("out", 0); Val _tr = c_out(rt_add(rt_add(rt_add(({ rt_trace_push("var_ref", 0); Val _tr = c_var_ref((*l_iv)); rt_trace_pop(); _tr; }), v_str_lit(" = v_int(_t")), rt_tostr((*l_tag))), v_str_lit("_i);\n"))); rt_trace_pop(); _tr; });
}
else {
({ rt_trace_push("out", 0); Val _tr = c_out(rt_add(rt_add(rt_add(rt_add(v_str_lit("g_"), (*l_iv)), v_str_lit(" = v_int(_t")), rt_tostr((*l_tag))), v_str_lit("_i);\n"))); rt_trace_pop(); _tr; });
}
}
else {
({ rt_trace_push("out", 0); Val _tr = c_out(rt_add(rt_add(rt_add(rt_add(v_str_lit("Val _t"), rt_tostr((*l_tag))), v_str_lit("_a = ")), (*l_ite)), v_str_lit(";\n"))); rt_trace_pop(); _tr; });
({ rt_trace_push("out", 0); Val _tr = c_out(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(v_str_lit("Val _t"), rt_tostr((*l_tag))), v_str_lit("_k = (_t")), rt_tostr((*l_tag))), v_str_lit("_a.t == V_DICT) ? rt_dict_keys(_t")), rt_tostr((*l_tag))), v_str_lit("_a) : _t")), rt_tostr((*l_tag))), v_str_lit("_a;\n"))); rt_trace_pop(); _tr; });
({ rt_trace_push("out", 0); Val _tr = c_out(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(v_str_lit("for (long long _t"), rt_tostr((*l_tag))), v_str_lit("_i = 0; _t")), rt_tostr((*l_tag))), v_str_lit("_i < (_t")), rt_tostr((*l_tag))), v_str_lit("_k.t == V_ARR && _t")), rt_tostr((*l_tag))), v_str_lit("_k.a ? _t")), rt_tostr((*l_tag))), v_str_lit("_k.a->n : 0); _t")), rt_tostr((*l_tag))), v_str_lit("_i++) {\n"))); rt_trace_pop(); _tr; });
if (rt_truth(g_ginfunc)) {
({ rt_trace_push("out", 0); Val _tr = c_out(rt_add(rt_add(rt_add(rt_add(rt_add(({ rt_trace_push("var_ref", 0); Val _tr = c_var_ref((*l_iv)); rt_trace_pop(); _tr; }), v_str_lit(" = rt_arr_get(_t")), rt_tostr((*l_tag))), v_str_lit("_k, v_int(_t")), rt_tostr((*l_tag))), v_str_lit("_i));\n"))); rt_trace_pop(); _tr; });
}
else {
({ rt_trace_push("out", 0); Val _tr = c_out(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(v_str_lit("g_"), (*l_iv)), v_str_lit(" = rt_arr_get(_t")), rt_tostr((*l_tag))), v_str_lit("_k, v_int(_t")), rt_tostr((*l_tag))), v_str_lit("_i));\n"))); rt_trace_pop(); _tr; });
}
}
({ rt_trace_push("gen_block2", 0); Val _tr = c_gen_block2(); rt_trace_pop(); _tr; });
({ rt_trace_push("out", 0); Val _tr = c_out(v_str_lit("}\n")); rt_trace_pop(); _tr; });
return v_bool(1);
}
if (rt_truth(rt_cmp((*l_t), v_str_lit("repeat"), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
Val *l_rn = rt_box_new(({ rt_trace_push("parse_expr", 0); Val _tr = c_parse_expr(); rt_trace_pop(); _tr; }));
g_gtmp = rt_add(g_gtmp, v_int(1));
Val *l_tag = rt_box_new(g_gtmp);
({ rt_trace_push("out", 0); Val _tr = c_out(rt_add(rt_add(rt_add(rt_add(v_str_lit("Val _t"), rt_tostr((*l_tag))), v_str_lit("_n = ")), (*l_rn)), v_str_lit(";\n"))); rt_trace_pop(); _tr; });
({ rt_trace_push("out", 0); Val _tr = c_out(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(v_str_lit("for (long long _t"), rt_tostr((*l_tag))), v_str_lit("_i = 0; _t")), rt_tostr((*l_tag))), v_str_lit("_i < _t")), rt_tostr((*l_tag))), v_str_lit("_n.i; _t")), rt_tostr((*l_tag))), v_str_lit("_i++) {\n"))); rt_trace_pop(); _tr; });
({ rt_trace_push("gen_block2", 0); Val _tr = c_gen_block2(); rt_trace_pop(); _tr; });
({ rt_trace_push("out", 0); Val _tr = c_out(v_str_lit("}\n")); rt_trace_pop(); _tr; });
return v_bool(1);
}
if (rt_truth(rt_or(rt_cmp((*l_t), v_str_lit("match"), "=="), rt_cmp((*l_t), v_str_lit("switch"), "==")))) {
Val *l_sw_ind = rt_box_new(({ rt_trace_push("top_ind", 0); Val _tr = c_top_ind(); rt_trace_pop(); _tr; }));
({ rt_trace_push("push_sw", 0); Val _tr = c_push_sw((*l_sw_ind)); rt_trace_pop(); _tr; });
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
Val *l_msub = rt_box_new(({ rt_trace_push("parse_expr", 0); Val _tr = c_parse_expr(); rt_trace_pop(); _tr; }));
g_gtmp = rt_add(g_gtmp, v_int(1));
Val *l_tag = rt_box_new(g_gtmp);
Val *l_mbrace = rt_box_new(v_bool(0));
if (rt_truth(rt_and(rt_cmp(g_gtok, v_str_lit("{"), "=="), rt_cmp(g_gtype, v_int(4), "==")))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
(*l_mbrace) = v_bool(1);
}
({ rt_trace_push("out", 0); Val _tr = c_out(rt_add(rt_add(rt_add(rt_add(v_str_lit("Val _m"), rt_tostr((*l_tag))), v_str_lit(" = ")), (*l_msub)), v_str_lit(";\n"))); rt_trace_pop(); _tr; });
({ rt_trace_push("out", 0); Val _tr = c_out(rt_add(rt_add(v_str_lit("int _m"), rt_tostr((*l_tag))), v_str_lit("_done = 0;\n"))); rt_trace_pop(); _tr; });
while (rt_truth(rt_and(rt_and(rt_cmp(g_gtype, v_int(5), "!="), rt_not(g_gfail)), rt_not(rt_and(rt_and((*l_mbrace), rt_cmp(g_gtok, v_str_lit("}"), "==")), rt_cmp(g_gtype, v_int(4), "==")))))) {
if (rt_truth(rt_cmp(g_gtype, v_int(6), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
}
if (rt_truth(rt_cmp(g_gtok, v_str_lit("case"), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
Val *l_cbind = rt_box_new(v_str_lit(""));
Val *l_cval = rt_box_new(v_str_lit("v_null()"));
if (rt_truth(rt_cmp(g_gtype, v_int(1), "=="))) {
(*l_cbind) = g_gtok;
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
if (rt_truth(rt_and(rt_cmp(g_gtok, v_str_lit("."), "=="), rt_cmp(g_gtype, v_int(4), "==")))) {
Val *l_ctype = rt_box_new((*l_cbind));
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
Val *l_cmem = rt_box_new(g_gtok);
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
(*l_cval) = rt_add(rt_add(rt_add(v_str_lit("g___enum_"), (*l_ctype)), v_str_lit("_")), (*l_cmem));
(*l_cbind) = v_str_lit("");
}
else {
if (rt_truth(rt_cmp((*l_cbind), v_str_lit("_"), "=="))) {
(*l_cbind) = v_str_lit("");
}
}
}
else {
(*l_cval) = ({ rt_trace_push("parse_expr", 0); Val _tr = c_parse_expr(); rt_trace_pop(); _tr; });
}
if (rt_truth(rt_cmp(g_gtok, v_str_lit(":"), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
}
if (rt_truth(rt_cmp((*l_cbind), v_str_lit(""), "!="))) {
({ rt_trace_push("out", 0); Val _tr = c_out(rt_add(rt_add(v_str_lit("if (_m"), rt_tostr((*l_tag))), v_str_lit("_done == 0) {\n"))); rt_trace_pop(); _tr; });
if (rt_truth(g_ginfunc)) {
g_glocals = rt_add(rt_add(g_glocals, (*l_cbind)), v_str_lit("|"));
({ rt_trace_push("out", 0); Val _tr = c_out(rt_add(rt_add(rt_add(rt_add(v_str_lit("Val *l_"), (*l_cbind)), v_str_lit(" = rt_box_new(_m")), rt_tostr((*l_tag))), v_str_lit(");\n"))); rt_trace_pop(); _tr; });
}
else {
({ rt_trace_push("add_var", 0); Val _tr = c_add_var((*l_cbind)); rt_trace_pop(); _tr; });
({ rt_trace_push("out", 0); Val _tr = c_out(rt_add(rt_add(rt_add(rt_add(v_str_lit("g_"), (*l_cbind)), v_str_lit(" = _m")), rt_tostr((*l_tag))), v_str_lit(";\n"))); rt_trace_pop(); _tr; });
}
({ rt_trace_push("out", 0); Val _tr = c_out(rt_add(rt_add(v_str_lit("_m"), rt_tostr((*l_tag))), v_str_lit("_done = 1;\n"))); rt_trace_pop(); _tr; });
({ rt_trace_push("gen_block2", 0); Val _tr = c_gen_block2(); rt_trace_pop(); _tr; });
(*l_sw_ind) = ({ rt_trace_push("top_sw", 0); Val _tr = c_top_sw(); rt_trace_pop(); _tr; });
({ rt_trace_push("out", 0); Val _tr = c_out(v_str_lit("}\n")); rt_trace_pop(); _tr; });
}
else {
({ rt_trace_push("out", 0); Val _tr = c_out(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(v_str_lit("if (_m"), rt_tostr((*l_tag))), v_str_lit("_done == 0 && rt_truth(rt_cmp(_m")), rt_tostr((*l_tag))), v_str_lit(", ")), (*l_cval)), v_str_lit(", \"==\"))) {\n"))); rt_trace_pop(); _tr; });
({ rt_trace_push("out", 0); Val _tr = c_out(rt_add(rt_add(v_str_lit("_m"), rt_tostr((*l_tag))), v_str_lit("_done = 1;\n"))); rt_trace_pop(); _tr; });
({ rt_trace_push("gen_block2", 0); Val _tr = c_gen_block2(); rt_trace_pop(); _tr; });
(*l_sw_ind) = ({ rt_trace_push("top_sw", 0); Val _tr = c_top_sw(); rt_trace_pop(); _tr; });
({ rt_trace_push("out", 0); Val _tr = c_out(v_str_lit("}\n")); rt_trace_pop(); _tr; });
}
}
else {
if (rt_truth(rt_or(rt_cmp(g_gtok, v_str_lit("default"), "=="), rt_cmp(g_gtok, v_str_lit("else"), "==")))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
if (rt_truth(rt_cmp(g_gtok, v_str_lit(":"), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
}
({ rt_trace_push("out", 0); Val _tr = c_out(rt_add(rt_add(v_str_lit("if (_m"), rt_tostr((*l_tag))), v_str_lit("_done == 0) {\n"))); rt_trace_pop(); _tr; });
({ rt_trace_push("gen_block2", 0); Val _tr = c_gen_block2(); rt_trace_pop(); _tr; });
(*l_sw_ind) = ({ rt_trace_push("top_sw", 0); Val _tr = c_top_sw(); rt_trace_pop(); _tr; });
({ rt_trace_push("out", 0); Val _tr = c_out(v_str_lit("}\n")); rt_trace_pop(); _tr; });
break;
}
else {
if (rt_truth(rt_cmp(g_gtype, v_int(7), "=="))) {
while (rt_truth(rt_and(rt_cmp(g_gtype, v_int(7), "=="), rt_cmp(({ rt_trace_push("top_ind", 0); Val _tr = c_top_ind(); rt_trace_pop(); _tr; }), (*l_sw_ind), ">")))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
}
if (rt_truth(rt_cmp(g_gtype, v_int(7), "=="))) {
break;
}
if (rt_truth(rt_and(rt_and(rt_cmp(g_gtok, v_str_lit("case"), "!="), rt_cmp(g_gtok, v_str_lit("default"), "!=")), rt_cmp(g_gtok, v_str_lit("else"), "!=")))) {
break;
}
}
else {
break;
}
}
}
}
if (rt_truth(rt_cmp(g_gtype, v_int(7), "=="))) {
while (rt_truth(rt_and(rt_cmp(g_gtype, v_int(7), "=="), rt_cmp(({ rt_trace_push("top_ind", 0); Val _tr = c_top_ind(); rt_trace_pop(); _tr; }), (*l_sw_ind), ">=")))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
}
}
if (rt_truth((*l_mbrace))) {
if (rt_truth(rt_and(rt_cmp(g_gtok, v_str_lit("}"), "=="), rt_cmp(g_gtype, v_int(4), "==")))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
}
}
({ rt_trace_push("pop_sw", 0); Val _tr = c_pop_sw(); rt_trace_pop(); _tr; });
return v_bool(1);
}
if (rt_truth(rt_cmp((*l_t), v_str_lit("try"), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
g_gtmp = rt_add(g_gtmp, v_int(1));
Val *l_tag = rt_box_new(g_gtmp);
({ rt_trace_push("out", 0); Val _tr = c_out(v_str_lit("if (setjmp(g_jmp_stack[g_jmp_n]) == 0) {\n")); rt_trace_pop(); _tr; });
({ rt_trace_push("out", 0); Val _tr = c_out(v_str_lit("g_jmp_n++;\n")); rt_trace_pop(); _tr; });
({ rt_trace_push("gen_block2", 0); Val _tr = c_gen_block2(); rt_trace_pop(); _tr; });
({ rt_trace_push("out", 0); Val _tr = c_out(v_str_lit("g_jmp_n--;\n")); rt_trace_pop(); _tr; });
({ rt_trace_push("out", 0); Val _tr = c_out(v_str_lit("} else {\n")); rt_trace_pop(); _tr; });
({ rt_trace_push("out", 0); Val _tr = c_out(v_str_lit("g_jmp_n--;\n")); rt_trace_pop(); _tr; });
Val *l_hadc = rt_box_new(v_bool(0));
while (rt_truth(rt_cmp(g_gtok, v_str_lit("catch"), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
Val *l_ctype = rt_box_new(v_str_lit(""));
Val *l_cvar = rt_box_new(v_str_lit(""));
if (rt_truth(rt_cmp(g_gtype, v_int(1), "=="))) {
(*l_cvar) = g_gtok;
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
if (rt_truth(rt_cmp(g_gtype, v_int(1), "=="))) {
(*l_ctype) = (*l_cvar);
(*l_cvar) = g_gtok;
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
if (rt_truth(rt_cmp(g_gtok, v_str_lit(","), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
}
}
else {
if (rt_truth(rt_cmp(g_gtok, v_str_lit(","), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
}
}
}
if (rt_truth(rt_cmp(g_gtok, v_str_lit(":"), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
}
if (rt_truth(rt_cmp((*l_ctype), v_str_lit(""), "=="))) {
if (rt_truth((*l_hadc))) {
({ rt_trace_push("out", 0); Val _tr = c_out(v_str_lit("else {\n")); rt_trace_pop(); _tr; });
}
else {
({ rt_trace_push("out", 0); Val _tr = c_out(v_str_lit("{\n")); rt_trace_pop(); _tr; });
}
}
else {
if (rt_truth((*l_hadc))) {
({ rt_trace_push("out", 0); Val _tr = c_out(rt_add(rt_add(v_str_lit("else if (strcmp(g_err_type, \""), (*l_ctype)), v_str_lit("\") == 0) {\n"))); rt_trace_pop(); _tr; });
}
else {
({ rt_trace_push("out", 0); Val _tr = c_out(rt_add(rt_add(v_str_lit("if (strcmp(g_err_type, \""), (*l_ctype)), v_str_lit("\") == 0) {\n"))); rt_trace_pop(); _tr; });
}
}
(*l_hadc) = v_bool(1);
if (rt_truth(g_ginfunc)) {
g_glocals = rt_add(rt_add(g_glocals, (*l_cvar)), v_str_lit("|"));
({ rt_trace_push("out", 0); Val _tr = c_out(rt_add(rt_add(v_str_lit("Val *l_"), (*l_cvar)), v_str_lit(" = rt_box_new(rt_err_obj());\n"))); rt_trace_pop(); _tr; });
}
else {
({ rt_trace_push("add_var", 0); Val _tr = c_add_var((*l_cvar)); rt_trace_pop(); _tr; });
({ rt_trace_push("out", 0); Val _tr = c_out(rt_add(rt_add(v_str_lit("g_"), (*l_cvar)), v_str_lit(" = rt_err_obj();\n"))); rt_trace_pop(); _tr; });
}
({ rt_trace_push("gen_block2", 0); Val _tr = c_gen_block2(); rt_trace_pop(); _tr; });
({ rt_trace_push("out", 0); Val _tr = c_out(v_str_lit("}\n")); rt_trace_pop(); _tr; });
}
if (rt_truth((*l_hadc))) {
({ rt_trace_push("out", 0); Val _tr = c_out(v_str_lit("else { rt_throw_again(); }\n")); rt_trace_pop(); _tr; });
}
else {
({ rt_trace_push("out", 0); Val _tr = c_out(v_str_lit("rt_throw_again();\n")); rt_trace_pop(); _tr; });
}
({ rt_trace_push("out", 0); Val _tr = c_out(v_str_lit("}\n")); rt_trace_pop(); _tr; });
if (rt_truth(rt_cmp(g_gtok, v_str_lit("finally"), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
({ rt_trace_push("gen_block2", 0); Val _tr = c_gen_block2(); rt_trace_pop(); _tr; });
}
return v_bool(1);
}
if (rt_truth(rt_cmp((*l_t), v_str_lit("exception"), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
if (rt_truth(rt_cmp(g_gtype, v_int(1), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
}
return v_bool(1);
}
if (rt_truth(rt_cmp((*l_t), v_str_lit("raise"), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
Val *l_ename = rt_box_new(g_gtok);
if (rt_truth(rt_cmp(g_gtype, v_int(1), "!="))) {
rt_print_val(v_str_lit("imporc.impor: raise 后需要异常类型名")); rt_print_nl();
g_gfail = v_bool(1);
return v_bool(0);
}
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
({ rt_trace_push("expect_op", 0); Val _tr = c_expect_op(v_str_lit("(")); rt_trace_pop(); _tr; });
Val *l_emsg = rt_box_new(({ rt_trace_push("parse_expr", 0); Val _tr = c_parse_expr(); rt_trace_pop(); _tr; }));
if (rt_truth(rt_cmp(g_gtok, v_str_lit(","), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
if (rt_truth(rt_cmp(g_gtok, v_str_lit("from"), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
}
Val *l_cdummy = rt_box_new(({ rt_trace_push("parse_expr", 0); Val _tr = c_parse_expr(); rt_trace_pop(); _tr; }));
}
({ rt_trace_push("expect_op", 0); Val _tr = c_expect_op(v_str_lit(")")); rt_trace_pop(); _tr; });
({ rt_trace_push("out", 0); Val _tr = c_out(rt_add(rt_add(rt_add(rt_add(v_str_lit("rt_throw(\""), (*l_ename)), v_str_lit("\", rt_tostr(")), (*l_emsg)), v_str_lit(").s);\n"))); rt_trace_pop(); _tr; });
return v_bool(1);
}
if (rt_truth(rt_cmp(g_gtype, v_int(1), "=="))) {
Val *l_e0 = rt_box_new(({ rt_trace_push("parse_expr", 0); Val _tr = c_parse_expr(); rt_trace_pop(); _tr; }));
if (rt_truth(rt_cmp(g_gtok, v_str_lit("="), "=="))) {
Val *l_ak = rt_box_new(g_gassign_kind);
Val *l_an = rt_box_new(g_gassign_name);
Val *l_ar = rt_box_new(g_gassign_raw);
Val *l_ab = rt_box_new(g_gassign_base);
Val *l_ap = rt_box_new(g_gassign_prop);
Val *l_ai = rt_box_new(g_gassign_idx);
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
Val *l_ev = rt_box_new(({ rt_trace_push("parse_expr", 0); Val _tr = c_parse_expr(); rt_trace_pop(); _tr; }));
if (rt_truth(rt_cmp((*l_ak), v_str_lit("prop"), "=="))) {
({ rt_trace_push("out", 0); Val _tr = c_out(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(v_str_lit("rt_objset("), (*l_ab)), v_str_lit(".o, \"")), (*l_ap)), v_str_lit("\", ")), (*l_ev)), v_str_lit(");\n"))); rt_trace_pop(); _tr; });
}
else {
if (rt_truth(rt_cmp((*l_ak), v_str_lit("var"), "=="))) {
if (rt_truth(rt_not(g_ginfunc))) {
({ rt_trace_push("add_var", 0); Val _tr = c_add_var((*l_ar)); rt_trace_pop(); _tr; });
}
({ rt_trace_push("out", 0); Val _tr = c_out(rt_add(rt_add(rt_add((*l_an), v_str_lit(" = ")), (*l_ev)), v_str_lit(";\n"))); rt_trace_pop(); _tr; });
}
else {
if (rt_truth(rt_cmp((*l_ak), v_str_lit("idx"), "=="))) {
({ rt_trace_push("out", 0); Val _tr = c_out(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(v_str_lit("rt_arr_set("), (*l_ab)), v_str_lit(", ")), (*l_ai)), v_str_lit(", ")), (*l_ev)), v_str_lit(");\n"))); rt_trace_pop(); _tr; });
}
else {
rt_print_val(v_str_lit("imporc.impor: 不能赋值的表达式")); rt_print_nl();
g_gfail = v_bool(1);
}
}
}
return v_bool(1);
}
({ rt_trace_push("out", 0); Val _tr = c_out(rt_add((*l_e0), v_str_lit(";\n"))); rt_trace_pop(); _tr; });
return v_bool(1);
}
rt_print_val(v_str_lit("imporc.impor: 无法识别的语句 [")); rt_print_sep(); rt_print_val((*l_t)); rt_print_sep(); rt_print_val(v_str_lit("] type=")); rt_print_sep(); rt_print_val(g_gtype); rt_print_sep(); rt_print_val(v_str_lit(" pos=")); rt_print_sep(); rt_print_val(g_gpos); rt_print_nl();
g_gfail = v_bool(1);
return v_bool(0);
return v_null();
}
static Val wrap_c_gen_stmt(void *env, Val *args) { (void)env; (void)args; return c_gen_stmt(); }
static Fn fob_c_gen_stmt = {wrap_c_gen_stmt, 0, 0, 0, "gen_stmt"};
static Val c_next_arg() {
Val *l_n = rt_box_new(rt_len(g_gargs));
while (rt_truth(rt_cmp(g_gp2, (*l_n), "<"))) {
Val *l_c3 = rt_box_new(rt_strcut(g_gargs, g_gp2, rt_add(g_gp2, v_int(1))));
if (rt_truth(rt_or(rt_or(rt_or(rt_cmp((*l_c3), v_str_lit(" "), "=="), rt_cmp((*l_c3), v_str_lit("\t"), "==")), rt_cmp((*l_c3), v_str_lit("\r"), "==")), rt_cmp((*l_c3), v_str_lit("\n"), "==")))) {
g_gp2 = rt_add(g_gp2, v_int(1));
}
else {
break;
}
}
if (rt_truth(rt_cmp(g_gp2, (*l_n), ">="))) {
g_gt2 = v_str_lit("");
return v_bool(1);
}
if (rt_truth(rt_cmp(rt_strcut(g_gargs, g_gp2, rt_add(g_gp2, v_int(1))), v_str_lit("\""), "=="))) {
g_gp2 = rt_add(g_gp2, v_int(1));
Val *l_stq = rt_box_new(g_gp2);
while (rt_truth(rt_cmp(g_gp2, (*l_n), "<"))) {
if (rt_truth(rt_cmp(rt_strcut(g_gargs, g_gp2, rt_add(g_gp2, v_int(1))), v_str_lit("\""), "=="))) {
break;
}
g_gp2 = rt_add(g_gp2, v_int(1));
}
g_gt2 = rt_strcut(g_gargs, (*l_stq), g_gp2);
if (rt_truth(rt_cmp(g_gp2, (*l_n), "<"))) {
g_gp2 = rt_add(g_gp2, v_int(1));
}
return v_bool(1);
}
Val *l_st = rt_box_new(g_gp2);
while (rt_truth(rt_cmp(g_gp2, (*l_n), "<"))) {
Val *l_c3 = rt_box_new(rt_strcut(g_gargs, g_gp2, rt_add(g_gp2, v_int(1))));
if (rt_truth(rt_or(rt_or(rt_or(rt_cmp((*l_c3), v_str_lit(" "), "=="), rt_cmp((*l_c3), v_str_lit("\t"), "==")), rt_cmp((*l_c3), v_str_lit("\r"), "==")), rt_cmp((*l_c3), v_str_lit("\n"), "==")))) {
break;
}
g_gp2 = rt_add(g_gp2, v_int(1));
}
g_gt2 = rt_strcut(g_gargs, (*l_st), g_gp2);
return v_bool(1);
return v_null();
}
static Val wrap_c_next_arg(void *env, Val *args) { (void)env; (void)args; return c_next_arg(); }
static Fn fob_c_next_arg = {wrap_c_next_arg, 0, 0, 0, "next_arg"};
static Val c_mi_is_word(Val p0) {
Val *l_c = rt_box_new(p0);
if (rt_truth(rt_cmp((*l_c), v_str_lit(""), "=="))) {
return v_bool(0);
}
if (rt_truth(rt_and(rt_cmp((*l_c), v_str_lit("a"), ">="), rt_cmp((*l_c), v_str_lit("z"), "<=")))) {
return v_bool(1);
}
if (rt_truth(rt_and(rt_cmp((*l_c), v_str_lit("A"), ">="), rt_cmp((*l_c), v_str_lit("Z"), "<=")))) {
return v_bool(1);
}
if (rt_truth(rt_and(rt_cmp((*l_c), v_str_lit("0"), ">="), rt_cmp((*l_c), v_str_lit("9"), "<=")))) {
return v_bool(1);
}
if (rt_truth(rt_cmp((*l_c), v_str_lit("_"), "=="))) {
return v_bool(1);
}
return v_bool(0);
return v_null();
}
static Val wrap_c_mi_is_word(void *env, Val *args) { (void)env; (void)args; return c_mi_is_word(args[0]); }
static Fn fob_c_mi_is_word = {wrap_c_mi_is_word, 0, 0, 1, "mi_is_word"};
static Val c_mi_replace_word(Val p0, Val p1, Val p2) {
Val *l_s = rt_box_new(p0);
Val *l_old = rt_box_new(p1);
Val *l_rep = rt_box_new(p2);
Val *l_n = rt_box_new(rt_len((*l_s)));
Val *l_m = rt_box_new(rt_len((*l_old)));
if (rt_truth(rt_cmp((*l_m), v_int(0), "=="))) {
return (*l_s);
}
Val *l_i = rt_box_new(v_int(0));
Val *l_out = rt_box_new(v_str_lit(""));
while (rt_truth(rt_cmp((*l_i), (*l_n), "<"))) {
if (rt_truth(rt_and(rt_cmp(rt_add((*l_i), (*l_m)), (*l_n), "<="), rt_cmp(rt_strcut((*l_s), (*l_i), rt_add((*l_i), (*l_m))), (*l_old), "==")))) {
Val *l_prev = rt_box_new(v_str_lit(""));
if (rt_truth(rt_cmp((*l_i), v_int(0), ">"))) {
(*l_prev) = rt_strcut((*l_s), rt_sub((*l_i), v_int(1)), (*l_i));
}
Val *l_nxt = rt_box_new(v_str_lit(""));
if (rt_truth(rt_cmp(rt_add((*l_i), (*l_m)), (*l_n), "<"))) {
(*l_nxt) = rt_strcut((*l_s), rt_add((*l_i), (*l_m)), rt_add(rt_add((*l_i), (*l_m)), v_int(1)));
}
if (rt_truth(rt_and(rt_not(({ rt_trace_push("mi_is_word", 0); Val _tr = c_mi_is_word((*l_prev)); rt_trace_pop(); _tr; })), rt_not(({ rt_trace_push("mi_is_word", 0); Val _tr = c_mi_is_word((*l_nxt)); rt_trace_pop(); _tr; }))))) {
(*l_out) = rt_add((*l_out), (*l_rep));
(*l_i) = rt_add((*l_i), (*l_m));
}
else {
(*l_out) = rt_add((*l_out), rt_strcut((*l_s), (*l_i), rt_add((*l_i), v_int(1))));
(*l_i) = rt_add((*l_i), v_int(1));
}
}
else {
(*l_out) = rt_add((*l_out), rt_strcut((*l_s), (*l_i), rt_add((*l_i), v_int(1))));
(*l_i) = rt_add((*l_i), v_int(1));
}
}
return (*l_out);
return v_null();
}
static Val wrap_c_mi_replace_word(void *env, Val *args) { (void)env; (void)args; return c_mi_replace_word(args[0], args[1], args[2]); }
static Fn fob_c_mi_replace_word = {wrap_c_mi_replace_word, 0, 0, 3, "mi_replace_word"};
static Val c_mi_trim(Val p0) {
Val *l_s = rt_box_new(p0);
Val *l_n = rt_box_new(rt_len((*l_s)));
Val *l_a = rt_box_new(v_int(0));
while (rt_truth(rt_cmp((*l_a), (*l_n), "<"))) {
Val *l_c = rt_box_new(rt_strcut((*l_s), (*l_a), rt_add((*l_a), v_int(1))));
if (rt_truth(rt_or(rt_or(rt_or(rt_cmp((*l_c), v_str_lit(" "), "=="), rt_cmp((*l_c), v_str_lit("\t"), "==")), rt_cmp((*l_c), v_str_lit("\r"), "==")), rt_cmp((*l_c), v_str_lit("\n"), "==")))) {
(*l_a) = rt_add((*l_a), v_int(1));
}
else {
break;
}
}
Val *l_b = rt_box_new((*l_n));
while (rt_truth(rt_cmp((*l_b), (*l_a), ">"))) {
Val *l_c2 = rt_box_new(rt_strcut((*l_s), rt_sub((*l_b), v_int(1)), (*l_b)));
if (rt_truth(rt_or(rt_or(rt_or(rt_cmp((*l_c2), v_str_lit(" "), "=="), rt_cmp((*l_c2), v_str_lit("\t"), "==")), rt_cmp((*l_c2), v_str_lit("\r"), "==")), rt_cmp((*l_c2), v_str_lit("\n"), "==")))) {
(*l_b) = rt_sub((*l_b), v_int(1));
}
else {
break;
}
}
return rt_strcut((*l_s), (*l_a), (*l_b));
return v_null();
}
static Val wrap_c_mi_trim(void *env, Val *args) { (void)env; (void)args; return c_mi_trim(args[0]); }
static Fn fob_c_mi_trim = {wrap_c_mi_trim, 0, 0, 1, "mi_trim"};
static Val c_mi_dir_of(Val p0) {
Val *l_p = rt_box_new(p0);
Val *l_n = rt_box_new(rt_len((*l_p)));
Val *l_i = rt_box_new(rt_sub((*l_n), v_int(1)));
while (rt_truth(rt_cmp((*l_i), v_int(0), ">="))) {
Val *l_c = rt_box_new(rt_strcut((*l_p), (*l_i), rt_add((*l_i), v_int(1))));
if (rt_truth(rt_or(rt_cmp((*l_c), v_str_lit("/"), "=="), rt_cmp((*l_c), v_str_lit("\\"), "==")))) {
return rt_strcut((*l_p), v_int(0), rt_add((*l_i), v_int(1)));
}
(*l_i) = rt_sub((*l_i), v_int(1));
}
return v_str_lit("");
return v_null();
}
static Val wrap_c_mi_dir_of(void *env, Val *args) { (void)env; (void)args; return c_mi_dir_of(args[0]); }
static Fn fob_c_mi_dir_of = {wrap_c_mi_dir_of, 0, 0, 1, "mi_dir_of"};
static Val c_mi_sanitize(Val p0) {
Val *l_s = rt_box_new(p0);
Val *l_n = rt_box_new(rt_len((*l_s)));
Val *l_i = rt_box_new(v_int(0));
Val *l_out = rt_box_new(v_str_lit(""));
while (rt_truth(rt_cmp((*l_i), (*l_n), "<"))) {
Val *l_c = rt_box_new(rt_strcut((*l_s), (*l_i), rt_add((*l_i), v_int(1))));
if (rt_truth(({ rt_trace_push("mi_is_word", 0); Val _tr = c_mi_is_word((*l_c)); rt_trace_pop(); _tr; }))) {
(*l_out) = rt_add((*l_out), (*l_c));
}
else {
(*l_out) = rt_add((*l_out), v_str_lit("_"));
}
(*l_i) = rt_add((*l_i), v_int(1));
}
return (*l_out);
return v_null();
}
static Val wrap_c_mi_sanitize(void *env, Val *args) { (void)env; (void)args; return c_mi_sanitize(args[0]); }
static Fn fob_c_mi_sanitize = {wrap_c_mi_sanitize, 0, 0, 1, "mi_sanitize"};
static Val c_mi_replace_dots(Val p0) {
Val *l_s = rt_box_new(p0);
Val *l_n = rt_box_new(rt_len((*l_s)));
Val *l_i = rt_box_new(v_int(0));
Val *l_out = rt_box_new(v_str_lit(""));
while (rt_truth(rt_cmp((*l_i), (*l_n), "<"))) {
Val *l_c = rt_box_new(rt_strcut((*l_s), (*l_i), rt_add((*l_i), v_int(1))));
if (rt_truth(rt_cmp((*l_c), v_str_lit("."), "=="))) {
(*l_out) = rt_add((*l_out), v_str_lit("/"));
}
else {
(*l_out) = rt_add((*l_out), (*l_c));
}
(*l_i) = rt_add((*l_i), v_int(1));
}
return (*l_out);
return v_null();
}
static Val wrap_c_mi_replace_dots(void *env, Val *args) { (void)env; (void)args; return c_mi_replace_dots(args[0]); }
static Fn fob_c_mi_replace_dots = {wrap_c_mi_replace_dots, 0, 0, 1, "mi_replace_dots"};
static Val c_mi_strip_header(Val p0) {
Val *l_src = rt_box_new(p0);
if (rt_truth(rt_cmp(({ rt_trace_push("strfind", 0); Val _tr = c_strfind((*l_src), v_str_lit("important")); rt_trace_pop(); _tr; }), v_int(0), "=="))) {
Val *l_nl = rt_box_new(({ rt_trace_push("strfind", 0); Val _tr = c_strfind((*l_src), v_str_lit("\n")); rt_trace_pop(); _tr; }));
if (rt_truth(rt_cmp((*l_nl), v_int(0), ">="))) {
return rt_strcut((*l_src), rt_add((*l_nl), v_int(1)), rt_len((*l_src)));
}
return v_str_lit("");
}
return (*l_src);
return v_null();
}
static Val wrap_c_mi_strip_header(void *env, Val *args) { (void)env; (void)args; return c_mi_strip_header(args[0]); }
static Fn fob_c_mi_strip_header = {wrap_c_mi_strip_header, 0, 0, 1, "mi_strip_header"};
static Val c_mi_strip_export(Val p0) {
Val *l_src = rt_box_new(p0);
Val *l_n = rt_box_new(rt_len((*l_src)));
Val *l_i = rt_box_new(v_int(0));
Val *l_out = rt_box_new(v_str_lit(""));
while (rt_truth(rt_cmp((*l_i), (*l_n), "<="))) {
Val *l_nl = rt_box_new(({ rt_trace_push("strfind", 0); Val _tr = c_strfind(rt_strcut((*l_src), (*l_i), (*l_n)), v_str_lit("\n")); rt_trace_pop(); _tr; }));
Val *l_end = rt_box_new((*l_n));
Val *l_line = rt_box_new(rt_strcut((*l_src), (*l_i), (*l_n)));
if (rt_truth(rt_cmp((*l_nl), v_int(0), ">="))) {
(*l_end) = rt_add((*l_i), (*l_nl));
(*l_line) = rt_strcut((*l_src), (*l_i), (*l_end));
}
if (rt_truth(rt_cmp(({ rt_trace_push("strfind", 0); Val _tr = c_strfind(({ rt_trace_push("mi_trim", 0); Val _tr = c_mi_trim((*l_line)); rt_trace_pop(); _tr; }), v_str_lit("export ")); rt_trace_pop(); _tr; }), v_int(0), "!="))) {
(*l_out) = rt_add((*l_out), (*l_line));
if (rt_truth(rt_cmp((*l_end), (*l_n), "<"))) {
(*l_out) = rt_add((*l_out), v_str_lit("\n"));
}
}
(*l_i) = rt_add((*l_end), v_int(1));
if (rt_truth(rt_cmp((*l_end), (*l_n), ">="))) {
break;
}
}
return (*l_out);
return v_null();
}
static Val wrap_c_mi_strip_export(void *env, Val *args) { (void)env; (void)args; return c_mi_strip_export(args[0]); }
static Fn fob_c_mi_strip_export = {wrap_c_mi_strip_export, 0, 0, 1, "mi_strip_export"};
static Val c_mi_export_symbols(Val p0) {
Val *l_src = rt_box_new(p0);
Val *l_n = rt_box_new(rt_len((*l_src)));
Val *l_i = rt_box_new(v_int(0));
Val *l_out = rt_box_new(v_str_lit("|"));
while (rt_truth(rt_cmp((*l_i), (*l_n), "<="))) {
Val *l_nl = rt_box_new(({ rt_trace_push("strfind", 0); Val _tr = c_strfind(rt_strcut((*l_src), (*l_i), (*l_n)), v_str_lit("\n")); rt_trace_pop(); _tr; }));
Val *l_end = rt_box_new((*l_n));
Val *l_line = rt_box_new(rt_strcut((*l_src), (*l_i), (*l_n)));
if (rt_truth(rt_cmp((*l_nl), v_int(0), ">="))) {
(*l_end) = rt_add((*l_i), (*l_nl));
(*l_line) = rt_strcut((*l_src), (*l_i), (*l_end));
}
Val *l_t = rt_box_new(({ rt_trace_push("mi_trim", 0); Val _tr = c_mi_trim((*l_line)); rt_trace_pop(); _tr; }));
if (rt_truth(rt_cmp(({ rt_trace_push("strfind", 0); Val _tr = c_strfind((*l_t), v_str_lit("export ")); rt_trace_pop(); _tr; }), v_int(0), "=="))) {
Val *l_rest = rt_box_new(({ rt_trace_push("mi_trim", 0); Val _tr = c_mi_trim(rt_strcut((*l_t), v_int(7), rt_len((*l_t)))); rt_trace_pop(); _tr; }));
Val *l_j = rt_box_new(v_int(0));
Val *l_cur = rt_box_new(v_str_lit(""));
while (rt_truth(rt_cmp((*l_j), rt_len((*l_rest)), "<="))) {
Val *l_ch = rt_box_new(v_str_lit(""));
if (rt_truth(rt_cmp((*l_j), rt_len((*l_rest)), "<"))) {
(*l_ch) = rt_strcut((*l_rest), (*l_j), rt_add((*l_j), v_int(1)));
}
if (rt_truth(rt_or(rt_cmp((*l_ch), v_str_lit(","), "=="), rt_cmp((*l_j), rt_len((*l_rest)), ">=")))) {
Val *l_name2 = rt_box_new(({ rt_trace_push("mi_trim", 0); Val _tr = c_mi_trim((*l_cur)); rt_trace_pop(); _tr; }));
if (rt_truth(rt_cmp(rt_len((*l_name2)), v_int(0), ">"))) {
(*l_out) = rt_add(rt_add((*l_out), (*l_name2)), v_str_lit("|"));
}
(*l_cur) = v_str_lit("");
}
else {
(*l_cur) = rt_add((*l_cur), (*l_ch));
}
(*l_j) = rt_add((*l_j), v_int(1));
}
}
(*l_i) = rt_add((*l_end), v_int(1));
if (rt_truth(rt_cmp((*l_end), (*l_n), ">="))) {
break;
}
}
return (*l_out);
return v_null();
}
static Val wrap_c_mi_export_symbols(void *env, Val *args) { (void)env; (void)args; return c_mi_export_symbols(args[0]); }
static Fn fob_c_mi_export_symbols = {wrap_c_mi_export_symbols, 0, 0, 1, "mi_export_symbols"};
static Val c_mi_decl_symbols(Val p0) {
Val *l_src = rt_box_new(p0);
Val *l_n = rt_box_new(rt_len((*l_src)));
Val *l_i = rt_box_new(v_int(0));
Val *l_out = rt_box_new(v_str_lit("|"));
while (rt_truth(rt_cmp((*l_i), (*l_n), "<="))) {
Val *l_nl = rt_box_new(({ rt_trace_push("strfind", 0); Val _tr = c_strfind(rt_strcut((*l_src), (*l_i), (*l_n)), v_str_lit("\n")); rt_trace_pop(); _tr; }));
Val *l_end = rt_box_new((*l_n));
Val *l_line = rt_box_new(rt_strcut((*l_src), (*l_i), (*l_n)));
if (rt_truth(rt_cmp((*l_nl), v_int(0), ">="))) {
(*l_end) = rt_add((*l_i), (*l_nl));
(*l_line) = rt_strcut((*l_src), (*l_i), (*l_end));
}
Val *l_t = rt_box_new(({ rt_trace_push("mi_trim", 0); Val _tr = c_mi_trim((*l_line)); rt_trace_pop(); _tr; }));
Val *l_head = rt_box_new(v_str_lit(""));
if (rt_truth(rt_cmp(({ rt_trace_push("strfind", 0); Val _tr = c_strfind((*l_t), v_str_lit("func ")); rt_trace_pop(); _tr; }), v_int(0), "=="))) {
(*l_head) = rt_strcut((*l_t), v_int(5), rt_len((*l_t)));
}
else {
if (rt_truth(rt_cmp(({ rt_trace_push("strfind", 0); Val _tr = c_strfind((*l_t), v_str_lit("var ")); rt_trace_pop(); _tr; }), v_int(0), "=="))) {
(*l_head) = rt_strcut((*l_t), v_int(4), rt_len((*l_t)));
}
}
if (rt_truth(rt_cmp(rt_len((*l_head)), v_int(0), ">"))) {
Val *l_name2 = rt_box_new(v_str_lit(""));
Val *l_j = rt_box_new(v_int(0));
while (rt_truth(rt_cmp((*l_j), rt_len((*l_head)), "<"))) {
Val *l_ch = rt_box_new(rt_strcut((*l_head), (*l_j), rt_add((*l_j), v_int(1))));
if (rt_truth(({ rt_trace_push("mi_is_word", 0); Val _tr = c_mi_is_word((*l_ch)); rt_trace_pop(); _tr; }))) {
(*l_name2) = rt_add((*l_name2), (*l_ch));
(*l_j) = rt_add((*l_j), v_int(1));
}
else {
break;
}
}
if (rt_truth(rt_and(rt_cmp(rt_len((*l_name2)), v_int(0), ">"), rt_cmp(({ rt_trace_push("strfind", 0); Val _tr = c_strfind((*l_out), rt_add(rt_add(v_str_lit("|"), (*l_name2)), v_str_lit("|"))); rt_trace_pop(); _tr; }), v_int(0), "<")))) {
(*l_out) = rt_add(rt_add((*l_out), (*l_name2)), v_str_lit("|"));
}
}
(*l_i) = rt_add((*l_end), v_int(1));
if (rt_truth(rt_cmp((*l_end), (*l_n), ">="))) {
break;
}
}
return (*l_out);
return v_null();
}
static Val wrap_c_mi_decl_symbols(void *env, Val *args) { (void)env; (void)args; return c_mi_decl_symbols(args[0]); }
static Fn fob_c_mi_decl_symbols = {wrap_c_mi_decl_symbols, 0, 0, 1, "mi_decl_symbols"};
static Val c_mi_mangle_symbols(Val p0, Val p1, Val p2) {
Val *l_src = rt_box_new(p0);
Val *l_syms = rt_box_new(p1);
Val *l_prefix = rt_box_new(p2);
Val *l_out = rt_box_new((*l_src));
Val *l_list = rt_box_new(rt_strcut((*l_syms), v_int(1), rt_len((*l_syms))));
Val *l_j = rt_box_new(v_int(0));
while (rt_truth(rt_cmp((*l_j), rt_len((*l_list)), "<"))) {
Val *l_bar = rt_box_new(({ rt_trace_push("strfind", 0); Val _tr = c_strfind(rt_strcut((*l_list), (*l_j), rt_len((*l_list))), v_str_lit("|")); rt_trace_pop(); _tr; }));
if (rt_truth(rt_cmp((*l_bar), v_int(0), "<"))) {
break;
}
Val *l_name2 = rt_box_new(rt_strcut((*l_list), (*l_j), rt_add((*l_j), (*l_bar))));
(*l_out) = ({ rt_trace_push("mi_replace_word", 0); Val _tr = c_mi_replace_word((*l_out), (*l_name2), rt_add((*l_prefix), (*l_name2))); rt_trace_pop(); _tr; });
(*l_j) = rt_add(rt_add((*l_j), (*l_bar)), v_int(1));
}
return (*l_out);
return v_null();
}
static Val wrap_c_mi_mangle_symbols(void *env, Val *args) { (void)env; (void)args; return c_mi_mangle_symbols(args[0], args[1], args[2]); }
static Fn fob_c_mi_mangle_symbols = {wrap_c_mi_mangle_symbols, 0, 0, 3, "mi_mangle_symbols"};
static Val c_mi_attrs_of(Val p0, Val p1) {
Val *l_syms = rt_box_new(p0);
Val *l_prefix = rt_box_new(p1);
Val *l_out = rt_box_new(v_str_lit("{"));
Val *l_list = rt_box_new(rt_strcut((*l_syms), v_int(1), rt_len((*l_syms))));
Val *l_j = rt_box_new(v_int(0));
Val *l_first = rt_box_new(v_bool(1));
while (rt_truth(rt_cmp((*l_j), rt_len((*l_list)), "<"))) {
Val *l_bar = rt_box_new(({ rt_trace_push("strfind", 0); Val _tr = c_strfind(rt_strcut((*l_list), (*l_j), rt_len((*l_list))), v_str_lit("|")); rt_trace_pop(); _tr; }));
if (rt_truth(rt_cmp((*l_bar), v_int(0), "<"))) {
break;
}
Val *l_name2 = rt_box_new(rt_strcut((*l_list), (*l_j), rt_add((*l_j), (*l_bar))));
if (rt_truth(rt_not((*l_first)))) {
(*l_out) = rt_add((*l_out), v_str_lit(", "));
}
(*l_first) = v_bool(0);
(*l_out) = rt_add(rt_add(rt_add(rt_add(rt_add((*l_out), v_str_lit("\"")), (*l_name2)), v_str_lit("\": ")), (*l_prefix)), (*l_name2));
(*l_j) = rt_add(rt_add((*l_j), (*l_bar)), v_int(1));
}
return rt_add((*l_out), v_str_lit("}"));
return v_null();
}
static Val wrap_c_mi_attrs_of(void *env, Val *args) { (void)env; (void)args; return c_mi_attrs_of(args[0], args[1]); }
static Fn fob_c_mi_attrs_of = {wrap_c_mi_attrs_of, 0, 0, 2, "mi_attrs_of"};
static Val c_mi_split_import(Val p0) {
Val *l_rest = rt_box_new(p0);
Val *l_pos = rt_box_new(({ rt_trace_push("strfind", 0); Val _tr = c_strfind((*l_rest), v_str_lit(" as ")); rt_trace_pop(); _tr; }));
if (rt_truth(rt_cmp((*l_pos), v_int(0), ">="))) {
Val *l_mod1 = rt_box_new(({ rt_trace_push("mi_trim", 0); Val _tr = c_mi_trim(rt_strcut((*l_rest), v_int(0), (*l_pos))); rt_trace_pop(); _tr; }));
Val *l_alias1 = rt_box_new(({ rt_trace_push("mi_trim", 0); Val _tr = c_mi_trim(rt_strcut((*l_rest), rt_add((*l_pos), v_int(4)), rt_len((*l_rest)))); rt_trace_pop(); _tr; }));
return rt_add(rt_add((*l_mod1), v_str_lit("|")), (*l_alias1));
}
Val *l_mod2 = rt_box_new(({ rt_trace_push("mi_trim", 0); Val _tr = c_mi_trim((*l_rest)); rt_trace_pop(); _tr; }));
Val *l_alias2 = rt_box_new((*l_mod2));
Val *l_i = rt_box_new(rt_sub(rt_len((*l_mod2)), v_int(1)));
while (rt_truth(rt_cmp((*l_i), v_int(0), ">="))) {
Val *l_c = rt_box_new(rt_strcut((*l_mod2), (*l_i), rt_add((*l_i), v_int(1))));
if (rt_truth(rt_or(rt_or(rt_cmp((*l_c), v_str_lit("."), "=="), rt_cmp((*l_c), v_str_lit("/"), "==")), rt_cmp((*l_c), v_str_lit("\\"), "==")))) {
(*l_alias2) = rt_strcut((*l_mod2), rt_add((*l_i), v_int(1)), rt_len((*l_mod2)));
break;
}
(*l_i) = rt_sub((*l_i), v_int(1));
}
return rt_add(rt_add((*l_mod2), v_str_lit("|")), (*l_alias2));
return v_null();
}
static Val wrap_c_mi_split_import(void *env, Val *args) { (void)env; (void)args; return c_mi_split_import(args[0]); }
static Fn fob_c_mi_split_import = {wrap_c_mi_split_import, 0, 0, 1, "mi_split_import"};
static Val c_mi_expand_imports(Val p0, Val p1) {
Val *l_src = rt_box_new(p0);
Val *l_script = rt_box_new(p1);
Val *l_n = rt_box_new(rt_len((*l_src)));
Val *l_i = rt_box_new(v_int(0));
Val *l_out = rt_box_new(v_str_lit(""));
Val *l_seen = rt_box_new(v_str_lit("|"));
while (rt_truth(rt_cmp((*l_i), (*l_n), "<="))) {
Val *l_nl = rt_box_new(({ rt_trace_push("strfind", 0); Val _tr = c_strfind(rt_strcut((*l_src), (*l_i), (*l_n)), v_str_lit("\n")); rt_trace_pop(); _tr; }));
Val *l_end = rt_box_new((*l_n));
Val *l_line = rt_box_new(rt_strcut((*l_src), (*l_i), (*l_n)));
if (rt_truth(rt_cmp((*l_nl), v_int(0), ">="))) {
(*l_end) = rt_add((*l_i), (*l_nl));
(*l_line) = rt_strcut((*l_src), (*l_i), (*l_end));
}
Val *l_t = rt_box_new(({ rt_trace_push("mi_trim", 0); Val _tr = c_mi_trim((*l_line)); rt_trace_pop(); _tr; }));
if (rt_truth(rt_cmp(({ rt_trace_push("strfind", 0); Val _tr = c_strfind((*l_t), v_str_lit("import ")); rt_trace_pop(); _tr; }), v_int(0), "=="))) {
Val *l_rest = rt_box_new(({ rt_trace_push("mi_trim", 0); Val _tr = c_mi_trim(rt_strcut((*l_t), v_int(7), rt_len((*l_t)))); rt_trace_pop(); _tr; }));
Val *l_pair = rt_box_new(({ rt_trace_push("mi_split_import", 0); Val _tr = c_mi_split_import((*l_rest)); rt_trace_pop(); _tr; }));
Val *l_bar = rt_box_new(({ rt_trace_push("strfind", 0); Val _tr = c_strfind((*l_pair), v_str_lit("|")); rt_trace_pop(); _tr; }));
Val *l_modname = rt_box_new(rt_strcut((*l_pair), v_int(0), (*l_bar)));
Val *l_alias = rt_box_new(rt_strcut((*l_pair), rt_add((*l_bar), v_int(1)), rt_len((*l_pair))));
Val *l_rel = rt_box_new(rt_add(({ rt_trace_push("mi_replace_dots", 0); Val _tr = c_mi_replace_dots((*l_modname)); rt_trace_pop(); _tr; }), v_str_lit(".impor")));
Val *l_mpath = rt_box_new(rt_add(({ rt_trace_push("mi_dir_of", 0); Val _tr = c_mi_dir_of((*l_script)); rt_trace_pop(); _tr; }), (*l_rel)));
Val *l_msrc = rt_box_new(rt_fileread((*l_mpath)));
if (rt_truth(rt_cmp(rt_len((*l_msrc)), v_int(0), "=="))) {
(*l_mpath) = (*l_rel);
(*l_msrc) = rt_fileread((*l_mpath));
}
if (rt_truth(rt_cmp(rt_len((*l_msrc)), v_int(0), "=="))) {
rt_print_val(v_str_lit("imporc: 找不到模块 ")); rt_print_sep(); rt_print_val((*l_modname)); rt_print_sep(); rt_print_val(v_str_lit("（尝试 ")); rt_print_sep(); rt_print_val((*l_mpath)); rt_print_sep(); rt_print_val(v_str_lit("）")); rt_print_nl();
(*l_out) = rt_add((*l_out), (*l_line));
}
else {
if (rt_truth(rt_cmp(({ rt_trace_push("strfind", 0); Val _tr = c_strfind((*l_seen), rt_add(rt_add(v_str_lit("|"), (*l_modname)), v_str_lit("|"))); rt_trace_pop(); _tr; }), v_int(0), ">="))) {
(*l_out) = rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add((*l_out), v_str_lit("var ")), (*l_alias)), v_str_lit(" = imporc_module(\"")), (*l_modname)), v_str_lit("\", ")), ({ rt_trace_push("mi_attrs_of", 0); Val _tr = c_mi_attrs_of(v_str_lit("|"), v_str_lit("mi_")); rt_trace_pop(); _tr; })), v_str_lit(")\n"));
}
else {
(*l_seen) = rt_add(rt_add((*l_seen), (*l_modname)), v_str_lit("|"));
Val *l_body = rt_box_new(({ rt_trace_push("mi_strip_header", 0); Val _tr = c_mi_strip_header((*l_msrc)); rt_trace_pop(); _tr; }));
Val *l_syms = rt_box_new(({ rt_trace_push("mi_export_symbols", 0); Val _tr = c_mi_export_symbols((*l_body)); rt_trace_pop(); _tr; }));
if (rt_truth(rt_cmp((*l_syms), v_str_lit("|"), "=="))) {
(*l_syms) = ({ rt_trace_push("mi_decl_symbols", 0); Val _tr = c_mi_decl_symbols((*l_body)); rt_trace_pop(); _tr; });
}
Val *l_prefix = rt_box_new(rt_add(rt_add(v_str_lit("mi_"), ({ rt_trace_push("mi_sanitize", 0); Val _tr = c_mi_sanitize((*l_modname)); rt_trace_pop(); _tr; })), v_str_lit("_")));
(*l_body) = ({ rt_trace_push("mi_strip_export", 0); Val _tr = c_mi_strip_export((*l_body)); rt_trace_pop(); _tr; });
(*l_body) = ({ rt_trace_push("mi_mangle_symbols", 0); Val _tr = c_mi_mangle_symbols((*l_body), (*l_syms), (*l_prefix)); rt_trace_pop(); _tr; });
(*l_out) = rt_add((*l_out), (*l_body));
if (rt_truth(rt_cmp(({ rt_trace_push("strfind", 0); Val _tr = c_strfind((*l_body), v_str_lit("\n")); rt_trace_pop(); _tr; }), rt_sub(rt_len((*l_body)), v_int(1)), "!="))) {
(*l_out) = rt_add((*l_out), v_str_lit("\n"));
}
(*l_out) = rt_add(rt_add(rt_add(rt_add(rt_add(rt_add(rt_add((*l_out), v_str_lit("var ")), (*l_alias)), v_str_lit(" = imporc_module(\"")), (*l_modname)), v_str_lit("\", ")), ({ rt_trace_push("mi_attrs_of", 0); Val _tr = c_mi_attrs_of((*l_syms), (*l_prefix)); rt_trace_pop(); _tr; })), v_str_lit(")\n"));
}
}
}
else {
(*l_out) = rt_add((*l_out), (*l_line));
if (rt_truth(rt_cmp((*l_end), (*l_n), "<"))) {
(*l_out) = rt_add((*l_out), v_str_lit("\n"));
}
}
(*l_i) = rt_add((*l_end), v_int(1));
if (rt_truth(rt_cmp((*l_end), (*l_n), ">="))) {
break;
}
}
return (*l_out);
return v_null();
}
static Val wrap_c_mi_expand_imports(void *env, Val *args) { (void)env; (void)args; return c_mi_expand_imports(args[0], args[1]); }
static Fn fob_c_mi_expand_imports = {wrap_c_mi_expand_imports, 0, 0, 2, "mi_expand_imports"};
int main(int argc, char **argv) {
    g_argc = argc; g_argv = argv;
g_gsrc = v_str_lit("");
g_gpos = v_int(0);
g_gtok = v_str_lit("");
g_gtype = v_int(0);
g_gvars = v_str_lit("");
g_gout = v_str_lit("");
g_gfuncs = v_str_lit("");
g_gfuncs2 = v_str_lit("");
g_ginfunc = v_bool(0);
g_glocals = v_str_lit("");
g_gindstack = v_str_lit("0|");
g_g_swinds = v_str_lit("");
g_gprev_nl = v_bool(1);
g_gfail = v_bool(0);
g_gded_pending = v_bool(0);
g_gded_target = v_int(0);
g_gcur_cls = v_str_lit("");
g_gsuper_pending = v_bool(0);
g_gassign_kind = v_str_lit("");
g_gassign_name = v_str_lit("");
g_gassign_base = v_str_lit("");
g_gargseq = v_int(0);
g_gassign_prop = v_str_lit("");
g_gassign_idx = v_str_lit("");
g_gparams = v_str_lit("");
g_gmethod = v_bool(0);
g_gassign_raw = v_str_lit("");
g_genum_types = v_str_lit("");
g_gclosure = v_str_lit("");
g_gdecls = v_str_lit("");
g_gcaps = v_str_lit("");
g_gclos_seq = v_int(0);
g_gclosure_depth = v_int(0);
g_gargs = v_str_lit("");
g_gt2 = v_str_lit("");
g_gp2 = v_int(0);
g_gm = rt_fileread(v_str_lit("imporc_args.txt"));
g_gargs = g_gm;
g_gp2 = v_int(0);
g_gtmp = v_int(0);
({ rt_trace_push("next_arg", 0); Val _tr = c_next_arg(); rt_trace_pop(); _tr; });
g_gmode = v_str_lit("build");
g_gscript = v_str_lit("arg_script.impor");
g_goutname = v_str_lit("out2.exe");
if (rt_truth(rt_cmp(rt_len(g_gt2), v_int(0), ">"))) {
g_gmode = g_gt2;
if (rt_truth(rt_cmp(g_gmode, v_str_lit("version"), "=="))) {
rt_print_val(v_str_lit("imporc (Impor 自举编译器) v4.2.0")); rt_print_nl();
rt_exit(v_int(0));
}
({ rt_trace_push("next_arg", 0); Val _tr = c_next_arg(); rt_trace_pop(); _tr; });
if (rt_truth(rt_cmp(rt_len(g_gt2), v_int(0), ">"))) {
g_gscript = g_gt2;
({ rt_trace_push("next_arg", 0); Val _tr = c_next_arg(); rt_trace_pop(); _tr; });
if (rt_truth(rt_cmp(g_gt2, v_str_lit("-o"), "=="))) {
({ rt_trace_push("next_arg", 0); Val _tr = c_next_arg(); rt_trace_pop(); _tr; });
g_goutname = g_gt2;
}
}
}
g_src = rt_fileread(g_gscript);
g_src = ({ rt_trace_push("mi_expand_imports", 0); Val _tr = c_mi_expand_imports(g_src, g_gscript); rt_trace_pop(); _tr; });
if (rt_truth(rt_cmp(({ rt_trace_push("strfind", 0); Val _tr = c_strfind(g_src, v_str_lit("important")); rt_trace_pop(); _tr; }), v_int(0), "=="))) {
g_nlpos = ({ rt_trace_push("strfind", 0); Val _tr = c_strfind(g_src, v_str_lit("\n")); rt_trace_pop(); _tr; });
if (rt_truth(rt_cmp(g_nlpos, v_int(0), ">="))) {
g_src = rt_strcut(g_src, rt_add(g_nlpos, v_int(1)), rt_len(g_src));
}
else {
g_src = v_str_lit("");
}
}
g_gsrc = g_src;
g_gpos = v_int(0);
g_gprev_nl = v_bool(1);
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
while (rt_truth(rt_cmp(g_gtype, v_int(5), "!="))) {
if (rt_truth(rt_cmp(g_gtok, v_str_lit("class"), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
if (rt_truth(rt_cmp(g_gtype, v_int(1), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
}
if (rt_truth(rt_cmp(g_gtok, v_str_lit("("), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
({ rt_trace_push("expect_op", 0); Val _tr = c_expect_op(v_str_lit(")")); rt_trace_pop(); _tr; });
}
g_cldepth = v_int(1);
while (rt_truth(rt_and(rt_cmp(g_cldepth, v_int(0), ">"), rt_cmp(g_gtype, v_int(5), "!=")))) {
if (rt_truth(rt_cmp(g_gtype, v_int(6), "=="))) {
g_cldepth = rt_add(g_cldepth, v_int(1));
}
else {
if (rt_truth(rt_cmp(g_gtype, v_int(7), "=="))) {
g_cldepth = rt_sub(g_cldepth, v_int(1));
}
}
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
}
}
else {
if (rt_truth(rt_or(rt_cmp(g_gtok, v_str_lit("func"), "=="), rt_cmp(g_gtok, v_str_lit("def"), "==")))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
if (rt_truth(rt_cmp(g_gtype, v_int(1), "=="))) {
g_fname = g_gtok;
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
g_np = v_int(0);
if (rt_truth(rt_cmp(g_gtok, v_str_lit("("), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
if (rt_truth(rt_cmp(g_gtok, v_str_lit(")"), "!="))) {
while (rt_truth(v_int(1))) {
g_np = rt_add(g_np, v_int(1));
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
if (rt_truth(rt_cmp(g_gtok, v_str_lit(","), "=="))) {
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
}
else {
break;
}
}
}
}
({ rt_trace_push("add_func", 0); Val _tr = c_add_func(g_fname, g_np); rt_trace_pop(); _tr; });
}
}
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
}
}
g_gpos = v_int(0);
g_gprev_nl = v_bool(1);
g_gindstack = v_str_lit("0|");
g_gvars = v_str_lit("");
g_gout = v_str_lit("");
g_gfuncs = v_str_lit("");
g_gclosure = v_str_lit("");
g_gdecls = v_str_lit("");
g_gcaps = v_str_lit("");
g_gclos_seq = v_int(0);
g_gclosure_depth = v_int(0);
({ rt_trace_push("scan", 0); Val _tr = c_scan(); rt_trace_pop(); _tr; });
while (rt_truth(rt_and(rt_cmp(g_gtype, v_int(5), "!="), rt_not(g_gfail)))) {
({ rt_trace_push("gen_block", 0); Val _tr = c_gen_block(); rt_trace_pop(); _tr; });
}
if (rt_truth(g_gfail)) {
rt_print_val(v_str_lit("imporc.impor: 编译失败，不生成产物")); rt_print_nl();
}
else {
g_rt = rt_fileread(v_str_lit("impor/c_runtime.c"));
g_ccode = rt_add(v_str_lit("/* by imporc.impor (self-hosted) */\n"), g_rt);
g_proto = v_str_lit("");
g_i2 = v_int(0);
g_f1 = v_str_lit("");
g_fcnt = v_int(0);
g_curname = v_str_lit("");
g_curnp = v_int(0);
while (rt_truth(rt_cmp(g_i2, rt_len(g_gfuncs2), "<"))) {
g_c2 = rt_strcut(g_gfuncs2, g_i2, rt_add(g_i2, v_int(1)));
if (rt_truth(rt_cmp(g_c2, v_str_lit(";"), "=="))) {
if (rt_truth(rt_cmp(g_fcnt, v_int(0), "=="))) {
g_curname = g_f1;
g_fcnt = v_int(1);
}
else {
g_curnp = rt_to_num(g_f1);
g_ps = v_str_lit("");
g_j2 = v_int(0);
while (rt_truth(rt_cmp(g_j2, g_curnp, "<"))) {
if (rt_truth(rt_cmp(g_j2, v_int(0), ">"))) {
g_ps = rt_add(g_ps, v_str_lit(", "));
}
g_ps = rt_add(g_ps, v_str_lit("Val"));
g_j2 = rt_add(g_j2, v_int(1));
}
g_proto = rt_add(rt_add(rt_add(rt_add(rt_add(g_proto, v_str_lit("static Val c_")), g_curname), v_str_lit("(")), g_ps), v_str_lit(");\n"));
g_fcnt = v_int(0);
}
g_f1 = v_str_lit("");
}
else {
g_f1 = rt_add(g_f1, g_c2);
}
g_i2 = rt_add(g_i2, v_int(1));
}
g_ccode = rt_add(g_ccode, g_proto);
g_ccode = rt_add(g_ccode, g_gdecls);
g_i = v_int(0);
g_vn = v_str_lit("");
while (rt_truth(rt_cmp(g_i, rt_len(g_gvars), "<"))) {
g_c = rt_strcut(g_gvars, g_i, rt_add(g_i, v_int(1)));
if (rt_truth(rt_cmp(g_c, v_str_lit("|"), "=="))) {
if (rt_truth(rt_cmp(rt_len(g_vn), v_int(0), ">"))) {
g_ccode = rt_add(rt_add(rt_add(g_ccode, v_str_lit("Val g_")), g_vn), v_str_lit(";\n"));
g_vn = v_str_lit("");
}
}
else {
g_vn = rt_add(g_vn, g_c);
}
g_i = rt_add(g_i, v_int(1));
}
g_ccode = rt_add(g_ccode, g_gfuncs);
g_ccode = rt_add(g_ccode, g_gclosure);
g_ccode = rt_add(rt_add(rt_add(g_ccode, v_str_lit("int main(int argc, char **argv) {\n    g_argc = argc; g_argv = argv;\n")), g_gout), v_str_lit("    return 0;\n}\n"));
rt_filewrite(v_str_lit("out2.c"), g_ccode);
g_rc = rt_system(rt_add(v_str_lit("impor\\tcc\\win32\\tcc.exe out2.c -o "), g_goutname));
if (rt_truth(rt_cmp(g_rc, v_int(0), "!="))) {
rt_print_val(v_str_lit("imporc.impor: tcc 失败 rc=")); rt_print_sep(); rt_print_val(g_rc); rt_print_nl();
}
else {
rt_print_val(v_str_lit("imporc.impor: 已生成 ")); rt_print_sep(); rt_print_val(g_goutname); rt_print_nl();
if (rt_truth(rt_cmp(g_gmode, v_str_lit("run"), "=="))) {
g_rc2 = rt_system(g_goutname);
rt_exit(g_rc2);
}
}
}
    return 0;
}
