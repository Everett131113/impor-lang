#include <stdio.h>
#include "lib.h"

int main(void) {
    const char *greeting = impor_greet("Impor");
    printf("add(2, 3) = %lld\n", impor_add(2, 3));
    printf("greet(\"Impor\") = %s\n", greeting);
    printf("twice((size_t)21) = %zu\n", impor_twice((size_t)21));
    impor_free_str(greeting);
    return 0;
}
