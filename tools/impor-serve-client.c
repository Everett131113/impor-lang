/* impor-serve-client.c —— `impor serve` 的客户端（P2）
 *
 * 为什么单独做一个 C 客户端：客户端唯一无法避免的成本就是“自己这次进程创建”
 * （本机 noop.exe 实测 18.6 ms）。所以它必须足够小、不能起 Python、不能加载
 * 解释器；真正的执行发生在常驻服务里（数值脚本走进程内 DLL，1–3 ms）。
 *
 * 用法：
 *   impor-serve-client.exe script.impor [参数...]
 *   impor-serve-client.exe -n 20 script.impor        # 同一连接跑 20 次，报每次耗时
 *   impor-serve-client.exe --port 12345 script.impor  # 不读 serve.json，直连指定端口
 *
 * 环境：IMPOR_HOME 覆盖 ~/.impor；IMPOR_SERVE_TIMING=1 打印每次服务端耗时。
 * 构建：gcc -O2 -std=gnu99 -s tools/impor-serve-client.c -o impor-serve-client.exe -lws2_32
 */
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAXBUF (1 << 20)

static int read_port_from_state(int *port)
{
    wchar_t path[4096];
    const wchar_t *home = _wgetenv(L"IMPOR_HOME");
    if (home && home[0])
        _snwprintf(path, 4096, L"%s\\serve.json", home);
    else {
        const wchar_t *up = _wgetenv(L"USERPROFILE");
        if (!up || !up[0])
            return -1;
        _snwprintf(path, 4096, L"%s\\.impor\\serve.json", up);
    }
    FILE *f = _wfopen(path, L"rb");
    if (!f)
        return -1;
    char buf[4096];
    size_t n = fread(buf, 1, sizeof buf - 1, f);
    fclose(f);
    buf[n] = 0;
    const char *p = strstr(buf, "\"port\"");
    if (!p)
        return -1;
    p = strchr(p, ':');
    if (!p)
        return -1;
    *port = atoi(p + 1);
    return (*port > 0 && *port < 65536) ? 0 : -1;
}

static void json_escape(const char *in, char *out, size_t cap)
{
    size_t j = 0;
    for (size_t i = 0; in[i] && j + 6 < cap; i++) {
        unsigned char c = (unsigned char)in[i];
        if (c == '"' || c == '\\') {
            out[j++] = '\\';
            out[j++] = (char)c;
        } else if (c == '\n') {
            out[j++] = '\\';
            out[j++] = 'n';
        } else if (c == '\r') {
            out[j++] = '\\';
            out[j++] = 'r';
        } else if (c == '\t') {
            out[j++] = '\\';
            out[j++] = 't';
        } else if (c < 0x20) {
            j += (size_t)snprintf(out + j, cap - j, "\\u%04x", c);
        } else {
            out[j++] = (char)c;   /* UTF-8 原样透传 */
        }
    }
    out[j] = 0;
}

static const char *json_int(const char *js, const char *key, long *out)
{
    const char *p = strstr(js, key);
    if (!p)
        return NULL;
    p = strchr(p, ':');
    if (!p)
        return NULL;
    *out = atol(p + 1);
    return p;
}

/* 把 "out":"…" 里的字符串解出来（只处理 \\ \" \n \r \t，其余原样） */
static int copy_out_field(const char *js, char *dst, size_t cap)
{
    const char *p = strstr(js, "\"out\"");
    if (!p)
        return 0;
    p = strchr(p, ':');
    if (!p)
        return 0;
    p = strchr(p, '"');
    if (!p)
        return 0;
    p++;
    size_t j = 0;
    while (*p && j + 1 < cap) {
        if (*p == '\\') {
            p++;
            switch (*p) {
            case 'n': dst[j++] = '\n'; break;
            case 'r': dst[j++] = '\r'; break;
            case 't': dst[j++] = '\t'; break;
            case '"': dst[j++] = '"'; break;
            case '\\': dst[j++] = '\\'; break;
            case 'u': {
                /* \uXXXX：只处理 ASCII 区间，其余按 UTF-8 还原（Impor 输出一般无需） */
                unsigned int cp = 0;
                for (int k = 1; k <= 4; k++) {
                    char c = p[k];
                    cp = cp * 16 + (unsigned int)((c >= '0' && c <= '9') ? c - '0'
                                    : (c >= 'a' && c <= 'f') ? c - 'a' + 10
                                    : (c >= 'A' && c <= 'F') ? c - 'A' + 10 : 0);
                }
                if (cp < 0x80) {
                    dst[j++] = (char)cp;
                } else if (cp < 0x800) {
                    dst[j++] = (char)(0xC0 | (cp >> 6));
                    dst[j++] = (char)(0x80 | (cp & 0x3F));
                } else {
                    dst[j++] = (char)(0xE0 | (cp >> 12));
                    dst[j++] = (char)(0x80 | ((cp >> 6) & 0x3F));
                    dst[j++] = (char)(0x80 | (cp & 0x3F));
                }
                p += 4;
                break;
            }
            default: dst[j++] = *p; break;
            }
            p++;
        } else if (*p == '"') {
            break;
        } else {
            dst[j++] = *p++;
        }
    }
    dst[j] = 0;
    return (int)j;
}

int main(void)
{
    static char req[MAXBUF];
    static char resp[MAXBUF];
    char *script = NULL;
    char **sargs = NULL;
    int nsargs = 0;
    int repeats = 1;
    int force_port = 0;

    int argc = 0;
    char *argv[256];
    {   /* 自己切命令行，避免依赖 CRT 的宽字符路径处理 */
        char *cmd = GetCommandLineA();
        char *p = cmd;
        while (*p && argc < 256) {
            while (*p == ' ' || *p == '\t')
                p++;
            if (!*p)
                break;
            int q = (*p == '"');
            if (q)
                p++;
            argv[argc++] = p;
            if (q) {
                while (*p && *p != '"')
                    p++;
                if (*p)
                    *p++ = 0;
            } else {
                while (*p && *p != ' ' && *p != '\t')
                    p++;
                if (*p)
                    *p++ = 0;
            }
        }
    }
    sargs = (char **)calloc(256, sizeof(char *));
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-n") == 0 && i + 1 < argc) {
            repeats = atoi(argv[++i]);
            if (repeats < 1)
                repeats = 1;
        } else if (strcmp(argv[i], "--port") == 0 && i + 1 < argc) {
            force_port = atoi(argv[++i]);
        } else if (!script) {
            script = argv[i];
        } else {
            sargs[nsargs++] = argv[i];
        }
    }
    if (!script) {
        fprintf(stderr, "用法：impor-serve-client.exe [-n N] [--port P] script.impor [参数...]\n");
        return 2;
    }

    char abs[MAX_PATH * 2];
    if (!GetFullPathNameA(script, MAX_PATH * 2, abs, NULL)) {
        fprintf(stderr, "路径解析失败：%s\n", script);
        return 2;
    }
    int port = force_port;
    if (!port && read_port_from_state(&port) != 0) {
        fprintf(stderr, "没有在跑的服务（~/.impor/serve.json 不存在）。先运行：impor serve start\n");
        return 3;
    }

    WSADATA wsa;
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) {
        fprintf(stderr, "WSAStartup 失败\n");
        return 3;
    }
    SOCKET s = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (s == INVALID_SOCKET) {
        fprintf(stderr, "socket 失败：%d\n", WSAGetLastError());
        return 3;
    }
    struct sockaddr_in addr;
    ZeroMemory(&addr, sizeof addr);
    addr.sin_family = AF_INET;
    addr.sin_port = htons((u_short)port);
    addr.sin_addr.s_addr = inet_addr("127.0.0.1");
    if (connect(s, (struct sockaddr *)&addr, sizeof addr) != 0) {
        fprintf(stderr, "连不上 127.0.0.1:%d（服务没起或端口已变）\n", port);
        closesocket(s);
        return 3;
    }

    /* 请求体只发一次；重复次数由客户端循环发送（这样测的是“每次请求”的成本） */
    static char esc[MAXBUF];
    json_escape(abs, esc, MAXBUF);
    size_t off = (size_t)snprintf(req, MAXBUF, "{\"script\":\"%s\",\"args\":[", esc);
    for (int i = 0; i < nsargs; i++) {
        char a2[4096];
        json_escape(sargs[i], a2, 4096);
        off += (size_t)snprintf(req + off, MAXBUF - off, "%s\"%s\"", i ? "," : "", a2);
    }
    snprintf(req + off, MAXBUF - off, "],\"opt\":2}\n");

    LARGE_INTEGER f, t0, t1, tot;
    QueryPerformanceFrequency(&f);
    tot.QuadPart = 0;
    int code = 0;
    int counted = 0;
    static char out[MAXBUF];
    long srv_ms = 0;
    const char *tier = "?";

    for (int it = 0; it < repeats; it++) {
        QueryPerformanceCounter(&t0);
        if (send(s, req, (int)strlen(req), 0) < 0) {
            fprintf(stderr, "发送失败：%d\n", WSAGetLastError());
            closesocket(s);
            return 3;
        }
        int n = 0;
        while (n < MAXBUF - 1) {
            int got = recv(s, resp + n, MAXBUF - 1 - n, 0);
            if (got <= 0)
                break;
            n += got;
            resp[n] = 0;
            if (resp[n - 1] == '\n')
                break;
        }
        QueryPerformanceCounter(&t1);
        if (!n) {
            fprintf(stderr, "服务没有响应（连接被关）\n");
            closesocket(s);
            return 3;
        }
        resp[n] = 0;
        long v = 0;
        code = json_int(resp, "\"code\"", &v) ? (int)v : 0;
        srv_ms = json_int(resp, "\"ms\"", &v) ? v : 0;
        tier = strstr(resp, "\"tier\":\"dll\"") ? "dll"
             : strstr(resp, "\"tier\":\"full\"") ? "full"
             : strstr(resp, "\"tier\":\"interp\"") ? "interp" : "?";
        if (it == 0 && repeats > 1) {
            /* 首个请求含 DLL 加载 / 产物首次预热，不计入稳态 */
        } else {
            tot.QuadPart += (t1.QuadPart - t0.QuadPart);
            counted++;
        }
    }
    copy_out_field(resp, out, MAXBUF);
    fwrite(out, 1, strlen(out), stdout);
    fflush(stdout);
    if (_wgetenv(L"IMPOR_SERVE_TIMING"))
        fprintf(stderr, "[serve] tier=%s 服务端 %ld ms\n", tier, srv_ms);
    if (counted > 0)
        fprintf(stderr, "[serve] 每次请求 %.2f ms（%d 次均值，客户端计时）\n",
                (double)tot.QuadPart * 1000.0 / (double)f.QuadPart / (double)counted,
                counted);
    closesocket(s);
    WSACleanup();
    return code;
}
