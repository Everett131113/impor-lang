
/* Impor C 运行库 —— 由 impor build 自动内嵌，勿手改 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <ctype.h>
#include <time.h>
#include <stdarg.h>
#include <setjmp.h>
#include <limits.h>
#ifdef _WIN32
#include <windows.h>
/* TCC 自带的最小 Windows 头没有声明 winnls 接口，手动补上。
   仅用于把 UTF-8 路径转成 UTF-16，避免中文目录被 ANSI 代码页误读。 */
#ifndef CP_UTF8
#define CP_UTF8 65001
#endif
#ifndef CP_ACP
#define CP_ACP 0
#endif
int __stdcall MultiByteToWideChar(unsigned int CodePage, unsigned long dwFlags,
                                  const char *lpMultiByteStr, int cbMultiByte,
                                  wchar_t *lpWideCharStr, int cchWideChar);
int __stdcall WideCharToMultiByte(unsigned int CodePage, unsigned long dwFlags,
                                  const wchar_t *lpWideCharStr, int cchWideChar,
                                  char *lpMultiByteStr, int cbMultiByte,
                                  const char *lpDefaultChar, int *lpUsedDefaultChar);
#else
#include <unistd.h>
#include <dlfcn.h>
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

/* v3.9：FFI 调用。ffi_open 返回库路径；ffi_call 由代码生成器按字面量
   argtypes 生成强类型包装（见 ctrans._gen_ffi_call），这里提供符号解析。 */
static Val rt_ffi_open(Val path);
static Val rt_ffi_call(Val lib, Val func, Val args, Val ret, Val argtypes);
static void *rt_ffi_sym(Val lib, Val func);

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
#ifndef IMPOR_HAS_SRC
static const char *g_src_lines[] = { "" };
static int g_src_n = 0;
#else
extern const char *g_src_lines[];
extern int g_src_n;
#endif
static int g_cur_line = 0;
static void rt_set_line(int n) { g_cur_line = n; }
static void rt_print_snippet(void) {
    int ln = g_cur_line;
    if (ln < 1 || ln > g_src_n) return;
    printf("  %d │ %s\n", ln, g_src_lines[ln - 1]);
    printf("    │ ^\n");
}

static const char *rt_diag_why(const char *t) {
    if (strcmp(t, "ZeroDivisionError") == 0) return "除数是 0，数学上没有定义，运行无法继续。";
    if (strcmp(t, "TypeError") == 0) return "操作数的类型不匹配；Impor 不做隐式类型转换。";
    if (strcmp(t, "IndexError") == 0) return "下标超出了容器的有效范围（Impor 下标从 0 开始，-1 表示最后一项）。";
    if (strcmp(t, "KeyError") == 0) return "字典里没有这个键；读取前应先确认键存在。";
    if (strcmp(t, "ValueError") == 0) return "传入的值不符合该函数或操作的要求。";
    if (strcmp(t, "AttributeError") == 0) return "这个对象 / 类 / 实例上没有该属性。";
    if (strcmp(t, "AssertionError") == 0) return "assert 后面的条件求值为 false，说明代码假设不成立。";
    if (strcmp(t, "NameError") == 0) return "Impor 不做隐式全局：名字必须先用 var / const / func / import 声明，之后才能使用。";
    if (strcmp(t, "ConstError") == 0) return "const 声明的名字是只读的，再次赋值会破坏它的不变式。";
    if (strcmp(t, "ImportError") == 0) return "导入的名字在模块搜索路径里找不到对应的文件或包。";
    if (strcmp(t, "RecursionError") == 0) return "递归层数超过了上限，继续下去会耗尽调用栈。";
    if (strcmp(t, "OverflowError") == 0) return "整数超出 64 位有符号范围，无法继续精确计算。";
    if (strcmp(t, "MemoryError") == 0) return "内存分配失败，通常是请求的容器太大。";
    if (strcmp(t, "FFIArchError") == 0) return "解释器/产物与目标库的位数不一致：32 位进程装不进 64 位 DLL，64 位进程也装不进 32 位 DLL。";
    if (strcmp(t, "FFIAccessError") == 0) return "这个原生库不在编译期烘焙的 FFI 允许清单里；运行时不能临时扩清单。";
    if (strcmp(t, "FFIError") == 0) return "原生库或函数不可用，或 FFI 参数/返回类型与函数签名不匹配。";
    return "Impor 执行到这一步时遇到了未预期的情况。";
}

static const char *rt_diag_fix(const char *t) {
    if (strcmp(t, "ZeroDivisionError") == 0) return "在除法前判断除数（if b != 0 { ... }），或先检查数据来源是否可能为 0。";
    if (strcmp(t, "TypeError") == 0) return "用 str() / num() / int() / float() 显式转换后再运算，或修正其中一个操作数。";
    if (strcmp(t, "IndexError") == 0) return "先用 len(容器) 检查边界，或改用 for x in 容器 / range(len(容器)) 遍历。";
    if (strcmp(t, "KeyError") == 0) return "先用 if key in dict 判断，或改用 dict.get(key, 默认值)。";
    if (strcmp(t, "ValueError") == 0) return "检查输入内容与格式，必要时先打印原始值再解析。";
    if (strcmp(t, "AttributeError") == 0) return "检查属性名拼写；实例属性要在 __init__ 里先赋值，再去读取。";
    if (strcmp(t, "AssertionError") == 0) return "检查断言的前置条件，必要时在断言前打印相关变量，确认哪一步和预期不一致。";
    if (strcmp(t, "NameError") == 0) return "先用 var x = ... 声明，或用 import 引入；同时检查拼写与大小写是否一致。";
    if (strcmp(t, "ConstError") == 0) return "确实需要修改就把它声明成 var；否则把赋值改成对另一个变量的计算。";
    if (strcmp(t, "ImportError") == 0) return "确认文件名（含 .impor 后缀）与相对路径正确；可用 impor list-modules 查看当前可发现的模块。";
    if (strcmp(t, "RecursionError") == 0) return "检查递归的终止条件是否会到达；也可以把递归改写成循环。";
    if (strcmp(t, "OverflowError") == 0) return "改用更小的数值范围，或拆成多步计算并检查边界。";
    if (strcmp(t, "MemoryError") == 0) return "减少一次申请的元素数量，或改用分批处理。";
    if (strcmp(t, "FFIArchError") == 0) return "32 位产物请加载 32 位库，64 位产物请加载 64 位库；64 位库用 --c-source 以 cl / gcc -m64 编译。";
    if (strcmp(t, "FFIAccessError") == 0) return "在源码同目录或上层的 .impor/ffi_allow.json 的 allow 数组登记库文件名，然后重新 build（清单会烘焙进产物）。";
    if (strcmp(t, "FFIError") == 0) return "确认库文件存在、函数名拼写正确，并让 argtypes 与原生函数签名逐项一致；C 后端只支持字面量 argtypes。";
    return "检查该行的输入与状态；若怀疑是编译器内部问题，设 IMPOR_DEBUG=1 查看完整调试栈。";
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
    const char *t = g_err_type[0] ? g_err_type : "Error";
    if (g_cur_line < 1 && g_trace_n > 0) g_cur_line = g_trace_line[g_trace_n - 1];
    printf("运行错误: 第%d行: %s: %s [%s]\n", g_cur_line, t,
           g_err_msg, rt_err_code(g_err_type));
    printf("  为什么： %s\n", rt_diag_why(t));
    printf("  修复： %s\n", rt_diag_fix(t));
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
    if (g_cur_line < 1 && g_trace_n > 0) g_cur_line = g_trace_line[g_trace_n - 1];
    printf("运行错误: 第%d行: %s: %s [%s]\n", g_cur_line, t, msg ? msg : "", rt_err_code(t));
    printf("  为什么： %s\n", rt_diag_why(t));
    printf("  修复： %s\n", rt_diag_fix(t));
    rt_print_snippet();
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

/* 顺序切片缓存：strcut(src, i, i+1) 在扫描器里按 i 递增调用，
   若每次都从头 rt_u8_at() 会把 86KB 自举种子变成 O(n^2)。
   这里缓存“同一字符串上一次的字符下标 → 字节偏移”，顺序访问退化为 O(1)。 */
static const char *rt_u8c_s = NULL;
static size_t rt_u8c_blen = 0;
static long long rt_u8c_n = 0;
static long long rt_u8c_idx = 0;
static size_t rt_u8c_off = 0;
static unsigned char rt_u8c_first = 0, rt_u8c_last = 0;

static void rt_u8c_reset(const char *s) {
    rt_u8c_s = s;
    rt_u8c_blen = s ? strlen(s) : 0;
    rt_u8c_n = s ? rt_u8len(s) : 0;
    rt_u8c_idx = 0;
    rt_u8c_off = 0;
    rt_u8c_first = (s && rt_u8c_blen) ? (unsigned char)s[0] : 0;
    rt_u8c_last = (s && rt_u8c_blen) ? (unsigned char)s[rt_u8c_blen - 1] : 0;
}

static size_t rt_u8c_at(const char *s, long long idx) {
    if (!s) return 0;
    if (s != rt_u8c_s ||
        (rt_u8c_blen && ((unsigned char)s[0] != rt_u8c_first ||
                         (unsigned char)s[rt_u8c_blen - 1] != rt_u8c_last)))
        rt_u8c_reset(s);
    if (idx <= 0) return 0;
    if (idx > rt_u8c_n) idx = rt_u8c_n;
    if (idx < rt_u8c_idx) {
        rt_u8c_idx = 0;
        rt_u8c_off = 0;
    }
    while (rt_u8c_idx < idx && rt_u8c_off < rt_u8c_blen) {
        unsigned char c = (unsigned char)s[rt_u8c_off];
        size_t w = 1;
        if (c >= 0xF0) w = 4; else if (c >= 0xE0) w = 3; else if (c >= 0xC0) w = 2;
        rt_u8c_off += w;
        rt_u8c_idx++;
    }
    if (rt_u8c_off > rt_u8c_blen) rt_u8c_off = rt_u8c_blen;
    return rt_u8c_off;
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
        /* 两个操作数都是字符串时直接引用原缓冲区：字符串不可变，这里只读，
           避免 rt_tostr() 对累积缓冲再各复制一份（自举编译 86KB 种子时这是主要开销）。 */
        sa = a; sb = b;
        na = strlen(sa.s ? sa.s : ""); nb = strlen(sb.s ? sb.s : "");
        n = na + nb + 1;
        buf = (char *)malloc(n);
        if (!buf) rt_abort("内存不足");
        memcpy(buf, sa.s ? sa.s : "", na);
        memcpy(buf + na, sb.s ? sb.s : "", nb + 1);
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
static char g_exedir[4096] = ".";
static void rt_args_init(int argc, char **argv) {
    g_argc = argc;
    g_argv = argv;
    {
        int have_dir = 0;
#ifdef _WIN32
        /* Windows 的 argv[0] 是 ANSI 代码页字节，含中文路径会与 UTF-8 字符串混用；
           直接取宽字符模块路径再转 UTF-8，exedir() 在任何代码页下都可用。 */
        wchar_t wpath[4096];
        if (GetModuleFileNameW(NULL, wpath, (DWORD)(sizeof(wpath) / sizeof(wpath[0]))) > 0
            && WideCharToMultiByte(CP_UTF8, 0, wpath, -1, g_exedir,
                                   (int)sizeof(g_exedir), NULL, NULL) > 0) {
            have_dir = 1;
        }
#endif
        if (!have_dir && argc > 0 && argv && argv[0] && argv[0][0]) {
            size_t n = strlen(argv[0]);
            if (n >= sizeof(g_exedir)) n = sizeof(g_exedir) - 1;
            memcpy(g_exedir, argv[0], n);
            g_exedir[n] = '\0';
            have_dir = 1;
        }
        if (have_dir) {
            char *s1 = strrchr(g_exedir, '\\');
            char *s2 = strrchr(g_exedir, '/');
            char *s = NULL;
            if (s1 && s2) s = s1 > s2 ? s1 : s2;
            else s = s1 ? s1 : s2;
            if (s) *s = '\0';
            else strcpy(g_exedir, ".");
        } else {
            strcpy(g_exedir, ".");
        }
    }
}
static Val rt_args(void) {
    Val r = v_arr();
    r.a = arr_new(g_argc > 1 ? g_argc - 1 : 0);
    for (int k = 1; k < g_argc; k++) {
#ifdef _WIN32
        /* CRT 的 main 收到的是 ANSI argv；Impor 字符串统一 UTF-8，
           不转换会让中文路径变成 U+FFFD，tcc/重命名都无法使用。 */
        wchar_t warg[4096];
        char uarg[8192];
        if (MultiByteToWideChar(CP_ACP, 0, g_argv[k], -1, warg, 4096) > 0 &&
            WideCharToMultiByte(CP_UTF8, 0, warg, -1, uarg, 8192, NULL, NULL) > 0) {
            arr_push(r.a, v_str(uarg));
            continue;
        }
#endif
        arr_push(r.a, v_str(g_argv[k]));
    }
    return r;
}
/* 当前可执行文件所在目录；供自举编译器定位随包运行时。 */
static Val rt_exedir(void) { return v_str(g_exedir); }

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
    if (d == 0) rt_throw("ZeroDivisionError", "division by zero");
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
static Val rt_to_int(Val v) {
    if (v.t == V_INT) return v;
    if (v.t == V_FLOAT) return v_int((long long)v.f);
    if (v.t == V_BOOL) return v_int(v.i ? 1 : 0);
    if (v.t == V_STR) {
        const char *s = v.s ? v.s : "";
        while (*s == ' ' || *s == '\t' || *s == '\n') s++;
        return v_int(atoll(s));
    }
    return v_int(0);
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
    long long a = rt_int(st), b = rt_int(ed);
    long long n = rt_u8c_n;             /* 缓存字符数，避免每次 O(len) 重扫 */
    size_t boff, eoff;
    char *buf;
    Val r;
    if (s != rt_u8c_s) {
        rt_u8c_reset(s);
        n = rt_u8c_n;
    }
    if (a < 0) a = 0;
    if (b > n) b = n;
    if (a >= b) return v_str("");
    boff = rt_u8c_at(s, a);
    eoff = rt_u8c_at(s, b);
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
    if (y == 0) rt_throw("ZeroDivisionError", "division by zero");
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
/* v3.6.13：Windows 下路径字符串是 UTF-8（args 文件 / 源码字符串），
   直接 fopen 会按 ANSI 代码页解释，中文目录打不开。统一先转 UTF-16。 */
static FILE *rt_fopen_utf8(const char *path, const char *mode) {
#ifdef _WIN32
    wchar_t wpath[4096];
    wchar_t wmode[16];
    if (MultiByteToWideChar(CP_UTF8, 0, path, -1, wpath, 4096) > 0 &&
        MultiByteToWideChar(CP_UTF8, 0, mode, -1, wmode, 16) > 0) {
        FILE *fp = _wfopen(wpath, wmode);
        if (fp) return fp;
    }
#endif
    return fopen(path, mode);
}
static Val rt_fileread(Val path) {
    FILE *fp = rt_fopen_utf8(rt_str_of(path), "rb");
    if (!fp) rt_abort("fileread: 无法打开文件");
    fseek(fp, 0, SEEK_END);
    long n = ftell(fp);
    fseek(fp, 0, SEEK_SET);
    char *buf = (char *)malloc((size_t)n + 1);
    if (!buf) { fclose(fp); rt_abort("fileread: 内存不足"); }
    size_t got = fread(buf, 1, (size_t)n, fp);
    buf[got] = '\0';
    fclose(fp);
    /* v3.8.3：源码文件带 UTF-8 BOM 时剥离，避免 imporc 把 \ufeff 当成语句。 */
    if (got >= 3 && (unsigned char)buf[0] == 0xEF &&
        (unsigned char)buf[1] == 0xBB && (unsigned char)buf[2] == 0xBF) {
        memmove(buf, buf + 3, got - 2);
    }
    return v_str(buf);
}
static Val rt_filewrite(Val path, Val content) {
    FILE *fp = rt_fopen_utf8(rt_str_of(path), "wb");
    if (!fp) rt_abort("filewrite: 无法写入文件");
    Val cs = rt_tostr(content);
    fwrite(cs.s, 1, strlen(cs.s), fp);
    fclose(fp);
    return v_bool(1);
}
static Val rt_filemv(Val oldpath, Val newpath) {
#ifdef _WIN32
    wchar_t wold[4096], wnew[4096];
    if (MultiByteToWideChar(CP_UTF8, 0, rt_str_of(oldpath), -1, wold, 4096) <= 0 ||
        MultiByteToWideChar(CP_UTF8, 0, rt_str_of(newpath), -1, wnew, 4096) <= 0)
        rt_abort("filemv: 路径无效");
    BOOL moved = MoveFileExW(wold, wnew, MOVEFILE_REPLACE_EXISTING);
    if (!moved) {
        /* 自举编译器从命令行拿到的路径可能是 ANSI（GBK）；UTF-8 解释失败时按 ANSI 再试。 */
        MultiByteToWideChar(CP_ACP, 0, rt_str_of(oldpath), -1, wold, 4096);
        MultiByteToWideChar(CP_ACP, 0, rt_str_of(newpath), -1, wnew, 4096);
        moved = MoveFileExW(wold, wnew, MOVEFILE_REPLACE_EXISTING);
    }
    if (!moved) {
        if (!CopyFileW(wold, wnew, FALSE))
            rt_abort("filemv: 移动/重命名失败");
        DeleteFileW(wold);
    }
#else
    if (rename(rt_str_of(oldpath), rt_str_of(newpath)) != 0)
        rt_abort("filemv: 移动/重命名失败");
#endif
    return v_bool(1);
}

static Val rt_filewritebytes(Val path, Val data) {
    /* v3.8 原生工具链：把 0..255 的整数数组按字节写入文件 */
    if (data.t != V_ARR) rt_abort("filewritebytes: 第二个参数必须是整数数组");
    FILE *fp = rt_fopen_utf8(rt_str_of(path), "wb");
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
    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    ZeroMemory(&si, sizeof(si));
    si.cb = sizeof(si);
    ZeroMemory(&pi, sizeof(pi));
    wchar_t wcl[8192];
    if (MultiByteToWideChar(CP_UTF8, 0, rt_str_of(cmd), -1, wcl, 8192) <= 0)
        return v_int(-1);
    if (!CreateProcessW(NULL, wcl, NULL, NULL, FALSE, 0, NULL, NULL, &si, &pi))
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

/* ================= v3.2.1 JSON / 正则 / FFI =================
   目标：解释器、C 后端、自举编译器共享同一组基础语义。
   JSON 解码器支持对象/数组/字符串/数字/true/false/null；正则支持
   ^ $ . * + ? \d \w \s、字符类 [abc]/[a-z]/[^...] 与转义字面量。
   这是“基础正则”，不引入 PCRE 依赖，保证 tcc 单文件可编译。 */

typedef struct { char *s; long long n, cap; } RtBuf;
static void rt_buf_init(RtBuf *b) { b->s = NULL; b->n = 0; b->cap = 0; }
static void rt_buf_need(RtBuf *b, long long extra) {
    if (b->n + extra + 1 > b->cap) {
        long long cap = b->cap ? b->cap * 2 : 64;
        while (cap < b->n + extra + 1) cap *= 2;
        b->s = (char *)realloc(b->s, (size_t)cap);
        if (!b->s) rt_abort("内存不足");
        b->cap = cap;
    }
}
static void rt_buf_addc(RtBuf *b, char c) { rt_buf_need(b, 1); b->s[b->n++] = c; b->s[b->n] = '\0'; }
static void rt_buf_add(RtBuf *b, const char *s) {
    long long n = (long long)strlen(s ? s : "");
    rt_buf_need(b, n);
    memcpy(b->s + b->n, s ? s : "", (size_t)n);
    b->n += n;
    b->s[b->n] = '\0';
}
static void rt_json_escape(RtBuf *b, const char *s) {
    rt_buf_addc(b, '"');
    for (const char *p = s ? s : ""; *p; p++) {
        unsigned char c = (unsigned char)*p;
        if (c == '"' || c == '\\') { rt_buf_addc(b, '\\'); rt_buf_addc(b, (char)c); }
        else if (c == '\n') rt_buf_add(b, "\\n");
        else if (c == '\r') rt_buf_add(b, "\\r");
        else if (c == '\t') rt_buf_add(b, "\\t");
        else if (c < 0x20) {
            char tmp[8]; snprintf(tmp, sizeof(tmp), "\\u%04x", c); rt_buf_add(b, tmp);
        } else rt_buf_addc(b, (char)c);
    }
    rt_buf_addc(b, '"');
}
static void rt_json_enc(Val v, RtBuf *b);
static void rt_json_enc_arr(Val v, RtBuf *b) {
    rt_buf_addc(b, '[');
    for (long long i = 0; i < (v.a ? v.a->n : 0); i++) {
        if (i) rt_buf_addc(b, ',');
        rt_json_enc(v.a->items[i], b);
    }
    rt_buf_addc(b, ']');
}
static void rt_json_enc_dict(Val v, RtBuf *b) {
    rt_buf_addc(b, '{');
    for (long long i = 0; i + 1 < (v.a ? v.a->n : 0); i += 2) {
        if (i) rt_buf_addc(b, ',');
        Val k = rt_tostr(v.a->items[i]);
        rt_json_escape(b, k.s ? k.s : "");
        rt_buf_addc(b, ':');
        rt_json_enc(v.a->items[i + 1], b);
    }
    rt_buf_addc(b, '}');
}
static void rt_json_enc(Val v, RtBuf *b) {
    char tmp[64];
    switch (v.t) {
        case V_NULL: rt_buf_add(b, "null"); break;
        case V_BOOL: rt_buf_add(b, v.i ? "true" : "false"); break;
        case V_INT: snprintf(tmp, sizeof(tmp), "%lld", v.i); rt_buf_add(b, tmp); break;
        case V_FLOAT: snprintf(tmp, sizeof(tmp), "%.17g", v.f); rt_buf_add(b, tmp); break;
        case V_STR: rt_json_escape(b, v.s ? v.s : ""); break;
        case V_ARR: rt_json_enc_arr(v, b); break;
        case V_DICT: rt_json_enc_dict(v, b); break;
        default: rt_buf_add(b, "null"); break;
    }
}
static Val rt_json_encode(Val v) {
    RtBuf b; rt_buf_init(&b); rt_buf_need(&b, 0);
    rt_json_enc(v, &b);
    Val out = v_str(b.s ? b.s : "");
    free(b.s);
    return out;
}

typedef struct { const char *p; int err; } RtJson;
static void rt_json_ws(RtJson *j) {
    while (*j->p == ' ' || *j->p == '\n' || *j->p == '\r' || *j->p == '\t') j->p++;
}
static Val rt_json_value(RtJson *j);
static Val rt_json_string(RtJson *j) {
    RtBuf b; rt_buf_init(&b); rt_buf_need(&b, 0);
    j->p++;
    while (*j->p && *j->p != '"') {
        char c = *j->p++;
        if (c == '\\') {
            char e = *j->p++;
            if (e == 'n') c = '\n';
            else if (e == 'r') c = '\r';
            else if (e == 't') c = '\t';
            else if (e == 'b') c = '\b';
            else if (e == 'f') c = '\f';
            else if (e == 'u') {
                /* 只解 BMP 常用字符；代理对留给上层工具处理。 */
                unsigned int cp = 0;
                for (int k = 0; k < 4 && j->p[k]; k++) {
                    char h = j->p[k];
                    cp = cp * 16 + (unsigned int)(h >= '0' && h <= '9' ? h - '0' :
                                  h >= 'a' && h <= 'f' ? h - 'a' + 10 :
                                  h >= 'A' && h <= 'F' ? h - 'A' + 10 : 0);
                }
                j->p += 4;
                if (cp < 128) c = (char)cp;
                else { rt_buf_add(&b, "?"); continue; }
            } else c = e;
        }
        rt_buf_addc(&b, c);
    }
    if (*j->p == '"') j->p++;
    else j->err = 1;
    Val out = v_str(b.s ? b.s : "");
    free(b.s);
    return out;
}
static Val rt_json_value(RtJson *j) {
    rt_json_ws(j);
    if (*j->p == '{') {
        j->p++;
        Val d = rt_dict_new();
        rt_json_ws(j);
        if (*j->p == '}') { j->p++; return d; }
        while (*j->p) {
            rt_json_ws(j);
            if (*j->p != '"') { j->err = 1; break; }
            Val k = rt_json_string(j);
            rt_json_ws(j);
            if (*j->p != ':') { j->err = 1; break; }
            j->p++;
            Val v = rt_json_value(j);
            rt_dict_set(d, k, v);
            rt_json_ws(j);
            if (*j->p == ',') { j->p++; continue; }
            if (*j->p == '}') { j->p++; break; }
            j->err = 1; break;
        }
        return d;
    }
    if (*j->p == '[') {
        j->p++;
        Val a = v_arr(); a.a = arr_new(4);
        rt_json_ws(j);
        if (*j->p == ']') { j->p++; return a; }
        while (*j->p) {
            Val v = rt_json_value(j);
            arr_push(a.a, v);
            rt_json_ws(j);
            if (*j->p == ',') { j->p++; continue; }
            if (*j->p == ']') { j->p++; break; }
            j->err = 1; break;
        }
        return a;
    }
    if (*j->p == '"') return rt_json_string(j);
    if (strncmp(j->p, "true", 4) == 0) { j->p += 4; return v_bool(1); }
    if (strncmp(j->p, "false", 5) == 0) { j->p += 5; return v_bool(0); }
    if (strncmp(j->p, "null", 4) == 0) { j->p += 4; return v_null(); }
    if (*j->p == '-' || (*j->p >= '0' && *j->p <= '9')) {
        const char *start = j->p;
        if (*j->p == '-') j->p++;
        while (*j->p >= '0' && *j->p <= '9') j->p++;
        int isf = 0;
        if (*j->p == '.') { isf = 1; j->p++; while (*j->p >= '0' && *j->p <= '9') j->p++; }
        if (*j->p == 'e' || *j->p == 'E') {
            isf = 1; j->p++;
            if (*j->p == '+' || *j->p == '-') j->p++;
            while (*j->p >= '0' && *j->p <= '9') j->p++;
        }
        char *end = NULL;
        if (isf) return v_float(strtod(start, &end));
        long long iv = 0; int neg = 0; const char *q = start;
        if (*q == '-') { neg = 1; q++; }
        while (*q >= '0' && *q <= '9') { iv = iv * 10 + (*q - '0'); q++; }
        return v_int(neg ? -iv : iv);
    }
    j->err = 1;
    return v_null();
}
static Val rt_json_decode(Val text) {
    RtJson j; j.p = rt_str_of(text); j.err = 0;
    Val v = rt_json_value(&j);
    rt_json_ws(&j);
    if (j.err || *j.p) rt_throw("ValueError", "JSON 解析失败");
    return v;
}

/* ---- 基础正则（无捕获组；支持 ^ $ . * + ? \d \w \s 与字符类） ---- */
static int rt_re_class(const char **pp, char c) {
    const char *p = *pp; int neg = 0, hit = 0;
    if (*p == '^') { neg = 1; p++; }
    while (*p && *p != ']') {
        char a = *p++;
        if (*p == '-' && p[1] && p[1] != ']') {
            p++; char b = *p++;
            if (c >= a && c <= b) hit = 1;
        } else if (a == c) hit = 1;
    }
    if (*p == ']') p++;
    *pp = p;
    return neg ? !hit : hit;
}
static const char *rt_re_here(const char *p, const char *s, int *ok);
static const char *rt_re_atom(const char *p, const char *s, int *ok) {
    *ok = 0;
    if (!*p) { *ok = 1; return s; }
    if (*p == '.') { if (*s) { *ok = 1; return s + 1; } return NULL; }
    if (*p == '\\') {
        char e = p[1];
        if (e == 'd') { if (*s >= '0' && *s <= '9') { *ok = 1; return s + 1; } return NULL; }
        if (e == 'w') {
            if ((*s >= '0' && *s <= '9') || (*s >= 'a' && *s <= 'z') ||
                (*s >= 'A' && *s <= 'Z') || *s == '_') { *ok = 1; return s + 1; }
            return NULL;
        }
        if (e == 's') {
            if (*s == ' ' || *s == '\t' || *s == '\n' || *s == '\r') { *ok = 1; return s + 1; }
            return NULL;
        }
        if (*s == e) { *ok = 1; return s + 1; }
        return NULL;
    }
    if (*p == '[') {
        const char *q = p + 1;
        int hit = rt_re_class(&q, *s);
        if (*s && hit) { *ok = 1; return s + 1; }
        return NULL;
    }
    if (*p == '^' || *p == '$') { *ok = 1; return s; }
    if (*s == *p) { *ok = 1; return s + 1; }
    return NULL;
}
static const char *rt_re_here(const char *p, const char *s, int *ok) {
    *ok = 1;
    if (!*p) return s;
    if (*p == '$' && !p[1]) { if (!*s) return s; *ok = 0; return NULL; }
    if (*p == '^') return rt_re_here(p + 1, s, ok);
    const char *atom_end = p + 1;
    if (*p == '\\') atom_end = p + 2;
    else if (*p == '[') {
        atom_end = p + 1;
        while (*atom_end && *atom_end != ']') atom_end++;
        if (*atom_end == ']') atom_end++;
    }
    char quant = *atom_end;
    if (quant == '*' || quant == '+' || quant == '?') {
        const char *rest = atom_end + 1;
        int min = quant == '+' ? 1 : 0;
        int max = quant == '?' ? 1 : -1;
        const char *cur = s; int count = 0; int aok = 0;
        while (1) {
            const char *next = rt_re_atom(p, cur, &aok);
            if (!aok || !next) break;
            cur = next; count++;
            if (max > 0 && count >= max) break;
        }
        if (count < min) { *ok = 0; return NULL; }
        for (int k = count; k >= min; k--) {
            const char *cand = s;
            for (int z = 0; z < k; z++) {
                int aok2 = 0; const char *nx = rt_re_atom(p, cand, &aok2);
                if (!aok2 || !nx) break;
                cand = nx;
            }
            int rok = 0;
            const char *end = rt_re_here(rest, cand, &rok);
            if (rok) return end;
        }
        *ok = 0; return NULL;
    }
    const char *next = rt_re_atom(p, s, ok);
    if (!*ok || !next) return NULL;
    return rt_re_here(atom_end, next, ok);
}
static int rt_re_full(const char *text, const char *pat) {
    int ok = 0;
    const char *end = rt_re_here(pat, text, &ok);
    return ok && end && *end == '\0';
}
static Val rt_regex_match(Val text, Val pattern) {
    return v_bool(rt_re_full(rt_str_of(text), rt_str_of(pattern)));
}
static Val rt_regex_find(Val text, Val pattern) {
    const char *s = rt_str_of(text), *p = rt_str_of(pattern);
    Val out = v_arr(); out.a = arr_new(4);
    int n = (int)strlen(s);
    for (int i = 0; i <= n; i++) {
        int ok = 0;
        const char *beg = s + i;
        const char *end = rt_re_here(p, beg, &ok);
        if (ok && end && end > beg) {
            char *piece = (char *)malloc((size_t)(end - beg) + 1);
            memcpy(piece, beg, (size_t)(end - beg)); piece[end - beg] = '\0';
            arr_push(out.a, v_str(piece));
            free(piece);
            i = (int)(end - s) - 1;
        }
    }
    return out;
}
static Val rt_regex_replace(Val text, Val pattern, Val repl) {
    const char *s = rt_str_of(text), *p = rt_str_of(pattern), *r = rt_str_of(repl);
    RtBuf b; rt_buf_init(&b); rt_buf_need(&b, 0);
    int n = (int)strlen(s);
    for (int i = 0; i <= n; i++) {
        int ok = 0;
        const char *beg = s + i;
        const char *end = rt_re_here(p, beg, &ok);
        if (ok && end && end > beg) {
            rt_buf_add(&b, r);
            i = (int)(end - s) - 1;
        } else if (i < n) rt_buf_addc(&b, s[i]);
    }
    Val out = v_str(b.s ? b.s : "");
    free(b.s);
    return out;
}

/* 3.2.1：C 后端 async 是同步语义，任务在调用点已经完成。
   run_tasks 返回已求值的结果数组；task_status 恒为 done。 */
static Val rt_run_tasks(Val tasks) {
    return tasks;
}

static Val rt_task_status(Val task) {
    (void)task;
    return v_str("done");
}

/* ---------------- v3.9：FFI（C 后端） ----------------
   允许清单由 ctrans 在编译期烘焙为 g_ffi_allow[]；运行时只读，不扩清单。
   ffi_call 的强类型调用在代码生成阶段按字面量 argtypes 生成（ctrans._gen_ffi_call），
   这里只负责库加载、清单校验和符号解析。 */
typedef struct { char *path; void *handle; } RtFfiLib;
#ifndef IMPOR_FFI_ALLOW_BAKED
/* 自举编译器生成的 C 没有 ctrans 的烘焙块：默认拒绝一切原生库。 */
static const char *g_ffi_allow[] = { NULL };
static int g_ffi_allow_n = 0;
#endif
static RtFfiLib g_ffi_libs[32];
static int g_ffi_lib_n = 0;

static const char *rt_ffi_base(const char *path) {
    const char *base = path ? path : "";
    const char *p = base;
    while (*p) {
        if (*p == '/' || *p == '\\') base = p + 1;
        p++;
    }
    return base;
}

static int rt_ffi_ieq(const char *a, const char *b) {
    if (!a || !b) return 0;
    while (*a && *b) {
        if (tolower((unsigned char)*a) != tolower((unsigned char)*b)) return 0;
        a++; b++;
    }
    return *a == '\0' && *b == '\0';
}

static int rt_ffi_allowed(const char *path) {
    const char *base = rt_ffi_base(path);
    for (int i = 0; i < g_ffi_allow_n; i++) {
        if (g_ffi_allow[i] && rt_ffi_ieq(base, rt_ffi_base(g_ffi_allow[i])))
            return 1;
    }
    return 0;
}

#ifdef _WIN32
static int rt_pe_machine(const char *path, unsigned short *out) {
    FILE *f = fopen(path, "rb");
    if (!f) return 0;
    unsigned char sig[4];
    unsigned int off = 0;
    unsigned short machine = 0;
    int ok = 0;
    if (fread(sig, 1, 2, f) == 2 && sig[0] == 'M' && sig[1] == 'Z') {
        fseek(f, 0x3c, SEEK_SET);
        if (fread(&off, 1, 4, f) == 4) {
            fseek(f, (long)off, SEEK_SET);
            if (fread(sig, 1, 4, f) == 4 && sig[0] == 'P' && sig[1] == 'E' &&
                sig[2] == 0 && sig[3] == 0) {
                fseek(f, (long)off + 4, SEEK_SET);
                if (fread(&machine, 1, 2, f) == 2) {
                    *out = machine;
                    ok = 1;
                }
            }
        }
    }
    fclose(f);
    return ok;
}
#endif

static void *rt_ffi_load(const char *path) {
    if (!path || !*path)
        rt_throw("FFIError", "ffi_open 需要非空的库路径");
    if (!rt_ffi_allowed(path)) {
        char msg[512];
        snprintf(msg, sizeof(msg),
                 "原生库 %s 不在编译期 FFI 允许清单内（清单来自 .impor/ffi_allow.json 与 IMPOR_FFI_ALLOW）",
                 path);
        rt_throw("FFIAccessError", msg);
    }
#ifdef _WIN32
    {
        unsigned short machine = 0;
        if (rt_pe_machine(path, &machine)) {
            int lib_bits = (machine == 0x014c || machine == 0x01c4) ? 32 : 64;
            int host_bits = (int)(sizeof(void *) * 8);
            if (lib_bits != host_bits) {
                char msg[640];
                snprintf(msg, sizeof(msg),
                         "位宽不匹配：当前产物为 %d 位，目标库为 %d 位。"
                         "32 位产物请加载 32 位库；64 位库请用 --c-source 以 cl / gcc -m64 编译",
                         host_bits, lib_bits);
                rt_throw("FFIArchError", msg);
            }
        }
    }
#endif
    for (int i = 0; i < g_ffi_lib_n; i++) {
        if (g_ffi_libs[i].path && strcmp(g_ffi_libs[i].path, path) == 0)
            return g_ffi_libs[i].handle;
    }
    if (g_ffi_lib_n >= 32)
        rt_throw("FFIError", "已加载的原生库超过 32 个");
#ifdef _WIN32
    HMODULE h = LoadLibraryA(path);
#else
    void *h = dlopen(path, RTLD_NOW | RTLD_LOCAL);
#endif
    if (!h) {
        char msg[640];
        snprintf(msg, sizeof(msg), "加载原生库失败：%s（请确认位数与当前产物一致，且路径可访问）", path);
        rt_throw("FFIError", msg);
    }
    g_ffi_libs[g_ffi_lib_n].path = strdup(path);
    g_ffi_libs[g_ffi_lib_n].handle = (void *)h;
    g_ffi_lib_n++;
    return (void *)h;
}

static Val rt_ffi_open(Val path) {
    if (path.t != V_STR || !path.s)
        rt_throw("FFIError", "ffi_open 需要字符串库路径");
    rt_ffi_load(path.s);
    return v_str(path.s);
}

static void *rt_ffi_sym(Val lib, Val func) {
    if (lib.t != V_STR || !lib.s)
        rt_throw("FFIError", "ffi_call 的库句柄不是字符串（请先用 ffi_open 打开）");
    if (func.t != V_STR || !func.s)
        rt_throw("FFIError", "ffi_call 的函数名必须是字符串");
    void *h = rt_ffi_load(lib.s);
#ifdef _WIN32
    void *p = (void *)GetProcAddress((HMODULE)h, func.s);
#else
    void *p = dlsym(h, func.s);
#endif
    if (!p) {
        char msg[512];
        snprintf(msg, sizeof(msg), "原生库 %s 中找不到函数 %s", lib.s, func.s);
        rt_throw("FFIError", msg);
    }
    return p;
}

/* 动态 argtypes 无法安全生成 C 调用；代码生成器会在编译期拦截并给出明确报错。 */
static Val rt_ffi_call(Val lib, Val func, Val args, Val ret, Val argtypes) {
    (void)lib; (void)func; (void)args; (void)ret; (void)argtypes;
    rt_throw("FFIError",
             "C 后端的 ffi_call 需要字面量参数数组与 argtypes，才能生成强类型原生调用");
    return v_null();
}

