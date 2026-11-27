# -*- coding: utf-8 -*-
"""生成 unicode_case 表：同一份数据同时写进 Python 侧与 C 运行时，保证三端一致。

口径（写进文档，不假装全覆盖）：
- 覆盖 ASCII、拉丁-1 补充、拉丁扩展 A/B、希腊、西里尔、全角字母；
- 只收录 **1 对 1** 的简单大小写映射；
- 不做 ß→SS、ﬁ→FI、土耳其语 i 这类一对多/语境相关映射（两端都保持原字符，行为一致）。
"""
import io, os

ROOT = r"C:\Users\41482\AppData\Local\Temp\impor431p7"
RANGES = [(0x0041, 0x005A), (0x0061, 0x007A),
          (0x00C0, 0x024F), (0x0370, 0x03FF), (0x0400, 0x04FF),
          (0xFF21, 0xFF3A), (0xFF41, 0xFF5A)]


def collect():
    up, low = {}, {}
    for lo, hi in RANGES:
        for cp in range(lo, hi + 1):
            ch = chr(cp)
            u = ch.upper()
            l = ch.lower()
            if len(u) == 1 and u != ch:
                up[cp] = ord(u)
            if len(l) == 1 and l != ch:
                low[cp] = ord(l)
    return up, low


def write_py(up, low):
    path = os.path.join(ROOT, "impor", "unicode_case.py")
    lines = [
        "# -*- coding: utf-8 -*-",
        '"""Impor 4.5.0 —— 大小写映射表（由 tools/gen_unicode_case.py 生成，勿手改）。',
        "",
        "解释器与 C 运行时用**同一张表**做 upper/lower，所以三端结果逐字符一致：",
        "覆盖 ASCII / 拉丁-1 / 拉丁扩展 A-B / 希腊 / 西里尔 / 全角字母，只收录 1:1 映射；",
        "ß→SS、ﬁ→FI、土耳其语 i 这类一对多或语境相关映射不做（两端都保持原字符）。",
        '"""',
        "",
        "UPPER_MAP = {",
    ]
    for cp in sorted(up):
        lines.append("    0x%04X: 0x%04X," % (cp, up[cp]))
    lines.append("}")
    lines.append("")
    lines.append("LOWER_MAP = {")
    for cp in sorted(low):
        lines.append("    0x%04X: 0x%04X," % (cp, low[cp]))
    lines.append("}")
    lines.append("")
    lines.append("RANGE_LABEL = \"ASCII / 拉丁-1 / 拉丁扩展 A-B / 希腊 / 西里尔 / 全角\"")
    lines.append("")
    lines.append("""
def _apply(s, table):
    out = []
    for ch in s:
        cp = ord(ch)
        to = table.get(cp)
        out.append(chr(to) if to is not None else ch)
    return "".join(out)


def upper(s):
    return _apply(s, UPPER_MAP)


def lower(s):
    return _apply(s, LOWER_MAP)
""")
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    return path


def write_c(up, low):
    path = os.path.join(ROOT, "impor", "c_runtime.c")
    with io.open(path, "r", encoding="utf-8", newline="") as f:
        src = f.read()
    if "rt_uc_upper_from" in src:
        return "skip"
    keys = sorted(up)
    lows = sorted(low)
    body = ["", "/* ===== 4.5.0：Unicode 简单大小写映射（同 impor/unicode_case.py 的表） =====",
            "/* 只覆盖 1:1 映射；二分查找，未命中的码点原样保留。 */",
            "#define RT_UC_N %d" % len(keys),
            "static const unsigned int rt_uc_upper_from[] = {",
            "    " + ", ".join("0x%04X" % k for k in keys),
            "};",
            "static const unsigned int rt_uc_upper_to[] = {",
            "    " + ", ".join("0x%04X" % up[k] for k in keys),
            "};",
            "#define RT_UC_LN %d" % len(lows),
            "static const unsigned int rt_uc_lower_from[] = {",
            "    " + ", ".join("0x%04X" % k for k in lows),
            "};",
            "static const unsigned int rt_uc_lower_to[] = {",
            "    " + ", ".join("0x%04X" % low[k] for k in lows),
            "};", "",
            "static unsigned int rt_uc_lookup(const unsigned int *from, const unsigned int *to,",
            "                                 int n, unsigned int cp) {",
            "    int lo = 0, hi = n - 1;",
            "    while (lo <= hi) {",
            "        int mid = (lo + hi) / 2;",
            "        if (from[mid] == cp) return to[mid];",
            "        if (from[mid] < cp) lo = mid + 1; else hi = mid - 1;",
            "    }",
            "    return cp;",
            "}", "",
            "/* UTF-8 解码一个码点；*len 返回字节数 */",
            "static unsigned int rt_uc_decode(const unsigned char *p, int *len) {",
            "    unsigned int cp;",
            "    if (p[0] < 0x80) { *len = 1; return p[0]; }",
            "    if ((p[0] & 0xE0) == 0xC0) { cp = p[0] & 0x1F; *len = 2; }",
            "    else if ((p[0] & 0xF0) == 0xE0) { cp = p[0] & 0x0F; *len = 3; }",
            "    else if ((p[0] & 0xF8) == 0xF0) { cp = p[0] & 0x07; *len = 4; }",
            "    else { *len = 1; return p[0]; }",
            "    {",
            "        int i;",
            "        for (i = 1; i < *len; i++) {",
            "            if ((p[i] & 0xC0) != 0x80) { *len = 1; return p[0]; }",
            "            cp = (cp << 6) | (p[i] & 0x3F);",
            "        }",
            "    }",
            "    return cp;",
            "}", "",
            "static void rt_uc_encode(unsigned int cp, char *out, int *n) {",
            "    if (cp < 0x80) { out[0] = (char)cp; *n = 1; }",
            "    else if (cp < 0x800) {",
            "        out[0] = (char)(0xC0 | (cp >> 6));",
            "        out[1] = (char)(0x80 | (cp & 0x3F)); *n = 2;",
            "    } else if (cp < 0x10000) {",
            "        out[0] = (char)(0xE0 | (cp >> 12));",
            "        out[1] = (char)(0x80 | ((cp >> 6) & 0x3F));",
            "        out[2] = (char)(0x80 | (cp & 0x3F)); *n = 3;",
            "    } else {",
            "        out[0] = (char)(0xF0 | (cp >> 18));",
            "        out[1] = (char)(0x80 | ((cp >> 12) & 0x3F));",
            "        out[2] = (char)(0x80 | ((cp >> 6) & 0x3F));",
            "        out[3] = (char)(0x80 | (cp & 0x3F)); *n = 4;",
            "    }",
            "}", "",
            "static Val rt_uc_map(Val v, int to_upper) {",
            "    const char *s = rt_str_of(v);",
            "    size_t blen = strlen(s);",
            "    char *buf = (char *)malloc(blen * 2 + 1);",
            "    size_t o = 0;",
            "    const unsigned char *p = (const unsigned char *)s;",
            "    if (!buf) rt_abort(\"内存不足\");",
            "    while (*p) {",
            "        int clen = 1, elen = 1;",
            "        unsigned int cp = rt_uc_decode(p, &clen);",
            "        unsigned int out;",
            "        char enc[4];",
            "        if (to_upper) out = rt_uc_lookup(rt_uc_upper_from, rt_uc_upper_to, RT_UC_N, cp);",
            "        else out = rt_uc_lookup(rt_uc_lower_from, rt_uc_lower_to, RT_UC_LN, cp);",
            "        rt_uc_encode(out, enc, &elen);",
            "        memcpy(buf + o, enc, (size_t)elen);",
            "        o += (size_t)elen;",
            "        p += clen;",
            "    }",
            "    buf[o] = '\\0';",
            "    {",
            "        Val r = v_str(buf);",
            "        free(buf);",
            "        return r;",
            "    }",
            "}", ""]
    with io.open(path, "w", encoding="utf-8", newline="") as f:
        f.write(src.rstrip("\n") + "\n" + "\n".join(body) + "\n")
    return "appended upper=%d lower=%d" % (len(keys), len(lows))


if __name__ == "__main__":
    up, low = collect()
    print(write_py(up, low))
    print(write_c(up, low))
