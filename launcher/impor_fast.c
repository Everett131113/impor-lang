// Impor (I++) 原生启动壳：先让 Windows 创建控制台窗口，再启动 Python。
// 控制台窗口由系统在进程创建时立即建立，不等待 Python 导入完成。
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <string.h>

#ifndef CP_UTF8
#define CP_UTF8 65001
#endif
int MultiByteToWideChar(UINT CodePage, DWORD dwFlags, LPCCH lpMultiByteStr,
                        int cbMultiByte, LPWSTR lpWideCharStr, int cchWideChar);

static void trace_startup(void) {
    wchar_t file[MAX_PATH];
    DWORD n = GetEnvironmentVariableW(L"IMPOR_LAUNCHER_TRACE", file, MAX_PATH);
    if (n == 0 || n >= MAX_PATH) return;
    HANDLE h = CreateFileW(file, GENERIC_WRITE, FILE_SHARE_READ, NULL, CREATE_ALWAYS,
                           FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) return;
    FILETIME creation, exit, kernel, user;
    GetProcessTimes(GetCurrentProcess(), &creation, &exit, &kernel, &user);
    ULARGE_INTEGER c; c.LowPart = creation.dwLowDateTime; c.HighPart = creation.dwHighDateTime;
    FILETIME nowft; GetSystemTimeAsFileTime(&nowft);
    ULARGE_INTEGER now; now.LowPart = nowft.dwLowDateTime; now.HighPart = nowft.dwHighDateTime;
    char buf[256];
    _snprintf(buf, sizeof(buf),
              "entry_ms=%llu\r\n",
              (unsigned long long)((now.QuadPart - c.QuadPart) / 10000ULL));
    DWORD written = 0;
    WriteFile(h, buf, (DWORD)strlen(buf), &written, NULL);
    CloseHandle(h);
}

static void append_quoted(wchar_t *dst, size_t cap, const wchar_t *src) {
    size_t len = wcslen(dst);
    if (len + 3 >= cap) return;
    dst[len++] = L'"';
    for (const wchar_t *p = src; *p && len + 2 < cap; ++p) {
        if (*p == L'"') dst[len++] = L'\\';
        dst[len++] = *p;
    }
    dst[len++] = L'"';
    dst[len] = 0;
}

static int find_python(wchar_t *out, size_t cap) {
    DWORD n = GetEnvironmentVariableW(L"IMPOR_PYTHON", out, (DWORD)cap);
    if (n > 0 && n < cap) return 1;

    wchar_t root[MAX_PATH];
    GetModuleFileNameW(NULL, root, MAX_PATH);
    wchar_t *slash = wcsrchr(root, L'\\');
    if (slash) *slash = 0;
    wchar_t cfg[MAX_PATH];
    _snwprintf(cfg, MAX_PATH, L"%s\\impor_python.txt", root);
    HANDLE h = CreateFileW(cfg, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING,
                           FILE_ATTRIBUTE_NORMAL, NULL);
    if (h != INVALID_HANDLE_VALUE) {
        char buf[MAX_PATH * 4]; DWORD got = 0;
        ReadFile(h, buf, sizeof(buf) - 1, &got, NULL); CloseHandle(h);
        buf[got] = 0;
        char *e = buf; while (*e && *e != '\r' && *e != '\n') ++e; *e = 0;
        if (buf[0]) {
            MultiByteToWideChar(CP_UTF8, 0, buf, -1, out, (int)cap);
            if (GetFileAttributesW(out) != INVALID_FILE_ATTRIBUTES) return 1;
        }
    }

    if (SearchPathW(NULL, L"python.exe", NULL, (DWORD)cap, out, NULL)) return 1;

    const wchar_t *fallback = L"D:\\编程软件\\python3.11\\python.exe";
    if (GetFileAttributesW(fallback) != INVALID_FILE_ATTRIBUTES) {
        wcsncpy(out, fallback, cap); out[cap - 1] = 0; return 1;
    }
    return 0;
}

int main(void) {
    SetConsoleTitleW(L"Impor (I++)");
    trace_startup();

    wchar_t root[MAX_PATH];
    GetModuleFileNameW(NULL, root, MAX_PATH);
    wchar_t *slash = wcsrchr(root, L'\\');
    if (slash) *slash = 0;

    wchar_t python[MAX_PATH];
    if (!find_python(python, MAX_PATH)) {
        printf("Impor 启动失败：找不到 Python。\r\n");
        printf("请设置 IMPOR_PYTHON，或在启动器旁放 impor_python.txt。\r\n");
        Sleep(4000);
        return 1;
    }

    wchar_t script[MAX_PATH];
    _snwprintf(script, MAX_PATH, L"%s\\Impor.py", root);

    // 取原始命令行里可执行文件之后的参数，保留引号与顺序。
    wchar_t *cmdline = GetCommandLineW();
    wchar_t *p = cmdline;
    if (*p == L'"') { ++p; while (*p && (*p != L'"' || p[-1] == L'\\')) ++p; if (*p) ++p; }
    else { while (*p && *p != L' ' && *p != L'\t') ++p; }
    while (*p == L' ' || *p == L'\t') ++p;

    wchar_t *cmd = (wchar_t *)HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, 32768 * sizeof(wchar_t));
    if (!cmd) return 1;
    append_quoted(cmd, 32768, python);
    wcsncat(cmd, L" -S -I -X utf8 ", 32768 - wcslen(cmd) - 1);
    append_quoted(cmd, 32768, script);
    if (*p) {
        wcsncat(cmd, L" ", 32768 - wcslen(cmd) - 1);
        wcsncat(cmd, p, 32768 - wcslen(cmd) - 1);
    }

    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    ZeroMemory(&si, sizeof(si)); si.cb = sizeof(si);
    ZeroMemory(&pi, sizeof(pi));
    if (!CreateProcessW(NULL, cmd, NULL, NULL, TRUE, 0, NULL, NULL, &si, &pi)) {
        printf("Impor 启动失败：无法运行 Python（错误 %lu）。\r\n", GetLastError());
        Sleep(4000);
        HeapFree(GetProcessHeap(), 0, cmd);
        return 1;
    }
    WaitForSingleObject(pi.hProcess, INFINITE);
    DWORD code = 1;
    GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hThread); CloseHandle(pi.hProcess);
    HeapFree(GetProcessHeap(), 0, cmd);
    return (int)code;
}
