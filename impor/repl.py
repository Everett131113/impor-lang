# -*- coding: utf-8 -*-
"""
Impor (I++) 交互式解释器（REPL）。

命令：
  exit / quit    退出
  clear          清屏
  vars           列出当前全局变量
  help           帮助
  其余输入按 Impor 代码执行；支持多行块（以 : 结尾继续输入）。

v2.5：输入历史持久化到 ~/.impor_history（最多 500 条，连续重复去重）。
"""

import os

from . import __version__
from .lexer import tokenize
from .parser import Parser
from .interpreter import Interpreter, ImporException
from .diagnostics import parse_position, render_diagnostic

PROMPT = "impor> "
CONT = "  ...> "
HISTORY_FILE = os.path.join(os.path.expanduser("~"), ".impor_history")
HISTORY_MAX = 500


def _load_history():
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return [ln.rstrip("\n") for ln in f if ln.strip()]
    except OSError:
        return []


def _save_history(hist):
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(hist[-HISTORY_MAX:]) + "\n")
    except OSError:
        pass


BLOCK_KEYS = ("if", "elseif", "else", "while", "for", "def", "func",
              "class", "try", "catch", "finally")


def _is_incomplete(buf, err=None):
    """判断是否需要继续读入（多行块）。"""
    lines = [ln for ln in buf.splitlines() if ln.strip()]
    if not lines:
        return False
    raw_last = lines[-1]
    last = raw_last.strip()
    if last.endswith(":") or last.endswith("\\"):
        return True
    if raw_last[:1] in (" ", "\t"):
        return True
    # 块头关键字（省略冒号时也要求后续缩进体）
    first = last.split(None, 1)[0]
    if first in BLOCK_KEYS:
        return True
    return False


def _clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def _dump_vars(interp, _print):
    names = sorted(k for k in interp.global_env.store if not k.startswith("_"))
    if not names:
        _print("(空)\n")
        return
    from .interpreter import typename
    for n in names:
        v = interp.global_env.store[n]
        _print(f"{n} : {typename(v)} = {v!r}\n")


def _show_help(_print):
    _print(
        "Impor REPL 帮助\n"
        "  exit / quit      退出\n"
        "  clear            清屏\n"
        "  vars             列出全局变量\n"
        "  help             本帮助\n"
        "  help()           列出全部内建函数\n"
        "  help(名字)       查看内建函数/关键字说明（如 help(print)、help(if)）\n"
        "多行语句：以冒号结尾的行会自动继续（用缩进延续）。\n"
    )


def _show_builtin_docs(_print, name=None):
    from .builtins import BUILTIN_DOCS
    if name:
        from .syntax_ref import format_entry
        text = format_entry(name)
        if text:
            _print(text)
            return
        doc = BUILTIN_DOCS.get(name)
        if doc:
            _print(f"{name}：{doc}\n")
            return
        _print(f"（没有 {name} 的语法条目；help() 查看全部内建，或运行 impor syntax）\n")
        return
    if not BUILTIN_DOCS:
        _print("（无内建函数）\n")
        return
    for k in sorted(BUILTIN_DOCS):
        _print(f"  {k.ljust(12)} {BUILTIN_DOCS[k]}\n")


def _handle_help_cmd(buf, _print):
    """REPL 帮助命令：help / ? / help() / help(名字)。返回 True 表示已处理。"""
    s = buf.strip()
    if s in ("help", ":h", "?", ":?"):
        _show_help(_print)
        return True
    if s == "help()":
        _show_builtin_docs(_print)
        return True
    if s.startswith("help(") and s.endswith(")"):
        inner = s[5:-1].strip().strip("\"'")
        if inner:
            _show_builtin_docs(_print, inner)
            return True
    return False


def repl(stdin=None, stdout=None):
    """启动 REPL。stdin/stdout 可注入（测试用）。"""
    _print = stdout.write if stdout else print
    if stdin is not None:
        def _readline(_p):
            line = stdin.readline()
            if line == "":
                raise EOFError
            return line
        _input = _readline
    else:
        _input = input

    interp = Interpreter(log_cb=(lambda t: _print(t + "\n")))
    _print(f"Impor (I++) {__version__} REPL — 输入 help 查看帮助，exit 退出\n")
    hist = _load_history()

    def exec_buf(buf):
        """执行一段输入；返回 True 表示退出。"""
        if buf in ("exit", "quit", ":q", "exit()", "quit()"):
            return True
        if buf == "clear":
            _clear_screen()
            return False
        if buf == "vars":
            _dump_vars(interp, _print)
            return False
        if _handle_help_cmd(buf, _print):
            return False
        try:
            ast = Parser(tokenize(buf)).program()
        except SyntaxError as e:
            _ln, _col, _msg = parse_position(str(e))
            # REPL 已回显用户输入，不再重复片段；只给位置 + 为什么 + 修复
            _print(render_diagnostic("语法错误", _msg, _ln or 0, _col or 0, None,
                                     filename="<repl>", subject="语法错误") + "\n")
            return False
        try:
            interp.run(ast)
        except ImporException as e:
            _print(render_diagnostic("运行错误", f"{e.name}: {e.msg}", e.lineno,
                                     getattr(e, "col", 0) or 0, None,
                                     filename="<repl>", subject=e.name) + "\n")
        except Exception as e:
            _print(f"内部错误: {type(e).__name__}: {e}\n")
        # 记录历史（连续重复不存；帮助类命令不记）
        if buf and not _handle_help_cmd(buf, _print) and buf not in ("exit", "quit", ":q", "clear", "vars"):
            if not hist or hist[-1] != buf:
                hist.append(buf)
        return False

    buf = ""
    while True:
        try:
            line = _input(CONT if buf else PROMPT)
        except EOFError:
            _save_history(hist)
            _print("\n")
            break
        except KeyboardInterrupt:
            _print("\n")
            buf = ""
            continue

        if buf and line == "":
            # 空行结束多行输入
            if exec_buf(buf):
                break
            buf = ""
            continue
        if not line:
            continue

        stripped_line = line.rstrip("\n")
        if buf and stripped_line == "":
            # 空行结束多行输入
            if exec_buf(buf):
                break
            buf = ""
            continue

        buf = (buf + "\n" if buf else "") + stripped_line
        if _is_incomplete(buf):
            continue
        if exec_buf(buf):
            break
        buf = ""
