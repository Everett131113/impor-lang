/* Impor 绑定：HTTP(S)（v4.3.2）
 *
 * 为什么用 WinHTTP：
 *   std.http 原来靠 system("curl ...")，TLS 强度、证书校验、重定向策略全部交给
 *   外部 curl，机器上没 curl 就直接不能用。WinHTTP 是 Windows 自带组件，
 *   默认做证书链校验、支持 TLS 1.2/1.3、自动跟随重定向（有上限），
 *   也不依赖任何第三方 DLL。
 *
 * 编译：
 *   gcc -shared -O2 -o impor_bind_http.dll impor_bind_http.c -lwinhttp
 *
 * 导出：
 *   int  impor_http_get(cstr url, int timeout_ms)                  → 请求序号（>0）
 *   cstr impor_http_result(int id)                                 → {"status":..,"body":"..","error":".."}
 *   int  impor_http_request(cstr method, cstr url, cstr headers, cstr body, int timeout_ms)
 *   cstr impor_http_last_error()
 *
 * 说明：
 *   - 只接受 http:// / https://；https 强制 TLS 1.2 起，证书校验不可关闭。
 *   - 重定向最多 5 次；超时单位毫秒（0 表示默认 30s）。
 *   - 响应体按文本返回（UTF-8/原样字节），JSON 转义后交给 Impor 的 json_decode。
 */
#include <windows.h>
#include <winhttp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_RESULTS 8
#define MAX_BODY (16 * 1024 * 1024)

/* MinGW 4.8.1 的 winhttp.h 还没有这两个常量（SDK 里是 84 / 88 与 TLS1.2 位）。 */
#ifndef WINHTTP_OPTION_SECURE_PROTOCOLS
#define WINHTTP_OPTION_SECURE_PROTOCOLS 84
#endif
#ifndef WINHTTP_OPTION_MAX_REDIRECTS
#define WINHTTP_OPTION_MAX_REDIRECTS 88
#endif
#ifndef WINHTTP_FLAG_SECURE_PROTOCOL_TLS1_2
#define WINHTTP_FLAG_SECURE_PROTOCOL_TLS1_2 0x00000800
#endif

typedef struct {
    int used;
    int status;
    char *body;
    char err[256];
} HttpResult;

static HttpResult g_res[MAX_RESULTS];
static char g_last[512];

static void set_last(const char *msg)
{
    if (!msg) msg = "";
    strncpy(g_last, msg, sizeof(g_last) - 1);
    g_last[sizeof(g_last) - 1] = '\0';
}

const char *impor_http_last_error(void)
{
    return g_last;
}

static void to_wide(const char *src, wchar_t *dst, int cap)
{
    dst[0] = L'\0';
    if (!src) return;
    MultiByteToWideChar(CP_UTF8, 0, src, -1, dst, cap);
    dst[cap - 1] = L'\0';
}

static char *dup_utf8(const void *src, DWORD n)
{
    char *p = (char *)malloc((size_t)n + 1);
    if (!p) return NULL;
    if (n) memcpy(p, src, n);
    p[n] = '\0';
    return p;
}

static int alloc_result(void)
{
    int i;
    for (i = 0; i < MAX_RESULTS; i++) {
        if (!g_res[i].used) {
            g_res[i].used = 1;
            g_res[i].status = 0;
            g_res[i].body = NULL;
            g_res[i].err[0] = '\0';
            return i + 1;
        }
    }
    return 0;
}

static void free_result_body(int id)
{
    if (id < 1 || id > MAX_RESULTS) return;
    if (g_res[id - 1].body) {
        free(g_res[id - 1].body);
        g_res[id - 1].body = NULL;
    }
}

static int do_request(const char *method, const char *url, const char *headers,
                      const char *body, int timeout_ms, int *out_status, char **out_body,
                      char *errbuf, size_t errcap)
{
    wchar_t wurl[4096];
    wchar_t whost[512];
    wchar_t wpath[3072];
    wchar_t wmethod[32];
    wchar_t wheaders[4096];
    URL_COMPONENTS uc;
    HINTERNET ses = NULL, conn = NULL, req = NULL;
    int ok = 0;
    DWORD status = 0, status_len = sizeof(status);
    DWORD opt;
    char *acc = NULL;
    size_t acc_len = 0;

    if (!url || !url[0]) { snprintf(errbuf, errcap, "URL 为空"); return 0; }
    to_wide(url, wurl, 4096);
    memset(&uc, 0, sizeof(uc));
    uc.dwStructSize = sizeof(uc);
    uc.dwSchemeLength = (DWORD)-1;
    uc.dwHostNameLength = (DWORD)-1;
    uc.dwUrlPathLength = (DWORD)-1;
    uc.dwExtraInfoLength = (DWORD)-1;
    if (!WinHttpCrackUrl(wurl, 0, 0, &uc)) {
        snprintf(errbuf, errcap, "URL 解析失败（%lu）", (unsigned long)GetLastError());
        return 0;
    }
    if (uc.nScheme != INTERNET_SCHEME_HTTP && uc.nScheme != INTERNET_SCHEME_HTTPS) {
        snprintf(errbuf, errcap, "只接受 http:// 或 https://");
        return 0;
    }
    /* WinHttpCrackUrl 给的是「指向原串的切片」，不保证以 \0 结尾；
     * WinHttpConnect / OpenRequest 要的是独立 C 串，所以先复制出来。 */
    if (uc.dwHostNameLength <= 0 || uc.dwHostNameLength >= 512) {
        snprintf(errbuf, errcap, "主机名无效");
        return 0;
    }
    wcsncpy(whost, uc.lpszHostName, uc.dwHostNameLength);
    whost[uc.dwHostNameLength] = L'\0';
    if (uc.dwUrlPathLength <= 0) {
        wcscpy(wpath, L"/");
    } else {
        if (uc.dwUrlPathLength >= 3072) { snprintf(errbuf, errcap, "路径过长"); return 0; }
        wcsncpy(wpath, uc.lpszUrlPath, uc.dwUrlPathLength);
        wpath[uc.dwUrlPathLength] = L'\0';
        if (uc.dwExtraInfoLength > 0 &&
            uc.dwUrlPathLength + uc.dwExtraInfoLength < 3072) {
            wcsncat(wpath, uc.lpszExtraInfo, uc.dwExtraInfoLength);
        }
    }

    ses = WinHttpOpen(L"Impor/4.3.2", WINHTTP_ACCESS_TYPE_DEFAULT_PROXY,
                      WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0);
    if (!ses) { snprintf(errbuf, errcap, "WinHttpOpen 失败"); goto done; }
    if (timeout_ms <= 0) timeout_ms = 30000;
    WinHttpSetTimeouts(ses, timeout_ms, timeout_ms, timeout_ms, timeout_ms);

    /* 硬化：TLS 1.2 起；不忽略证书错误；重定向最多 5 次。 */
    opt = WINHTTP_FLAG_SECURE_PROTOCOL_TLS1_2;
    WinHttpSetOption(ses, WINHTTP_OPTION_SECURE_PROTOCOLS, &opt, sizeof(opt));
    opt = 5;
    WinHttpSetOption(ses, WINHTTP_OPTION_MAX_REDIRECTS, &opt, sizeof(opt));

    conn = WinHttpConnect(ses, whost, uc.nPort, 0);
    if (!conn) { snprintf(errbuf, errcap, "连接失败（%lu）", (unsigned long)GetLastError()); goto done; }

    to_wide(method && method[0] ? method : "GET", wmethod, 32);
    {
        DWORD flags = (uc.nScheme == INTERNET_SCHEME_HTTPS) ? WINHTTP_FLAG_SECURE : 0;
        req = WinHttpOpenRequest(conn, wmethod, wpath, NULL,
                                 WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES, flags);
    }
    if (!req) { snprintf(errbuf, errcap, "创建请求失败（%lu）", (unsigned long)GetLastError()); goto done; }

    if (headers && headers[0]) {
        to_wide(headers, wheaders, 4096);
        if (!WinHttpAddRequestHeaders(req, wheaders, (DWORD)-1L,
                                      WINHTTP_ADDREQ_FLAG_ADD | WINHTTP_ADDREQ_FLAG_REPLACE)) {
            snprintf(errbuf, errcap, "请求头无效");
            goto done;
        }
    }

    if (!WinHttpSendRequest(req, WINHTTP_NO_ADDITIONAL_HEADERS, 0,
                            (LPVOID)(body && body[0] ? body : NULL),
                            (DWORD)(body ? strlen(body) : 0), 0, 0)) {
        snprintf(errbuf, errcap, "发送失败（%lu）", (unsigned long)GetLastError());
        goto done;
    }
    if (!WinHttpReceiveResponse(req, NULL)) {
        snprintf(errbuf, errcap, "接收响应失败（%lu）", (unsigned long)GetLastError());
        goto done;
    }
    status = 0;
    status_len = sizeof(status);
    WinHttpQueryHeaders(req, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
                        WINHTTP_HEADER_NAME_BY_INDEX, &status, &status_len, WINHTTP_NO_HEADER_INDEX);

    for (;;) {
        DWORD avail = 0, got = 0;
        char *nb;
        if (!WinHttpQueryDataAvailable(req, &avail)) {
            snprintf(errbuf, errcap, "读取长度失败（%lu）", (unsigned long)GetLastError());
            goto done;
        }
        if (avail == 0) break;
        if (acc_len + avail > MAX_BODY) {
            snprintf(errbuf, errcap, "响应体超过 16 MB 上限");
            goto done;
        }
        nb = (char *)realloc(acc, acc_len + avail + 1);
        if (!nb) { snprintf(errbuf, errcap, "内存不足"); goto done; }
        acc = nb;
        if (!WinHttpReadData(req, acc + acc_len, avail, &got)) {
            snprintf(errbuf, errcap, "读取响应失败（%lu）", (unsigned long)GetLastError());
            goto done;
        }
        acc_len += got;
    }
    if (!acc) acc = dup_utf8("", 0);
    if (!acc) { snprintf(errbuf, errcap, "内存不足"); goto done; }
    acc[acc_len] = '\0';
    *out_status = (int)status;
    *out_body = acc;
    acc = NULL;
    ok = 1;

done:
    if (acc) free(acc);
    if (req) WinHttpCloseHandle(req);
    if (conn) WinHttpCloseHandle(conn);
    if (ses) WinHttpCloseHandle(ses);
    return ok;
}

int impor_http_request(const char *method, const char *url, const char *headers,
                       const char *body, int timeout_ms)
{
    int id = alloc_result();
    int status = 0;
    char *resp = NULL;
    char err[256];
    if (!id) { set_last("同时进行的请求过多（上限 8）"); return 0; }
    err[0] = '\0';
    free_result_body(id);
    if (!do_request(method, url, headers, body, timeout_ms, &status, &resp, err, sizeof(err))) {
        strncpy(g_res[id - 1].err, err, sizeof(g_res[id - 1].err) - 1);
        set_last(err);
        g_res[id - 1].status = 0;
        return id; /* 返回句柄，让调用方用 result() 拿结构化错误 */
    }
    g_res[id - 1].status = status;
    g_res[id - 1].body = resp;
    return id;
}

int impor_http_get(const char *url, int timeout_ms)
{
    return impor_http_request("GET", url, "", "", timeout_ms);
}

static void json_escape(const char *s, size_t n, char **out, size_t *cap, size_t *len)
{
    size_t i;
    for (i = 0; i < n; i++) {
        unsigned char c = (unsigned char)s[i];
        char esc[8];
        const char *rep = NULL;
        switch (c) {
        case '"': rep = "\\\""; break;
        case '\\': rep = "\\\\"; break;
        case '\b': rep = "\\b"; break;
        case '\f': rep = "\\f"; break;
        case '\n': rep = "\\n"; break;
        case '\r': rep = "\\r"; break;
        case '\t': rep = "\\t"; break;
        default: break;
        }
        if (!rep) {
            if (c < 0x20) { sprintf(esc, "\\u%04x", c); rep = esc; }
            else { esc[0] = (char)c; esc[1] = '\0'; rep = esc; }
        }
        if (*len + strlen(rep) + 1 > *cap) {
            *cap = (*cap) * 2 + strlen(rep) + 64;
            *out = (char *)realloc(*out, *cap);
            if (!*out) return;
        }
        strcpy(*out + *len, rep);
        *len += strlen(rep);
    }
}

/* 拆开取：响应体原样返回，避免把整段 HTML 塞进 JSON 再转义/反解，
 * 既省一次大字符串拷贝，也避开 JSON 转义与 UTF-8 边界问题。 */
int impor_http_status(int id)
{
    if (id < 1 || id > MAX_RESULTS) return 0;
    if (!g_res[id - 1].used) return 0;
    return g_res[id - 1].status;
}

const char *impor_http_body(int id)
{
    if (id < 1 || id > MAX_RESULTS) return "";
    if (!g_res[id - 1].used || !g_res[id - 1].body) return "";
    return g_res[id - 1].body;
}

const char *impor_http_error(int id)
{
    if (id < 1 || id > MAX_RESULTS) return "bad id";
    if (!g_res[id - 1].used) return "bad id";
    return g_res[id - 1].err;
}

const char *impor_http_result(int id)
{    static char *buf = NULL;
    static size_t cap = 0, len = 0;
    HttpResult *r;
    if (id < 1 || id > MAX_RESULTS) return "{\"status\":0,\"body\":\"\",\"error\":\"bad id\"}";
    r = &g_res[id - 1];
    if (!r->used) return "{\"status\":0,\"body\":\"\",\"error\":\"bad id\"}";

    len = 0;
    if (!buf) { cap = 4096; buf = (char *)malloc(cap); }
    if (!buf) return "{\"status\":0,\"body\":\"\",\"error\":\"oom\"}";
    buf[0] = '\0';

    {
        char head[128];
        sprintf(head, "{\"status\":%d,\"body\":\"", r->status);
        len = strlen(head);
        if (len + 1 > cap) { cap = len + 64; buf = (char *)realloc(buf, cap); }
        strcpy(buf, head);
    }
    if (r->body) json_escape(r->body, strlen(r->body), &buf, &cap, &len);
    {
        char tail[512];
        snprintf(tail, sizeof(tail), "\",\"error\":\"%s\"}", r->err);
        if (len + strlen(tail) + 1 > cap) { cap = len + strlen(tail) + 64; buf = (char *)realloc(buf, cap); }
        strcpy(buf + len, tail);
    }
    return buf;
}
