# -*- coding: utf-8 -*-
"""
Impor (I++) 代码格式化工具（impor fmt / impor check-fmt）。

v1 安全策略（不破坏任何语义）：
  - 块缩进为可配置宽度/层（默认 4 空格，impor.toml [fmt] indent_width 可改；基于词法 INDENT/DEDENT 深度，不触碰多行字符串/多行注释内容）
  - 删除行尾空白；tab 缩进换算为 4 空格
  - 行内（字符串/注释之外）：连续空白压缩为 1、二元运算符两侧加空格、
    一元 `-` 不加、逗号后加空格、`.` 属性访问去除两侧空格、行尾注释前补一个空格
  - 连续空行压缩为 1（头部标记行后保留至多 1 个空行）
  - 换行符统一为 \\n
  - 头部标记 important<<... 原样保留

v2.5 配置：impor.toml [fmt] 段支持 indent_width（缩进宽度）、max_line_length
（check-fmt 超长行提示阈值，0=不检查）；CLI 可用 --indent / --max-line 临时覆盖。
"""

import sys

from .lexer import tokenize, TOK_INDENT, TOK_DEDENT, TOK_NEWLINE, TOK_EOF, TOK_OP, DEPRECATED_ALIASES

# v3.7.2：fmt 只输出主写法。let 仅在语句开头替换，避免误改同名变量。
_FMT_ALIASES = {k: v for k, v in DEPRECATED_ALIASES.items()
                if k not in ("let", "loop")}

INDENT_UNIT = 4
DEFAULT_FMT_OPTS = {"indent_width": 4, "max_line_length": 0}

# 二元运算符（两侧加空格）；`-`/`+` 一元时除外
_BIN_OPS = frozenset(("+", "-", "*", "/", "%", "==", "!=", "<", "<=", ">", ">=", "=",
                      "+=", "-=", "*=", "/=", "%=", "?", ":", "->", "|>"))  # |> v2.9 管道；-> v3.8.2 返回类型
# 括号/分隔类（两侧不加空格）
_NO_SPACE_OPS = frozenset(("(", ")", "[", "]", "{", "}", ",", "."))

_UNARY_PREV = frozenset(("(", "[", "{", ",", ":", "=", "?", "+", "-", "*", "/", "%",
                         "==", "!=", "<", "<=", ">", ">=", "+=", "-=", "*=", "/=", "%="))


def is_header_line(line):
    return line.startswith("important<<") or line.lstrip().startswith("important<<")


def _format_inline(text):
    """对一行代码做行内空格规范。字符串/注释内容原样保留。"""
    out = []
    i, n = 0, len(text)
    in_str = None          # 当前字符串定界符（" / ' / """ / '''）
    prev = ""              # 上一个输出的非空白字符
    pending_space = False  # 是否挂起一个空格

    def flush_space():
        nonlocal pending_space
        if pending_space:
            out.append(" ")
            pending_space = False

    def is_word_char(c):
        return c.isalnum() or c == "_"

    while i < n:
        c = text[i]

        # ---- 注释 ----
        if in_str is None and (c == "#" or (c == "/" and i + 1 < n and text[i + 1] == "/")):
            flush_space()
            out.append(text[i:])   # 整段注释原样
            break

        # ---- 字符串 ----
        if in_str is None and (c in ('"', "'")):
            flush_space()  # v2.8 修复：= "..." / print "..." 等运算符后的空格不再丢失
            # 三引号
            if i + 2 < n and text[i:i + 3] in ('"""', "'''"):
                q3 = text[i:i + 3]
                j = i + 3
                while j < n:
                    if text[j:j + 3] == q3:
                        j += 3
                        break
                    j += 1
                out.append(text[i:j])
                prev = q3
                pending_space = True
                i = j
                continue
            in_str = c
            out.append(c)
            prev = c
            i += 1
            continue
        if in_str is not None:
            out.append(c)
            if c == "\\":
                if i + 1 < n:
                    out.append(text[i + 1])
                    i += 2
                    continue
            elif c == in_str:
                in_str = None
                pending_space = True  # v3.8：字符串结束后与 + / , / 关键字之间保留空格
            prev = c
            i += 1
            continue

        # ---- 空白 ----
        if c.isspace():
            # v3.8：左括号/左方括号后不留空格；左大括号后保留（块内语句要缩进）
            if out and out[-1] not in ("(", "["):
                pending_space = True
            i += 1
            continue

        # ---- 原始字符串前缀 r"..." ----
        if c in ("r", "R") and i + 1 < n and text[i + 1] in ('"', "'"):
            flush_space()  # v2.8 修复：r 前缀前保留运算符/赋值空格
            out.append(c)
            prev = c
            pending_space = False  # r 与引号紧贴（r"..." 一体）
            i += 1
            continue

        # ---- 标识符 / 数字：连续词元整体输出 ----
        if is_word_char(c):
            flush_space()
            j = i
            while j < n and is_word_char(text[j]):
                j += 1
            word = text[i:j]
            if word == "loop":
                # loop 是 while true 的简写，不能直接替换成缺条件的 while。
                out.append("while true")
            else:
                out.append(_FMT_ALIASES.get(word, word))
            prev = text[j - 1]
            pending_space = True
            i = j
            continue

        # ---- 运算符 ----
        two = text[i:i + 2]
        tok = two if (two in _BIN_OPS or two in _NO_SPACE_OPS) else c

        if tok in _NO_SPACE_OPS:
            if tok == ",":
                if out and out[-1] == " ":
                    out.pop()
                out.append(",")
                pending_space = True
            elif tok == ".":
                if out and out[-1] == " ":
                    out.pop()
                out.append(".")
                pending_space = False
            elif tok in ("{", "}"):
                # v2.10 大括号块语法：块头 { 前留空格、块内语句两侧留空格
                flush_space()
                if out and out[-1] != " " and prev not in ("", "{", "("):
                    out.append(" ")
                out.append(tok)
                pending_space = True
            else:
                # ( [ ：前不留空格，后不留
                if tok == "[" and prev in ("=", "==", "!=", "<", "<=", ">", ">=",
                                           "+", "-", "*", "/", "%", "+=", "-=",
                                           "*=", "/=", "%=", ":", "return", "in",
                                           "not", "and", "or", "is"):
                    flush_space()  # v2.8 修复：赋值/二元运算符后的列表字面量保留空格
                elif out and out[-1] == " ":
                    out.pop()
                out.append(tok)
                pending_space = False
            prev = tok
            i += len(tok)
            continue

        if tok in _BIN_OPS:
            if tok == ":" and prev in ('"', "'", '"""', "'''"):
                # v3.8：字典键 "width": W 的冒号前不留空格（三元 a ? b : c 不受影响）
                pending_space = False
            if tok in ("+", "-") and (prev == "" or prev in _UNARY_PREV):
                # 一元：与前不留空格（删掉挂起的空格）
                if out and out[-1] == " ":
                    out.pop()
                out.append(tok)
                pending_space = False
            else:
                flush_space()
                out.append(tok)
                pending_space = True
            prev = tok
            i += len(tok)
            continue

        # 兜底：单字符
        flush_space()
        out.append(c)
        prev = c
        pending_space = True
        i += 1

    return "".join(out)


def _code_line_numbers(source):
    """返回 {行号(1起): 缩进层数}：仅代码行（含缩进块与大括号块）。

    v3.8：原来只累计 INDENT/DEDENT，遇到 `def f() { ... }` 这类大括号块
    时深度恒为 0，格式化结果会把函数体顶到行首。现在同时累计大括号：
      - 纯缩进块用 INDENT/DEDENT 深度；
      - 大括号块用 `{`/`}` 配对深度，并忽略块内原始空白造成的假缩进；
      - 以 `}` 开头的行先减一层，让闭合括号与块头对齐。
    v3.9：括号内的续行标记为 -1，保留原缩进（用于长行换行后的幂等格式化）。
    注释行与多行字符串内容不会进入结果，调用方原样保留。
    """
    result = {}
    try:
        tokens = tokenize(source)
    except SyntaxError:
        # 语法错误时退回：不格式化缩进，仅做行内与行尾清理
        return {}

    by_line = {}
    for tok in tokens:
        if tok.type == TOK_EOF or tok.line <= 0:
            continue
        by_line.setdefault(tok.line, []).append(tok)

    indent_depth = 0
    brace_depth = 0
    brace_base_indent = 0
    paren_depth = 0
    for line_no in sorted(by_line):
        ts = by_line[line_no]
        continuation = paren_depth > 0
        for tok in ts:
            if tok.type == TOK_INDENT:
                indent_depth += 1
            elif tok.type == TOK_DEDENT:
                indent_depth = max(0, indent_depth - 1)

        sig = [t for t in ts if t.type not in (TOK_INDENT, TOK_DEDENT, TOK_NEWLINE)]
        if not sig:
            continue

        if continuation:
            result[line_no] = -1
        else:
            if brace_depth > 0:
                # 大括号块内仍可能混用缩进子块（loop / if / while 无花括号），
                # 必须把超出块头的 INDENT 深度叠加上去，否则子块会被拍平。
                depth = (brace_base_indent + brace_depth
                         + max(0, indent_depth - brace_base_indent - brace_depth))
            else:
                depth = indent_depth
            leading_close = 0
            for tok in sig:
                if tok.type == TOK_OP and tok.value == "}":
                    leading_close += 1
                else:
                    break
            result[line_no] = max(0, depth - leading_close)

        opens = sum(1 for t in sig if t.type == TOK_OP and t.value == "{")
        closes = sum(1 for t in sig if t.type == TOK_OP and t.value == "}")
        if brace_depth == 0 and opens:
            brace_base_indent = indent_depth
        brace_depth = max(0, brace_depth + opens - closes)
        if brace_depth == 0:
            brace_base_indent = 0
        for tok in sig:
            if tok.type != TOK_OP:
                continue
            if tok.value in ("(", "["):
                paren_depth += 1
            elif tok.value in (")", "]"):
                paren_depth = max(0, paren_depth - 1)
    return result


def _matching_bracket(text, start):
    """从 text[start] 的开括号找匹配闭括号；跳过字符串。找不到返回 -1。"""
    pairs = {"(": ")", "[": "]", "{": "}"}
    opener = text[start]
    closer = pairs[opener]
    depth = 0
    in_str = None
    i = start
    while i < len(text):
        c = text[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == in_str:
                in_str = None
            i += 1
            continue
        if c in ("\"", "'"):
            in_str = c
            i += 1
            continue
        if c == opener:
            depth += 1
        elif c == closer:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _split_top_level(text):
    """按顶层逗号切分文本；忽略括号与字符串内部的逗号。"""
    parts = []
    start = 0
    depth = 0
    in_str = None
    i = 0
    while i < len(text):
        c = text[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == in_str:
                in_str = None
            i += 1
            continue
        if c in ("\"", "'"):
            in_str = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth = max(0, depth - 1)
        elif c == "," and depth == 0:
            parts.append(text[start:i])
            start = i + 1
        i += 1
    parts.append(text[start:])
    return parts


def _wrap_long_line(line, max_len, indent_width):
    """把单个调用/字面量参数列表按顶层逗号拆成多行（仅括号内换行，语义不变）。"""
    if max_len <= 0 or len(line) <= max_len:
        return [line]
    if "#" in line or "//" in line or line.rstrip().endswith("{"):
        return [line]
    lead = line[:len(line) - len(line.lstrip())]
    candidates = []
    i = 0
    in_str = None
    while i < len(line):
        c = line[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == in_str:
                in_str = None
            i += 1
            continue
        if c in ("\"", "'"):
            in_str = c
            i += 1
            continue
        if c in "([":
            close = _matching_bracket(line, i)
            if close > i + 1 and not line[close + 1:].strip():
                candidates.append((i, close, c, line[close]))
        i += 1
    for open_pos, close_pos, opener, closer in candidates:
        args = line[open_pos + 1:close_pos]
        parts = [p.strip() for p in _split_top_level(args)]
        if len(parts) < 2:
            continue
        prefix = line[:open_pos].rstrip()
        if open_pos > 0 and line[open_pos - 1].isspace():
            prefix += " "
        suffix = line[close_pos + 1:].rstrip()
        cont = lead + " " * indent_width
        out = [prefix + opener]
        for idx, part in enumerate(parts):
            comma = "," if idx < len(parts) - 1 else ""
            out.append(cont + part + comma)
        out.append(lead + closer + suffix)
        return out
    return [line]



def fmt_source(src, indent_width=DEFAULT_FMT_OPTS["indent_width"],
               max_line_length=DEFAULT_FMT_OPTS["max_line_length"]):
    """格式化 Impor 源码（含头部标记），返回格式化后的字符串。"""
    src = src.replace("\r\n", "\n").replace("\r", "\n")
    lines = src.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]

    # 头部行
    head_idx = 0
    if lines and is_header_line(lines[0]):
        head_idx = 1

    body = "\n".join(lines[head_idx:])
    code_depth = _code_line_numbers(body)  # body 内行号
    # 转换：body 行号 -> 全文行号（偏移 head_idx）
    full_depth = {ln + head_idx: d for ln, d in code_depth.items()}

    out = []
    blank_run = 0
    for idx, raw in enumerate(lines):
        line_no = idx + 1
        stripped = raw.strip()
        if not stripped:
            # 空行：压缩连续空行（头部后第一个空行允许）
            blank_run += 1
            if blank_run == 1 and out:
                out.append("")
            continue
        blank_run = 0

        # 注释行 / 多行字符串续行等非代码行：保持原样（去行尾空白）
        if line_no not in full_depth:
            out.append(raw.rstrip())
            continue

        depth = full_depth[line_no]
        if depth == -1:
            # 括号内续行：保留原缩进，仅整理行内空格
            lead = raw[:len(raw) - len(raw.lstrip())]
            content = _format_inline(raw.strip())
            out.append(lead + content if content else "")
            continue
        indent = " " * (depth * indent_width)
        content = _format_inline(raw.strip())
        if content.startswith("let ") and content[4:5] and (content[4].isalpha() or content[4] == "_"):
            content = "var " + content[4:]
        out.append(indent + content if content else "")

    # v3.9：按 max_line_length 对调用/列表参数做括号内换行（语义不变）
    if max_line_length and max_line_length > 0:
        wrapped = []
        for idx, line in enumerate(out):
            if idx == 0 and lines and is_header_line(lines[0]):
                wrapped.append(line)
                continue
            wrapped.extend(_wrap_long_line(line, max_line_length, indent_width))
        out = wrapped

    text = "\n".join(out)
    return text + "\n" if src.endswith("\n") else text


def check_fmt(src, indent_width=DEFAULT_FMT_OPTS["indent_width"],
              max_line_length=DEFAULT_FMT_OPTS["max_line_length"]):
    """返回 (是否已格式化, 格式化后的文本)。"""
    formatted = fmt_source(src, indent_width, max_line_length)
    return formatted == src, formatted


def fmt_options_from_dir(start_dir):
    """读取 impor.toml [fmt] 段（含 CLI 覆盖前的默认）。失败时返回默认值。"""
    opts = dict(DEFAULT_FMT_OPTS)
    try:
        from .config import find_config
        found = find_config(start_dir)
        if found is not None:
            _, cfg = found
            sec = cfg.get("fmt") or {}
            if "indent_width" in sec:
                opts["indent_width"] = max(1, int(sec["indent_width"]))
            if "max_line_length" in sec:
                opts["max_line_length"] = max(0, int(sec["max_line_length"]))
    except Exception:
        pass
    return opts


def main_fmt(args):
    """impor fmt FILE [-o OUT] [--indent N] [--max-line N] / impor fmt --check FILE"""
    import os
    check_only = False
    out_path = None
    files = []
    cli_opts = {}
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-c", "--check"):
            check_only = True
        elif a in ("-o", "--out"):
            i += 1
            out_path = args[i] if i < len(args) else None
        elif a == "--indent":
            i += 1
            if i < len(args):
                cli_opts["indent_width"] = max(1, int(args[i]))
        elif a == "--max-line":
            i += 1
            if i < len(args):
                cli_opts["max_line_length"] = max(0, int(args[i]))
        else:
            files.append(a)
        i += 1
    if not files:
        print("用法: impor fmt FILE [-o OUT] [--indent N] [--max-line N] / impor fmt --check FILE",
              file=sys.stderr)
        return 3
    # 配置优先级：impor.toml [fmt] < CLI 参数（--indent/--max-line 覆盖）
    opts = fmt_options_from_dir(
        os.path.dirname(os.path.abspath(files[0])) if files else None)
    opts.update(cli_opts)
    ok = True
    for f in files:
        try:
            with open(f, "r", encoding="utf-8") as fh:
                src = fh.read()
        except OSError as e:
            print(f"impor fmt: {e}", file=sys.stderr)
            return 2
        formatted = fmt_source(src, opts["indent_width"], opts["max_line_length"])
        if check_only:
            problems = []
            if formatted != src:
                problems.append("未格式化")
            if opts["max_line_length"]:
                for ln, raw in enumerate(src.split("\n"), start=1):
                    if len(raw) > opts["max_line_length"]:
                        problems.append(f"第 {ln} 行超长（{len(raw)} > {opts['max_line_length']}）")
            if problems:
                print(f"{f}: {'；'.join(problems)}")
                ok = False
            else:
                print(f"{f}: 已格式化")
        else:
            target = out_path or f
            with open(target, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(formatted)
            if out_path:
                print(f"已格式化输出到: {out_path}")
            else:
                print(f"已格式化: {f}")
    return 0 if ok else 1
