
/* Impor C 运行库 —— 由 impor build 自动内嵌，勿手改 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <ctype.h>
#include <time.h>
#include <stdarg.h>
#include <stdint.h>
#include <setjmp.h>
#include <limits.h>
#include <errno.h>
#include <sys/stat.h>
#ifdef _WIN32
#include <windows.h>
#include <direct.h>
#include <wchar.h>
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
#define IMPOR_STDCALL __stdcall
#else
#include <unistd.h>
#include <dirent.h>
#include <dlfcn.h>
#include <pthread.h>
#define IMPOR_STDCALL
#endif

/* ===== v4.1 DLL 模式：绕开 CRT 堆，改用进程堆 =====
   背景：tcc 生成的 DLL 第一次走 CRT malloc 时，会往 msvcrt.dll 的共享退出表登记
   堆清理钩子；宿主 FreeLibrary 卸载 DLL 后，进程退出仍会调到已卸载代码，触发
   0xC0000005。改用 GetProcessHeap() 分配后，DLL 不再登记该钩子，
   “加载 → 调用 → 卸载 → 退出”全程退出码为 0（2026-11-21 实测）。 */
#if defined(IMPOR_BUILD_DLL) && defined(_WIN32)
static void *rtd_alloc(size_t n) {
    return HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, n ? n : (size_t)1);
}
static void *rtd_realloc(void *p, size_t n) {
    HANDLE hp = GetProcessHeap();
    if (!p) return HeapAlloc(hp, HEAP_ZERO_MEMORY, n ? n : (size_t)1);
    if (!n) { HeapFree(hp, 0, p); return NULL; }
    return HeapReAlloc(hp, HEAP_ZERO_MEMORY, p, n);
}
static void rtd_free(void *p) {
    if (p) HeapFree(GetProcessHeap(), 0, p);
}
static char *rtd_strdup(const char *s) {
    size_t n = strlen(s ? s : "") + 1;
    char *d = (char *)rtd_alloc(n);
    if (d) memcpy(d, s ? s : "", n);
    return d;
}
#define malloc(n) rtd_alloc((size_t)(n))
#define calloc(c, n) rtd_alloc((size_t)(c) * (size_t)(n))
#define realloc(p, n) rtd_realloc((void *)(p), (size_t)(n))
#define free(p) rtd_free((void *)(p))
#define strdup(s) rtd_strdup(s)
#endif

typedef enum { V_NULL, V_INT, V_FLOAT, V_STR, V_BOOL, V_ARR, V_OBJ, V_DICT, V_FUNC, V_MODULE } VType;
typedef struct Arr Arr;
typedef struct Obj Obj;
typedef struct Fn Fn;
/* v4.7.2：Val 收紧为 tagged union（56 字节 -> 16 字节）。
   6 个指针槽天然互斥（一个 Val 只有一个类型），合并成匿名 union 后语义等价：
   生产端（v_null/v_int/v_float/...）一律最后写目标字段，前面的清零不会污染 payload。 */
typedef struct { VType t; union { long long i; double f; char *s; Arr *a; Obj *o; Fn *fn; }; } Val;
/* 4.7.2 硬门禁：Val 必须是 16 字节（tag 4 + payload 8 + 对齐）。谁加字段谁编译失败。 */
typedef char val_size_must_be_16[(sizeof(Val) == 16) ? 1 : -1];
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
static Val rt_ffi_alloc(Val size);
static Val rt_ffi_read_i64(Val addr);
static Val rt_ffi_read_str_at(Val addr);
static void *rt_ffi_sym(Val lib, Val func);

static void rt_report_abort(const char *msg);
static void rt_abort(const char *msg) {
    rt_report_abort(msg);
    exit(1);
}

/* ================= v4.2 并发基础：自旋锁 + 线程局部状态 =================
   为什么要有这两样东西：
   1) 没有线程之前，驻留池 / 数组登记链表 / 统计计数器都是单线程独占的，
      多线程下 push 链表会互相踩指针 → 用一把进程级自旋锁包住临界区。
      刻意不用 CRITICAL_SECTION：它的初始化本身有竞态，而自旋锁靠
      InterlockedCompareExchange / __sync 的原子性，不需要初始化，
      也不用担心 DLL 首次调用 CRT 的坑（v4.1 已经踩过一次）。
   2) try/raise 用的是 setjmp 跳转栈，全局一份 → 两个线程各自 try 会乱跳。
      改成每线程一份（Win32 TLS / pthread_key），按需分配，进程内不回收
      （线程数有限，回收反而引入竞态）。 */

/* 锁与原子操作只用 32 位 CAS：32 位 tcc 下 64 位变量的对齐不保证 8 字节，
   Interlocked*64 会踩到对齐要求；64 位计数值统一用 stat 锁保护，
   先保证正确，再谈无锁（见末尾并发区注释）。 */
#if defined(_WIN32)
#define RT_CAS32(p, nv, ov) InterlockedCompareExchange((volatile LONG *)(p), (LONG)(nv), (LONG)(ov))
#define RT_XCHG32(p, nv) InterlockedExchange((volatile LONG *)(p), (LONG)(nv))
#else
#define RT_CAS32(p, nv, ov) __sync_val_compare_and_swap((int *)(p), (int)(ov), (int)(nv))
#define RT_XCHG32(p, nv) __sync_lock_test_and_set((int *)(p), (int)(nv))
#endif

static volatile int g_rt_lock_mem = 0;
static volatile int g_rt_lock_stat = 0;
static long long g_rt_tid_seq = 0;

static void rt_lock_mem(void) {
    while (RT_CAS32(&g_rt_lock_mem, 1, 0) != 0) {
#if defined(_WIN32)
        Sleep(0);
#endif
    }
}
static void rt_unlock_mem(void) { RT_XCHG32(&g_rt_lock_mem, 0); }
static void rt_lock_stat(void) {
    while (RT_CAS32(&g_rt_lock_stat, 1, 0) != 0) {
#if defined(_WIN32)
        Sleep(0);
#endif
    }
}
static void rt_unlock_stat(void) { RT_XCHG32(&g_rt_lock_stat, 0); }

typedef struct RtTls {
    jmp_buf jmp[64];
    int jmp_n;
    char err_type[128];
    char err_msg[512];
    const char *trace[128];
    int trace_line[128];
    int trace_n;
    int cur_line;
    long long tid;          /* 首次访问时分配，线程内稳定 */
} RtTls;

#if defined(_WIN32)
static DWORD g_rt_tls_idx = TLS_OUT_OF_INDEXES;
static RtTls *rt_tls(void) {
    RtTls *p;
    if (g_rt_tls_idx == TLS_OUT_OF_INDEXES) {
        DWORD idx = TlsAlloc();
        if (idx == TLS_OUT_OF_INDEXES) rt_abort("线程局部存储分配失败");
        if (RT_CAS32(&g_rt_tls_idx, (int)idx, (int)TLS_OUT_OF_INDEXES) != (int)TLS_OUT_OF_INDEXES) {
            TlsFree(idx);
        }
    }
    p = (RtTls *)TlsGetValue(g_rt_tls_idx);
    if (!p) {
        p = (RtTls *)calloc(1, sizeof(RtTls));
        if (!p) rt_abort("线程局部存储内存不足");
        TlsSetValue(g_rt_tls_idx, p);
    }
    return p;
}
#else
static pthread_key_t g_rt_tls_key;
static int g_rt_tls_ready = 0;
static RtTls *rt_tls(void) {
    RtTls *p;
    if (!g_rt_tls_ready) {
        pthread_key_create(&g_rt_tls_key, NULL);
        g_rt_tls_ready = 1;
    }
    p = (RtTls *)pthread_getspecific(g_rt_tls_key);
    if (!p) {
        p = (RtTls *)calloc(1, sizeof(RtTls));
        if (!p) rt_abort("线程局部存储内存不足");
        pthread_setspecific(g_rt_tls_key, p);
    }
    return p;
}
#endif

static long long rt_next_tid(void) {
    long long t;
    rt_lock_stat();
    t = ++g_rt_tid_seq;
    rt_unlock_stat();
    return t;
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
static void rt_wait_all_threads(void);   /* v4.2：定义在本文件末尾的并发区 */
static void rt_note_alloc(long long bytes) {
    rt_lock_stat();
    g_rt_allocs++;
    g_rt_bytes += bytes;
    if (g_rt_bytes > g_rt_peak) g_rt_peak = g_rt_bytes;
    rt_unlock_stat();
#if defined(IMPOR_BUILD_DLL)
    /* 共享库模式不注册 atexit：tcc 的 DLL CRT 在 FreeLibrary/进程退出时会再次
       走退出钩子，导致 0xC0000005 崩溃（2026-11-21 实测）。宿主可显式调用
       impor_runtime_shutdown() 回收，或交给操作系统在卸载时收回。 */
    (void)0;
#else
    if (!g_rt_shutdown_ready) {
        g_rt_shutdown_ready = 1;
        atexit(rt_shutdown);
    }
#endif
}
static void rt_note_free(long long bytes) {
    rt_lock_stat();
    g_rt_frees++;
    g_rt_bytes -= bytes;
    if (g_rt_bytes < 0) g_rt_bytes = 0;
    rt_unlock_stat();
}
/* 4.5：内存差额检查（IMPOR_LEAK_CHECK=1 打印，=2 打印并在有差额时标记 rc=3。
   托管数组在进程退出时统一释放，所以这里统计的是「运行时记账的分配/释放次数差」，
   用来发现绕过托管路径的分配。CLI 侧见 `impor run --check-leaks`。 */
static long long g_rt_leak_live = 0;
static void rt_leak_report(void) {
    const char *mode = getenv("IMPOR_LEAK_CHECK");
    long long live;
    if (!mode || !*mode) return;
    live = g_rt_allocs - g_rt_frees;
    if (live < 0) live = 0;
    g_rt_leak_live = live;
    fprintf(stderr, "[leaks] 分配 %lld 次 / 释放 %lld 次 / 未释放 %lld 块\n",
            g_rt_allocs, g_rt_frees, live);
    fflush(stderr);
}
static void rt_arr_reg(Arr *a) {
    rt_lock_mem();
    a->rp_prev = NULL;
    a->rp_next = g_rt_arr_head;
    if (g_rt_arr_head) g_rt_arr_head->rp_prev = a;
    g_rt_arr_head = a;
    g_rt_arr_n++;
    rt_unlock_mem();
}
static void rt_arr_unreg(Arr *a) {
    if (!a) return;
    rt_lock_mem();
    if (a->rp_prev) a->rp_prev->rp_next = a->rp_next;
    else if (g_rt_arr_head == a) g_rt_arr_head = a->rp_next;
    if (a->rp_next) a->rp_next->rp_prev = a->rp_prev;
    a->rp_prev = NULL;
    a->rp_next = NULL;
    if (g_rt_arr_n > 0) g_rt_arr_n--;
    rt_unlock_mem();
}
static void rt_shutdown(void) {
    Arr *p;
    rt_wait_all_threads();   /* v4.2：先等线程收尾，再释放数组登记表 */
    rt_lock_mem();
    p = g_rt_arr_head;
    while (p) {
        Arr *nx = p->rp_next;
        if (p->items) free(p->items);
        free(p);
        p = nx;
    }
    g_rt_arr_head = NULL;
    g_rt_arr_n = 0;
    rt_unlock_mem();
    rt_leak_report();   /* 4.5：IMPOR_LEAK_CHECK=1 时打印分配/释放差额，=2 时泄漏即退出码 3 */
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
    rt_lock_mem();
    if (n <= RT_INTERN_MAXLEN && g_rt_intern_n < RT_INTERN_MAX) {
        RtStrNode *b = g_rt_intern[h % RT_INTERN_BUCKETS];
        while (b) {
            if (b->h == h && strcmp(b->s, s) == 0) {
                rt_unlock_mem();
                return b->s;
            }
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
                rt_unlock_mem();
                return node->s;
            }
            free(node);
        }
    }
    rt_unlock_mem();
    {
        char *d = (char *)malloc(n + 1);
        if (!d) rt_abort("内存不足");
        memcpy(d, s, n + 1);
        rt_note_alloc((long long)n + 1);
        return d;
    }
}

/* ---- mem()/gc()：由 ctrans 内建映射调用（实现见 v_str 之后的函数区） ---- */


/* ---- 异常运行时（try / raise）：setjmp 跳转栈，由编译器生成的 try 语句使用 ----
   v4.2：这些状态全部改成线程局部（宏指向 rt_tls()），否则两个线程各自 try
   会共用同一份跳转栈，longjmp 到别的线程的帧上 → 直接崩。 */
#define g_jmp_stack (rt_tls()->jmp)
#define g_jmp_n (rt_tls()->jmp_n)
#define g_err_type (rt_tls()->err_type)
#define g_err_msg (rt_tls()->err_msg)

/* 4.6 收尾：mingw-w64 在 x64 上把 setjmp 定义成
       #define setjmp(BUF) _setjmp((BUF), __builtin_frame_address(0))
   第二个参数是「SEH 链锚点」，_setjmp 把它存进 jmp_buf，longjmp 时由它恢复栈链。
   -O2 没有帧指针，这个地址在某些栈布局下指向会被后续调用覆盖的区域；longjmp 之后
   链已失效，下一次异常展开直接死在 ntdll!RtlVirtualUnwind2 —— 实测同一份 C：
   -O0 20/20 通过，-O2 6/20 崩（0xC0000005），加 -fno-omit-frame-pointer 仍 16/40 崩。
   纯 C 跳转不需要 SEH：传 NULL 即「不安装 SEH 帧」（与 mingw 的 32 位分支
   _setjmp3(BUF, NULL) 同源），实测 -O2 40/40、-O0 40/40 全通过。
   tcc / msvc / Linux 的 setjmp 没有这个二参数形态，保持原样。 */
#if defined(_WIN64) && defined(__MINGW32__)
#  define IMPOR_SETJMP(BUF) _setjmp((BUF), NULL)
#else
#  define IMPOR_SETJMP(BUF) setjmp(BUF)
#endif

/* ================= v4.2：统一错误码 + 调用栈 ================= */
#define g_trace (rt_tls()->trace)
#define g_trace_line (rt_tls()->trace_line)
#define g_trace_n (rt_tls()->trace_n)

/* v4.3：独立 exe 也显示源码片段（g_src_lines 由代码生成器写入） */
#ifndef IMPOR_HAS_SRC
static const char *g_src_lines[] = { "" };
static int g_src_n = 0;
#else
extern const char *g_src_lines[];
extern int g_src_n;
#endif
#define g_cur_line (rt_tls()->cur_line)
static void rt_set_line(int n) { g_cur_line = n; }
static void rt_print_snippet(void) {
    int ln = g_cur_line;
    if (ln < 1 || ln > g_src_n) return;
    fprintf(stderr, "  %d │ %s\n", ln, g_src_lines[ln - 1]);
    fprintf(stderr, "    │ ^\n");
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
    if (strcmp(t, "OverflowError") == 0) return "交付层把整数固定为 int64（−9223372036854775808 ~ 9223372036854775807）。加减乘越界时报错中止，而不是像 C 那样静默回绕成一个错误的值。";
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
    if (strcmp(t, "OverflowError") == 0) return "缩小数值范围，或把大数拆成多步并检查边界；确实需要任意精度时用 `impor run 脚本.impor --big`（C 产物按 int64 编译，不提供大整数）。";
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
    fprintf(stderr, "调用栈（自顶向下）:\n");
    if (g_trace_n == 0) { fprintf(stderr, "  <main>\n"); return; }
    for (i = g_trace_n - 1; i >= 0; i--) {
        if (g_trace_line[i] > 0)
            fprintf(stderr, "%s %s（第%d行）\n", i == g_trace_n - 1 ? " ->" : "   ",
                    g_trace[i], g_trace_line[i]);
        else
            fprintf(stderr, "%s %s\n", i == g_trace_n - 1 ? " ->" : "   ", g_trace[i]);
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
    const char *c = rt_err_code(g_err_type);
    if (g_cur_line < 1 && g_trace_n > 0) g_cur_line = g_trace_line[g_trace_n - 1];
    fprintf(stderr, "运行错误: 第%d行: %s: %s [%s]\n", g_cur_line, t, g_err_msg, c);
    rt_print_snippet();
    fprintf(stderr, "  为什么： %s\n", rt_diag_why(t));
    fprintf(stderr, "  修复： %s\n", rt_diag_fix(t));
    fprintf(stderr, "  文档： docs/errors.md#%s\n", c);
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
    const char *c = rt_err_code(t);
    if (g_cur_line < 1 && g_trace_n > 0) g_cur_line = g_trace_line[g_trace_n - 1];
    fprintf(stderr, "运行错误: 第%d行: %s: %s [%s]\n", g_cur_line, t, msg ? msg : "", c);
    rt_print_snippet();
    fprintf(stderr, "  为什么： %s\n", rt_diag_why(t));
    fprintf(stderr, "  修复： %s\n", rt_diag_fix(t));
    fprintf(stderr, "  文档： docs/errors.md#%s\n", c);
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

static Val v_null(void) { Val v; v.t = V_NULL; v.i = 0; return v; }
static Val v_int(long long x) { Val v; v.t = V_INT; v.i = x; return v; }
static Val v_float(double x) { Val v; v.t = V_FLOAT; v.f = x; return v; }
static Val v_bool(int b) { Val v; v.t = V_BOOL; v.i = b ? 1 : 0; return v; }
static Val v_str(const char *s) { Val v; v.t = V_STR; v.s = strdup(s ? s : ""); return v; }
/* v3.2：字符串字面量走驻留池（相同常量复用同一块内存；该指针不会被 rt_val_free 释放） */
static Val v_str_lit(const char *s) { Val v; v.t = V_STR; v.s = rt_intern(s); return v; }

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
static Val v_arr(void) { Val v; v.t = V_ARR; v.a = NULL; return v; }
static Val v_obj(Obj *o) { Val v; v.t = V_OBJ; v.o = o; return v; }
/* v4.2：字典复用 Arr 存储，items 按 [key0, val0, key1, val1, ...] 成对排列 */
static Val v_dict(void) { Val v; v.t = V_DICT; v.a = NULL; return v; }
/* v4.2：一等函数值（闭包） */
static Val v_fn(Fn *f) { Val v; v.t = V_FUNC; v.fn = f; return v; }
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
/* patch8：Python 的浮点 % 结果符号跟随除数，C 的 fmod 跟随被除数，必须校正 */
static double rt_fmod_py(double a, double b) {
    double r = fmod(a, b);
    if (r != 0.0 && ((r < 0.0) != (b < 0.0))) r += b;
    return r;
}

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

/* 64 位有符号整数溢出检测（与解释器 Python 任意整数不同：C 侧溢出时报错）
 * 4.7：加 inline 与 __builtin_expect —— 溢出是异常路径，正常路径必须能被
 * 内联进循环且分支高度可预测，否则 1e8 级循环会为“可能的溢出”多付 30%+。 */
static inline long long rt_ck_add(long long a, long long b) {
    if (__builtin_expect(b > 0 ? a > LLONG_MAX - b : a < LLONG_MIN - b, 0))
        rt_abort("整数溢出：超出 64 位有符号整数范围（加法）");
    return a + b;
}
static inline long long rt_ck_sub(long long a, long long b) {
    if (__builtin_expect(b < 0 ? a > LLONG_MAX + b : a < LLONG_MIN + b, 0))
        rt_abort("整数溢出：超出 64 位有符号整数范围（减法）");
    return a - b;
}
static inline long long rt_ck_mul(long long a, long long b) {
    if (a == 0 || b == 0) return 0;
    if (a == -1 && b == LLONG_MIN) rt_abort("整数溢出：超出 64 位有符号整数范围（乘法）");
    if (b == -1 && a == LLONG_MIN) rt_abort("整数溢出：超出 64 位有符号整数范围（乘法）");
    if (a > 0) {
        if (b > 0) { if (a > LLONG_MAX / b) rt_abort("整数溢出：超出 64 位有符号整数范围（乘法）"); }
        else { if (b < LLONG_MIN / a) rt_abort("整数溢出：超出 64 位有符号整数范围（乘法）"); }
    } else {
        if (b > 0) { if (a < LLONG_MIN / b) rt_abort("整数溢出：超出 64 位有符号整数范围（乘法）"); }
        else { if (b < LLONG_MAX / a) rt_abort("整数溢出：超出 64 位有符号整数范围（乘法）"); }
    }
    return a * b;
}

/*/* patch8：浮点打印按 Python repr 的口径（最短可往返 + 指数两位 + 整数补 .0）。
 * 旧实现是 snprintf("%.12g")：0.1+0.2 会打成 0.3（丢精度），1e16 打成 1e+016，
 * 与解释器不一致——对拍 c30_float_repr 就是这么挂的。
 * 三条坑（都实测过）：
 *  1) 不能用 %.*f 出定点结果：MSVCRT 的 %f 末位会错（-698301.6521509961 -> …962）；
 *  2) 不能用 powl：本机 MinGW 没有 powl 实现（隐式声明，调用得到垃圾值）；
 *  3) 不能用 %.0e 取数量级：它会先舍入到 1 位（952510.2 -> 1e+006，多一位）。
 * 所以：数量级取 %.17e 的指数；位数用 %.*e 的最短可往返，再对末位做 ±1 邻位微调，
 * 用 long double 手工乘 10 的幂挑出离真值最近的那个。 */
/*/* patch8：浮点打印按 Python repr 的口径（最短可往返 + 指数两位 + 整数补 .0）。
 * 旧实现是 snprintf("%.12g")：0.1+0.2 会打成 0.3（丢精度），1e16 打成 1e+016，
 * 与解释器不一致——对拍 c30_float_repr 就是这么挂的。
 * 实测踩过的坑（都别再用）：
 *  1) %.*f 的末位会错（MSVCRT）：-698301.6521509961 被打成 …962；
 *  2) powl 在本机 MinGW 没有实现（隐式声明，调用拿到垃圾值），long double 运算也不可靠；
 *  3) tcc 的 win32 运行库没有 strtoll；
 *  4) %.0e 会先舍入到 1 位（952510.2 直接变 1e+006，数量级多一位）。
 * 现在的做法：一次 "%.20e" 取 21 位（前 17 位是准的，后几位当保护位），
 * 自己在十进制串上做第 p 位的四舍五入，再用 strtod 校验能否还原同一个 double，
 * 第一个能还原的 p 就是 Python 的最短位数。全程只依赖 snprintf/strtod。 */
static void rt_fmt_double(double d, char *buf, size_t n) {
    char full[96];
    char digits[28];
    char cand[64];
    int p, exp10 = 0, neg = 0, dlen = 0;
    size_t dn = 0, k = 0, i;
    if (d != d) { snprintf(buf, n, "nan"); return; }
    if (d == 0.0) { snprintf(buf, n, (1.0 / d < 0) ? "-0.0" : "0.0"); return; }
    if (d > 1.7976931348623157e308) { snprintf(buf, n, "inf"); return; }
    if (d < -1.7976931348623157e308) { snprintf(buf, n, "-inf"); return; }
    if (d < 0) { neg = 1; d = -d; }
    snprintf(full, sizeof full, "%.20e", d);          /* 例如 9.52510211185840190e+005 */
    {
        char *e = strchr(full, 'e');
        exp10 = e ? (int)atoi(e + 1) : 0;             /* atoi 在 tcc 里可用 */
        if (e) *e = 0;
        for (const char *q = full; *q; q++) {
            if (*q == '-' || *q == '+') continue;
            if (*q == '.') continue;
            if (dlen + 2 < (int)sizeof digits) digits[dlen++] = *q;
        }
        digits[dlen] = 0;
    }
    for (p = 1; p <= 17; p++) {
        char tdig[28];
        int carry, j;
        memcpy(tdig, digits, (size_t)p);
        tdig[p] = 0;
        carry = (digits[p] >= '5');
        j = p - 1;
        while (carry && j >= 0) {
            if (tdig[j] == '9') { tdig[j] = '0'; j--; }
            else { tdig[j] = (char)(tdig[j] + 1); carry = 0; }
        }
        if (carry) {                                  /* 999… -> 1000…：数量级 +1 */
            tdig[0] = '1';
            for (j = 1; j < p; j++) tdig[j] = '0';
            exp10 += 1;
        }
        /* 组串校验往返 */
        {
            size_t m = 0;
            if (neg) cand[m++] = '-';
            cand[m++] = tdig[0];
            if (p > 1) {
                cand[m++] = '.';
                for (j = 1; j < p; j++) cand[m++] = tdig[j];
            }
            snprintf(cand + m, sizeof cand - m, "e%c%02d", exp10 < 0 ? '-' : '+',
                     exp10 < 0 ? -exp10 : exp10);
        }
        if (strtod(cand, NULL) == (neg ? -d : d)) {
            /* 去掉尾部多余的 0（最短表示不含它们）；全 0 时保留一位 */
            dn = (size_t)p;
            while (dn > 1 && tdig[dn - 1] == '0') dn--;
            memcpy(digits, tdig, dn);
            digits[dn] = 0;
            break;
        }
        if (carry) exp10 -= 1;                        /* 这次进位没成，还原指数 */
    }
    if (p > 17) { snprintf(buf, n, "%.17e", neg ? -d : d); return; }
    if (neg && k + 1 < n) buf[k++] = '-';
    if (exp10 < -4 || exp10 >= 16) {
        if (k + 1 < n) buf[k++] = digits[0];
        if (dn > 1) {
            if (k + 1 < n) buf[k++] = '.';
            for (i = 1; i < dn; i++) if (k + 1 < n) buf[k++] = digits[i];
        }
        if (k + 4 < n)
            k += snprintf(buf + k, n - k, "e%c%02d", exp10 < 0 ? '-' : '+',
                          exp10 < 0 ? -exp10 : exp10);
    } else if (exp10 >= 0) {
        size_t ip = (size_t)(exp10 + 1);
        for (i = 0; i < ip; i++) {
            if (k + 1 < n) buf[k++] = (i < dn) ? digits[i] : '0';
        }
        if (k + 1 < n) buf[k++] = '.';
        if (dn > ip) {
            for (i = ip; i < dn; i++) if (k + 1 < n) buf[k++] = digits[i];
        } else {
            if (k + 1 < n) buf[k++] = '0';
        }
    } else {
        if (k + 1 < n) buf[k++] = '0';
        if (k + 1 < n) buf[k++] = '.';
        for (i = 0; i < (size_t)(-exp10 - 1); i++) if (k + 1 < n) buf[k++] = '0';
        for (i = 0; i < dn; i++) if (k + 1 < n) buf[k++] = digits[i];
    }
    if (k >= n) k = n - 1;
    buf[k] = 0;
    if (!strchr(buf, 'e')) {
        char *dot = strchr(buf, '.');
        if (dot) {
            char *end = buf + strlen(buf) - 1;
            while (end > dot + 1 && *end == '0') *end-- = 0;
        } else {
            size_t m2 = strlen(buf);
            if (m2 + 2 < n) { buf[m2] = '.'; buf[m2 + 1] = '0'; buf[m2 + 2] = 0; }
        }
    }
}

/* 数值格式化（对齐 Impor str()：整数 float 显示 .0） */
static void rt_fmt_num(Val v, char *buf, size_t n) {
    if (v.t == V_FLOAT) {
        rt_fmt_double(v.f, buf, n);
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
        r.t = V_STR; r.s = buf;
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
    if (a.t != V_ARR || !a.a) rt_throw("TypeError", "下标访问需要数组");
    long long i = rt_int(b);
    /* v4.1：越界改为 rt_throw，让 try/catch 能捕获（与解释器一致） */
    if (i < 0 || i >= a.a->n) rt_throw("IndexError", "list index out of range");
    return a.a->items[i];
}
static Val rt_arr_set(Val a, Val b, Val c) {
    if (a.t == V_DICT) return rt_dict_set(a, b, c);
    if (a.t != V_ARR || !a.a) rt_throw("TypeError", "下标赋值需要数组");
    long long i = rt_int(b);
    if (i < 0 || i >= a.a->n) rt_throw("IndexError", "list index out of range");
    /* 先复制再释放：c 可能正是槽里那个值（如 a[0] = a[0]、
       d["k"] = arrappend(d["k"], x)——arrappend 原地改并返回同一引用），
       先 free 会让 rt_val_dup 读到已释放内存。 */
    Val dup = rt_val_dup(c);
    rt_val_free(a.a->items[i]);
    a.a->items[i] = dup;
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
            /* 先复制再释放：v 可能引用旧值本身（含 arrappend 原地改后返回同一引用） */
            Val dup = rt_val_dup(v);
            rt_val_free(d.a->items[i + 1]);
            d.a->items[i + 1] = dup;
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
    Val v; v.t = V_MODULE;
    v.o = (Obj *)m;
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

/* ================= v4.1：自定义异常与类型化 catch =================
   exception Foo 只在编译期登记名字；raise Foo("消息") 直接落成
   rt_throw("Foo", "消息")，catch Foo e 用 rt_err_matches("Foo") 过滤。
   类型不匹配时 rt_rethrow() 把异常抛给外一层 try，语义与解释器一致。 */
static int rt_err_matches(const char *t) {
    if (!t || !t[0]) return 1;
    return strcmp(g_err_type, t) == 0;
}
static void rt_rethrow(int level) {
    if (level > 0) longjmp(g_jmp_stack[level - 1], 1);
    rt_report_error();
    exit(1);
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
/* v4.3.2：按字节区间造一个字符串。调用方负责保证区间落在 UTF-8 字符边界上
   （strstr 命中的分隔符前后天然是边界）。 */
static Val rt_vstr_range(const char *p, size_t len) {
    char *b = (char *)malloc(len + 1);
    Val r;
    if (!b) rt_abort("内存不足");
    memcpy(b, p, len);
    b[len] = '\0';
    r.t = V_STR; r.s = b;
    return r;
}

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
        if (strcmp(m, "starts_with") == 0 || strcmp(m, "startsWith") == 0
            || strcmp(m, "startswith") == 0) {
            if (n < 1) return v_bool(0);
            return v_bool(strncmp(s, rt_str_of(args[0]), strlen(rt_str_of(args[0]))) == 0);
        }
        if (strcmp(m, "ends_with") == 0 || strcmp(m, "endsWith") == 0
            || strcmp(m, "endswith") == 0) {
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
        if (strcmp(m, "split") == 0) {
            /* v4.3.2：此前字符串 split 完全缺失，`use std.text` 的程序编 exe
               必抛 E1006。这里补上与解释器一致的语义：带分隔符按分隔符切；
               不带参数按连续空白切并丢弃空段；第二参数是 maxsplit。 */
            Val r = v_arr();
            r.a = arr_new(4);   /* v_arr() 只置类型，容器要单独分配 */
            if (n < 1) {
                const char *p = s;
                while (*p) {
                    const char *q;
                    while (*p == ' ' || *p == '\t' || *p == '\n'
                           || *p == '\r' || *p == '\v' || *p == '\f') p++;
                    if (!*p) break;
                    q = p;
                    while (*q && !(*q == ' ' || *q == '\t' || *q == '\n'
                                   || *q == '\r' || *q == '\v' || *q == '\f')) q++;
                    arr_push(r.a, rt_vstr_range(p, (size_t)(q - p)));
                    p = q;
                }
                return r;
            } else {
                const char *sep = rt_str_of(args[0]);
                size_t sl = strlen(sep);
                const char *p = s;
                long long left = (n >= 2) ? rt_int(args[1]) : -1;
                if (sl == 0) {
                    rt_throw("ValueError", "split 的分隔符不能为空");
                    return r;
                }
                while (1) {
                    const char *hit = strstr(p, sep);
                    if (!hit || left == 0) {
                        arr_push(r.a, rt_vstr_range(p, strlen(p)));
                        break;
                    }
                    arr_push(r.a, rt_vstr_range(p, (size_t)(hit - p)));
                    p = hit + sl;
                    if (left > 0) left--;
                }
                return r;
            }
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
        r.t = V_STR; r.s = buf;
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
    r.t = V_STR; r.s = buf;
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
        if (d == 0) rt_throw("ZeroDivisionError", "integer division or modulo by zero");
        return v_float(rt_fmod_py(rt_num(a), d));
    }
    long long d = rt_int(b);
    if (d == 0) rt_throw("ZeroDivisionError", "integer modulo by zero");
    long long r = rt_int(a) % d;
    if (r != 0 && ((r < 0) != (d < 0))) r += d;  /* Python 取正模语义 */
    return v_int(r);
}
static Val rt_neg(Val a) {
    if (a.t == V_FLOAT) return v_float(-a.f);
    if (rt_int(a) == LLONG_MIN) rt_abort("整数溢出：超出 64 位有符号整数范围（取负）");
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

/* 4.9.1：`x have <类型名>` —— 运行期类型判定，返回布尔。
   判定口径与解释器 typename() 一致：bool 是独立类型，1 have float 为假。
   "object" / "instance" 是给类的通用判定（任意实例都为真）。 */
static Val rt_have(Val v, const char *tn) {
    Val t = rt_type(v);
    const char *s = (t.t == V_STR && t.s) ? t.s : "";
    if (!tn) return v_bool(0);
    if (strcmp(tn, "object") == 0 || strcmp(tn, "instance") == 0)
        return v_bool(v.t == V_OBJ);
    return v_bool(strcmp(s, tn) == 0);
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
static Val rt_uc_map(Val v, int to_upper);   /* 4.5：定义在文件末尾（Unicode 简单大小写） */
static Val rt_ord(Val v);                    /* 4.5：码点转换，定义在文件末尾 */
static Val rt_chr(Val v);
static Val rt_upper(Val v) { return rt_uc_map(v, 1); }
static Val rt_lower(Val v) { return rt_uc_map(v, 0); }
static Val rt_strpad(Val v, Val w, Val ch) {
    const char *s = rt_str_of(v);
    long long width = rt_int(w);
    const char *pad = rt_str_of(ch);
    size_t n = strlen(s);              /* 字节长度，用于拷贝 */
    long long chars = rt_u8len(s);     /* 4.5：按字符数比较，与解释器 rjust 一致 */
    long long cnt;
    size_t plen;
    char *buf;
    if (!pad[0]) pad = " ";
    plen = strlen(pad);
    if (width <= 0 || chars >= width) return v_str(s);
    cnt = width - chars;
    buf = (char *)malloc((size_t)cnt * plen + n + 1);
    if (!buf) rt_abort("内存不足");
    for (long long k = 0; k < cnt; k++) memcpy(buf + (size_t)k * plen, pad, plen);
    memcpy(buf + (size_t)cnt * plen, s, n);
    buf[(size_t)cnt * plen + n] = '\0';
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
    r.t = V_STR; r.s = buf;
    return r;
}

static Val rt_strfind(Val v, Val sub, Val start) {
    const char *s = rt_str_of(v);
    const char *needle = rt_str_of(sub);
    long long st = rt_int(start);
    size_t off;
    const char *hit;
    if (s != rt_u8c_s) rt_u8c_reset(s);
    if (st < 0) st = 0;
    if (st > rt_u8c_n) return v_int(-1);
    off = rt_u8c_at(s, st);
    hit = strstr(s + off, needle);
    return v_int(hit ? (st + rt_u8len_prefix(s + off, hit)) : -1);
}

static Val rt_strjoin(Val arr, Val sep) {
    const char *s = rt_str_of(sep);
    size_t sl = strlen(s), total = 1, used = 0;
    long long i, n;
    char *buf;
    Val r;
    if (arr.t != V_ARR || !arr.a) rt_throw("TypeError", "strjoin 需要数组");
    n = arr.a->n;
    for (i = 0; i < n; i++) total += strlen(rt_str_of(arr.a->items[i])) + sl;
    buf = (char *)malloc(total);
    if (!buf) rt_abort("内存不足");
    for (i = 0; i < n; i++) {
        const char *item = rt_str_of(arr.a->items[i]);
        size_t il = strlen(item);
        if (i > 0) { memcpy(buf + used, s, sl); used += sl; }
        memcpy(buf + used, item, il); used += il;
    }
    buf[used] = '\0';
    r = v_str(buf);
    free(buf);
    return r;
}

static Val rt_strreplace(Val v, Val oldv, Val newv) {
    const char *s = rt_str_of(v);
    const char *o = rt_str_of(oldv);
    const char *n = rt_str_of(newv);
    size_t sl = strlen(s), ol = strlen(o), nl = strlen(n), count = 0, total;
    const char *p, *hit;
    char *buf, *w;
    Val r;
    if (ol == 0) return v_str(s);
    for (p = s; (hit = strstr(p, o)) != NULL; p = hit + ol) count++;
    if (nl >= ol) total = sl + 1 + count * (nl - ol);
    else total = sl + 1 - count * (ol - nl);
    buf = (char *)malloc(total);
    if (!buf) rt_abort("内存不足");
    p = s; w = buf;
    while ((hit = strstr(p, o)) != NULL) {
        size_t keep = (size_t)(hit - p);
        memcpy(w, p, keep); w += keep;
        memcpy(w, n, nl); w += nl;
        p = hit + ol;
    }
    memcpy(w, p, strlen(p) + 1);
    r = v_str(buf);
    free(buf);
    return r;
}

static Val rt_strlines(Val v) {
    const char *s = rt_str_of(v);
    const char *start = s, *p = s;
    Val r = v_arr();
    r.a = arr_new(16);
    while (1) {
        if (*p == '\n' || *p == '\0') {
            size_t len;
            char *buf;
            Val line;
            if (*p == '\0' && p == start) break;
            len = (size_t)(p - start);
            if (len > 0 && start[len - 1] == '\r') len--;
            buf = (char *)malloc(len + 1);
            if (!buf) rt_abort("内存不足");
            memcpy(buf, start, len); buf[len] = '\0';
            line = v_str(buf);
            free(buf);
            arr_push(r.a, line);
            if (*p == '\0') break;
            start = p + 1;
        }
        p++;
    }
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
    {
        Val out = v_str(buf);
        free(buf);
        return out;
    }
}
static Val rt_filelines(Val path) {
    Val text = rt_fileread(path);
    Val lines = rt_strlines(text);
    if (text.s) free(text.s);
    return lines;
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

static Val rt_fileexists(Val path) {
#ifdef _WIN32
    wchar_t w[4096];
    if (MultiByteToWideChar(CP_UTF8, 0, rt_str_of(path), -1, w, 4096) <= 0) {
        return v_bool(0);
    }
    DWORD attr = GetFileAttributesW(w);
    return v_bool(attr != INVALID_FILE_ATTRIBUTES);
#else
    struct stat st;
    return v_bool(stat(rt_str_of(path), &st) == 0);
#endif
}

static Val rt_filesize(Val path) {
    FILE *fp = rt_fopen_utf8(rt_str_of(path), "rb");
    if (!fp) {
        return v_int(0);
    }
    fseek(fp, 0, SEEK_END);
    long n = ftell(fp);
    fclose(fp);
    return v_int(n < 0 ? 0 : n);
}

static Val rt_filemkdir(Val path) {
#ifdef _WIN32
    wchar_t w[4096];
    if (MultiByteToWideChar(CP_UTF8, 0, rt_str_of(path), -1, w, 4096) <= 0) {
        rt_abort("filemkdir: 路径无效");
    }
    if (!CreateDirectoryW(w, NULL)) {
        DWORD err = GetLastError();
        if (err != ERROR_ALREADY_EXISTS) {
            rt_abort("filemkdir: 创建目录失败");
        }
    }
#else
    struct stat st;
    if (stat(rt_str_of(path), &st) != 0) {
        if (mkdir(rt_str_of(path), 0755) != 0 && errno != EEXIST) {
            rt_abort("filemkdir: 创建目录失败");
        }
    }
#endif
    return v_bool(1);
}

static Val rt_filelist(Val path) {
    Val r = v_arr();
    r.a = arr_new(16);
#ifdef _WIN32
    wchar_t w[4096], pattern[4098];
    if (MultiByteToWideChar(CP_UTF8, 0, rt_str_of(path), -1, w, 4096) <= 0) {
        rt_abort("filelist: 路径无效");
    }
    wcscpy(pattern, w);
    wcscat(pattern, L"\\*");
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(pattern, &fd);
    if (h == INVALID_HANDLE_VALUE) {
        return r;
    }
    do {
        if (wcscmp(fd.cFileName, L".") == 0 || wcscmp(fd.cFileName, L"..") == 0) {
            continue;
        }
        char name[4096];
        if (WideCharToMultiByte(CP_UTF8, 0, fd.cFileName, -1, name, 4096, NULL, NULL) > 0) {
            arr_push(r.a, v_str(name));
        }
    } while (FindNextFileW(h, &fd));
    FindClose(h);
#else
    DIR *d = opendir(rt_str_of(path));
    if (!d) {
        return r;
    }
    struct dirent *e;
    while ((e = readdir(d)) != NULL) {
        if (strcmp(e->d_name, ".") == 0 || strcmp(e->d_name, "..") == 0) {
            continue;
        }
        arr_push(r.a, v_str(e->d_name));
    }
    closedir(d);
#endif
    return r;
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
    wchar_t wsh[8192 + 64];
    const char *cs = rt_str_of(cmd);
    BOOL ok;
    DWORD code = 0;
    if (MultiByteToWideChar(CP_UTF8, 0, cs, -1, wcl, 8192) <= 0)
        return v_int(-1);
    /* 统一走 cmd.exe /c，与 POSIX system() 的语义对齐：
       - .bat/.cmd 不是 PE，CreateProcessW 直调必然失败（这就是自举链跑不动的原因）；
       - echo/cd/set 之类是 cmd 内建命令，根本不是可执行文件；
       - 单个 exe + 参数经 cmd 转发后行为不变，退出码也照样透传。
       以前这里直调 CreateProcessW，`system("build.bat")` 只能拿到 -1。 */
    if (swprintf(wsh, 8192 + 64, L"cmd.exe /c \"%ls\"", wcl) < 0)
        return v_int(-1);
    ok = CreateProcessW(NULL, wsh, NULL, NULL, FALSE, 0, NULL, NULL, &si, &pi);
    if (!ok)
        return v_int(-1);
    WaitForSingleObject(pi.hProcess, INFINITE);
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
/* v4.1：浮点序列化取“最短且能原样读回”的写法。
   之前固定 %.17g 会把 6.6 写成 6.5999999999999996，与解释器 json.dumps 不同。 */
static void rt_fmt_shortest_double(double d, char *buf, size_t cap) {
    int p;
    for (p = 15; p <= 17; p++) {
        snprintf(buf, cap, "%.*g", p, d);
        if (strtod(buf, NULL) == d) break;
    }
    /* Python 的浮点 repr 一定带小数点：1024 要写成 1024.0 */
    if (!strchr(buf, '.') && !strchr(buf, 'e') && !strchr(buf, 'E')) {
        size_t n = strlen(buf);
        if (n + 3 <= cap) { buf[n] = '.'; buf[n + 1] = '0'; buf[n + 2] = '\0'; }
    }
}
static void rt_json_enc(Val v, RtBuf *b) {
    char tmp[64];
    switch (v.t) {
        case V_NULL: rt_buf_add(b, "null"); break;
        case V_BOOL: rt_buf_add(b, v.i ? "true" : "false"); break;
        case V_INT: snprintf(tmp, sizeof(tmp), "%lld", v.i); rt_buf_add(b, tmp); break;
        case V_FLOAT: rt_fmt_shortest_double(v.f, tmp, sizeof(tmp)); rt_buf_add(b, tmp); break;
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

/* v3.10：最小指针桥。只提供“分配一块输出缓冲区 / 读一个地址 / 读一个 C 字符串”，
   不提供结构体布局、指针运算或把任意对象暴露给原生侧。 */
static Val rt_ffi_alloc(Val size) {
    long long n = size.i;
    if (size.t != V_INT || n <= 0)
        rt_throw("FFIError", "ffi_alloc 大小必须是正整数");
    void *p = calloc(1, (size_t)n);
    if (!p)
        rt_throw("FFIError", "ffi_alloc 分配失败");
    return v_int((long long)(intptr_t)p);
}

static Val rt_ffi_read_i64(Val addr) {
    if (addr.t != V_INT || addr.i == 0)
        rt_throw("FFIError", "ffi_read_i64 的地址必须是非零整数");
    return v_int(*(long long *)(intptr_t)addr.i);
}

static Val rt_ffi_read_str_at(Val addr) {
    if (addr.t != V_INT || addr.i == 0)
        return v_str("");
    const char *p = (const char *)(intptr_t)addr.i;
    return v_str(p ? p : "");
}

/* 动态 argtypes 无法安全生成 C 调用；代码生成器会在编译期拦截并给出明确报错。 */
static Val rt_ffi_call(Val lib, Val func, Val args, Val ret, Val argtypes) {
    (void)lib; (void)func; (void)args; (void)ret; (void)argtypes;
    rt_throw("FFIError",
             "C 后端的 ffi_call 需要字面量参数数组与 argtypes，才能生成强类型原生调用");
    return v_null();
}


/* ================= v4.2 并发：线程 / 互斥锁 / 原子量 =================
   与 impor/rt_concurrency.py 一一对应：句柄是整数、atomic_* 返回旧值、
   thread_join 返回线程函数结果。改一侧必须同步另一侧。
   共享可变容器（数组/字典/对象）跨线程使用时必须由调用方用 mutex 包住，
   运行库不做引用计数，也不提供 GC 屏障。 */

#define RT_MAX_THREADS 256
#define RT_MAX_MUTEXES 64
#define RT_MAX_ATOMS 128
#define RT_MAX_CONDS 64

typedef struct {
    int used, joined, done;
    Fn *fn;
    Val result;
#if defined(_WIN32)
    HANDLE th;
#else
    pthread_t th;
    int th_valid;
#endif
} RtThreadSlot;

typedef struct {
    int used;
#if defined(_WIN32)
    CRITICAL_SECTION cs;
#else
    pthread_mutex_t m;
#endif
} RtMutexSlot;

typedef struct {
    int used;
    long long v;
} RtAtomSlot;

typedef struct {
    int used;
#if defined(_WIN32)
#if defined(__TINYC__)
    /* 内置 tcc 的 win32 头文件没有 CONDITION_VARIABLE（Vista+ API），
       32 位端退化为「代际计数 + 1 ms 轮询」：语义仍是挂起后唤醒、不会丢通知，
       但唤醒粒度是 1 ms，不宣称「真阻塞」。 */
    volatile int gen;
#else
    CONDITION_VARIABLE cv;
#endif
#else
    pthread_cond_t c;
#endif
} RtCondSlot;

static RtThreadSlot g_threads[RT_MAX_THREADS];
static RtMutexSlot g_mutexes[RT_MAX_MUTEXES];
static RtAtomSlot g_atoms[RT_MAX_ATOMS];
static RtCondSlot g_conds[RT_MAX_CONDS];

static RtThreadSlot *rt_thread_slot(Val h, const char *what) {
    long long i;
    if (h.t != V_INT) rt_abort(what);
    i = h.i - 1;
    if (i < 0 || i >= RT_MAX_THREADS || !g_threads[i].used) rt_abort(what);
    return &g_threads[i];
}

#if defined(_WIN32)
static DWORD WINAPI rt_thread_entry(LPVOID arg)
#else
static void *rt_thread_entry(void *arg)
#endif
{
    RtThreadSlot *s = (RtThreadSlot *)arg;
    rt_tls();                     /* 本线程独立的跳转栈 / 错误槽 */
    s->result = s->fn ? rt_call(v_fn(s->fn), NULL, 0) : v_null();
    s->done = 1;
#if defined(_WIN32)
    return 0;
#else
    return NULL;
#endif
}

static Val rt_thread_spawn(Val fn) {
    int i;
    RtThreadSlot *s;
    if (fn.t != V_FUNC || !fn.fn) rt_abort("thread_spawn 需要函数（无参闭包）");
    rt_lock_mem();
    for (i = 0; i < RT_MAX_THREADS; i++) if (!g_threads[i].used) break;
    if (i >= RT_MAX_THREADS) { rt_unlock_mem(); rt_abort("线程数超过上限 256"); }
    s = &g_threads[i];
    memset(s, 0, sizeof(*s));
    s->used = 1;
    s->fn = fn.fn;
    rt_unlock_mem();
#if defined(_WIN32)
    s->th = CreateThread(NULL, 0, rt_thread_entry, s, 0, NULL);
    if (!s->th) { s->used = 0; rt_abort("CreateThread 失败"); }
#else
    if (pthread_create(&s->th, NULL, rt_thread_entry, s) != 0) {
        s->used = 0;
        rt_abort("pthread_create 失败");
    }
    s->th_valid = 1;
#endif
    return v_int(i + 1);
}

static Val rt_thread_join(Val h) {
    RtThreadSlot *s = rt_thread_slot(h, "thread_join 的句柄无效");
    if (!s->joined) {
#if defined(_WIN32)
        if (s->th) {
            WaitForSingleObject(s->th, INFINITE);
            CloseHandle(s->th);
            s->th = NULL;
        }
#else
        if (s->th_valid) {
            pthread_join(s->th, NULL);
            s->th_valid = 0;
        }
#endif
        s->joined = 1;
    }
    return s->result;
}

static Val rt_thread_alive(Val h) {
    RtThreadSlot *s = rt_thread_slot(h, "thread_alive 的句柄无效");
    return v_bool(!s->done);
}

static Val rt_thread_id(void) {
    RtTls *t = rt_tls();
    if (!t->tid) t->tid = rt_next_tid();
    return v_int(t->tid);
}

static Val rt_cpu_count(void) {
#if defined(_WIN32)
    SYSTEM_INFO si;
    GetSystemInfo(&si);
    return v_int((long long)(si.dwNumberOfProcessors ? si.dwNumberOfProcessors : 1));
#else
    long n = sysconf(_SC_NPROCESSORS_ONLN);
    return v_int(n > 0 ? (long long)n : 1);
#endif
}

/* 退出前等所有线程收尾，避免 rt_shutdown 释放数组时还有线程在跑 */
static void rt_wait_all_threads(void) {
    int i;
    for (i = 0; i < RT_MAX_THREADS; i++) {
        RtThreadSlot *s = &g_threads[i];
        if (!s->used || s->joined) continue;
#if defined(_WIN32)
        if (s->th) { WaitForSingleObject(s->th, INFINITE); CloseHandle(s->th); s->th = NULL; }
#else
        if (s->th_valid) { pthread_join(s->th, NULL); s->th_valid = 0; }
#endif
        s->joined = 1;
    }
}

static RtMutexSlot *rt_mutex_slot(Val h, const char *what) {
    long long i;
    if (h.t != V_INT) rt_abort(what);
    i = h.i - 1;
    if (i < 0 || i >= RT_MAX_MUTEXES || !g_mutexes[i].used) rt_abort(what);
    return &g_mutexes[i];
}

static Val rt_mutex_new(void) {
    int i;
    for (i = 0; i < RT_MAX_MUTEXES; i++) {
        if (!g_mutexes[i].used) {
            g_mutexes[i].used = 1;
#if defined(_WIN32)
            InitializeCriticalSection(&g_mutexes[i].cs);
#else
            pthread_mutex_init(&g_mutexes[i].m, NULL);
#endif
            return v_int(i + 1);
        }
    }
    rt_abort("互斥锁数量超过上限 64");
    return v_null();
}

static Val rt_mutex_lock(Val h) {
    RtMutexSlot *m = rt_mutex_slot(h, "mutex_lock 的句柄无效");
#if defined(_WIN32)
    EnterCriticalSection(&m->cs);
#else
    pthread_mutex_lock(&m->m);
#endif
    return v_int(0);
}

static Val rt_mutex_unlock(Val h) {
    RtMutexSlot *m = rt_mutex_slot(h, "mutex_unlock 的句柄无效");
#if defined(_WIN32)
    LeaveCriticalSection(&m->cs);
#else
    pthread_mutex_unlock(&m->m);
#endif
    return v_int(0);
}

static Val rt_mutex_trylock(Val h) {
    RtMutexSlot *m = rt_mutex_slot(h, "mutex_trylock 的句柄无效");
#if defined(_WIN32)
    return v_bool(TryEnterCriticalSection(&m->cs) != 0);
#else
    return v_bool(pthread_mutex_trylock(&m->m) == 0);
#endif
}

/* ---- v4.3.1 条件变量：把轮询等待换成挂起/唤醒 ----
 * 口径与 impor/rt_concurrency.py 一致：
 *   - 句柄是整数 id，与锁/线程句柄不通用；
 *   - cond_wait 必须由已持有 mutex 的线程调用：内部先释放锁、挂起，
 *     被唤醒后重新拿回锁才返回；cond_wait_ms 超时返回 false；
 *   - cond_notify 唤醒一个，cond_notify_all 唤醒全部；无等待者时通知丢失
 *     （条件变量不是信号量，条件状态要调用方自己记住）。
 * Windows 用 CONDITION_VARIABLE + SleepConditionVariableCS（原子释放 CS），
 * POSIX 用 pthread_cond_t + pthread_cond_wait / timedwait。
 */
static RtCondSlot *rt_cond_slot(Val h, const char *what) {
    long long i;
    if (h.t != V_INT) rt_abort(what);
    i = h.i - 1;
    if (i < 0 || i >= RT_MAX_CONDS || !g_conds[i].used) rt_abort(what);
    return &g_conds[i];
}

static Val rt_cond_new(void) {
    int i;
    for (i = 0; i < RT_MAX_CONDS; i++) {
        if (!g_conds[i].used) {
            g_conds[i].used = 1;
#if defined(_WIN32)
#if defined(__TINYC__)
            g_conds[i].gen = 0;
#else
            InitializeConditionVariable(&g_conds[i].cv);
#endif
#else
            pthread_cond_init(&g_conds[i].c, NULL);
#endif
            return v_int(i + 1);
        }
    }
    rt_abort("条件变量数量超过上限 64");
    return v_null();
}

static Val rt_cond_wait(Val ch, Val mh) {
    RtCondSlot *c = rt_cond_slot(ch, "cond_wait 的条件变量句柄无效");
    RtMutexSlot *m = rt_mutex_slot(mh, "cond_wait 的互斥锁句柄无效");
#if defined(_WIN32)
#if defined(__TINYC__)
    int g = c->gen;
    LeaveCriticalSection(&m->cs);
    while (c->gen == g) Sleep(1);
    EnterCriticalSection(&m->cs);
#else
    if (!SleepConditionVariableCS(&c->cv, &m->cs, INFINITE))
        rt_abort("cond_wait 等待失败");
#endif
#else
    pthread_cond_wait(&c->c, &m->m);
#endif
    return v_int(0);
}

static Val rt_cond_wait_ms(Val ch, Val mh, Val ms) {
    RtCondSlot *c = rt_cond_slot(ch, "cond_wait_ms 的条件变量句柄无效");
    RtMutexSlot *m = rt_mutex_slot(mh, "cond_wait_ms 的互斥锁句柄无效");
    long long t = 0;
    if (ms.t == V_INT) t = ms.i; else if (ms.t == V_FLOAT) t = (long long)ms.f;
    if (t < 0) t = 0;
#if defined(_WIN32)
#if defined(__TINYC__)
    int g = c->gen;
    long long waited = 0;
    LeaveCriticalSection(&m->cs);
    while (c->gen == g && waited < t) { Sleep(1); waited += 1; }
    EnterCriticalSection(&m->cs);
    return v_bool(c->gen != g);
#else
    if (!SleepConditionVariableCS(&c->cv, &m->cs, (DWORD)t)) {
        if (GetLastError() == ERROR_TIMEOUT) return v_bool(0);
        rt_abort("cond_wait_ms 等待失败");
    }
#endif
#else
    if (t == 0) {
        pthread_cond_wait(&c->c, &m->m);
    } else {
        struct timespec ts;
        clock_gettime(CLOCK_REALTIME, &ts);
        ts.tv_sec += t / 1000;
        ts.tv_nsec += (t % 1000) * 1000000L;
        if (ts.tv_nsec >= 1000000000L) { ts.tv_sec += 1; ts.tv_nsec -= 1000000000L; }
        if (pthread_cond_timedwait(&c->c, &m->m, &ts) != 0) return v_bool(0);
    }
#endif
    return v_bool(1);
}

static Val rt_cond_notify(Val ch) {
    RtCondSlot *c = rt_cond_slot(ch, "cond_notify 的条件变量句柄无效");
#if defined(_WIN32)
#if defined(__TINYC__)
    c->gen = c->gen + 1;
#else
    WakeConditionVariable(&c->cv);
#endif
#else
    pthread_cond_signal(&c->c);
#endif
    return v_int(0);
}

static Val rt_cond_notify_all(Val ch) {
    RtCondSlot *c = rt_cond_slot(ch, "cond_notify_all 的条件变量句柄无效");
#if defined(_WIN32)
#if defined(__TINYC__)
    c->gen = c->gen + 1;
#else
    WakeAllConditionVariable(&c->cv);
#endif
#else
    pthread_cond_broadcast(&c->c);
#endif
    return v_int(0);
}

static RtAtomSlot *rt_atom_slot(Val h, const char *what) {
    long long i;
    if (h.t != V_INT) rt_abort(what);
    i = h.i - 1;
    if (i < 0 || i >= RT_MAX_ATOMS || !g_atoms[i].used) rt_abort(what);
    return &g_atoms[i];
}

static Val rt_atomic_new(Val v) {
    int i;
    long long init = 0;
    if (v.t == V_INT) init = v.i;
    else if (v.t == V_FLOAT) init = (long long)v.f;
    else if (v.t == V_BOOL) init = v.i ? 1 : 0;
    for (i = 0; i < RT_MAX_ATOMS; i++) {
        if (!g_atoms[i].used) {
            g_atoms[i].used = 1;
            g_atoms[i].v = init;
            return v_int(i + 1);
        }
    }
    rt_abort("原子量数量超过上限 128");
    return v_null();
}

static Val rt_atomic_load(Val a) {
    RtAtomSlot *s = rt_atom_slot(a, "atomic_load 的句柄无效");
    long long cur;
    rt_lock_stat();
    cur = s->v;
    rt_unlock_stat();
    return v_int(cur);
}

static Val rt_atomic_store(Val a, Val v) {
    RtAtomSlot *s = rt_atom_slot(a, "atomic_store 的句柄无效");
    long long nv = (v.t == V_FLOAT) ? (long long)v.f : v.i;
    long long old;
    rt_lock_stat();
    old = s->v;
    s->v = nv;
    rt_unlock_stat();
    return v_int(old);
}

static Val rt_atomic_add(Val a, Val n) {
    RtAtomSlot *s = rt_atom_slot(a, "atomic_add 的句柄无效");
    long long d = (n.t == V_FLOAT) ? (long long)n.f : n.i;
    long long old;
    rt_lock_stat();
    old = s->v;
    s->v = old + d;
    rt_unlock_stat();
    return v_int(old);
}

static Val rt_atomic_sub(Val a, Val n) {
    RtAtomSlot *s = rt_atom_slot(a, "atomic_sub 的句柄无效");
    long long d = (n.t == V_FLOAT) ? (long long)n.f : n.i;
    long long old;
    rt_lock_stat();
    old = s->v;
    s->v = old - d;
    rt_unlock_stat();
    return v_int(old);
}

static Val rt_atomic_exchange(Val a, Val v) {
    return rt_atomic_store(a, v);
}

static Val rt_atomic_cas(Val a, Val expect, Val nv) {
    RtAtomSlot *s = rt_atom_slot(a, "atomic_cas 的句柄无效");
    long long ov = (expect.t == V_FLOAT) ? (long long)expect.f : expect.i;
    long long wv = (nv.t == V_FLOAT) ? (long long)nv.f : nv.i;
    int ok = 0;
    rt_lock_stat();
    if (s->v == ov) {
        s->v = wv;
        ok = 1;
    }
    rt_unlock_stat();
    return v_bool(ok);
}


/* ===== 4.5.0：IR 下降后端通用助手 =====
 * `impor/cir.py` 把 IR 的三地址码指令映射到这些函数上。放在文件末尾，
 * 保证引用的 rt_* 都已定义；ctrans 不调用它们，静态函数未使用不影响编译。
 */

/* ** 运算：整数走快速幂并检查溢出，任一侧是浮点则用 pow */
static Val rt_pow_impor(Val a, Val b) {
    if (a.t == V_FLOAT || b.t == V_FLOAT)
        return v_float(pow(rt_num(a), rt_num(b)));
    long long base = rt_int(a), e = rt_int(b);
    if (e < 0) {
        if (base == 0) rt_throw("DivisionByZero", "0 的负数次幂未定义");
        return v_float(pow((double)base, (double)e));
    }
    long long acc = 1;
    while (e > 0) {
        if (e & 1) acc = rt_ck_mul(acc, base);
        base = rt_ck_mul(base, base);
        e >>= 1;
    }
    return v_int(acc);
}

/* 一元 +：数值原样，字符串/其它类型按解释器口径返回原值 */
static Val rt_pos_impor(Val v) { return v; }

/* 下标写入：dict 走键值表，其余走数组（IR 的 SETINDEX 不区分容器种类） */
static Val rt_setindex(Val c, Val i, Val v) {
    if (c.t == V_DICT) return rt_dict_set(c, i, v);
    return rt_arr_set(c, i, v);
}

/* for 循环迭代：长度与第 i 个元素，容器种类在运行期决定 */
static long long rt_iter_n(Val c) {
    if (c.t == V_ARR) return c.a ? c.a->n : 0;
    if (c.t == V_STR) return rt_u8len(c.s ? c.s : "");
    if (c.t == V_DICT) return c.a ? c.a->n / 2 : 0;
    return 0;
}

/* dict 迭代按「键」进行（对齐解释器 for k in d）；数组/字符串原样返回 */
static Val rt_iter_keys(Val c) {
    if (c.t == V_DICT) return rt_dict_keys(c);
    return c;
}

static Val rt_iter_at(Val c, long long i) {
    if (c.t == V_STR) {
        size_t clen = 0;
        const char *q = rt_u8_at(c.s ? c.s : "", i, &clen);
        if (!q) return v_null();
        return rt_vstr_range(q, clen);
    }
    return rt_index(c, v_int(i));
}


/* range(start, stop[, step])：IR 里 range 是普通调用，需要多参数版本 */
static Val rt_range3(Val s, Val e, Val st) {
    long long start = rt_int(s), stop = rt_int(e), step = rt_int(st);
    if (step == 0) rt_throw("ValueError", "range 的步长不能为 0");
    Arr *a = arr_new(8);
    long long i;
    if (step > 0) {
        for (i = start; i < stop; i += step) arr_push(a, v_int(i));
    } else {
        for (i = start; i > stop; i += step) arr_push(a, v_int(i));
    }
    Val v = v_arr();
    v.a = a;
    return v;
}

/* ===== 4.5.0：Unicode 简单大小写映射（同 impor/unicode_case.py 的表） =====
/* 只覆盖 1:1 映射；二分查找，未命中的码点原样保留。 */
#define RT_UC_N 420
static const unsigned int rt_uc_upper_from[] = {
    0x0061, 0x0062, 0x0063, 0x0064, 0x0065, 0x0066, 0x0067, 0x0068, 0x0069, 0x006A, 0x006B, 0x006C, 0x006D, 0x006E, 0x006F, 0x0070, 0x0071, 0x0072, 0x0073, 0x0074, 0x0075, 0x0076, 0x0077, 0x0078, 0x0079, 0x007A, 0x00E0, 0x00E1, 0x00E2, 0x00E3, 0x00E4, 0x00E5, 0x00E6, 0x00E7, 0x00E8, 0x00E9, 0x00EA, 0x00EB, 0x00EC, 0x00ED, 0x00EE, 0x00EF, 0x00F0, 0x00F1, 0x00F2, 0x00F3, 0x00F4, 0x00F5, 0x00F6, 0x00F8, 0x00F9, 0x00FA, 0x00FB, 0x00FC, 0x00FD, 0x00FE, 0x00FF, 0x0101, 0x0103, 0x0105, 0x0107, 0x0109, 0x010B, 0x010D, 0x010F, 0x0111, 0x0113, 0x0115, 0x0117, 0x0119, 0x011B, 0x011D, 0x011F, 0x0121, 0x0123, 0x0125, 0x0127, 0x0129, 0x012B, 0x012D, 0x012F, 0x0131, 0x0133, 0x0135, 0x0137, 0x013A, 0x013C, 0x013E, 0x0140, 0x0142, 0x0144, 0x0146, 0x0148, 0x014B, 0x014D, 0x014F, 0x0151, 0x0153, 0x0155, 0x0157, 0x0159, 0x015B, 0x015D, 0x015F, 0x0161, 0x0163, 0x0165, 0x0167, 0x0169, 0x016B, 0x016D, 0x016F, 0x0171, 0x0173, 0x0175, 0x0177, 0x017A, 0x017C, 0x017E, 0x017F, 0x0180, 0x0183, 0x0185, 0x0188, 0x018C, 0x0192, 0x0195, 0x0199, 0x019A, 0x019E, 0x01A1, 0x01A3, 0x01A5, 0x01A8, 0x01AD, 0x01B0, 0x01B4, 0x01B6, 0x01B9, 0x01BD, 0x01BF, 0x01C5, 0x01C6, 0x01C8, 0x01C9, 0x01CB, 0x01CC, 0x01CE, 0x01D0, 0x01D2, 0x01D4, 0x01D6, 0x01D8, 0x01DA, 0x01DC, 0x01DD, 0x01DF, 0x01E1, 0x01E3, 0x01E5, 0x01E7, 0x01E9, 0x01EB, 0x01ED, 0x01EF, 0x01F2, 0x01F3, 0x01F5, 0x01F9, 0x01FB, 0x01FD, 0x01FF, 0x0201, 0x0203, 0x0205, 0x0207, 0x0209, 0x020B, 0x020D, 0x020F, 0x0211, 0x0213, 0x0215, 0x0217, 0x0219, 0x021B, 0x021D, 0x021F, 0x0223, 0x0225, 0x0227, 0x0229, 0x022B, 0x022D, 0x022F, 0x0231, 0x0233, 0x023C, 0x023F, 0x0240, 0x0242, 0x0247, 0x0249, 0x024B, 0x024D, 0x024F, 0x0371, 0x0373, 0x0377, 0x037B, 0x037C, 0x037D, 0x03AC, 0x03AD, 0x03AE, 0x03AF, 0x03B1, 0x03B2, 0x03B3, 0x03B4, 0x03B5, 0x03B6, 0x03B7, 0x03B8, 0x03B9, 0x03BA, 0x03BB, 0x03BC, 0x03BD, 0x03BE, 0x03BF, 0x03C0, 0x03C1, 0x03C2, 0x03C3, 0x03C4, 0x03C5, 0x03C6, 0x03C7, 0x03C8, 0x03C9, 0x03CA, 0x03CB, 0x03CC, 0x03CD, 0x03CE, 0x03D0, 0x03D1, 0x03D5, 0x03D6, 0x03D7, 0x03D9, 0x03DB, 0x03DD, 0x03DF, 0x03E1, 0x03E3, 0x03E5, 0x03E7, 0x03E9, 0x03EB, 0x03ED, 0x03EF, 0x03F0, 0x03F1, 0x03F2, 0x03F3, 0x03F5, 0x03F8, 0x03FB, 0x0430, 0x0431, 0x0432, 0x0433, 0x0434, 0x0435, 0x0436, 0x0437, 0x0438, 0x0439, 0x043A, 0x043B, 0x043C, 0x043D, 0x043E, 0x043F, 0x0440, 0x0441, 0x0442, 0x0443, 0x0444, 0x0445, 0x0446, 0x0447, 0x0448, 0x0449, 0x044A, 0x044B, 0x044C, 0x044D, 0x044E, 0x044F, 0x0450, 0x0451, 0x0452, 0x0453, 0x0454, 0x0455, 0x0456, 0x0457, 0x0458, 0x0459, 0x045A, 0x045B, 0x045C, 0x045D, 0x045E, 0x045F, 0x0461, 0x0463, 0x0465, 0x0467, 0x0469, 0x046B, 0x046D, 0x046F, 0x0471, 0x0473, 0x0475, 0x0477, 0x0479, 0x047B, 0x047D, 0x047F, 0x0481, 0x048B, 0x048D, 0x048F, 0x0491, 0x0493, 0x0495, 0x0497, 0x0499, 0x049B, 0x049D, 0x049F, 0x04A1, 0x04A3, 0x04A5, 0x04A7, 0x04A9, 0x04AB, 0x04AD, 0x04AF, 0x04B1, 0x04B3, 0x04B5, 0x04B7, 0x04B9, 0x04BB, 0x04BD, 0x04BF, 0x04C2, 0x04C4, 0x04C6, 0x04C8, 0x04CA, 0x04CC, 0x04CE, 0x04CF, 0x04D1, 0x04D3, 0x04D5, 0x04D7, 0x04D9, 0x04DB, 0x04DD, 0x04DF, 0x04E1, 0x04E3, 0x04E5, 0x04E7, 0x04E9, 0x04EB, 0x04ED, 0x04EF, 0x04F1, 0x04F3, 0x04F5, 0x04F7, 0x04F9, 0x04FB, 0x04FD, 0x04FF, 0xFF41, 0xFF42, 0xFF43, 0xFF44, 0xFF45, 0xFF46, 0xFF47, 0xFF48, 0xFF49, 0xFF4A, 0xFF4B, 0xFF4C, 0xFF4D, 0xFF4E, 0xFF4F, 0xFF50, 0xFF51, 0xFF52, 0xFF53, 0xFF54, 0xFF55, 0xFF56, 0xFF57, 0xFF58, 0xFF59, 0xFF5A
};
static const unsigned int rt_uc_upper_to[] = {
    0x0041, 0x0042, 0x0043, 0x0044, 0x0045, 0x0046, 0x0047, 0x0048, 0x0049, 0x004A, 0x004B, 0x004C, 0x004D, 0x004E, 0x004F, 0x0050, 0x0051, 0x0052, 0x0053, 0x0054, 0x0055, 0x0056, 0x0057, 0x0058, 0x0059, 0x005A, 0x00C0, 0x00C1, 0x00C2, 0x00C3, 0x00C4, 0x00C5, 0x00C6, 0x00C7, 0x00C8, 0x00C9, 0x00CA, 0x00CB, 0x00CC, 0x00CD, 0x00CE, 0x00CF, 0x00D0, 0x00D1, 0x00D2, 0x00D3, 0x00D4, 0x00D5, 0x00D6, 0x00D8, 0x00D9, 0x00DA, 0x00DB, 0x00DC, 0x00DD, 0x00DE, 0x0178, 0x0100, 0x0102, 0x0104, 0x0106, 0x0108, 0x010A, 0x010C, 0x010E, 0x0110, 0x0112, 0x0114, 0x0116, 0x0118, 0x011A, 0x011C, 0x011E, 0x0120, 0x0122, 0x0124, 0x0126, 0x0128, 0x012A, 0x012C, 0x012E, 0x0049, 0x0132, 0x0134, 0x0136, 0x0139, 0x013B, 0x013D, 0x013F, 0x0141, 0x0143, 0x0145, 0x0147, 0x014A, 0x014C, 0x014E, 0x0150, 0x0152, 0x0154, 0x0156, 0x0158, 0x015A, 0x015C, 0x015E, 0x0160, 0x0162, 0x0164, 0x0166, 0x0168, 0x016A, 0x016C, 0x016E, 0x0170, 0x0172, 0x0174, 0x0176, 0x0179, 0x017B, 0x017D, 0x0053, 0x0243, 0x0182, 0x0184, 0x0187, 0x018B, 0x0191, 0x01F6, 0x0198, 0x023D, 0x0220, 0x01A0, 0x01A2, 0x01A4, 0x01A7, 0x01AC, 0x01AF, 0x01B3, 0x01B5, 0x01B8, 0x01BC, 0x01F7, 0x01C4, 0x01C4, 0x01C7, 0x01C7, 0x01CA, 0x01CA, 0x01CD, 0x01CF, 0x01D1, 0x01D3, 0x01D5, 0x01D7, 0x01D9, 0x01DB, 0x018E, 0x01DE, 0x01E0, 0x01E2, 0x01E4, 0x01E6, 0x01E8, 0x01EA, 0x01EC, 0x01EE, 0x01F1, 0x01F1, 0x01F4, 0x01F8, 0x01FA, 0x01FC, 0x01FE, 0x0200, 0x0202, 0x0204, 0x0206, 0x0208, 0x020A, 0x020C, 0x020E, 0x0210, 0x0212, 0x0214, 0x0216, 0x0218, 0x021A, 0x021C, 0x021E, 0x0222, 0x0224, 0x0226, 0x0228, 0x022A, 0x022C, 0x022E, 0x0230, 0x0232, 0x023B, 0x2C7E, 0x2C7F, 0x0241, 0x0246, 0x0248, 0x024A, 0x024C, 0x024E, 0x0370, 0x0372, 0x0376, 0x03FD, 0x03FE, 0x03FF, 0x0386, 0x0388, 0x0389, 0x038A, 0x0391, 0x0392, 0x0393, 0x0394, 0x0395, 0x0396, 0x0397, 0x0398, 0x0399, 0x039A, 0x039B, 0x039C, 0x039D, 0x039E, 0x039F, 0x03A0, 0x03A1, 0x03A3, 0x03A3, 0x03A4, 0x03A5, 0x03A6, 0x03A7, 0x03A8, 0x03A9, 0x03AA, 0x03AB, 0x038C, 0x038E, 0x038F, 0x0392, 0x0398, 0x03A6, 0x03A0, 0x03CF, 0x03D8, 0x03DA, 0x03DC, 0x03DE, 0x03E0, 0x03E2, 0x03E4, 0x03E6, 0x03E8, 0x03EA, 0x03EC, 0x03EE, 0x039A, 0x03A1, 0x03F9, 0x037F, 0x0395, 0x03F7, 0x03FA, 0x0410, 0x0411, 0x0412, 0x0413, 0x0414, 0x0415, 0x0416, 0x0417, 0x0418, 0x0419, 0x041A, 0x041B, 0x041C, 0x041D, 0x041E, 0x041F, 0x0420, 0x0421, 0x0422, 0x0423, 0x0424, 0x0425, 0x0426, 0x0427, 0x0428, 0x0429, 0x042A, 0x042B, 0x042C, 0x042D, 0x042E, 0x042F, 0x0400, 0x0401, 0x0402, 0x0403, 0x0404, 0x0405, 0x0406, 0x0407, 0x0408, 0x0409, 0x040A, 0x040B, 0x040C, 0x040D, 0x040E, 0x040F, 0x0460, 0x0462, 0x0464, 0x0466, 0x0468, 0x046A, 0x046C, 0x046E, 0x0470, 0x0472, 0x0474, 0x0476, 0x0478, 0x047A, 0x047C, 0x047E, 0x0480, 0x048A, 0x048C, 0x048E, 0x0490, 0x0492, 0x0494, 0x0496, 0x0498, 0x049A, 0x049C, 0x049E, 0x04A0, 0x04A2, 0x04A4, 0x04A6, 0x04A8, 0x04AA, 0x04AC, 0x04AE, 0x04B0, 0x04B2, 0x04B4, 0x04B6, 0x04B8, 0x04BA, 0x04BC, 0x04BE, 0x04C1, 0x04C3, 0x04C5, 0x04C7, 0x04C9, 0x04CB, 0x04CD, 0x04C0, 0x04D0, 0x04D2, 0x04D4, 0x04D6, 0x04D8, 0x04DA, 0x04DC, 0x04DE, 0x04E0, 0x04E2, 0x04E4, 0x04E6, 0x04E8, 0x04EA, 0x04EC, 0x04EE, 0x04F0, 0x04F2, 0x04F4, 0x04F6, 0x04F8, 0x04FA, 0x04FC, 0x04FE, 0xFF21, 0xFF22, 0xFF23, 0xFF24, 0xFF25, 0xFF26, 0xFF27, 0xFF28, 0xFF29, 0xFF2A, 0xFF2B, 0xFF2C, 0xFF2D, 0xFF2E, 0xFF2F, 0xFF30, 0xFF31, 0xFF32, 0xFF33, 0xFF34, 0xFF35, 0xFF36, 0xFF37, 0xFF38, 0xFF39, 0xFF3A
};
#define RT_UC_LN 432
static const unsigned int rt_uc_lower_from[] = {
    0x0041, 0x0042, 0x0043, 0x0044, 0x0045, 0x0046, 0x0047, 0x0048, 0x0049, 0x004A, 0x004B, 0x004C, 0x004D, 0x004E, 0x004F, 0x0050, 0x0051, 0x0052, 0x0053, 0x0054, 0x0055, 0x0056, 0x0057, 0x0058, 0x0059, 0x005A, 0x00C0, 0x00C1, 0x00C2, 0x00C3, 0x00C4, 0x00C5, 0x00C6, 0x00C7, 0x00C8, 0x00C9, 0x00CA, 0x00CB, 0x00CC, 0x00CD, 0x00CE, 0x00CF, 0x00D0, 0x00D1, 0x00D2, 0x00D3, 0x00D4, 0x00D5, 0x00D6, 0x00D8, 0x00D9, 0x00DA, 0x00DB, 0x00DC, 0x00DD, 0x00DE, 0x0100, 0x0102, 0x0104, 0x0106, 0x0108, 0x010A, 0x010C, 0x010E, 0x0110, 0x0112, 0x0114, 0x0116, 0x0118, 0x011A, 0x011C, 0x011E, 0x0120, 0x0122, 0x0124, 0x0126, 0x0128, 0x012A, 0x012C, 0x012E, 0x0132, 0x0134, 0x0136, 0x0139, 0x013B, 0x013D, 0x013F, 0x0141, 0x0143, 0x0145, 0x0147, 0x014A, 0x014C, 0x014E, 0x0150, 0x0152, 0x0154, 0x0156, 0x0158, 0x015A, 0x015C, 0x015E, 0x0160, 0x0162, 0x0164, 0x0166, 0x0168, 0x016A, 0x016C, 0x016E, 0x0170, 0x0172, 0x0174, 0x0176, 0x0178, 0x0179, 0x017B, 0x017D, 0x0181, 0x0182, 0x0184, 0x0186, 0x0187, 0x0189, 0x018A, 0x018B, 0x018E, 0x018F, 0x0190, 0x0191, 0x0193, 0x0194, 0x0196, 0x0197, 0x0198, 0x019C, 0x019D, 0x019F, 0x01A0, 0x01A2, 0x01A4, 0x01A6, 0x01A7, 0x01A9, 0x01AC, 0x01AE, 0x01AF, 0x01B1, 0x01B2, 0x01B3, 0x01B5, 0x01B7, 0x01B8, 0x01BC, 0x01C4, 0x01C5, 0x01C7, 0x01C8, 0x01CA, 0x01CB, 0x01CD, 0x01CF, 0x01D1, 0x01D3, 0x01D5, 0x01D7, 0x01D9, 0x01DB, 0x01DE, 0x01E0, 0x01E2, 0x01E4, 0x01E6, 0x01E8, 0x01EA, 0x01EC, 0x01EE, 0x01F1, 0x01F2, 0x01F4, 0x01F6, 0x01F7, 0x01F8, 0x01FA, 0x01FC, 0x01FE, 0x0200, 0x0202, 0x0204, 0x0206, 0x0208, 0x020A, 0x020C, 0x020E, 0x0210, 0x0212, 0x0214, 0x0216, 0x0218, 0x021A, 0x021C, 0x021E, 0x0220, 0x0222, 0x0224, 0x0226, 0x0228, 0x022A, 0x022C, 0x022E, 0x0230, 0x0232, 0x023A, 0x023B, 0x023D, 0x023E, 0x0241, 0x0243, 0x0244, 0x0245, 0x0246, 0x0248, 0x024A, 0x024C, 0x024E, 0x0370, 0x0372, 0x0376, 0x037F, 0x0386, 0x0388, 0x0389, 0x038A, 0x038C, 0x038E, 0x038F, 0x0391, 0x0392, 0x0393, 0x0394, 0x0395, 0x0396, 0x0397, 0x0398, 0x0399, 0x039A, 0x039B, 0x039C, 0x039D, 0x039E, 0x039F, 0x03A0, 0x03A1, 0x03A3, 0x03A4, 0x03A5, 0x03A6, 0x03A7, 0x03A8, 0x03A9, 0x03AA, 0x03AB, 0x03CF, 0x03D8, 0x03DA, 0x03DC, 0x03DE, 0x03E0, 0x03E2, 0x03E4, 0x03E6, 0x03E8, 0x03EA, 0x03EC, 0x03EE, 0x03F4, 0x03F7, 0x03F9, 0x03FA, 0x03FD, 0x03FE, 0x03FF, 0x0400, 0x0401, 0x0402, 0x0403, 0x0404, 0x0405, 0x0406, 0x0407, 0x0408, 0x0409, 0x040A, 0x040B, 0x040C, 0x040D, 0x040E, 0x040F, 0x0410, 0x0411, 0x0412, 0x0413, 0x0414, 0x0415, 0x0416, 0x0417, 0x0418, 0x0419, 0x041A, 0x041B, 0x041C, 0x041D, 0x041E, 0x041F, 0x0420, 0x0421, 0x0422, 0x0423, 0x0424, 0x0425, 0x0426, 0x0427, 0x0428, 0x0429, 0x042A, 0x042B, 0x042C, 0x042D, 0x042E, 0x042F, 0x0460, 0x0462, 0x0464, 0x0466, 0x0468, 0x046A, 0x046C, 0x046E, 0x0470, 0x0472, 0x0474, 0x0476, 0x0478, 0x047A, 0x047C, 0x047E, 0x0480, 0x048A, 0x048C, 0x048E, 0x0490, 0x0492, 0x0494, 0x0496, 0x0498, 0x049A, 0x049C, 0x049E, 0x04A0, 0x04A2, 0x04A4, 0x04A6, 0x04A8, 0x04AA, 0x04AC, 0x04AE, 0x04B0, 0x04B2, 0x04B4, 0x04B6, 0x04B8, 0x04BA, 0x04BC, 0x04BE, 0x04C0, 0x04C1, 0x04C3, 0x04C5, 0x04C7, 0x04C9, 0x04CB, 0x04CD, 0x04D0, 0x04D2, 0x04D4, 0x04D6, 0x04D8, 0x04DA, 0x04DC, 0x04DE, 0x04E0, 0x04E2, 0x04E4, 0x04E6, 0x04E8, 0x04EA, 0x04EC, 0x04EE, 0x04F0, 0x04F2, 0x04F4, 0x04F6, 0x04F8, 0x04FA, 0x04FC, 0x04FE, 0xFF21, 0xFF22, 0xFF23, 0xFF24, 0xFF25, 0xFF26, 0xFF27, 0xFF28, 0xFF29, 0xFF2A, 0xFF2B, 0xFF2C, 0xFF2D, 0xFF2E, 0xFF2F, 0xFF30, 0xFF31, 0xFF32, 0xFF33, 0xFF34, 0xFF35, 0xFF36, 0xFF37, 0xFF38, 0xFF39, 0xFF3A
};
static const unsigned int rt_uc_lower_to[] = {
    0x0061, 0x0062, 0x0063, 0x0064, 0x0065, 0x0066, 0x0067, 0x0068, 0x0069, 0x006A, 0x006B, 0x006C, 0x006D, 0x006E, 0x006F, 0x0070, 0x0071, 0x0072, 0x0073, 0x0074, 0x0075, 0x0076, 0x0077, 0x0078, 0x0079, 0x007A, 0x00E0, 0x00E1, 0x00E2, 0x00E3, 0x00E4, 0x00E5, 0x00E6, 0x00E7, 0x00E8, 0x00E9, 0x00EA, 0x00EB, 0x00EC, 0x00ED, 0x00EE, 0x00EF, 0x00F0, 0x00F1, 0x00F2, 0x00F3, 0x00F4, 0x00F5, 0x00F6, 0x00F8, 0x00F9, 0x00FA, 0x00FB, 0x00FC, 0x00FD, 0x00FE, 0x0101, 0x0103, 0x0105, 0x0107, 0x0109, 0x010B, 0x010D, 0x010F, 0x0111, 0x0113, 0x0115, 0x0117, 0x0119, 0x011B, 0x011D, 0x011F, 0x0121, 0x0123, 0x0125, 0x0127, 0x0129, 0x012B, 0x012D, 0x012F, 0x0133, 0x0135, 0x0137, 0x013A, 0x013C, 0x013E, 0x0140, 0x0142, 0x0144, 0x0146, 0x0148, 0x014B, 0x014D, 0x014F, 0x0151, 0x0153, 0x0155, 0x0157, 0x0159, 0x015B, 0x015D, 0x015F, 0x0161, 0x0163, 0x0165, 0x0167, 0x0169, 0x016B, 0x016D, 0x016F, 0x0171, 0x0173, 0x0175, 0x0177, 0x00FF, 0x017A, 0x017C, 0x017E, 0x0253, 0x0183, 0x0185, 0x0254, 0x0188, 0x0256, 0x0257, 0x018C, 0x01DD, 0x0259, 0x025B, 0x0192, 0x0260, 0x0263, 0x0269, 0x0268, 0x0199, 0x026F, 0x0272, 0x0275, 0x01A1, 0x01A3, 0x01A5, 0x0280, 0x01A8, 0x0283, 0x01AD, 0x0288, 0x01B0, 0x028A, 0x028B, 0x01B4, 0x01B6, 0x0292, 0x01B9, 0x01BD, 0x01C6, 0x01C6, 0x01C9, 0x01C9, 0x01CC, 0x01CC, 0x01CE, 0x01D0, 0x01D2, 0x01D4, 0x01D6, 0x01D8, 0x01DA, 0x01DC, 0x01DF, 0x01E1, 0x01E3, 0x01E5, 0x01E7, 0x01E9, 0x01EB, 0x01ED, 0x01EF, 0x01F3, 0x01F3, 0x01F5, 0x0195, 0x01BF, 0x01F9, 0x01FB, 0x01FD, 0x01FF, 0x0201, 0x0203, 0x0205, 0x0207, 0x0209, 0x020B, 0x020D, 0x020F, 0x0211, 0x0213, 0x0215, 0x0217, 0x0219, 0x021B, 0x021D, 0x021F, 0x019E, 0x0223, 0x0225, 0x0227, 0x0229, 0x022B, 0x022D, 0x022F, 0x0231, 0x0233, 0x2C65, 0x023C, 0x019A, 0x2C66, 0x0242, 0x0180, 0x0289, 0x028C, 0x0247, 0x0249, 0x024B, 0x024D, 0x024F, 0x0371, 0x0373, 0x0377, 0x03F3, 0x03AC, 0x03AD, 0x03AE, 0x03AF, 0x03CC, 0x03CD, 0x03CE, 0x03B1, 0x03B2, 0x03B3, 0x03B4, 0x03B5, 0x03B6, 0x03B7, 0x03B8, 0x03B9, 0x03BA, 0x03BB, 0x03BC, 0x03BD, 0x03BE, 0x03BF, 0x03C0, 0x03C1, 0x03C3, 0x03C4, 0x03C5, 0x03C6, 0x03C7, 0x03C8, 0x03C9, 0x03CA, 0x03CB, 0x03D7, 0x03D9, 0x03DB, 0x03DD, 0x03DF, 0x03E1, 0x03E3, 0x03E5, 0x03E7, 0x03E9, 0x03EB, 0x03ED, 0x03EF, 0x03B8, 0x03F8, 0x03F2, 0x03FB, 0x037B, 0x037C, 0x037D, 0x0450, 0x0451, 0x0452, 0x0453, 0x0454, 0x0455, 0x0456, 0x0457, 0x0458, 0x0459, 0x045A, 0x045B, 0x045C, 0x045D, 0x045E, 0x045F, 0x0430, 0x0431, 0x0432, 0x0433, 0x0434, 0x0435, 0x0436, 0x0437, 0x0438, 0x0439, 0x043A, 0x043B, 0x043C, 0x043D, 0x043E, 0x043F, 0x0440, 0x0441, 0x0442, 0x0443, 0x0444, 0x0445, 0x0446, 0x0447, 0x0448, 0x0449, 0x044A, 0x044B, 0x044C, 0x044D, 0x044E, 0x044F, 0x0461, 0x0463, 0x0465, 0x0467, 0x0469, 0x046B, 0x046D, 0x046F, 0x0471, 0x0473, 0x0475, 0x0477, 0x0479, 0x047B, 0x047D, 0x047F, 0x0481, 0x048B, 0x048D, 0x048F, 0x0491, 0x0493, 0x0495, 0x0497, 0x0499, 0x049B, 0x049D, 0x049F, 0x04A1, 0x04A3, 0x04A5, 0x04A7, 0x04A9, 0x04AB, 0x04AD, 0x04AF, 0x04B1, 0x04B3, 0x04B5, 0x04B7, 0x04B9, 0x04BB, 0x04BD, 0x04BF, 0x04CF, 0x04C2, 0x04C4, 0x04C6, 0x04C8, 0x04CA, 0x04CC, 0x04CE, 0x04D1, 0x04D3, 0x04D5, 0x04D7, 0x04D9, 0x04DB, 0x04DD, 0x04DF, 0x04E1, 0x04E3, 0x04E5, 0x04E7, 0x04E9, 0x04EB, 0x04ED, 0x04EF, 0x04F1, 0x04F3, 0x04F5, 0x04F7, 0x04F9, 0x04FB, 0x04FD, 0x04FF, 0xFF41, 0xFF42, 0xFF43, 0xFF44, 0xFF45, 0xFF46, 0xFF47, 0xFF48, 0xFF49, 0xFF4A, 0xFF4B, 0xFF4C, 0xFF4D, 0xFF4E, 0xFF4F, 0xFF50, 0xFF51, 0xFF52, 0xFF53, 0xFF54, 0xFF55, 0xFF56, 0xFF57, 0xFF58, 0xFF59, 0xFF5A
};

static unsigned int rt_uc_lookup(const unsigned int *from, const unsigned int *to,
                                 int n, unsigned int cp) {
    int lo = 0, hi = n - 1;
    while (lo <= hi) {
        int mid = (lo + hi) / 2;
        if (from[mid] == cp) return to[mid];
        if (from[mid] < cp) lo = mid + 1; else hi = mid - 1;
    }
    return cp;
}

/* UTF-8 解码一个码点；*len 返回字节数 */
static unsigned int rt_uc_decode(const unsigned char *p, int *len) {
    unsigned int cp;
    if (p[0] < 0x80) { *len = 1; return p[0]; }
    if ((p[0] & 0xE0) == 0xC0) { cp = p[0] & 0x1F; *len = 2; }
    else if ((p[0] & 0xF0) == 0xE0) { cp = p[0] & 0x0F; *len = 3; }
    else if ((p[0] & 0xF8) == 0xF0) { cp = p[0] & 0x07; *len = 4; }
    else { *len = 1; return p[0]; }
    {
        int i;
        for (i = 1; i < *len; i++) {
            if ((p[i] & 0xC0) != 0x80) { *len = 1; return p[0]; }
            cp = (cp << 6) | (p[i] & 0x3F);
        }
    }
    return cp;
}

static void rt_uc_encode(unsigned int cp, char *out, int *n) {
    if (cp < 0x80) { out[0] = (char)cp; *n = 1; }
    else if (cp < 0x800) {
        out[0] = (char)(0xC0 | (cp >> 6));
        out[1] = (char)(0x80 | (cp & 0x3F)); *n = 2;
    } else if (cp < 0x10000) {
        out[0] = (char)(0xE0 | (cp >> 12));
        out[1] = (char)(0x80 | ((cp >> 6) & 0x3F));
        out[2] = (char)(0x80 | (cp & 0x3F)); *n = 3;
    } else {
        out[0] = (char)(0xF0 | (cp >> 18));
        out[1] = (char)(0x80 | ((cp >> 12) & 0x3F));
        out[2] = (char)(0x80 | ((cp >> 6) & 0x3F));
        out[3] = (char)(0x80 | (cp & 0x3F)); *n = 4;
    }
}

static Val rt_uc_map(Val v, int to_upper) {
    const char *s = rt_str_of(v);
    size_t blen = strlen(s);
    char *buf = (char *)malloc(blen * 2 + 1);
    size_t o = 0;
    const unsigned char *p = (const unsigned char *)s;
    if (!buf) rt_abort("内存不足");
    while (*p) {
        int clen = 1, elen = 1;
        unsigned int cp = rt_uc_decode(p, &clen);
        unsigned int out;
        char enc[4];
        if (to_upper) out = rt_uc_lookup(rt_uc_upper_from, rt_uc_upper_to, RT_UC_N, cp);
        else out = rt_uc_lookup(rt_uc_lower_from, rt_uc_lower_to, RT_UC_LN, cp);
        rt_uc_encode(out, enc, &elen);
        memcpy(buf + o, enc, (size_t)elen);
        o += (size_t)elen;
        p += clen;
    }
    buf[o] = '\0';
    {
        Val r = v_str(buf);
        free(buf);
        return r;
    }
}

/* 4.5：ord / chr —— 与解释器同口径（按 Unicode 码点，不是字节偏移）。
   依赖上面的 rt_uc_decode / rt_uc_encode，因此定义在本文件末尾。 */
static Val rt_ord(Val v) {
    const unsigned char *p = (const unsigned char *)rt_str_of(v);
    int len = 1;
    if (!p[0]) rt_abort("ord 的参数不能是空串");
    return v_int((long long)rt_uc_decode(p, &len));
}

static Val rt_chr(Val v) {
    long long cp = rt_int(v);
    char enc[4];
    char buf[8];
    int n = 1;
    if (cp < 0 || cp > 0x10FFFF) rt_abort("chr 的码点超出范围（0..1114111）");
    rt_uc_encode((unsigned int)cp, enc, &n);
    memcpy(buf, enc, (size_t)n);
    buf[n] = 0;
    return v_str(buf);
}
