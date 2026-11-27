/* impor-fast.c —— Impor 原生启动器（Windows）
 *
 * 目的：`impor <脚本>` 的“热执行”不再经过 Python。AST 解释器跑在 CPython 里，
 * 仅 Python 启动+导入 impor 就有 150–200 ms 的地板，靠解释器内部优化永远追不上 C；
 * 能追上 C 的只有“让 run 直接执行已编译的原生产物”这条路。
 *
 * 行为（patch6 + patch7）：
 *   1. 只接管 `impor <script.impor> [参数…]` 与 `impor run <script.impor> [参数…]`；
 *      其余命令（build / lint / pkg / repl / 带任何 - 开头的参数）一律原样转给 Impor.py。
 *   2. 缓存键 = 脚本内容 + 脚本 mtime/size + impor/cfast.py 等工具链指纹 + 版本串。
 *      改脚本或改转译器都会换键，不会命中旧产物。
 *   3. 命中 **DLL**（patch7）：本进程内 LoadLibrary + 调用 impor_entry，折合约 3 ms，
 *      不再创建第二个进程——这是 P1，把 38–40 ms 压到 20–24 ms 的那一步。
 *   4. 没有 DLL 但有 exe（patch6 路径）：_P_OVERLAY 直接把进程换成缓存里的 exe。
 *   5. 都没有：先调 `python … Impor.py build --dll <脚本> -o <缓存.dll> --quiet --no-cache`
 *      编 DLL 并调用；编不动（脚本不在可静态编译子集内）则落一个 .nodll 标记，
 *      今后直接走 exe / 解释器，不再每次白付一次编译尝试。
 *      编译失败最终回落 `python … Impor.py run <脚本>`，语义与原来完全一致。
 *
 * 构建：gcc -O2 -std=gnu99 -s tools/impor-fast.c -o impor-fast.exe
 */
#include <windows.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>
#include <process.h>
#include <errno.h>

#define FNV_OFFSET 1469598103934665603ULL
#define FNV_PRIME  1099511628211ULL
#define VER_STR    L"4.0.0p8"

static wchar_t g_root[4096];
static wchar_t g_cache_dir[4096];
static wchar_t g_exe[4096];
static wchar_t g_exe_full[4096];
static wchar_t g_dll[4096];
static wchar_t g_nodll[4096];
static wchar_t g_nofull[4096];
static wchar_t g_cmd[32768];

static uint64_t fnv_bytes(uint64_t h, const unsigned char *p, size_t n)
{
    for (size_t i = 0; i < n; i++) {
        h ^= (uint64_t)p[i];
        h *= FNV_PRIME;
    }
    return h;
}

static int hash_file(const wchar_t *path, uint64_t *h)
{
    HANDLE f = CreateFileW(path, GENERIC_READ,
                           FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                           NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (f == INVALID_HANDLE_VALUE)
        return -1;
    unsigned char buf[65536];
    DWORD n = 0;
    while (ReadFile(f, buf, (DWORD)sizeof buf, &n, NULL) && n > 0)
        *h = fnv_bytes(*h, buf, (size_t)n);
    CloseHandle(f);
    return 0;
}

static void hash_stat(const wchar_t *path, uint64_t *h)
{
    WIN32_FILE_ATTRIBUTE_DATA d;
    if (!GetFileAttributesExW(path, GetFileExInfoStandard, &d)) {
        *h = fnv_bytes(*h, (const unsigned char *)"missing", 7);
        return;
    }
    uint64_t t = ((uint64_t)d.ftLastWriteTime.dwHighDateTime << 32) | d.ftLastWriteTime.dwLowDateTime;
    uint64_t s = (uint64_t)d.nFileSizeLow;
    *h = fnv_bytes(*h, (const unsigned char *)&t, 8);
    *h = fnv_bytes(*h, (const unsigned char *)&s, 8);
}

static void join_path(wchar_t *out, size_t cap, const wchar_t *a, const wchar_t *b)
{
    if (a[0] && b[0] && (a[wcslen(a) - 1] == L'\\' || a[wcslen(a) - 1] == L'/'))
        _snwprintf(out, cap, L"%s%s", a, b);
    else
        _snwprintf(out, cap, L"%s\\%s", a, b);
}

static void append_quoted(const wchar_t *arg)
{
    size_t cur = wcslen(g_cmd);
    if (cur + wcslen(arg) + 4 >= sizeof g_cmd / sizeof g_cmd[0])
        return;
    g_cmd[cur++] = L'"';
    g_cmd[cur] = 0;
    wcscat(g_cmd, arg);
    cur = wcslen(g_cmd);
    g_cmd[cur++] = L'"';
    g_cmd[cur] = 0;
}

static int run_child(const wchar_t *cwd, DWORD *out_code, int quiet)
{
    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    ZeroMemory(&si, sizeof si);
    si.cb = sizeof si;
    ZeroMemory(&pi, sizeof pi);
    HANDLE nul = INVALID_HANDLE_VALUE;
    if (quiet) {
        /* 探测性编译（build --dll 试一次 / 老 exe 试一次）：失败是正常情况，
         * 诊断信息不该漏到用户眼前，stderr 直接丢进 NUL。 */
        SECURITY_ATTRIBUTES sa;
        ZeroMemory(&sa, sizeof sa);
        sa.nLength = sizeof sa;
        sa.bInheritHandle = TRUE;
        nul = CreateFileW(L"NUL", GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE,
                          &sa, OPEN_EXISTING, 0, NULL);
        si.dwFlags |= STARTF_USESTDHANDLES;
        si.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
        si.hStdOutput = GetStdHandle(STD_OUTPUT_HANDLE);
        si.hStdError = (nul != INVALID_HANDLE_VALUE) ? nul : GetStdHandle(STD_ERROR_HANDLE);
    }
    BOOL ok = CreateProcessW(NULL, g_cmd, NULL, NULL, TRUE, 0, NULL,
                             cwd && cwd[0] ? cwd : NULL, &si, &pi);
    if (nul != INVALID_HANDLE_VALUE)
        CloseHandle(nul);
    if (!ok) {
        fwprintf(stderr, L"[impor-fast] 启动失败（错误码 %lu）\n", GetLastError());
        return -1;
    }
    WaitForSingleObject(pi.hProcess, INFINITE);
    DWORD code = 0;
    GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    *out_code = code;
    return 0;
}

static int find_python(wchar_t *out, size_t cap, int *is_launcher)
{
    const wchar_t *env = _wgetenv(L"IMPOR_PYTHON");
    if (env && env[0]) {
        wcsncpy(out, env, cap - 1);
        out[cap - 1] = 0;
        *is_launcher = 0;
        return 0;
    }
    wchar_t buf[4096];
    if (SearchPathW(NULL, L"python.exe", NULL, 4096, buf, NULL)) {
        wcsncpy(out, buf, cap - 1);
        out[cap - 1] = 0;
        *is_launcher = 0;
        return 0;
    }
    if (SearchPathW(NULL, L"py.exe", NULL, 4096, buf, NULL)) {
        wcsncpy(out, buf, cap - 1);
        out[cap - 1] = 0;
        *is_launcher = 1;
        return 0;
    }
    return -1;
}

static int exec_cached(const wchar_t *exe, wchar_t **argv, int start, int argc)
{
    wchar_t **av = (wchar_t **)calloc((size_t)(argc - start) + 2, sizeof(wchar_t *));
    if (!av)
        return ENOMEM;
    av[0] = (wchar_t *)exe;
    for (int i = start + 1; i < argc; i++)
        av[i - start] = argv[i];
    av[argc - start] = NULL;
    _wspawnv(_P_OVERLAY, exe, (const wchar_t *const *)av);
    int e = errno;
    free(av);
    return e;
}

static int split_args(wchar_t *s, wchar_t **out, int cap)
{
    /* 极简命令行切分（支持双引号包裹、空格分隔）；不引入 shell32，
     * 因为加载 shell32 本身就要 10 ms 量级——启动器不能在这里付账。 */
    int n = 0;
    wchar_t *p = s;
    while (*p && n < cap - 1) {
        while (*p == L' ' || *p == L'\t')
            p++;
        if (!*p)
            break;
        int quoted = (*p == L'"');
        if (quoted)
            p++;
        out[n++] = p;
        if (quoted) {
            while (*p && *p != L'"')
                p++;
            if (*p == L'"')
                *p++ = 0;
        } else {
            while (*p && *p != L' ' && *p != L'\t')
                p++;
            if (*p)
                *p++ = 0;
        }
    }
    out[n] = NULL;
    return n;
}

static int endswith(const wchar_t *s, const wchar_t *tail)
{
    size_t a = wcslen(s), b = wcslen(tail);
    return a >= b && _wcsicmp(s + a - b, tail) == 0;
}

static int file_exists(const wchar_t *p)
{
    return GetFileAttributesW(p) != INVALID_FILE_ATTRIBUTES;
}

/* patch7：在本进程里加载编译产物并调用 impor_entry。
 * 返回值：0 表示真的执行了（退出码写进 *out_code）；-1 表示这个 DLL 不能用，调用方继续回落。 */
static int run_dll(const wchar_t *dll, DWORD *out_code)
{
    typedef int (*PFN)(const char *);   /* patch8：入口多收 out_path，NULL=写控制台 */
    HMODULE h = LoadLibraryW(dll);
    if (!h)
        return -1;
    PFN fn = (PFN)GetProcAddress(h, "impor_entry");
    if (!fn) {
        FreeLibrary(h);
        return -1;
    }
    int rc = fn(NULL);
    /* 进程马上退出，不必 FreeLibrary：省一次卸载（含 CRT atexit 与页回收） */
    *out_code = (DWORD)rc;
    return 0;
}

static void touch_mark(const wchar_t *p)
{
    HANDLE f = CreateFileW(p, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS,
                           FILE_ATTRIBUTE_NORMAL, NULL);
    if (f != INVALID_HANDLE_VALUE)
        CloseHandle(f);
}

int main(void)
{
    static wchar_t cl[32768];
    static wchar_t *argv[256];
    int argc = 0;
    {   /* 用 GetCommandLineW 自行切分，避免加载 shell32 */
        const wchar_t *src = GetCommandLineW();
        size_t len = wcslen(src);
        if (len >= sizeof cl / sizeof cl[0])
            len = sizeof cl / sizeof cl[0] - 1;
        memcpy(cl, src, len * sizeof(wchar_t));
        cl[len] = 0;
        argc = split_args(cl, argv, 256);
    }

    if (!GetModuleFileNameW(NULL, g_root, 4096)) {
        return 1;
    }
    wchar_t *slash = wcsrchr(g_root, L'\\');
    if (!slash) {
        return 1;
    }
    *slash = 0; /* g_root = 安装目录（Impor.py 所在） */
    const wchar_t *caller_cwd = _wgetenv(L"IMPOR_CALLER_CWD");

    /* ---- 判定是否接管 ---- */
    int start = 1;
    if (argc >= 2 && _wcsicmp(argv[1], L"run") == 0)
        start = 2;
    int takeover = 0;
    if (start < argc) {
        const wchar_t *script = argv[start];
        if (script[0] != L'-' && endswith(script, L".impor"))
            takeover = 1;
    }

    wchar_t py[4096];
    int py_launcher = 0;
    int have_py = (find_python(py, 4096, &py_launcher) == 0);

    /* 不接管：原样转给 Impor.py */
    if (!takeover) {
        if (!have_py) {
            fwprintf(stderr, L"[impor-fast] 找不到 Python：请设置 IMPOR_PYTHON 或把 python 加进 PATH\n");
                return 1;
        }
        swprintf(g_cmd, sizeof g_cmd / sizeof g_cmd[0], L"\"%s\"", py);
        if (py_launcher)
            wcscat(g_cmd, L" -3");
        wcscat(g_cmd, L" -S -I -X utf8 ");
        wchar_t pyfile[4096];
        join_path(pyfile, 4096, g_root, L"Impor.py");
        append_quoted(pyfile);
        for (int i = 1; i < argc; i++) {
            wcscat(g_cmd, L" ");
            append_quoted(argv[i]);
        }
        DWORD code = 1;
        if (run_child(caller_cwd && caller_cwd[0] ? caller_cwd : g_root, &code, 0) != 0)
            code = 1;
        return (int)code;
    }

    const wchar_t *script = argv[start];
    wchar_t script_abs[4096];
    {   /* 相对路径按“调用者当前目录”解析：impor.bat 会先 cd 到安装目录 */
        const wchar_t *caller = _wgetenv(L"IMPOR_CALLER_CWD");
        wchar_t joined[4096];
        const wchar_t *raw = script;
        if (caller && caller[0] && script[0] != L'\\' && script[0] != L'/' &&
            script[1] != L':') {
            join_path(joined, 4096, caller, script);
            raw = joined;
        }
        if (GetFullPathNameW(raw, 4096, script_abs, NULL) == 0) {
            return 1;
        }
    }
    wchar_t workdir[4096];
    wcsncpy(workdir, script_abs, 4096);
    workdir[4095] = 0;
    wchar_t *ls = wcsrchr(workdir, L'\\');
    if (ls)
        *ls = 0;

    /* ---- 缓存目录 ---- */
    const wchar_t *home = _wgetenv(L"IMPOR_HOME");
    if (home && home[0]) {
        wcsncpy(g_cache_dir, home, 4096 - 32);
        g_cache_dir[4064] = 0;
    } else {
        const wchar_t *up = _wgetenv(L"USERPROFILE");
        if (!up || !up[0]) {
                return 1;
        }
        join_path(g_cache_dir, 4096, up, L".impor");
    }
    CreateDirectoryW(g_cache_dir, NULL);
    wcscat(g_cache_dir, L"\\fast-cache");
    CreateDirectoryW(g_cache_dir, NULL);

    /* ---- 缓存键 ---- */
    uint64_t h = FNV_OFFSET;
    if (hash_file(script_abs, &h) != 0) {
        /* 读不到脚本：交给解释器去报可读的错误 */
        takeover = 0;
        have_py = have_py;
    }
    hash_stat(script_abs, &h);
    const wchar_t *stems[] = { L"impor\\cfast.py", L"impor\\ctrans.py",
                               L"impor\\c_runtime.c", L"impor\\cli.py" };
    for (int i = 0; i < 4; i++) {
        wchar_t p[4096];
        join_path(p, 4096, g_root, stems[i]);
        hash_stat(p, &h);
    }
    h = fnv_bytes(h, (const unsigned char *)VER_STR, wcslen(VER_STR) * sizeof(wchar_t));

    wchar_t key_dll[40], key_exe[40], key_mark[40], key_efull[40], key_nofull[40];
    swprintf(key_dll, 40, L"%016llx.dll", (unsigned long long)h);
    swprintf(key_exe, 40, L"%016llx.exe", (unsigned long long)h);
    swprintf(key_mark, 40, L"%016llx.nodll", (unsigned long long)h);
    swprintf(key_efull, 40, L"%016llxF.exe", (unsigned long long)h);
    swprintf(key_nofull, 40, L"%016llx.nofull", (unsigned long long)h);
    join_path(g_dll, 4096, g_cache_dir, key_dll);
    join_path(g_exe, 4096, g_cache_dir, key_exe);
    join_path(g_exe_full, 4096, g_cache_dir, key_efull);
    join_path(g_nodll, 4096, g_cache_dir, key_mark);
    join_path(g_nofull, 4096, g_cache_dir, key_nofull);

    DWORD code = 0;
    int ran = 0;

    /* ① DLL 热执行（patch7）：本进程内 LoadLibrary + 调 impor_entry，只付一次进程创建 */
    if (file_exists(g_dll)) {
        SetCurrentDirectoryW(workdir);
        if (run_dll(g_dll, &code) == 0)
            return (int)code;
    }

    /* ② 老缓存（patch6 的 exe）：直接把进程换成产物 */
    if (file_exists(g_exe)) {
        SetCurrentDirectoryW(workdir);
        exec_cached(g_exe, argv, start, argc);   /* _P_OVERLAY：成功则不返回 */
    }

    if (!ran) {
        if (!have_py) {
            fwprintf(stderr, L"[impor-fast] 未命中缓存且找不到 Python：请设置 IMPOR_PYTHON 或把 python 加进 PATH\n");
                return 1;
        }
        wchar_t pyfile[4096];
        join_path(pyfile, 4096, g_root, L"Impor.py");

        /* ③ 未命中：编 DLL（静默）。编不动就落 .nodll 标记，免得以后每次都白试一次 */
        if (!file_exists(g_nodll)) {
            swprintf(g_cmd, sizeof g_cmd / sizeof g_cmd[0], L"\"%s\"", py);
            if (py_launcher)
                wcscat(g_cmd, L" -3");
            wcscat(g_cmd, L" -S -I -X utf8 ");
            append_quoted(pyfile);
            wcscat(g_cmd, L" build --dll ");
            append_quoted(script_abs);
            wcscat(g_cmd, L" -o ");
            append_quoted(g_dll);
            wcscat(g_cmd, L" --quiet --no-cache");
            DWORD bcode = 1;
            if (run_child(workdir, &bcode, 1) == 0 && bcode == 0 && file_exists(g_dll)) {
                SetCurrentDirectoryW(workdir);
                if (run_dll(g_dll, &code) == 0)
                    return (int)code;
            }
            touch_mark(g_nodll);
        }

        /* ④ 退一步：patch6 的 exe 路径（快路径产物） */
        if (!file_exists(g_exe)) {
            swprintf(g_cmd, sizeof g_cmd / sizeof g_cmd[0], L"\"%s\"", py);
            if (py_launcher)
                wcscat(g_cmd, L" -3");
            wcscat(g_cmd, L" -S -I -X utf8 ");
            append_quoted(pyfile);
            wcscat(g_cmd, L" build ");
            append_quoted(script_abs);
            wcscat(g_cmd, L" -o ");
            append_quoted(g_exe);
            wcscat(g_cmd, L" --quiet --no-cache");
            DWORD ecode = 1;
            if (run_child(workdir, &ecode, 1) == 0 && ecode == 0 && file_exists(g_exe)) {
                SetCurrentDirectoryW(workdir);
                exec_cached(g_exe, argv, start, argc);   /* _P_OVERLAY：成功则不返回 */
            }
        }

        /* ④b patch8（P3）：完整 C 后端产物。字符串/列表/函数/异常这些快路径不认、
         * 以前一律回落解释器（同规模脚本 2132–8646 ms）的脚本，在这里编成原生 C 产物，
         * 之后每次执行只付一次进程创建（约 16–20 ms）。编不动就落 .nofull 标记。 */
        if (!file_exists(g_exe_full) && !file_exists(g_nofull)) {
            swprintf(g_cmd, sizeof g_cmd / sizeof g_cmd[0], L"\"%s\"", py);
            if (py_launcher)
                wcscat(g_cmd, L" -3");
            wcscat(g_cmd, L" -S -I -X utf8 ");
            append_quoted(pyfile);
            wcscat(g_cmd, L" build --no-fast ");
            append_quoted(script_abs);
            wcscat(g_cmd, L" -o ");
            append_quoted(g_exe_full);
            wcscat(g_cmd, L" --quiet --no-cache");
            DWORD fcode = 1;
            if (run_child(workdir, &fcode, 1) == 0 && fcode == 0 && file_exists(g_exe_full)) {
                SetCurrentDirectoryW(workdir);
                exec_cached(g_exe_full, argv, start, argc);
            }
            touch_mark(g_nofull);
        }

        /* ④c 已有完整后端产物：直接换进程 */
        if (file_exists(g_exe_full)) {
            SetCurrentDirectoryW(workdir);
            exec_cached(g_exe_full, argv, start, argc);
        }

        /* ⑤ 最后回落解释器：语义与原来完全一致 */
        {
            swprintf(g_cmd, sizeof g_cmd / sizeof g_cmd[0], L"\"%s\"", py);
            if (py_launcher)
                wcscat(g_cmd, L" -3");
            wcscat(g_cmd, L" -S -I -X utf8 ");
            append_quoted(pyfile);
            wcscat(g_cmd, L" run ");
            append_quoted(script_abs);
            for (int i = start + 1; i < argc; i++) {
                wcscat(g_cmd, L" ");
                append_quoted(argv[i]);
            }
            if (run_child(workdir, &code, 0) != 0)
                code = 1;
        }
    }

    if (_wgetenv(L"IMPOR_FAST_DEBUG"))
        fwprintf(stderr, L"[impor-fast] 调试：未命中缓存路径结束\n");
    return (int)code;
}
