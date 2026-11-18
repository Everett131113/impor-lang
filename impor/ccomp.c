/*
 * imporc —— Impor (I++) 自举 C 编译器（v2.14 阶段一）
 * ----------------------------------------------------------------
 * 把 Impor 编译器本体移植到 C：本文件是纯 C 实现的
 *   词法分析器 + 语法分析器 + C 代码转译器。
 * 用 tcc 编译本文件得到 imporc.exe，运行完全不依赖 Python 宿主。
 *
 * 用法：
 *   imporc build 源文件.impor [-o 输出.exe]
 *
 * 支持子集（与 Python 端 ctrans 的 C 后端子集对齐）：
 *   语句：var / set / print / if / elif / else / while / for-range /
 *         func / return / break / continue / 表达式调用
 *   表达式：数字 / 字符串 / 布尔 / 变量 / 函数调用 / 内建调用 /
 *          + - * / %  比较 == != < > <= >=  and or not  一元负 括号
 *   内建：print len type to_num to_str trim upper lower strpad strcut
 *         abs min max sign clamp fileread filewrite system sleep input
 *         inputstr randi seed now clock randstr wait
 *
 * 与 Python 端共享 impor/c_runtime.c（同一份 C 运行库），因此
 * ctrans 与 imporc 的编译产物行为一致——这是自举验证的基础。
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <stdarg.h>
#ifdef _WIN32
#include <windows.h>
#endif

/* ================= 动态字符串缓冲 ================= */
typedef struct { char *b; size_t n, cap; } SB;
static void sb_grow(SB *s, size_t add) {
    if (s->n + add + 1 > s->cap) {
        size_t nc = s->cap ? s->cap * 2 : 256;
        while (nc < s->n + add + 1) nc *= 2;
        s->b = (char *)realloc(s->b, nc);
        s->cap = nc;
    }
}
static void sb_puts(SB *s, const char *t) {
    if (!t) return;
    size_t L = strlen(t);
    sb_grow(s, L);
    memcpy(s->b + s->n, t, L);
    s->n += L;
    s->b[s->n] = '\0';
}
static void sb_putc(SB *s, char c) {
    sb_grow(s, 1);
    s->b[s->n++] = c;
    s->b[s->n] = '\0';
}
static void sb_printf(SB *s, const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    char tmp[2048];
    vsnprintf(tmp, sizeof(tmp), fmt, ap);
    va_end(ap);
    sb_puts(s, tmp);
}

/* 执行外部命令并等待（Windows 用 CreateProcess，规避 system() 引号兼容问题） */
static int run_cmd(const char *cmd) {
#ifdef _WIN32
    STARTUPINFOA si;
    PROCESS_INFORMATION pi;
    ZeroMemory(&si, sizeof(si));
    si.cb = sizeof(si);
    ZeroMemory(&pi, sizeof(pi));
    char cl[8192];
    strncpy(cl, cmd, sizeof(cl) - 1);
    cl[sizeof(cl) - 1] = '\0';
    if (!CreateProcessA(NULL, cl, NULL, NULL, FALSE, 0, NULL, NULL, &si, &pi))
        return -1;
    WaitForSingleObject(pi.hProcess, INFINITE);
    DWORD code = 0;
    GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);
    return (int)code;
#else
    return system(cmd);
#endif
}

/* ================= Token ================= */
enum { T_EOF = 0, T_ID, T_NUM, T_STR, T_OP, T_NL, T_IND, T_DED };
typedef struct { int t; char s[1024]; int line; } Tok;
#define MAXTOK 400000
static Tok toks[MAXTOK];
static int ntok = 0;

static const char *KWS[] = {
    "if", "else", "elif", "elseif", "while", "for", "in", "func", "def",
    "var", "set", "return", "true", "false", "and", "or", "not",
    "break", "continue", "range", "print", NULL
};
static int is_kw(const char *s) {
    for (int i = 0; KWS[i]; i++)
        if (strcmp(s, KWS[i]) == 0) return 1;
    return 0;
}

static void lexerr(int line, const char *msg, const char *extra) {
    fprintf(stderr, "imporc: 第 %d 行: %s%s\n", line, msg, extra ? extra : "");
    exit(1);
}

/* ================= 词法分析（缩进块） ================= */
static int ind_stack[4096];
static int ind_top = 0;

static void lex(const char *src) {
    const char *p = src;
    int line = 1;
    char buf[1024];
    ntok = 0;
    /* 头部标记行：important<<... 整行跳过 */
    {
        const char *q = p;
        while (*q == '\r' || *q == '\n' || *q == ' ' || *q == '\t') {
            if (*q == '\n') line++;
            q++;
        }
        if (strncmp(q, "important<<", 11) == 0) {
            while (*q && *q != '\n') q++;
            p = q;
        }
    }
    while (*p) {
        /* 读一行（跳过 \r） */
        const char *ls = p;
        while (*p && *p != '\n') p++;
        size_t llen = (size_t)(p - ls);
        if (*p == '\n') p++;
        /* 去掉行尾 \r */
        if (llen > 0 && ls[llen - 1] == '\r') llen--;
        char *ln = (char *)malloc(llen + 1);
        memcpy(ln, ls, llen);
        ln[llen] = '\0';
        line++;

        /* 缩进计算 */
        int ind = 0, i = 0;
        while (i < (int)llen && ln[i] == ' ') { ind++; i++; }
        while (i < (int)llen && ln[i] == '\t') { ind += 4; i++; }
        /* 跳过纯空白 / 注释 / 头部标记行 */
        int j = i;
        while (j < (int)llen && (ln[j] == ' ' || ln[j] == '\t')) j++;
        int has_code = (j < (int)llen);
        if (has_code && (ln[j] == '#' || (ln[j] == '/' && j + 1 < (int)llen && ln[j + 1] == '/')))
            has_code = 0;
        if (has_code && strncmp(ln + j, "important<<", 11) == 0)
            has_code = 0;
        if (has_code) {
            if (ntok + 8 >= MAXTOK) lexerr(line, "token 过多", NULL);
            if (ind > ind_stack[ind_top]) {
                ind_stack[++ind_top] = ind;
                toks[ntok].t = T_IND; toks[ntok].s[0] = 0; toks[ntok].line = line - 1; ntok++;
            } else {
                while (ind_top > 0 && ind < ind_stack[ind_top]) {
                    ind_top--;
                    toks[ntok].t = T_DED; toks[ntok].s[0] = 0; toks[ntok].line = line - 1; ntok++;
                }
                if (ind_top > 0 && ind != ind_stack[ind_top]) {
                    fprintf(stderr, "imporc: 第 %d 行: 缩进不一致（期望 %d 空格）\n",
                            line - 1, ind_stack[ind_top]);
                    exit(1);
                }
            }
            /* 行内 token 化 */
            int k = i;
            while (k < (int)llen) {
                char c = ln[k];
                if (c == ' ' || c == '\t') { k++; continue; }
                if (c == '#' || (c == '/' && k + 1 < (int)llen && ln[k + 1] == '/')) break;
                Tok *tk = &toks[ntok];
                tk->line = line - 1;
                /* 字符串 */
                if (c == '"' || c == '\'') {
                    char quote = c;
                    k++;
                    int bl = 0;
                    while (k < (int)llen) {
                        char d = ln[k];
                        if (d == '\\' && k + 1 < (int)llen) {
                            char e = ln[k + 1];
                            switch (e) {
                                case 'n': buf[bl++] = '\n'; break;
                                case 't': buf[bl++] = '\t'; break;
                                case 'r': buf[bl++] = '\r'; break;
                                case '\\': buf[bl++] = '\\'; break;
                                case '"': buf[bl++] = '"'; break;
                                case '\'': buf[bl++] = '\''; break;
                                case '{': buf[bl++] = '{'; break;
                                case '}': buf[bl++] = '}'; break;
                                default: buf[bl++] = '\\'; buf[bl++] = e; break;
                            }
                            k += 2;
                        } else if (d == quote) {
                            k++;
                            break;
                        } else {
                            buf[bl++] = d;
                            k++;
                        }
                    }
                    buf[bl] = '\0';
                    tk->t = T_STR;
                    strcpy(tk->s, buf);
                    ntok++;
                    continue;
                }
                /* 数字（v2.17 支持下划线 1_000_000） */
                if (isdigit((unsigned char)c) ||
                    (c == '.' && k + 1 < (int)llen && isdigit((unsigned char)ln[k + 1]))) {
                    int bl = 0;
                    int seen_dot = 0;
                    while (k < (int)llen) {
                        char d = ln[k];
                        if (isdigit((unsigned char)d)) { buf[bl++] = d; k++; }
                        else if (d == '_' && k + 1 < (int)llen && isdigit((unsigned char)ln[k + 1])) {
                            k++;  /* 数字下划线：跳过分隔符 */
                        }
                        else if (d == '.' && !seen_dot && k + 1 < (int)llen &&
                                 (isdigit((unsigned char)ln[k + 1]) || ln[k + 1] == ' ' ||
                                  ln[k + 1] == ')' || ln[k + 1] == ',' || ln[k + 1] == '+' ||
                                  ln[k + 1] == '-' || ln[k + 1] == '*' || ln[k + 1] == '/' ||
                                  ln[k + 1] == '%' || ln[k + 1] == '>')) {
                            seen_dot = 1; buf[bl++] = d; k++;
                        }
                        else break;
                    }
                    buf[bl] = '\0';
                    tk->t = T_NUM;
                    strcpy(tk->s, buf);
                    ntok++;
                    continue;
                }
                /* 标识符 / 关键字 */
                if (isalpha((unsigned char)c) || c == '_') {
                    int bl = 0;
                    while (k < (int)llen) {
                        char d = ln[k];
                        if (isalnum((unsigned char)d) || d == '_') { buf[bl++] = d; k++; }
                        else break;
                    }
                    buf[bl] = '\0';
                    tk->t = is_kw(buf) ? T_OP : T_ID;  /* 关键字复用 OP 槽，用文本判断 */
                    strcpy(tk->s, buf);
                    ntok++;
                    continue;
                }
                /* 多字符运算符 */
                if (k + 1 < (int)llen) {
                    const char *two = NULL;
                    if (ln[k] == '=' && ln[k + 1] == '=') two = "==";
                    else if (ln[k] == '!' && ln[k + 1] == '=') two = "!=";
                    else if (ln[k] == '>' && ln[k + 1] == '=') two = ">=";
                    else if (ln[k] == '<' && ln[k + 1] == '=') two = "<=";
                    else if (ln[k] == '+' && ln[k + 1] == '=') two = "+=";
                    else if (ln[k] == '-' && ln[k + 1] == '=') two = "-=";
                    else if (ln[k] == '*' && ln[k + 1] == '=') two = "*=";
                    else if (ln[k] == '/' && ln[k + 1] == '=') two = "/=";
                    else if (ln[k] == '%' && ln[k + 1] == '=') two = "%=";
                    if (two) {
                        if (two[0] == '+' || two[0] == '-' || two[0] == '*' ||
                            two[0] == '/' || two[0] == '%') {
                            fprintf(stderr, "imporc: 第 %d 行: 不支持复合赋值 %s（请展开为 set 语句）\n",
                                    line - 1, two);
                            exit(1);
                        }
                        strcpy(tk->s, two);
                        tk->t = T_OP;
                        ntok++;
                        k += 2;
                        continue;
                    }
                }
                if (c == '{' || c == '}') {
                    fprintf(stderr, "imporc: 第 %d 行: 不支持大括号块 %c，请使用缩进块\n", line - 1, c);
                    exit(1);
                }
                /* 单字符运算符 */
                if (strchr("+-*/%()=,<>.", c)) {
                    buf[0] = c; buf[1] = '\0';
                    strcpy(tk->s, buf);
                    tk->t = T_OP;
                    ntok++;
                    k++;
                    continue;
                }
                if ((unsigned char)c >= 0x80) {
                    fprintf(stderr, "imporc: 第 %d 行: 暂不支持非 ASCII 字符（当前自举子集仅英文标识符）\n",
                            line - 1);
                    exit(1);
                }
                fprintf(stderr, "imporc: 第 %d 行: 无法识别字符 '%c'\n", line - 1, c);
                exit(1);
            }
            /* 行尾 NEWLINE */
            toks[ntok].t = T_NL; toks[ntok].s[0] = 0; toks[ntok].line = line - 1; ntok++;
        }
        free(ln);
    }
    while (ind_top > 0) {
        ind_top--;
        toks[ntok].t = T_DED; toks[ntok].s[0] = 0; toks[ntok].line = line; ntok++;
    }
    toks[ntok].t = T_EOF; toks[ntok].s[0] = 0; toks[ntok].line = line; ntok++;
    if (getenv("IMPORC_TRACE")) {
        fprintf(stderr, "-- tokens --\n");
        for (int i = 0; i < ntok; i++) {
            const char *tn = toks[i].t == T_IND ? "IND" : toks[i].t == T_DED ? "DED" :
                             toks[i].t == T_NL ? "NL " : toks[i].t == T_EOF ? "EOF" :
                             toks[i].t == T_NUM ? "NUM" : toks[i].t == T_STR ? "STR" : "TOK";
            fprintf(stderr, "%3d %s %d %s\n", i, tn, toks[i].line, toks[i].s);
        }
    }
}

/* ================= 符号表 ================= */
#define MAXSYM 1024
#define MAXFN 512
static char globals[MAXSYM][128];
static int nglob = 0;
typedef struct {
    char name[128];
    char params[16][128];
    int nparams;
    char locals[MAXSYM][128];
    int nlocals;
} Func;
static Func funcs[MAXFN];
static int nfuncs = 0;

static Func *find_func(const char *name) {
    for (int i = 0; i < nfuncs; i++)
        if (strcmp(funcs[i].name, name) == 0) return &funcs[i];
    return NULL;
}
static int find_global(const char *name) {
    for (int i = 0; i < nglob; i++)
        if (strcmp(globals[i], name) == 0) return 1;
    return 0;
}
static void add_global(const char *name) {
    if (!find_global(name)) {
        if (nglob >= MAXSYM) { fprintf(stderr, "imporc: 全局符号过多\n"); exit(1); }
        strcpy(globals[nglob++], name);
    }
}
static int has_local(Func *f, const char *name) {
    for (int i = 0; i < f->nlocals; i++)
        if (strcmp(f->locals[i], name) == 0) return 1;
    return 0;
}
static void add_local(Func *f, const char *name) {
    if (!has_local(f, name)) {
        if (f->nlocals >= MAXSYM) { fprintf(stderr, "imporc: 函数 %s 局部符号过多\n", f->name); exit(1); }
        strcpy(f->locals[f->nlocals++], name);
    }
}

/* ================= 内建映射 ================= */
typedef struct { const char *name; const char *rt; int nargs; } BI;
static const BI BI_TABLE[] = {
    {"len", "rt_len", 1}, {"type", "rt_type", 1}, {"type_of", "rt_type", 1},
    {"to_num", "rt_to_num", 1}, {"to_str", "rt_tostr", 1},
    {"str", "rt_tostr", 1}, {"int", "rt_to_int", 1},
    {"trim", "rt_trim", 1}, {"upper", "rt_upper", 1}, {"lower", "rt_lower", 1},
    {"strpad", "rt_strpad", 2}, {"strcut", "rt_strcut", 3},
    {"abs", "rt_abs", 1}, {"div", "rt_idiv", 2},
    {"min", "rt_min2", 2}, {"max", "rt_max2", 2},
    {"sign", "rt_sign", 1}, {"clamp", "rt_clamp", 3},
    {"fileread", "rt_fileread", 1}, {"filewrite", "rt_filewrite", 2},
    {"system", "rt_system", 1},
    {"exit", "rt_exit", 1},
    {"sleep", "rt_sleep", 1}, {"wait", "rt_sleep", 1},
    {"input", "rt_input", 1}, {"inputstr", "rt_inputstr", 1},
    {"randi", "rt_randi", 2}, {"seed", "rt_seed", 1},
    {"now", "rt_now", 0}, {"clock", "rt_clock", 0},
    {"randstr", "rt_randstr", 1},
    {NULL, NULL, 0}
};
static const char *bi_rt(const char *name, int *nargs) {
    for (int i = 0; BI_TABLE[i].name; i++) {
        if (strcmp(BI_TABLE[i].name, name) == 0) {
            *nargs = BI_TABLE[i].nargs;
            return BI_TABLE[i].rt;
        }
    }
    return NULL;
}

/* ================= 转译器（递归下降） ================= */
static Tok *tk;
static int pos;
static int in_func_depth = 0;
static Func *curf = NULL;
static SB body;
static SB *OB = &body;
static SB fndefs;
static int for_id = 0;

static void err_here(const char *msg) {
    fprintf(stderr, "imporc: 第 %d 行: %s\n", tk[pos].line, msg);
    exit(1);
}
static Tok *cur(void) { return &tk[pos]; }
static int is_op(const char *s) {
    Tok *t = &tk[pos];
    return (t->t == T_OP || t->t == T_ID) && strcmp(t->s, s) == 0;
}
static void expect_op(const char *s) {
    if (!is_op(s)) {
        fprintf(stderr, "imporc: 第 %d 行: 期望 '%s'，实际是 '%s'\n",
                cur()->line, s, cur()->t == T_EOF ? "文件结束" : cur()->s);
        exit(1);
    }
    pos++;
}
static void skip_nl(void) {
    while (cur()->t == T_NL) pos++;
}

/* 变量引用 → l_/g_ */
static void gen_varref(const char *name) {
    if (curf && has_local(curf, name)) {
        sb_printf(OB, "l_%s", name);
    } else {
        add_global(name);  /* 函数内引用未声明全局视为全局（声明在顶部） */
        sb_printf(OB, "g_%s", name);
    }
}

static void gen_expr(void);
static void gen_stmt(void);

/* C 字符串字面量转义 */
static void esc_c(SB *s, const char *src) {
    for (const char *p = src; *p; p++) {
        char c = *p;
        if (c == '"') sb_puts(s, "\\\"");
        else if (c == '\\') sb_puts(s, "\\\\");
        else if (c == '\n') sb_puts(s, "\\n");
        else if (c == '\t') sb_puts(s, "\\t");
        else if (c == '\r') sb_puts(s, "\\r");
        else sb_putc(s, c);
    }
}

static void gen_primary(void) {
    Tok *t = cur();
    if (t->t == T_NUM) {
        if (strchr(t->s, '.')) sb_printf(OB, "v_float(%s)", t->s);
        else sb_printf(OB, "v_int(%s)", t->s);
        pos++;
        return;
    }
    if (t->t == T_STR) {
        sb_puts(OB, "v_str(\"");
        esc_c(OB, t->s);
        sb_puts(OB, "\")");
        pos++;
        return;
    }
    if (t->t == T_OP && (strcmp(t->s, "true") == 0 || strcmp(t->s, "false") == 0)) {
        sb_printf(OB, "v_bool(%d)", strcmp(t->s, "true") == 0 ? 1 : 0);
        pos++;
        return;
    }
    if (t->t == T_ID) {
        char name[128];
        strcpy(name, t->s);
        pos++;
        if (is_op("(")) {
            /* 调用：参数生成进 OB 后截取组装 */
            pos++;
            skip_nl();
            size_t a0 = OB->n;
            int nargs = 0;
            while (!is_op(")")) {
                if (nargs > 0) sb_puts(OB, ", ");
                gen_expr();
                nargs++;
                skip_nl();
                if (is_op(",")) { pos++; skip_nl(); }
                else break;
            }
            expect_op(")");
            size_t alen = OB->n - a0;
            char *argstr = (char *)malloc(alen + 1);
            if (alen > 0 && OB->b) memcpy(argstr, OB->b + a0, alen);
            argstr[alen] = '\0';
            OB->n = a0;
            if (OB->b) OB->b[a0] = '\0';
            if (strcmp(name, "print") == 0) {
                fprintf(stderr, "imporc: 第 %d 行: print 只能作为语句使用\n", cur()->line);
                exit(1);
            }
            int bn = 0;
            const char *rtn = bi_rt(name, &bn);
            if (rtn) {
                if (nargs != bn) {
                    fprintf(stderr, "imporc: 第 %d 行: 内建 %s 需要 %d 个参数，给了 %d 个\n",
                            cur()->line, name, bn, nargs);
                    exit(1);
                }
                sb_printf(OB, "%s(%s)", rtn, argstr);
            } else {
                Func *f = find_func(name);
                if (!f) {
                    fprintf(stderr, "imporc: 第 %d 行: 未定义函数：%s\n", cur()->line, name);
                    exit(1);
                }
                if (nargs != f->nparams) {
                    fprintf(stderr, "imporc: 第 %d 行: 函数 %s 需要 %d 个参数，给了 %d 个\n",
                            cur()->line, name, f->nparams, nargs);
                    exit(1);
                }
                sb_printf(OB, "c_%s(%s)", name, argstr);
            }
            free(argstr);
            return;
        }
        /* 裸变量 */
        gen_varref(name);
        return;
    }
    if (is_op("(")) {
        pos++;
        gen_expr();
        expect_op(")");
        return;
    }
    fprintf(stderr, "imporc: 第 %d 行: 表达式位置出现意外 token '%s'\n",
            t->line, t->t == T_EOF ? "文件结束" : t->s);
    exit(1);
}
static void gen_unary(void) {
    if (is_op("-")) {
        pos++;
        sb_puts(OB, "rt_neg(");
        gen_unary();
        sb_puts(OB, ")");
        return;
    }
    if (is_op("not")) {
        pos++;
        sb_puts(OB, "rt_not(");
        gen_unary();
        sb_puts(OB, ")");
        return;
    }
    gen_primary();
}
/* 二元表达式：左操作数先入临时缓冲，遇运算符后组装 rt_x(a, b) */
static void gen_mul(void) {
    SB a, b;
    memset(&a, 0, sizeof(a)); memset(&b, 0, sizeof(b));
    SB *saved = OB;
    OB = &a; gen_unary(); OB = saved;
    while (1) {
        const char *fn = NULL;
        if (is_op("*")) fn = "rt_mul";
        else if (is_op("/")) fn = "rt_div";
        else if (is_op("%")) fn = "rt_mod";
        if (!fn) break;
        pos++;
        OB = &b; gen_unary(); OB = saved;
        SB t; memset(&t, 0, sizeof(t));
        sb_printf(&t, "%s(%s, %s)", fn, a.b ? a.b : "", b.b ? b.b : "");
        free(a.b); a = t;
        free(b.b); b.b = NULL; b.n = 0; b.cap = 0;
    }
    sb_puts(OB, a.b ? a.b : "");
    free(a.b); free(b.b);
}
static void gen_add(void) {
    SB a, b;
    memset(&a, 0, sizeof(a)); memset(&b, 0, sizeof(b));
    SB *saved = OB;
    OB = &a; gen_mul(); OB = saved;
    while (1) {
        const char *fn = NULL;
        if (is_op("+")) fn = "rt_add";
        else if (is_op("-")) fn = "rt_sub";
        if (!fn) break;
        pos++;
        OB = &b; gen_mul(); OB = saved;
        SB t; memset(&t, 0, sizeof(t));
        sb_printf(&t, "%s(%s, %s)", fn, a.b ? a.b : "", b.b ? b.b : "");
        free(a.b); a = t;
        free(b.b); b.b = NULL; b.n = 0; b.cap = 0;
    }
    sb_puts(OB, a.b ? a.b : "");
    free(a.b); free(b.b);
}
static void gen_cmp(void) {
    SB a, b;
    memset(&a, 0, sizeof(a)); memset(&b, 0, sizeof(b));
    SB *saved = OB;
    OB = &a; gen_add(); OB = saved;
    while (1) {
        if (!(is_op("==") || is_op("!=") || is_op("<") || is_op(">") || is_op("<=") || is_op(">=")))
            break;
        char op[4];
        strcpy(op, cur()->s);
        pos++;
        OB = &b; gen_add(); OB = saved;
        SB t; memset(&t, 0, sizeof(t));
        sb_printf(&t, "rt_cmp(%s, %s, \"%s\")", a.b ? a.b : "", b.b ? b.b : "", op);
        free(a.b); a = t;
        free(b.b); b.b = NULL; b.n = 0; b.cap = 0;
    }
    sb_puts(OB, a.b ? a.b : "");
    free(a.b); free(b.b);
}
static void gen_and(void) {
    SB a, b;
    memset(&a, 0, sizeof(a)); memset(&b, 0, sizeof(b));
    SB *saved = OB;
    OB = &a; gen_cmp(); OB = saved;
    while (is_op("and")) {
        pos++;
        OB = &b; gen_cmp(); OB = saved;
        SB t; memset(&t, 0, sizeof(t));
        sb_printf(&t, "rt_and(%s, %s)", a.b ? a.b : "", b.b ? b.b : "");
        free(a.b); a = t;
        free(b.b); b.b = NULL; b.n = 0; b.cap = 0;
    }
    sb_puts(OB, a.b ? a.b : "");
    free(a.b); free(b.b);
}
static void gen_or(void) {
    SB a, b;
    memset(&a, 0, sizeof(a)); memset(&b, 0, sizeof(b));
    SB *saved = OB;
    OB = &a; gen_and(); OB = saved;
    while (is_op("or")) {
        pos++;
        OB = &b; gen_and(); OB = saved;
        SB t; memset(&t, 0, sizeof(t));
        sb_printf(&t, "rt_or(%s, %s)", a.b ? a.b : "", b.b ? b.b : "");
        free(a.b); a = t;
        free(b.b); b.b = NULL; b.n = 0; b.cap = 0;
    }
    sb_puts(OB, a.b ? a.b : "");
    free(a.b); free(b.b);
}
static void gen_expr(void) { gen_or(); }

/* ---- 语句 ---- */
static void gen_block(void);
static void gen_func_body(Func *f) {
    /* 已在函数头后：locals 已在解析 var 时声明 */
    in_func_depth++;
    Func *saved = curf;
    curf = f;
    gen_block();
    curf = saved;
    in_func_depth--;
    sb_puts(OB, "return v_null();\n}\n");
}

static void gen_stmt(void) {
    Tok *t = cur();
    if (getenv("IMPORC_TRACE")) {
        fprintf(stderr, "[stmt] pos=%d line=%d type=%d tok='%s'\n",
                pos, t->line, t->t, t->s);
    }
    if (t->t == T_NL) { pos++; return; }
    if (t->t == T_IND) { err_here("意外的缩进开始"); }
    if (t->t == T_DED) { err_here("意外的缩进结束"); }
    if (t->t == T_EOF) return;

    if ((t->t == T_OP || t->t == T_ID) && strcmp(t->s, "var") == 0) {
        pos++;
        if (cur()->t != T_ID) err_here("var 后需要变量名");
        char name[128];
        strcpy(name, cur()->s);
        pos++;
        if (curf) {
            add_local(curf, name);
            sb_printf(OB, "Val l_%s", name);
        } else {
            add_global(name);
            sb_printf(OB, "g_%s", name);
        }
        if (is_op("=")) {
            pos++;
            sb_puts(OB, " = ");
            gen_expr();
        } else {
            sb_puts(OB, " = v_null()");
        }
        sb_puts(OB, ";\n");
        skip_nl();
        return;
    }
    if (strcmp(t->s, "set") == 0) {
        pos++;
        if (cur()->t != T_ID) err_here("set 后需要变量名");
        char name[128];
        strcpy(name, cur()->s);
        pos++;
        if (!is_op("=")) err_here("set 后需要 =");
        pos++;
        if (curf && has_local(curf, name)) sb_printf(OB, "l_%s = ", name);
        else {
            add_global(name);
            sb_printf(OB, "g_%s = ", name);
        }
        gen_expr();
        sb_puts(OB, ";\n");
        skip_nl();
        return;
    }
    if (strcmp(t->s, "func") == 0 || strcmp(t->s, "def") == 0) {
        pos++;
        if (cur()->t != T_ID) err_here("func 后需要函数名");
        char name[128];
        strcpy(name, cur()->s);
        pos++;
        expect_op("(");
        Func *f = find_func(name);
        if (!f) err_here("内部错误：函数未预登记");
        f->nparams = 0;
        skip_nl();
        while (!is_op(")")) {
            if (cur()->t != T_ID) err_here("参数名应为标识符");
            strcpy(f->params[f->nparams], cur()->s);
            add_local(f, cur()->s);
            f->nparams++;
            pos++;
            skip_nl();
            if (is_op(",")) { pos++; skip_nl(); }
            else break;
        }
        expect_op(")");
        skip_nl();
        /* 函数定义输出到独立 fndefs 段（不能进 main 体内） */
        SB *saved_out = OB;
        OB = &fndefs;
        sb_printf(OB, "static Val c_%s(", name);
        for (int i = 0; i < f->nparams; i++) {
            if (i) sb_puts(OB, ", ");
            sb_printf(OB, "Val l_%s", f->params[i]);
        }
        sb_puts(OB, ") {\n");
        /* 函数体块 */
        in_func_depth++;
        Func *saved = curf;
        curf = f;
        gen_block();
        curf = saved;
        in_func_depth--;
        sb_puts(OB, "return v_null();\n}\n");
        OB = saved_out;
        return;
    }
    if (strcmp(t->s, "if") == 0) {
        pos++;
        sb_puts(OB, "if (rt_truth(");
        gen_expr();
        sb_puts(OB, ")) {\n");
        gen_block();
        sb_puts(OB, "}\n");
        while (1) {
            if (is_op("elif") || is_op("elseif")) {
                pos++;
                sb_puts(OB, "else if (rt_truth(");
                gen_expr();
                sb_puts(OB, ")) {\n");
                gen_block();
                sb_puts(OB, "}\n");
            } else if (is_op("else")) {
                pos++;
                sb_puts(OB, "else {\n");
                gen_block();
                sb_puts(OB, "}\n");
                break;
            } else break;
        }
        return;
    }
    if (strcmp(t->s, "while") == 0) {
        pos++;
        sb_puts(OB, "while (rt_truth(");
        gen_expr();
        sb_puts(OB, ")) {\n");
        gen_block();
        sb_puts(OB, "}\n");
        return;
    }
    if (strcmp(t->s, "for") == 0) {
        pos++;
        if (cur()->t != T_ID) err_here("for 后需要循环变量名");
        char vname[128];
        strcpy(vname, cur()->s);
        pos++;
        if (!is_op("in")) err_here("for 中需要 in");
        pos++;
        if (!is_op("range")) err_here("for 目前仅支持 range(...)");
        pos++;
        expect_op("(");
        skip_nl();
        int fid = for_id++;
        int nr = 0;
        /* 第一个参数 */
        if (is_op(")")) {
            err_here("range() 至少需要一个参数");
        }
        sb_printf(OB, "{ Val __f%di = ", fid);
        gen_expr();
        sb_puts(OB, "; ");
        nr = 1;
        skip_nl();
        if (is_op(",")) {
            pos++; skip_nl();
            sb_printf(OB, "Val __f%de = ", fid);
            gen_expr();
            sb_puts(OB, "; ");
            nr = 2;
            skip_nl();
            if (is_op(",")) {
                pos++; skip_nl();
                sb_printf(OB, "Val __f%ds = ", fid);
                gen_expr();
                sb_puts(OB, "; ");
                nr = 3;
                skip_nl();
                if (is_op(",")) err_here("range() 最多三个参数");
            } else {
                sb_printf(OB, "Val __f%ds = v_int(1); ", fid);
            }
        } else {
            sb_printf(OB, "Val __f%de = v_int(0); ", fid);
            sb_printf(OB, "Val __f%ds = v_int(1); ", fid);
        }
        expect_op(")");
        skip_nl();
        /* 循环变量作用域 */
        if (curf) add_local(curf, vname);
        else add_global(vname);
        const char *lv = curf ? "l_" : "g_";
        /* body 只解析一次，存入临时缓冲，输出两次（正/负步长分支） */
        SB tbody;
        memset(&tbody, 0, sizeof(tbody));
        {
            SB *saved = OB;
            OB = &tbody;
            gen_block();
            OB = saved;
        }
        sb_printf(OB, "if (rt_num(__f%ds) >= 0) {\n", fid);
        sb_printf(OB, "  while (rt_cmp_i(__f%di, __f%de) < 0) {\n", fid, fid);
        sb_printf(OB, "    %s%s = __f%di;\n", lv, vname, fid);
        sb_puts(OB, tbody.b ? tbody.b : "");
        sb_printf(OB, "    __f%di = rt_add(__f%di, __f%ds); }\n", fid, fid, fid);
        sb_puts(OB, "} else {\n");
        sb_printf(OB, "  while (rt_cmp_i(__f%di, __f%de) > 0) {\n", fid, fid);
        sb_printf(OB, "    %s%s = __f%di;\n", lv, vname, fid);
        sb_puts(OB, tbody.b ? tbody.b : "");
        sb_printf(OB, "    __f%di = rt_add(__f%di, __f%ds); }\n", fid, fid, fid);
        sb_puts(OB, "} }\n");
        free(tbody.b);
        return;
    }
    if (strcmp(t->s, "return") == 0) {
        pos++;
        skip_nl();
        if (cur()->t == T_NL || cur()->t == T_DED || cur()->t == T_EOF) {
            sb_puts(OB, "return v_null();\n");
        } else {
            sb_puts(OB, "return ");
            gen_expr();
            sb_puts(OB, ";\n");
        }
        skip_nl();
        return;
    }
    if (strcmp(t->s, "break") == 0) {
        pos++;
        sb_puts(OB, "break;\n");
        skip_nl();
        return;
    }
    if (strcmp(t->s, "continue") == 0) {
        pos++;
        sb_puts(OB, "continue;\n");
        skip_nl();
        return;
    }
    if (strcmp(t->s, "print") == 0) {
        pos++;
        expect_op("(");
        skip_nl();
        int first = 1;
        while (!is_op(")")) {
            if (!first) sb_puts(OB, "rt_print_sep(); ");
            sb_puts(OB, "rt_print_val(");
            gen_expr();
            sb_puts(OB, "); ");
            first = 0;
            skip_nl();
            if (is_op(",")) { pos++; skip_nl(); }
            else break;
        }
        expect_op(")");
        sb_puts(OB, "rt_print_nl();\n");
        skip_nl();
        return;
    }
    /* 表达式语句（函数调用） */
    if (t->t == T_ID) {
        char name[128];
        strcpy(name, t->s);
        pos++;
        if (is_op("(")) {
            int nargs = 0;
            SB a; memset(&a, 0, sizeof(a));
            pos++;
            skip_nl();
            while (!is_op(")")) {
                if (nargs > 0) sb_puts(&a, ", ");
                size_t m0 = OB->n;
                gen_expr();
                /* 截取刚生成的表达式文本 */
                /* OB->b[m0..OB->n] */
                /* 移到 a 中 */
                size_t len = OB->n - m0;
                char *tmp = (char *)malloc(len + 1);
                memcpy(tmp, OB->b + m0, len);
                tmp[len] = '\0';
                OB->n = m0;
                OB->b[m0] = '\0';
                sb_puts(&a, tmp);
                free(tmp);
                nargs++;
                skip_nl();
                if (is_op(",")) { pos++; skip_nl(); }
                else break;
            }
            expect_op(")");
            if (strcmp(name, "print") == 0) err_here("print 已按语句处理");
            int bn = 0;
            const char *rtn = bi_rt(name, &bn);
            if (rtn) {
                if (nargs != bn) {
                    fprintf(stderr, "imporc: 第 %d 行: 内建 %s 需要 %d 个参数，给了 %d 个\n",
                            cur()->line, name, bn, nargs);
                    exit(1);
                }
                sb_printf(OB, "%s(%s);\n", rtn, a.b ? a.b : "");
            } else {
                Func *f = find_func(name);
                if (!f) {
                    fprintf(stderr, "imporc: 第 %d 行: 未定义函数：%s\n", cur()->line, name);
                    exit(1);
                }
                if (nargs != f->nparams) {
                    fprintf(stderr, "imporc: 第 %d 行: 函数 %s 需要 %d 个参数，给了 %d 个\n",
                            cur()->line, name, f->nparams, nargs);
                    exit(1);
                }
                sb_printf(OB, "c_%s(%s);\n", name, a.b ? a.b : "");
            }
            skip_nl();
            return;
        }
        fprintf(stderr, "imporc: 第 %d 行: 表达式语句必须是函数调用（裸变量不可作为语句）\n",
                t->line);
        exit(1);
    }
    fprintf(stderr, "imporc: 第 %d 行: 无法识别的语句 '%s'\n", t->line,
            t->t == T_EOF ? "文件结束" : t->s);
    exit(1);
}

static void gen_block(void) {
    /* 进入块：跳过行尾 NL，再消费 INDENT */
    while (cur()->t == T_NL) pos++;
    if (cur()->t == T_IND) pos++;
    else if (cur()->t != T_DED && cur()->t != T_EOF) {
        /* 顶层块模式：直接语句 */
    }
    while (1) {
        Tok *t = cur();
        if (t->t == T_EOF) break;
        if (t->t == T_DED) { pos++; break; }
        if (t->t == T_NL) { pos++; continue; }
        gen_stmt();
    }
}

/* ================= 主流程 ================= */
static char *read_file(const char *path, long *out_len) {
    FILE *f = fopen(path, "rb");
    if (!f) { fprintf(stderr, "imporc: 无法打开源文件 %s\n", path); return NULL; }
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    char *buf = (char *)malloc((size_t)n + 1);
    size_t got = fread(buf, 1, (size_t)n, f);
    buf[got] = '\0';
    fclose(f);
    *out_len = (long)got;
    return buf;
}

static char *find_file(const char *cand0, const char *cand1, const char *cand2) {
    static char path[4096];
    const char *cs[3];
    cs[0] = cand0; cs[1] = cand1; cs[2] = cand2;
    for (int i = 0; i < 3; i++) {
        if (cs[i] && cs[i][0]) {
            FILE *f = fopen(cs[i], "rb");
            if (f) {
                fclose(f);
                strcpy(path, cs[i]);
                /* 统一为反斜杠（cmd/system 兼容） */
                for (char *q = path; *q; q++)
                    if (*q == '/') *q = '\\';
                return path;
            }
        }
    }
    return NULL;
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr,
                "imporc —— Impor (I++) 自举 C 编译器 v2.14\n"
                "用法：\n"
                "  imporc build 源文件.impor [-o 输出.exe]\n");
        return 2;
    }
    const char *cmd = argv[1];
    if (strcmp(cmd, "build") != 0) {
        fprintf(stderr, "imporc: 未知命令 %s（支持 build）\n", cmd);
        return 2;
    }
    if (argc < 3) { fprintf(stderr, "imporc: build 需要源文件\n"); return 2; }
    const char *in = argv[2];
    const char *out = NULL;
    for (int i = 3; i < argc - 1; i++) {
        if (strcmp(argv[i], "-o") == 0) { out = argv[i + 1]; i++; }
    }
    if (!out) {
        static char ob[4096];
        strcpy(ob, in);
        char *dot = strrchr(ob, '.');
        if (dot && strcmp(dot, ".impor") == 0) strcpy(dot, ".exe");
        else strcat(ob, ".exe");
        out = ob;
    }

    /* 1. 读源文件 */
    long srclen = 0;
    char *src = read_file(in, &srclen);
    if (!src) return 1;

    /* 2. 定位运行库与 tcc */
    /* 取 argv[0] 目录 */
    static char selfdir[4096];
    strcpy(selfdir, argv[0]);
    char *slash = strrchr(selfdir, '/');
    char *bslash = strrchr(selfdir, '\\');
    char *last = slash > bslash ? slash : bslash;
    if (last) *last = '\0';
    else strcpy(selfdir, ".");
    char runtime_path[4096], tcc_path[4096];
    {
        char c0[4096], c1[4096], c2[4096];
        snprintf(c0, sizeof(c0), "%s/c_runtime.c", selfdir);
        snprintf(c1, sizeof(c1), "%s/../c_runtime.c", selfdir);
        snprintf(c2, sizeof(c2), "c_runtime.c");
        char *rp = find_file(c0, c1, c2);
        if (!rp) {
            fprintf(stderr, "imporc: 找不到共享运行库 c_runtime.c（应与 imporc.exe 同目录或上级目录）\n");
            return 1;
        }
        strcpy(runtime_path, rp);
    }
    {
        char c0[4096], c1[4096], c2[4096];
        snprintf(c0, sizeof(c0), "%s/tcc/win32/tcc.exe", selfdir);
        snprintf(c1, sizeof(c1), "%s/tcc/tcc.exe", selfdir);
        snprintf(c2, sizeof(c2), "tcc");
        char *tp = find_file(c0, c1, c2);
        if (!tp) {
            fprintf(stderr, "imporc: 找不到 C 编译器 tcc（应位于 imporc.exe 同目录的 tcc/win32/ 或加入 PATH）\n");
            return 1;
        }
        strcpy(tcc_path, tp);
    }

    /* 3. 预扫描：收集全局变量与函数签名（顶层缩进 0） */
    {
        const char *p = src;
        int line = 1;
        while (*p) {
            const char *ls = p;
            while (*p && *p != '\n') p++;
            size_t llen = (size_t)(p - ls);
            if (*p == '\n') p++;
            if (llen > 0 && ls[llen - 1] == '\r') llen--;
            char *ln = (char *)malloc(llen + 1);
            memcpy(ln, ls, llen);
            ln[llen] = '\0';
            line++;
            /* 缩进 */
            int i = 0, ind = 0;
            while (i < (int)llen && ln[i] == ' ') { ind++; i++; }
            while (i < (int)llen && ln[i] == '\t') { ind += 4; i++; }
            while (i < (int)llen && (ln[i] == ' ' || ln[i] == '\t')) i++;
            if (ind == 0) {
                if (strncmp(ln + i, "important<<", 11) == 0) { free(ln); continue; }
                if (strncmp(ln + i, "var ", 4) == 0) {
                    char nm[128]; int bi = 0;
                    i += 4;
                    while (i < (int)llen && ln[i] != ' ' && ln[i] != '\t' && ln[i] != '=' &&
                           ln[i] != '\n' && ln[i] != '\r') {
                        if (bi < 126) nm[bi++] = ln[i];
                        i++;
                    }
                    nm[bi] = '\0';
                    if (bi > 0) add_global(nm);
                } else if (strncmp(ln + i, "func ", 5) == 0 || strncmp(ln + i, "def ", 4) == 0) {
                    int o = (ln[i] == 'f') ? 5 : 4;
                    char nm[128]; int bi = 0;
                    i += o;
                    while (i < (int)llen && ln[i] != '(' && ln[i] != ' ' && ln[i] != '\t') {
                        if (bi < 126) nm[bi++] = ln[i];
                        i++;
                    }
                    nm[bi] = '\0';
                    if (bi > 0 && !find_func(nm)) {
                        if (nfuncs >= MAXFN) { fprintf(stderr, "imporc: 函数过多\n"); exit(1); }
                        strcpy(funcs[nfuncs].name, nm);
                        funcs[nfuncs].nparams = 0;
                        funcs[nfuncs].nlocals = 0;
                        nfuncs++;
                    }
                }
            }
            free(ln);
        }
    }

    /* 4. 词法 + 转译 */
    lex(src);
    free(src);
    memset(&body, 0, sizeof(body));
    pos = 0;
    tk = toks;
    curf = NULL;
    gen_block();  /* 顶层 */

    /* 5. 组装 C 源码 */
    SB csrc;
    memset(&csrc, 0, sizeof(csrc));
    sb_puts(&csrc, "/* 由 imporc（Impor 自举 C 编译器）生成 */\n");
    long rlen = 0;
    char *rt = read_file(runtime_path, &rlen);
    if (!rt) return 1;
    sb_puts(&csrc, rt);
    free(rt);
    /* 前向声明 */
    for (int i = 0; i < nfuncs; i++) {
        sb_printf(&csrc, "static Val c_%s(", funcs[i].name);
        for (int j = 0; j < funcs[i].nparams; j++) {
            if (j) sb_puts(&csrc, ", ");
            sb_puts(&csrc, "Val");
        }
        sb_puts(&csrc, ");\n");
    }
    /* 全局变量 */
    for (int i = 0; i < nglob; i++) {
        sb_printf(&csrc, "Val g_%s;\n", globals[i]);
    }
    /* 函数定义（文件作用域） */
    sb_puts(&csrc, fndefs.b ? fndefs.b : "");
    free(fndefs.b);
    sb_printf(&csrc, "int main(int argc, char **argv) {\n    (void)argc; (void)argv;\n");
    sb_puts(&csrc, OB->b ? OB->b : "");
    sb_puts(&csrc, "    return 0;\n}\n");
    free(OB->b);

    /* 6. 写 .c + 调 tcc */
    char cpath[4096];
    {
        snprintf(cpath, sizeof(cpath), "%s.c", out);
        FILE *f = fopen(cpath, "wb");
        if (!f) { fprintf(stderr, "imporc: 无法写入 %s\n", cpath); return 1; }
        fwrite(csrc.b, 1, csrc.n, f);
        fclose(f);
    }
    free(csrc.b);
    {
        char cmdline[8192];
        snprintf(cmdline, sizeof(cmdline), "\"%s\" \"%s\" -o \"%s\"", tcc_path, cpath, out);
        fprintf(stderr, "imporc: tcc 命令: %s\n", cmdline);
        int rc = run_cmd(cmdline);
        if (rc != 0) {
            fprintf(stderr, "imporc: tcc 编译失败（退出码 %d），中间文件保留在 %s\n", rc, cpath);
            return 1;
        }
        remove(cpath);
    }
    printf("imporc: 已生成独立可执行文件 %s（原生 C 编译，运行不依赖 Python）\n", out);
    return 0;
}
