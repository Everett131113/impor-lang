#include <stdio.h>
#include <stdlib.h>
#include <ctype.h>

int main(void) {
    if (getenv("IMPOR_COMPARE_FLOOR")) return 0;  /* 纯进程地板测量 */
    FILE *f = fopen("case.json", "rb");
    if (!f) { fprintf(stderr, "cannot open case.json\n"); return 1; }
    char *buf = (char *)malloc(1 << 20);
    if (!buf) return 1;
    size_t n = fread(buf, 1, 1 << 20, f);
    fclose(f);
    buf[n] = 0;

    /* case.json 只含 {"values":[1,2,...]}；找到数组起点后累加整数。 */
    char *p = buf;
    while (*p && *p != '[') p++;
    if (*p == '[') p++;
    long total = 0;
    while (*p && *p != ']') {
        if (isdigit((unsigned char)*p)) {
            total += strtol(p, &p, 10);
        } else {
            p++;
        }
    }
    printf("sum = %ld\n", total);
    free(buf);
    return 0;
}
