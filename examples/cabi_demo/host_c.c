/* host_c.c —— C 宿主：链接 Impor 生成的 64 位 DLL（导入库 logic.lib）
 * 编译（MinGW-w64）：
 *   gcc host_c.c logic.lib -o host_c.exe
 * 运行需要 logic.dll 在同目录。
 */
#include <stdio.h>
#include "logic.h"

int main(void) {
    printf("C 宿主: impor_add(2,3)=%lld  impor_fib(15)=%lld\n",
           impor_add(2, 3), impor_fib(15));
    const char *s = impor_greet("rust");
    printf("C 宿主: impor_greet(\"rust\")=%s\n", s);
    impor_free_str(s);
    impor_runtime_shutdown();
    return 0;
}
