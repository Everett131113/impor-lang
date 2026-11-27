/*
 * imporai.c — Impor AI 数值内核（C 共享库）。
 *
 * 设计：只做重活。ndarray 是连续 float64 内存 + shape/strides；
 * 解释器（impor/imporai Python 包）通过 ctypes 驱动本库，
 * 自动求导 / 优化器等编排逻辑留在 Python 层。
 *
 * 编译：gcc -O2 -shared -o imporai.dll imporai.c -lm
 * 约定：所有返回 IAArray* 的函数失败时返回 NULL；调用方负责 ia_free。
 */
#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#define IA_EXPORT __declspec(dllexport)
#else
#define IA_EXPORT
#endif

typedef struct IAArray {
    int ndim;
    int64_t *shape;
    int64_t *strides;      /* 以元素为单位 */
    int64_t size;
    double *data;          /* 始终连续、行优先 */
} IAArray;

/* ---------- 内部工具 ---------- */

static uint64_t ia_rng_state = 0x123456789abcdefULL;

static uint64_t xorshift64(void) {
    uint64_t x = ia_rng_state;
    x ^= x << 13;
    x ^= x >> 7;
    x ^= x << 17;
    ia_rng_state = x;
    return x;
}

static double uniform01(void) {
    /* 高 53 位映射到 [0,1) */
    return (double)(xorshift64() >> 11) * (1.0 / 9007199254740992.0);
}

static double randn_one(void) {
    double u1, u2;
    do { u1 = uniform01(); } while (u1 <= 1e-12);
    u2 = uniform01();
    return sqrt(-2.0 * log(u1)) * cos(6.283185307179586 * u2);
}

static int64_t prod_shape(int ndim, const int64_t *shape) {
    int64_t p = 1;
    for (int i = 0; i < ndim; i++) p *= shape[i];
    return p;
}

static void fill_strides(const IAArray *a) {
    int64_t s = 1;
    for (int d = a->ndim - 1; d >= 0; d--) {
        a->strides[d] = s;
        s *= a->shape[d];
    }
}

static IAArray *alloc_array(int ndim, const int64_t *shape) {
    if (ndim <= 0) return NULL;
    IAArray *a = (IAArray *)calloc(1, sizeof(IAArray));
    if (!a) return NULL;
    a->ndim = ndim;
    a->shape = (int64_t *)malloc(sizeof(int64_t) * ndim);
    a->strides = (int64_t *)malloc(sizeof(int64_t) * ndim);
    if (!a->shape || !a->strides) { free(a->shape); free(a->strides); free(a); return NULL; }
    for (int i = 0; i < ndim; i++) {
        if (shape[i] <= 0) { free(a->shape); free(a->strides); free(a); return NULL; }
        a->shape[i] = shape[i];
    }
    a->size = prod_shape(ndim, shape);
    a->data = (double *)calloc((size_t)a->size, sizeof(double));
    if (!a->data) { free(a->shape); free(a->strides); free(a); return NULL; }
    fill_strides(a);
    return a;
}

static int64_t flat_offset(const IAArray *a, const int64_t *idx) {
    int64_t off = 0;
    for (int d = 0; d < a->ndim; d++) off += idx[d] * a->strides[d];
    return off;
}

/* ---------- 导出 API ---------- */

IA_EXPORT void ia_seed_rng(uint64_t seed) {
    ia_rng_state = seed ? seed : 0x123456789abcdefULL;
}

IA_EXPORT void ia_free(IAArray *a) {
    if (!a) return;
    free(a->shape);
    free(a->strides);
    free(a->data);
    free(a);
}

IA_EXPORT int ia_ndim(const IAArray *a) { return a ? a->ndim : 0; }
IA_EXPORT int64_t ia_size(const IAArray *a) { return a ? a->size : 0; }
IA_EXPORT int64_t ia_shape_at(const IAArray *a, int d) {
    return (a && d < a->ndim) ? a->shape[d] : 0;
}
IA_EXPORT double *ia_data_ptr(IAArray *a) { return a ? a->data : NULL; }

IA_EXPORT IAArray *ia_zeros(int ndim, const int64_t *shape) {
    return alloc_array(ndim, shape);   /* calloc 已清零 */
}

IA_EXPORT IAArray *ia_ones(int ndim, const int64_t *shape) {
    IAArray *a = alloc_array(ndim, shape);
    if (!a) return NULL;
    for (int64_t i = 0; i < a->size; i++) a->data[i] = 1.0;
    return a;
}

IA_EXPORT IAArray *ia_from_buffer(const double *buf, int ndim, const int64_t *shape) {
    IAArray *a = alloc_array(ndim, shape);
    if (!a) return NULL;
    memcpy(a->data, buf, sizeof(double) * (size_t)a->size);
    return a;
}

IA_EXPORT IAArray *ia_copy(const IAArray *src) {
    if (!src) return NULL;
    IAArray *a = alloc_array(src->ndim, src->shape);
    if (!a) return NULL;
    memcpy(a->data, src->data, sizeof(double) * (size_t)src->size);
    return a;
}

IA_EXPORT double ia_get(const IAArray *a, const int64_t *idx) {
    if (!a) return 0.0;
    return a->data[flat_offset(a, idx)];
}

IA_EXPORT void ia_set(IAArray *a, const int64_t *idx, double v) {
    if (!a) return;
    a->data[flat_offset(a, idx)] = v;
}

IA_EXPORT IAArray *ia_reshape(const IAArray *src, int ndim, const int64_t *shape) {
    if (!src || prod_shape(ndim, shape) != src->size) return NULL;
    IAArray *a = alloc_array(ndim, shape);
    if (!a) return NULL;
    memcpy(a->data, src->data, sizeof(double) * (size_t)src->size);
    return a;
}

IA_EXPORT IAArray *ia_transpose2d(const IAArray *src) {
    if (!src || src->ndim != 2) return NULL;
    int64_t sh[2] = { src->shape[1], src->shape[0] };
    IAArray *a = alloc_array(2, sh);
    if (!a) return NULL;
    for (int64_t i = 0; i < src->shape[0]; i++)
        for (int64_t j = 0; j < src->shape[1]; j++)
            a->data[j * a->shape[1] + i] = src->data[i * src->shape[1] + j];
    return a;
}

/* 逐元素运算：op 0 add 1 sub 2 mul 3 div */
IA_EXPORT IAArray *ia_elem(const IAArray *x, const IAArray *y, int op) {
    if (!x || !y || x->size != y->size) return NULL;
    IAArray *a = alloc_array(x->ndim, x->shape);
    if (!a) return NULL;
    for (int64_t i = 0; i < x->size; i++) {
        double u = x->data[i], v = y->data[i];
        switch (op) {
            case 0: a->data[i] = u + v; break;
            case 1: a->data[i] = u - v; break;
            case 2: a->data[i] = u * v; break;
            default: a->data[i] = u / v; break;
        }
    }
    return a;
}

/* 标量运算：op 0 加 1 减(x-s) 2 乘 3 除(x/s) */
IA_EXPORT IAArray *ia_scalar(const IAArray *x, double s, int op) {
    if (!x) return NULL;
    IAArray *a = alloc_array(x->ndim, x->shape);
    if (!a) return NULL;
    for (int64_t i = 0; i < x->size; i++) {
        double u = x->data[i];
        switch (op) {
            case 0: a->data[i] = u + s; break;
            case 1: a->data[i] = u - s; break;
            case 2: a->data[i] = u * s; break;
            default: a->data[i] = u / s; break;
        }
    }
    return a;
}

/* 函数映射：fn 0 exp 1 log 2 tanh 3 sqrt 4 relu 5 sigmoid */
IA_EXPORT IAArray *ia_apply(const IAArray *x, int fn) {
    if (!x) return NULL;
    IAArray *a = alloc_array(x->ndim, x->shape);
    if (!a) return NULL;
    for (int64_t i = 0; i < x->size; i++) {
        double u = x->data[i];
        switch (fn) {
            case 0: a->data[i] = exp(u); break;
            case 1: a->data[i] = log(u); break;
            case 2: a->data[i] = tanh(u); break;
            case 3: a->data[i] = sqrt(u); break;
            case 4: a->data[i] = u > 0 ? u : 0.0; break;
            default: a->data[i] = 1.0 / (1.0 + exp(-u)); break;
        }
    }
    return a;
}

IA_EXPORT IAArray *ia_matmul(const IAArray *x, const IAArray *y) {
    if (!x || !y || x->ndim != 2 || y->ndim != 2 || x->shape[1] != y->shape[0])
        return NULL;
    int64_t m = x->shape[0], k = x->shape[1], n = y->shape[1];
    int64_t sh[2] = { m, n };
    IAArray *a = alloc_array(2, sh);
    if (!a) return NULL;
    for (int64_t i = 0; i < m; i++) {
        const double *xr = x->data + i * k;
        double *ar = a->data + i * n;
        for (int64_t p = 0; p < k; p++) {
            double v = xr[p];
            const double *yr = y->data + p * n;
            for (int64_t j = 0; j < n; j++) ar[j] += v * yr[j];
        }
    }
    return a;
}

IA_EXPORT double ia_sum(const IAArray *x) {
    if (!x) return 0.0;
    double s = 0.0;
    for (int64_t i = 0; i < x->size; i++) s += x->data[i];
    return s;
}

IA_EXPORT IAArray *ia_randn(int ndim, const int64_t *shape) {
    IAArray *a = alloc_array(ndim, shape);
    if (!a) return NULL;
    for (int64_t i = 0; i < a->size; i++) a->data[i] = randn_one();
    return a;
}

IA_EXPORT IAArray *ia_rand_uniform(int ndim, const int64_t *shape,
                                   double lo, double hi) {
    IAArray *a = alloc_array(ndim, shape);
    if (!a) return NULL;
    for (int64_t i = 0; i < a->size; i++)
        a->data[i] = lo + (hi - lo) * uniform01();
    return a;
}

/* 偏置广播：x (m,k) + row (1,k)，逐行相加 */
IA_EXPORT IAArray *ia_add_bias(const IAArray *x, const IAArray *row) {
    if (!x || !row || x->ndim != 2 || row->ndim != 2
        || row->shape[0] != 1 || x->shape[1] != row->shape[1])
        return NULL;
    IAArray *a = alloc_array(x->ndim, x->shape);
    if (!a) return NULL;
    int64_t k = x->shape[1];
    for (int64_t i = 0; i < x->shape[0]; i++)
        for (int64_t j = 0; j < k; j++)
            a->data[i * k + j] = x->data[i * k + j] + row->data[j];
    return a;
}

/* 沿第 0 轴求和：(m,k) -> (1,k)，偏置反向用 */
IA_EXPORT IAArray *ia_sum_axis0(const IAArray *x) {
    if (!x || x->ndim != 2) return NULL;
    int64_t sh[2] = { 1, x->shape[1] };
    IAArray *a = alloc_array(2, sh);
    if (!a) return NULL;
    int64_t k = x->shape[1];
    for (int64_t i = 0; i < x->shape[0]; i++)
        for (int64_t j = 0; j < k; j++)
            a->data[j] += x->data[i * k + j];
    return a;
}
