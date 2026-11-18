# -*- coding: utf-8 -*-
"""
Impor (I++) 自定义异常体系（v2.10）。

- ImporSyntaxError：词法 / 语法错误。携带 filename / lineno / col / hint（修复建议），
  绝不向用户抛出 Python 原生 SyntaxError。
- ImporRuntimeError：运行时错误统一基类（解释器层所有包装后的异常）。
- 定位字段统一：filename（源文件名）、lineno（1 起行号）、col（1 起列号）。

CLI 渲染规则：语法/运行错误输出「文件:行:列 + 源码行 + 波浪线 + 修复建议」，
Python 内部堆栈只在本异常之外（未知内部 bug）且设 IMPOR_DEBUG=1 时展示。
"""


class ImporSyntaxError(SyntaxError):
    """Impor 语法错误（lexer / parser 层）。

    继承 SyntaxError：旧的 `except SyntaxError` 捕获与 assertRaises(SyntaxError)
    测试保持兼容；新增字段携带精确位置与修复建议。
    hint：修复建议文本（如"可能漏写关键字"、"检查括号是否闭合"、"检查缩进"）；
    source_line：出错源码行文本（由 CLI 渲染时填充，便于波浪线对齐）。
    """

    def __init__(self, message, lineno=0, col=0, filename=None, hint=None,
                 source_line=None):
        super().__init__(message)
        self.msg = message
        self.lineno = lineno
        self.col = col
        self.filename = filename
        self.hint = hint
        self.source_line = source_line

    def __str__(self):
        pos = f"第{self.lineno}行" + (f" 列{self.col}" if self.col else "")
        return f"{pos}: {self.msg}"


class ImporRuntimeError(Exception):
    """Impor 运行时错误基类。

    解释器把 Python 底层异常包装成 ImporRuntimeError 子类/实例后向外抛出，
    携带脚本源码位置（lineno/col），隐藏 Python 内部堆栈。
    用户自定义异常（raise）与既有 ImporException 家族继续沿用 ImporException。
    """

    def __init__(self, name, msg, lineno=0, col=0, filename=None, cause=None):
        super().__init__(f"{name}: {msg}")
        self.name = name
        self.msg = msg
        self.lineno = lineno
        self.col = col
        self.filename = filename
        self.cause = cause

    def __str__(self):
        return f"{self.name}: {self.msg}"


# 常用修复建议（按语法场景提示，面向新手）
HINTS = {
    "expect": "检查是否缺少该语法成分（漏写关键字、括号或逗号未闭合）",
    "expr_missing": "该处需要一个值或表达式，检查是否漏写变量/字面量",
    "unclosed_block": "检查代码块是否缺少闭合符号（} 或缩进块结束）",
    "unclosed_str": "字符串缺少结束引号，检查引号是否成对",
    "unclosed_comment": "多行注释 /* 缺少匹配的 */，检查注释是否闭合",
    "indent": "检查缩进是否一致（同一代码块缩进需相同，勿混用空格/Tab）",
    "stmt_start": "该记号无法开始一条语句，检查是否漏写关键字或前一行是否完整",
    "lvalue": "赋值左侧必须是变量、下标或属性（不支持该左值赋值）",
}


def hint_for(kind):
    """返回修复建议文本；未知 kind 返回 None。"""
    return HINTS.get(kind)
