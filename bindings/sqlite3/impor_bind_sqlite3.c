/* Impor 绑定：SQLite3（v4.3.1）
 *
 * 为什么要有这一层：
 *   Impor 的 ffi_call 只做「函数调用」，参数只支持 int/bool/float/cstr，返回只支持
 *   int/float/bool/cstr/void，且不接受指针。sqlite3 的原生 API 是
 *   sqlite3_open(const char*, sqlite3**) / sqlite3_prepare_v2(..., sqlite3_stmt**)
 *   这类「出参指针 + 结构体句柄」，直接 ffi 调不了。
 *   所以这里用一个薄 C 层把句柄压成整数、把结果集编成 JSON 字符串返回，
 *   Impor 侧只看到 int / cstr 两种类型。
 *
 * 依赖：
 *   本文件不链接 sqlite3.lib，而是在运行时 LoadLibrary 指定路径的 sqlite3.dll，
 *   再用 GetProcAddress 取符号。这样可以配任意来源的 sqlite3.dll（Python 自带、
 *   sqlite.org 官方包、自己编译的），不需要 import lib。
 *
 * 编译（64 位，和 impor run 的解释器位宽一致）：
 *   gcc -shared -O2 -o impor_bind_sqlite3.dll impor_bind_sqlite3.c
 *
 * 导出函数：
 *   int  impor_sqlite_load(cstr dllpath)     加载 dll，返回 1/0
 *   cstr impor_sqlite_version()              版本号（未加载返回空串）
 *   int  impor_sqlite_open(cstr dbpath)      打开库，返回句柄 1..8，失败 0
 *   int  impor_sqlite_exec(int h, cstr sql)  执行无结果语句，返回 sqlite 错误码（0 = OK）
 *   cstr impor_sqlite_query(int h, cstr sql) 查询，返回 {"cols":[...],"rows":[[...]]}
 *   cstr impor_sqlite_error(int h)           最近一次错误信息
 *   int  impor_sqlite_close(int h)           关闭句柄，返回 1/0
 */
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct sqlite3 sqlite3;
typedef struct sqlite3_stmt sqlite3_stmt;

typedef int (*fn_open)(const char *, sqlite3 **);
typedef int (*fn_close)(sqlite3 *);
typedef int (*fn_exec)(sqlite3 *, const char *, void *, void *, char **);
typedef const char *(*fn_errmsg)(sqlite3 *);
typedef const char *(*fn_libversion)(void);
typedef int (*fn_prepare)(sqlite3 *, const char *, int, sqlite3_stmt **, const char **);
typedef int (*fn_step)(sqlite3_stmt *);
typedef int (*fn_finalize)(sqlite3_stmt *);
typedef int (*fn_colcount)(sqlite3_stmt *);
typedef const char *(*fn_colname)(sqlite3_stmt *, int);
typedef int (*fn_coltype)(sqlite3_stmt *, int);
typedef long long (*fn_colint64)(sqlite3_stmt *, int);
typedef double (*fn_coldouble)(sqlite3_stmt *, int);
typedef const unsigned char *(*fn_coltext)(sqlite3_stmt *, int);
typedef int (*fn_colbytes)(sqlite3_stmt *, int);

#define MAX_DBS 8
#define SQLITE_OK 0
#define SQLITE_ROW 100
#define SQLITE_DONE 101

static HMODULE g_mod = NULL;
static fn_open p_open = NULL;
static fn_close p_close = NULL;
static fn_exec p_exec = NULL;
static fn_errmsg p_errmsg = NULL;
static fn_libversion p_libversion = NULL;
static fn_prepare p_prepare = NULL;
static fn_step p_step = NULL;
static fn_finalize p_finalize = NULL;
static fn_colcount p_colcount = NULL;
static fn_colname p_colname = NULL;
static fn_coltype p_coltype = NULL;
static fn_colint64 p_colint64 = NULL;
static fn_coldouble p_coldouble = NULL;
static fn_coltext p_coltext = NULL;
static fn_colbytes p_colbytes = NULL;

static sqlite3 *g_dbs[MAX_DBS];
static int g_last = 0;
static char g_err[MAX_DBS][256];
static char *g_out = NULL;
static size_t g_out_cap = 0;

static void out_reset(void)
{
    if (!g_out) {
        g_out_cap = 4096;
        g_out = (char *)malloc(g_out_cap);
    }
    if (g_out) g_out[0] = '\0';
}

static void out_reserve(size_t need)
{
    size_t len = g_out ? strlen(g_out) : 0;
    if (len + need + 1 <= g_out_cap) return;
    while (g_out_cap < len + need + 1) g_out_cap *= 2;
    g_out = (char *)realloc(g_out, g_out_cap);
    if (!g_out) {
        /* 极端情况下退出，避免后续写空指针 */
        exit(2);
    }
}

static void out_str(const char *s)
{
    if (!s) s = "";
    out_reserve(strlen(s));
    strcat(g_out, s);
}

static void out_json_string(const char *s, int n)
{
    int i;
    out_reserve((size_t)n * 6 + 3);
    strcat(g_out, "\"");
    for (i = 0; i < n; i++) {
        unsigned char c = (unsigned char)s[i];
        char esc[8];
        switch (c) {
        case '"': strcat(g_out, "\\\""); break;
        case '\\': strcat(g_out, "\\\\"); break;
        case '\b': strcat(g_out, "\\b"); break;
        case '\f': strcat(g_out, "\\f"); break;
        case '\n': strcat(g_out, "\\n"); break;
        case '\r': strcat(g_out, "\\r"); break;
        case '\t': strcat(g_out, "\\t"); break;
        default:
            if (c < 0x20) {
                sprintf(esc, "\\u%04x", c);
                strcat(g_out, esc);
            } else {
                esc[0] = (char)c;
                esc[1] = '\0';
                strcat(g_out, esc);
            }
        }
    }
    strcat(g_out, "\"");
}

/* UTF-8（Impor 的 cstr 编码）→ UTF-16；路径里可能有中文，
 * LoadLibraryA 按 ANSI 解释会失败（实测 err=126），所以全程走 W 版 API。 */
static void to_wide(const char *src, wchar_t *dst, int cap)
{
    dst[0] = L'\0';
    if (!src) return;
    MultiByteToWideChar(CP_UTF8, 0, src, -1, dst, cap);
    dst[cap - 1] = L'\0';
}

static HMODULE load_lib(const char *path)
{
    wchar_t w[MAX_PATH * 2];
    to_wide(path, w, MAX_PATH * 2);
    return LoadLibraryW(w);
}

static void set_dir(const char *path)
{
    wchar_t w[MAX_PATH * 2];
    if (!path) {
        SetDllDirectoryW(NULL);
        return;
    }
    to_wide(path, w, MAX_PATH * 2);
    SetDllDirectoryW(w);
}

static int ensure_loaded(const char *dllpath)
{
    wchar_t wfull[MAX_PATH * 2];
    char full[MAX_PATH * 2];
    char dir[MAX_PATH * 2];
    char par[MAX_PATH * 2];
    char *slash;
    if (g_mod) return 1;
    if (!dllpath || !dllpath[0]) return 0;
    /* 先把相对路径按当前工作目录补全（Impor 的 ffi_open 已 abspath，这里兜个底）。 */
    to_wide(dllpath, wfull, MAX_PATH * 2);
    if (GetFullPathNameW(wfull, MAX_PATH * 2, wfull, NULL) > 0) {
        WideCharToMultiByte(CP_UTF8, 0, wfull, -1, full, sizeof(full), NULL, NULL);
        strncpy(dir, full, sizeof(dir) - 1);
        dir[sizeof(dir) - 1] = '\0';
        slash = strrchr(dir, '\\');
        if (slash) {
            *slash = '\0';
            /* 依赖库（vcruntime140.dll 等）可能在 DLL 自身目录，也可能在上级目录
             * （CPython 的 sqlite3.dll 在 DLLs\ 下，vcruntime 在 python 根目录）。 */
            set_dir(dir);
            g_mod = load_lib(full);
            if (!g_mod) {
                strncpy(par, dir, sizeof(par) - 1);
                par[sizeof(par) - 1] = '\0';
                slash = strrchr(par, '\\');
                if (slash) {
                    *slash = '\0';
                    set_dir(par);
                    g_mod = load_lib(full);
                }
            }
            set_dir(NULL);
        }
    }
    if (!g_mod) g_mod = load_lib(dllpath);
    if (!g_mod) return 0;
    p_open = (fn_open)GetProcAddress(g_mod, "sqlite3_open");
    p_close = (fn_close)GetProcAddress(g_mod, "sqlite3_close");
    p_exec = (fn_exec)GetProcAddress(g_mod, "sqlite3_exec");
    p_errmsg = (fn_errmsg)GetProcAddress(g_mod, "sqlite3_errmsg");
    p_libversion = (fn_libversion)GetProcAddress(g_mod, "sqlite3_libversion");
    p_prepare = (fn_prepare)GetProcAddress(g_mod, "sqlite3_prepare_v2");
    p_step = (fn_step)GetProcAddress(g_mod, "sqlite3_step");
    p_finalize = (fn_finalize)GetProcAddress(g_mod, "sqlite3_finalize");
    p_colcount = (fn_colcount)GetProcAddress(g_mod, "sqlite3_column_count");
    p_colname = (fn_colname)GetProcAddress(g_mod, "sqlite3_column_name");
    p_coltype = (fn_coltype)GetProcAddress(g_mod, "sqlite3_column_type");
    p_colint64 = (fn_colint64)GetProcAddress(g_mod, "sqlite3_column_int64");
    p_coldouble = (fn_coldouble)GetProcAddress(g_mod, "sqlite3_column_double");
    p_coltext = (fn_coltext)GetProcAddress(g_mod, "sqlite3_column_text");
    p_colbytes = (fn_colbytes)GetProcAddress(g_mod, "sqlite3_column_bytes");
    if (!p_open || !p_close || !p_exec || !p_errmsg || !p_libversion) {
        FreeLibrary(g_mod);
        g_mod = NULL;
        return 0;
    }
    return p_prepare && p_step && p_finalize && p_colcount && p_colname &&
           p_coltype && p_colint64 && p_coldouble && p_coltext && p_colbytes;
}

static int slot_valid(int h)
{
    return h >= 1 && h <= MAX_DBS && g_dbs[h - 1] != NULL;
}

__declspec(dllexport) int impor_sqlite_load(const char *dllpath)
{
    int ok;
    SetLastError(0);
    ok = ensure_loaded(dllpath);
    g_last = (int)GetLastError();
    return ok ? 1 : 0;
}

__declspec(dllexport) int impor_sqlite_last_error(void)
{
    return g_last;
}

__declspec(dllexport) const char *impor_sqlite_resolved(void)
{
    static char buf[MAX_PATH * 2];
    buf[0] = '\0';
    if (g_mod) GetModuleFileNameA(g_mod, buf, sizeof(buf));
    return buf;
}

__declspec(dllexport) const char *impor_sqlite_version(void)
{
    if (!g_mod || !p_libversion) return "";
    return p_libversion();
}

__declspec(dllexport) int impor_sqlite_open(const char *dbpath)
{
    int i;
    sqlite3 *db = NULL;
    if (!g_mod) return 0;
    if (!dbpath) return 0;
    for (i = 0; i < MAX_DBS; i++) {
        if (!g_dbs[i]) {
            if (p_open(dbpath, &db) != SQLITE_OK || !db) return 0;
            g_dbs[i] = db;
            g_err[i][0] = '\0';
            return i + 1;
        }
    }
    return 0;
}

__declspec(dllexport) int impor_sqlite_exec(int h, const char *sql)
{
    char *err = NULL;
    int rc;
    if (!slot_valid(h)) return -1;
    if (!sql) return -1;
    rc = p_exec(g_dbs[h - 1], sql, NULL, NULL, &err);
    if (rc != SQLITE_OK) {
        if (err) {
            strncpy(g_err[h - 1], err, sizeof(g_err[h - 1]) - 1);
            g_err[h - 1][sizeof(g_err[h - 1]) - 1] = '\0';
        } else {
            const char *m = p_errmsg(g_dbs[h - 1]);
            strncpy(g_err[h - 1], m ? m : "unknown", sizeof(g_err[h - 1]) - 1);
        }
    } else {
        g_err[h - 1][0] = '\0';
    }
    return rc;
}

__declspec(dllexport) const char *impor_sqlite_query(int h, const char *sql)
{
    sqlite3_stmt *stmt = NULL;
    int rc, i, first_row = 1;
    if (!slot_valid(h) || !sql) {
        out_reset();
        out_str("{\"cols\":[],\"rows\":[],\"error\":\"bad handle\"}");
        return g_out;
    }
    rc = p_prepare(g_dbs[h - 1], sql, -1, &stmt, NULL);
    if (rc != SQLITE_OK || !stmt) {
        const char *m = p_errmsg(g_dbs[h - 1]);
        strncpy(g_err[h - 1], m ? m : "prepare failed", sizeof(g_err[h - 1]) - 1);
        out_reset();
        out_str("{\"cols\":[],\"rows\":[],\"error\":");
        out_json_string(m ? m : "prepare failed", (int)strlen(m ? m : "prepare failed"));
        out_str("}");
        return g_out;
    }
    out_reset();
    out_str("{\"cols\":[");
    for (i = 0; i < p_colcount(stmt); i++) {
        const char *nm = p_colname(stmt, i);
        if (i) out_str(",");
        out_json_string(nm ? nm : "", nm ? (int)strlen(nm) : 0);
    }
    out_str("],\"rows\":[");
    while ((rc = p_step(stmt)) == SQLITE_ROW) {
        if (!first_row) out_str(",");
        first_row = 0;
        out_str("[");
        for (i = 0; i < p_colcount(stmt); i++) {
            char num[64];
            if (i) out_str(",");
            switch (p_coltype(stmt, i)) {
            case 1: /* SQLITE_INTEGER */
                sprintf(num, "%lld", p_colint64(stmt, i));
                out_str(num);
                break;
            case 2: /* SQLITE_FLOAT */
                sprintf(num, "%.17g", p_coldouble(stmt, i));
                out_str(num);
                break;
            case 5: /* SQLITE_NULL */
                out_str("null");
                break;
            default: {
                const unsigned char *t = p_coltext(stmt, i);
                int n = p_colbytes(stmt, i);
                out_json_string((const char *)(t ? t : (const unsigned char *)""), n);
            }
            }
        }
        out_str("]");
    }
    out_str("]}");
    if (rc != SQLITE_DONE) {
        const char *m = p_errmsg(g_dbs[h - 1]);
        strncpy(g_err[h - 1], m ? m : "step failed", sizeof(g_err[h - 1]) - 1);
    } else {
        g_err[h - 1][0] = '\0';
    }
    p_finalize(stmt);
    return g_out;
}

__declspec(dllexport) const char *impor_sqlite_error(int h)
{
    if (!slot_valid(h)) return "bad handle";
    return g_err[h - 1];
}

__declspec(dllexport) int impor_sqlite_close(int h)
{
    if (!slot_valid(h)) return 0;
    p_close(g_dbs[h - 1]);
    g_dbs[h - 1] = NULL;
    g_err[h - 1][0] = '\0';
    return 1;
}
