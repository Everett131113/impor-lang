# -*- coding: utf-8 -*-
"""
Impor (I++) 词法分析器。

Token 类型：
  OP, NUM, STR, ID, KEY, EOF, INDENT, DEDENT, NEWLINE

关键字（真正的保留字）：
  if else elseif while for in def func var set return true false
  and or not break continue import as try catch finally nonlocal
  global class new super exception raise loop exit cls

说明：
  - 支持 // 与 # 单行注释，/* */ 多行注释（可跨行）
  - 支持三引号多行字符串（可跨行）
  - 数字：整数保持 int，带小数点为 float
  - 字符串：单引号 / 双引号（支持 \\n \\t \\\\ \\\" \\' 转义）
  - 原始字符串：r"..." / r'...'（关闭转义解析，反斜杠原样保留）
  - 头部标记 important<<DAY<<english("English"): 由调用方在解析前剥离
"""

TOK_OP, TOK_NUM, TOK_STR, TOK_ID, TOK_KEY = "OP", "NUM", "STR", "ID", "KEY"
TOK_EOF, TOK_INDENT, TOK_DEDENT, TOK_NEWLINE = "EOF", "INDENT", "DEDENT", "NEWLINE"
from .errors import ImporSyntaxError, hint_for
import os
TOK_FSTR = "FSTR"  # v2.9 字符串插值："x = {expr}"

# v5.3 兼容层：旧中文关键字 → 英文关键字（默认关闭，仅 IMPOR_LEGACY_CN=1 或 --legacy-cn 时启用）
CN_TO_EN = {
    "如果": "if", "否则": "else", "否则如果": "elseif", "否则若": "elseif", "否则当": "elseif",
    "当": "while", "循环": "loop", "对于": "for", "遍历": "for", "每": "for", "重复": "for",
    "在": "in", "打断": "break", "继续": "continue",
    "函数": "func", "定义": "def", "变量": "var", "设置": "set", "为": "set", "将": "set",
    "把": "set", "让": "set", "类": "class", "新": "new", "异常": "exception",
    "导出": "export", "导入": "import", "从": "from", "用": "use",
    "返回": "return", "真": "true", "假": "false", "并且": "and", "且": "and",
    "或者": "or", "或": "or", "非": "not", "尝试": "try", "捕获": "catch",
    "最终": "finally", "抛出": "raise", "退出": "exit", "父类": "super",
    "全局": "global", "非局部": "nonlocal", "断言": "assert",
    "空": "none", "无": "none", "只要": "while", "直到": "until",
    "静态": "static", "只读": "readonly", "产出": "yield",
    "异步": "async", "等待": "await", "此": "this", "实例": "this",
    "打印": "print",
}


def legacy_cn_enabled():
    return os.environ.get("IMPOR_LEGACY_CN", "").strip().lower() in ("1", "true", "yes", "on")
TOK_DOC = "DOC"    # v3.5 文档注释：以 `##` 开头的整行，供 hover / impor doc 使用

KEYWORDS = {
    "if", "else", "elseif", "while", "for", "in",
    "def", "func", "var", "set", "return",
    "true", "false", "and", "or", "not",
    "break", "continue", "import", "as",
    "try", "catch", "finally",
    "nonlocal", "global", "class", "new", "super",
    "exception", "raise", "loop", "exit", "cls",
    "from", "export", "assert", "use",
    "yield", "with",  # v3.8.4：保留但未实现，语法阶段必须明确拒绝
    "const", "switch",  # v2.17：只读常量声明；match 的 C 风格别名
    # v2.18：新增关键字（目标 120 个保留字）——别名 / 字面量 / 预留特性
    # 注：fn / repeat 为软关键字（语句位置特殊解析，其他位置可作标识符，避免撞 std 模块与内建）
    "local", "elif", "until",
    "case", "default", "nil", "null", "none",
    # v3.7.2：以下 6 个改为软关键字（仍保留语法，但可作标识符）。
    # 从 KEYWORDS 移除后，parser 按上下文判断，未命中语法时按普通 ID 处理。
    # static / readonly / async / await / this / defer
    # v5.2：移除全部中文关键字，新增英文保留字（当前无语法，先占位保留）
    "interface", "namespace", "public", "private", "protected",
    "abstract", "override", "implements",
    "sealed",
}

# v3.7.2：同义关键字收敛。左边是 deprecated 别名，右边是唯一推荐主写法。
# fmt 只输出主写法；LSP 补全只推主写法；规范/教程/conformance 以主写法为准。
DEPRECATED_ALIASES = {
    "func": "def",
    "elseif": "elif",
    "switch": "match",
    "loop": "while",
    "let": "var",
}
DEPRECATED_KEYWORDS = frozenset(DEPRECATED_ALIASES)
CANONICAL_KEYWORDS = frozenset((KEYWORDS - DEPRECATED_KEYWORDS)
                               | set(DEPRECATED_ALIASES.values()))
# v3.7.2：软关键字——保留语法语义，但不在词法层封锁，可作普通标识符。
SOFT_KEYWORDS = frozenset({"static", "readonly", "async", "await", "this", "defer"})
# 其中只有 static / readonly / this 有可用语义（const 别名 / self 别名）；
# async / await / defer 尚未实现，必须在语法阶段拒绝，不能静默当标识符。
IMPLEMENTED_SOFT_KEYWORDS = frozenset({"static", "readonly", "this"})
UNIMPLEMENTED_SOFT_KEYWORDS = frozenset({"async", "await", "defer"})
# 预留但无运行时语义的保留字：出现即拒绝，避免“写了像支持”的静默接受。
UNIMPLEMENTED_RESERVED_KEYWORDS = frozenset({
    "interface", "namespace", "abstract", "override", "implements", "sealed",
    "final", "yield", "with",
})
UNIMPLEMENTED_KEYWORDS = UNIMPLEMENTED_SOFT_KEYWORDS | UNIMPLEMENTED_RESERVED_KEYWORDS
from .builtin_manifest import ALL_BUILTINS

BUILTIN_NAMES = frozenset(ALL_BUILTINS)

TWO_CHAR_OPS = ("==", "!=", ">=", "<=", "+=", "-=", "*=", "/=", "%=", "|>",
                "..", "->", "++", "--", "&&", "||", "<>", ":=")  # v3.4 宽容语法别名
SINGLE_OPS = "+-*/%<>()=:,[]{}!?.;"

_ESCAPE_MAP = {'n': '\n', 't': '\t', 'r': '\r', '\\': '\\', '"': '"', "'": "'",
               '{': '{', '}': '}'}  # v2.9：\{ \} 转义为字面花括号


class Token:
    __slots__ = ("type", "value", "line", "col", "src")
    def __init__(self, typ, value, line, col=0, src=None):
        self.type = typ
        self.value = value
        self.line = line
        self.col = col
        self.src = src      # v3.5：原始写法（中文软别名映射前），供风格检查用

    def __repr__(self):
        return f"Token({self.type}, {self.value!r}, line={self.line}, col={self.col})"


def unescape_impor(s):
    """处理双/单引号字符串中的转义。"""
    buf, i, n = [], 0, len(s)
    while i < n:
        c = s[i]
        if c == '\\':
            i += 1
            if i >= n:
                buf.append('\\')
                break
            buf.append(_ESCAPE_MAP.get(s[i], '\\' + s[i]))
            i += 1
        else:
            buf.append(c)
            i += 1
    return "".join(buf)


def _scan_number(s, i, n):
    buf = []
    seen_dot = False
    while i < n:
        c = s[i]
        if c.isdigit():
            buf.append(c); i += 1
        elif c == '_' and i + 1 < n and s[i + 1].isdigit():
            # v2.17 数字下划线：1_000_000 → 1000000（_ 不得出现在数字尾部/小数点后）
            i += 1
        elif c == '.' and not seen_dot and i + 1 < n and s[i + 1] != '.':
            # v3.4：`1..5` 不把第一个点当小数点（否则会被拆成 1.0 与 .5）
            seen_dot = True; buf.append(c); i += 1
        else:
            break
    text = "".join(buf)
    if text == ".":
        return ".", i
    val = int(text) if not seen_dot else float(text)
    return val, i


def _scan_line(tokens, s, line_no):
    """
    扫描一行文本，产出 token 到 tokens。
    若行尾处于多行注释/多行字符串中，返回多行状态：
      ('comment', None)
      ('str', qm, [已积累内容])
    否则返回 None。
    """
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == '#':
            break
        if i + 1 < n and s[i] == '/' and s[i + 1] == '/':
            break
        if i + 1 < n and s[i] == '/' and s[i + 1] == '*':
            j = s.find('*/', i + 2)
            if j == -1:
                return ('comment', None)
            i = j + 2
            continue
        if c.isspace():
            i += 1
            continue
        if i + 2 < n and ((s[i] == '"' and s[i+1] == '"' and s[i+2] == '"')
                          or (s[i] == "'" and s[i+1] == "'" and s[i+2] == "'")):
            qm = s[i:i+3]
            j = s.find(qm, i + 3)
            if j == -1:
                return ('str', qm, [s[i+3:] + "\n"])
            tokens.append(Token(TOK_STR, s[i+3:j], line_no, i + 1))
            i = j + 3
            continue
        if c in ('"', "'"):
            col0 = i + 1
            i += 1
            buf = []
            parts = None  # v2.9 字符串插值段列表；None = 普通字符串
            while i < n:
                ch = s[i]
                if ch == '\\' and i + 1 < n:
                    # 转义：跳过下一字符，防止 \" \' 提前截断字符串
                    buf.append(ch)
                    buf.append(s[i + 1])
                    i += 2
                    continue
                if ch == c:
                    break
                if ch == '{' and s[i - 1:i] != '\\':
                    # 插值开始：{表达式}（{ 前无反斜杠；} 必须在本字符串闭合引号之前，
                    # 防止与后续字符串字面量里的 } 跨串配对）
                    q_end = i + 1
                    while q_end < n:
                        if s[q_end] == c and s[q_end - 1] != '\\':
                            break
                        q_end += 1
                    j = s.find('}', i + 1, q_end if q_end < n else n)
                    if j == -1:
                        buf.append(ch); i += 1
                        continue
                    if parts is None:
                        parts = []
                    parts.append(('text', "".join(buf)))
                    buf = []
                    parts.append(('expr', s[i + 1:j]))
                    i = j + 1
                    continue
                buf.append(ch); i += 1
            i += 1
            if parts is not None:
                parts.append(('text', "".join(buf)))
                tokens.append(Token(TOK_FSTR, parts, line_no, col0))
            else:
                tokens.append(Token(TOK_STR, unescape_impor("".join(buf)), line_no, col0))
            continue
        if c in ('r', 'R') and i + 1 < n and s[i + 1] in ('"', "'"):
            # 原始字符串 r"..." / r'...'：关闭转义解析，反斜杠原样保留；
            # 但 \" 仍不结束字符串（反斜杠与引号都保留，Python 同规则）
            q = s[i + 1]
            col0 = i + 1
            i += 2
            buf = []
            while i < n:
                ch = s[i]
                if ch == '\\' and i + 1 < n and s[i + 1] == q:
                    buf.append(ch)
                    buf.append(q)
                    i += 2
                    continue
                if ch == q:
                    break
                buf.append(ch)
                i += 1
            i += 1
            tokens.append(Token(TOK_STR, "".join(buf), line_no, col0))
            continue
        if c.isdigit() or (c == '.' and i + 1 < n and s[i + 1].isdigit()):
            col0 = i + 1
            val, i = _scan_number(s, i, n)
            tokens.append(Token(TOK_NUM, val, line_no, col0))
            continue
        two = s[i:i+2]
        if two in TWO_CHAR_OPS:
            tokens.append(Token(TOK_OP, two, line_no, i + 1))
            i += 2
            continue
        if c in SINGLE_OPS:
            tokens.append(Token(TOK_OP, c, line_no, i + 1))
            i += 1
            continue
        if c.isalpha() or c == '_':
            col0 = i + 1
            buf = []
            while i < n and (s[i].isalnum() or s[i] == '_'):
                buf.append(s[i]); i += 1
            word = "".join(buf)
            src_word = word
            if word in KEYWORDS:
                word_out, t = word, TOK_KEY
            elif legacy_cn_enabled() and word in CN_TO_EN:
                word_out = CN_TO_EN[word]
                t = TOK_KEY if word_out in KEYWORDS else TOK_ID
            else:
                word_out, t = word, TOK_ID
            tokens.append(Token(t, word_out, line_no, col0, src_word))
            continue
        i += 1
    return None


def tokenize(source):
    """把 Impor 源码（已去掉头部标记）转成 Token 列表。"""
    tokens = []
    lines = source.splitlines()
    indent_stack = [0]
    line_no = 0
    multi = None  # 多行状态
    paren_depth = 0  # v3.4：圆括号/方括号深度；>0 表示续行，抑制 NEWLINE/INDENT/DEDENT

    for raw_line in lines:
        line_no += 1

        # 上一行遗留的多行注释 / 多行字符串
        if multi is not None:
            if multi[0] == 'comment':
                idx = raw_line.find('*/')
                if idx == -1:
                    continue
                multi = None
                _scan_line(tokens, raw_line[idx + 2:], line_no)
                tokens.append(Token(TOK_NEWLINE, "", line_no, len(stripped) + 1))
                continue
            qm, buf = multi[1], multi[2]
            idx = raw_line.find(qm)
            if idx == -1:
                buf.append(raw_line + "\n")
                continue
            buf.append(raw_line[:idx])
            tokens.append(Token(TOK_STR, "".join(buf), line_no))
            multi = None
            _scan_line(tokens, raw_line[idx + 3:], line_no)
            tokens.append(Token(TOK_NEWLINE, "", line_no, len(stripped) + 1))
            continue

        stripped = raw_line.lstrip()
        indent = len(raw_line) - len(stripped)

        # v3.5 文档注释：整行以 ## 开头（括号续行内仍按普通注释处理）
        if stripped.startswith("##") and paren_depth == 0:
            tokens.append(Token(TOK_DOC, stripped[2:].strip(), line_no, indent + 1))
            continue

        depth_before = paren_depth
        line_tokens = []
        multi = _scan_line(line_tokens, stripped, line_no)
        for tk in line_tokens:
            if tk.type == TOK_OP and tk.value in ("(", "["):
                paren_depth += 1
            elif tk.type == TOK_OP and tk.value in (")", "]"):
                paren_depth = max(0, paren_depth - 1)

        if not line_tokens and multi is None:
            # 空行 / 纯注释行：不参与缩进，不产生 NEWLINE
            continue

        if depth_before > 0:
            # v3.4 续行：括号未闭合期间不做缩进判定，也不产生 NEWLINE
            tokens.extend(line_tokens)
            if paren_depth == 0 and multi is None:
                tokens.append(Token(TOK_NEWLINE, "", line_no, len(stripped) + 1))
            continue

        if indent > indent_stack[-1]:
            indent_stack.append(indent)
            tokens.append(Token(TOK_INDENT, "", line_no))
        while indent < indent_stack[-1]:
            indent_stack.pop()
            tokens.append(Token(TOK_DEDENT, "", line_no))

        tokens.extend(line_tokens)
        if multi is None and paren_depth == 0:
            tokens.append(Token(TOK_NEWLINE, "", line_no, len(stripped) + 1))

    if multi is not None:
        if multi[0] == 'comment':
            raise ImporSyntaxError("多行注释 /* 没有匹配的 */", line_no, 1, None, hint_for("unclosed_comment"))
        raise ImporSyntaxError("多行字符串没有匹配的结束引号", line_no, 1, None, hint_for("unclosed_str"))

    while len(indent_stack) > 1:
        indent_stack.pop()
        tokens.append(Token(TOK_DEDENT, "", 999))
    tokens.append(Token(TOK_EOF, "", 9999))
    return tokens
