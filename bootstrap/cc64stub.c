/* impor 自举链的 C 编译器桩（cc64 包装器）。
 *
 * 为什么需要它：种子（bootstrap/imporc.impor）用 system() 调 bootstrap\cc64.bat，
 * 而 Impor 运行时的 rt_system 在 Windows 上走 CreateProcessW —— 它**只能启动 PE**，
 * 不能执行 .bat/.cmd（返回 FALSE，调用方只看到 rc=-1）。所以这里把名字沿用
 * 成 cc64.bat，实体却是一个 PE：CreateProcess 不检查扩展名。
 *
 * 参数约定（与 cc64.bat 一致）：cc64.bat <输入.c> -o <输出.exe>
 * gcc 路径通过环境变量 IMPOR_CC64_GCC 传入（不硬编码，换机器不用重编）。
 */
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(int argc, char **argv) {
    char cmd[8192];
    char newpath[8192];
    char gccdir[1024];
    const char *gccpath = getenv("IMPOR_CC64_GCC");
    const char *oldpath = getenv("PATH");
    const char *in = NULL;
    const char *out = NULL;
    STARTUPINFOA si;
    PROCESS_INFORMATION pi;
    DWORD code = 1;
    int i;
    char *slash;

    if (!gccpath || !gccpath[0]) return 3;

    /* 调试钩子：设 IMPOR_CC64_TRACE 时把收到的参数写盘，便于确认调用确实到达。 */
    {
        const char *trace = getenv("IMPOR_CC64_TRACE");
        if (trace && trace[0]) {
            FILE *f = fopen(trace, "a");
            if (f) {
                int k;
                fprintf(f, "called argc=%d cwd=", argc);
                { char cw[1024]; if (GetCurrentDirectoryA(sizeof(cw), cw)) fprintf(f, "%s", cw); }
                fprintf(f, "\n");
                for (k = 0; k < argc; k++) fprintf(f, "  argv[%d]=[%s]\n", k, argv[k]);
                fclose(f);
            }
        }
    }
    for (i = 1; i + 1 < argc; i++) {
        if (strcmp(argv[i], "-o") == 0) { out = argv[i + 1]; i++; }
        else if (!in) in = argv[i];
    }
    if (!in || !out) {
        fprintf(stderr, "cc64: usage: cc64.bat <input.c> -o <output.exe>\n");
        return 2;
    }

    /* gcc 启动 cc1.exe 时，cc1 要按 PATH 找 libgcc_s_seh-1.dll / libwinpthread-1.dll；
       没有 bin 目录就会 0xC0000135 静默失败。这里先把自己所在路径前置进子进程 PATH。 */
    strncpy(gccdir, gccpath, sizeof(gccdir) - 1);
    gccdir[sizeof(gccdir) - 1] = '\0';
    slash = strrchr(gccdir, '\\');
    if (slash) *slash = '\0';
    _snprintf(newpath, sizeof(newpath), "%s;%s", gccdir, oldpath ? oldpath : "");
    SetEnvironmentVariableA("PATH", newpath);

    _snprintf(cmd, sizeof(cmd), "\"%s\" -std=gnu99 -O0 -w -o \"%s\" \"%s\"",
              gccpath, out, in);

    ZeroMemory(&si, sizeof(si));
    si.cb = sizeof(si);
    ZeroMemory(&pi, sizeof(pi));
    if (!CreateProcessA(NULL, cmd, NULL, NULL, FALSE, 0, NULL, NULL, &si, &pi)) {
        fprintf(stderr, "cc64: CreateProcess failed (%lu) for: %s\n", GetLastError(), cmd);
        return 4;
    }
    WaitForSingleObject(pi.hProcess, INFINITE);
    GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);
    return (int)code;
}
