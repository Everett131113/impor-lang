/* Impor 绑定：zlib（v4.3.1）
 *
 * 与 sqlite3 绑定同样的套路：Impor 的 ffi_call 只认 int/cstr，所以这里把
 * zlib 的「输入输出缓冲区」压成十六进制字符串，Impor 侧只看到 cstr。
 *
 * 编译（64 位）：
 *   gcc -shared -O2 -o impor_bind_zlib.dll impor_bind_zlib.c
 *
 * 导出：
 *   int  impor_zlib_load(cstr dllpath)     加载 zlib1.dll，1/0
 *   cstr impor_zlib_version()              版本号
 *   int  impor_zlib_crc32(cstr text)       CRC32（按 UTF-8 字节）
 *   cstr impor_zlib_compress(cstr text)    zlib 压缩后的十六进制串
 *   cstr impor_zlib_uncompress(cstr hex)   解压回原文（失败返回空串）
 */
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef const char *(*fn_version)(void);
typedef unsigned long (*fn_crc32)(unsigned long, const unsigned char *, unsigned int);
typedef int (*fn_compress2)(unsigned char *, unsigned long *, const unsigned char *, unsigned long, int);
typedef int (*fn_uncompress)(unsigned char *, unsigned long *, const unsigned char *, unsigned long);

static HMODULE g_mod = NULL;
static fn_version p_version = NULL;
static fn_crc32 p_crc32 = NULL;
static fn_compress2 p_compress2 = NULL;
static fn_uncompress p_uncompress = NULL;
static char *g_hex = NULL;
static size_t g_hex_cap = 0;
static char *g_txt = NULL;
static size_t g_txt_cap = 0;

static void to_wide(const char *src, wchar_t *dst, int cap)
{
    dst[0] = L'\0';
    if (!src) return;
    MultiByteToWideChar(CP_UTF8, 0, src, -1, dst, cap);
    dst[cap - 1] = L'\0';
}

static int ensure_loaded(const char *dllpath)
{
    wchar_t w[MAX_PATH * 2];
    if (g_mod) return 1;
    if (!dllpath || !dllpath[0]) return 0;
    to_wide(dllpath, w, MAX_PATH * 2);
    g_mod = LoadLibraryW(w);
    if (!g_mod) {
        WCHAR full[MAX_PATH * 2];
        if (GetFullPathNameW(w, MAX_PATH * 2, full, NULL) > 0) g_mod = LoadLibraryW(full);
    }
    if (!g_mod) return 0;
    p_version = (fn_version)GetProcAddress(g_mod, "zlibVersion");
    p_crc32 = (fn_crc32)GetProcAddress(g_mod, "crc32");
    p_compress2 = (fn_compress2)GetProcAddress(g_mod, "compress2");
    p_uncompress = (fn_uncompress)GetProcAddress(g_mod, "uncompress");
    if (!p_version || !p_crc32 || !p_compress2 || !p_uncompress) {
        FreeLibrary(g_mod);
        g_mod = NULL;
        return 0;
    }
    return 1;
}

static void hex_encode(const unsigned char *buf, unsigned long n)
{
    unsigned long i;
    if (g_hex_cap < (size_t)n * 2 + 2) {
        g_hex_cap = (size_t)n * 2 + 2;
        g_hex = (char *)realloc(g_hex, g_hex_cap);
        if (!g_hex) exit(2);
    }
    for (i = 0; i < n; i++) {
        sprintf(g_hex + i * 2, "%02x", buf[i]);
    }
    g_hex[n * 2] = '\0';
}

static int hex_decode(const char *hex, unsigned char **out, unsigned long *out_n)
{
    unsigned long n, i;
    if (!hex) return 0;
    n = (unsigned long)strlen(hex);
    if (n % 2) return 0;
    *out_n = n / 2;
    *out = (unsigned char *)malloc(*out_n + 1);
    if (!*out) return 0;
    for (i = 0; i < *out_n; i++) {
        unsigned int v = 0;
        if (sscanf(hex + i * 2, "%2x", &v) != 1) {
            free(*out);
            *out = NULL;
            return 0;
        }
        (*out)[i] = (unsigned char)v;
    }
    return 1;
}

__declspec(dllexport) int impor_zlib_load(const char *dllpath)
{
    return ensure_loaded(dllpath) ? 1 : 0;
}

__declspec(dllexport) const char *impor_zlib_version(void)
{
    if (!g_mod) return "";
    return p_version();
}

__declspec(dllexport) int impor_zlib_crc32(const char *text)
{
    if (!g_mod || !text) return -1;
    return (int)p_crc32(0L, (const unsigned char *)text, (unsigned int)strlen(text));
}

__declspec(dllexport) const char *impor_zlib_compress(const char *text)
{
    unsigned long n, cap;
    unsigned char *buf;
    int rc;
    if (!g_mod || !text) return "";
    n = (unsigned long)strlen(text);
    cap = n * 2 + 64;
    buf = (unsigned char *)malloc(cap);
    if (!buf) return "";
    rc = p_compress2(buf, &cap, (const unsigned char *)text, n, 6);
    if (rc != 0) {
        free(buf);
        return "";
    }
    hex_encode(buf, cap);
    free(buf);
    return g_hex;
}

__declspec(dllexport) const char *impor_zlib_uncompress(const char *hex)
{
    unsigned char *buf = NULL;
    unsigned long n = 0, cap;
    int rc;
    if (!g_mod || !hex) return "";
    if (!hex_decode(hex, &buf, &n)) return "";
    cap = n * 8 + 1024;
    if (g_txt_cap < cap) {
        g_txt_cap = cap;
        g_txt = (char *)realloc(g_txt, g_txt_cap);
        if (!g_txt) exit(2);
    }
    rc = p_uncompress((unsigned char *)g_txt, &cap, buf, n);
    free(buf);
    if (rc != 0) return "";
    g_txt[cap] = '\0';
    return g_txt;
}
