/* host_c_dyn.c —— 动态加载探针（Windows / Linux 同一份源码）
 *
 * Windows：LoadLibraryA → GetProcAddress → FreeLibrary → 进程退出
 * Linux  ：dlopen     → dlsym         → dlclose     → 进程退出
 *
 * 用途：验证“加载 → 调用 → 卸载 → 退出”全程退出码 0（FreeLibrary 崩溃回归）。
 * 编译 Windows：gcc host_c_dyn.c -o host_c_dyn.exe
 * 编译 Linux  ：gcc host_c_dyn.c -o host_c_dyn -ldl
 */
#ifdef _WIN32
#include <windows.h>
#define IMPOR_OPEN(p)      (void *)LoadLibraryA(p)
#define IMPOR_SYM(h, n)    (void *)GetProcAddress((HMODULE)(h), n)
#define IMPOR_CLOSE(h)     FreeLibrary((HMODULE)(h))
#else
#include <dlfcn.h>
#define IMPOR_OPEN(p)      dlopen(p, RTLD_NOW | RTLD_LOCAL)
#define IMPOR_SYM(h, n)    dlsym(h, n)
#define IMPOR_CLOSE(h)     dlclose(h)
#endif
#include <stdio.h>

typedef long long (*fn_add)(long long, long long);
typedef long long (*fn_fib)(long long);
typedef const char *(*fn_greet)(const char *);
typedef void (*fn_free)(const char *);
typedef void (*fn_shut)(void);

int main(int argc, char **argv) {
    if (argc < 2) { printf("usage: host_c_dyn <lib>\n"); return 2; }
    void *h = IMPOR_OPEN(argv[1]);
    if (!h) { printf("open failed\n"); return 2; }
    fn_add add = (fn_add)IMPOR_SYM(h, "impor_add");
    fn_fib fib = (fn_fib)IMPOR_SYM(h, "impor_fib");
    fn_greet greet = (fn_greet)IMPOR_SYM(h, "impor_greet");
    fn_free frees = (fn_free)IMPOR_SYM(h, "impor_free_str");
    fn_shut shut = (fn_shut)IMPOR_SYM(h, "impor_runtime_shutdown");
    printf("add=%lld fib=%lld\n", add(2, 3), fib(10));
    const char *s = greet("rust");
    printf("greet=%s\n", s);
    frees(s);
    if (shut) shut();
    IMPOR_CLOSE(h);
    printf("unloaded\n");
    return 0;
}
