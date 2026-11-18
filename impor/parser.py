# -*- coding: utf-8 -*-
"""
Impor (I++) 递归下降解析器。

语法要点（v2）：
  - 缩进即代码块；if/while/for/def/func/loop/try/catch/finally/class 后的冒号可省略
  - var / set 声明变量；nonlocal set / global set 声明作用域
  - func 与 def 等价；loop 等价 while true
  - 三元表达式：条件 ? 真值 : 假值
  - import math | import important.math | import math as m
  - try / catch err / catch Type err / catch Type, err / finally
  - super() 调用父类方法
"""

from .ast_nodes import (
    Num, Str, FStr, Bool, Null, Var, Pipe, ExprStmt, AttrGet, AttrSet, BinOp, UnaryOp, TernaryExpr,
    Assign, CompoundAssign, Print, If, While, For, Def, Return, Call,
    NewInstance, SuperCall, Break, Continue, ExitScript, ClsOut,
    ListLit, DictLit, IndexGet, IndexSet, Slice, ImportNode, FromImportNode,
    ExportNode, AssertNode, Block, Defer, TryCatch,
    ClassDef, ExceptionDef, Raise, MatchNode, BindingPattern, AdtPattern, Lambda, Await,
)
from .errors import ImporSyntaxError, hint_for
from .lexer import TOK_OP, TOK_NUM, TOK_STR, TOK_FSTR, TOK_ID, TOK_KEY, TOK_EOF, TOK_INDENT, TOK_DEDENT, TOK_NEWLINE, TOK_DOC, unescape_impor


def _normalize_type_text(text):
    """把类型标注规范成无空格文本；保留 `?`、`<...>` 与 `|` 结构。"""
    text = "".join(str(text or "").split())
    return text


class Parser:
    def __init__(self, tokens, filename=None):
        self.toks = tokens
        self.pos = 0
        self.filename = filename  # v2.10 源码位置：文件名
        # v3.8：enum 成员表。enum 是软关键字，解析期脱糖成
        # `__enum_类型_成员` 全局整型常量，避免三端各自维护静态字段。
        self._enum_members = {}   # 类型名 -> {成员名: 脱糖变量名}
        self._enum_order = {}     # 类型名 -> [成员名]（声明顺序，供穷尽性检查）
        # 3.2.1：带载荷 ADT。类型名 -> {成员名: [字段名]}；空表示无载荷变体。
        self._adt_types = {}

    def _peek_ln_col(self, offset=0):
        """v2.10：当前（或偏移）token 的 (行号, 列号)。"""
        t = self.peek(offset)
        return t.line, t.col

    # ---------- 基础 ----------
    def peek(self, offset=0):
        idx = self.pos + offset
        return self.toks[idx] if idx < len(self.toks) else self.toks[-1]

    def consume(self):
        t = self.peek()
        self.pos += 1
        return t

    def expect(self, typ, val=None):
        t = self.consume()
        if t.type != typ:
            raise ImporSyntaxError(f"期望{typ}，得到 {t.value!r}", t.line, t.col, self.filename, hint_for("expect"))
        if val is not None and t.value != val:
            raise ImporSyntaxError(f"期望 {val!r}，得到 {t.value!r}", t.line, t.col, self.filename, hint_for("expect"))
        return t

    def _is_key(self, word):
        t = self.peek()
        if t.type != TOK_KEY:
            return False
        return t.value in word if isinstance(word, tuple) else t.value == word

    def _is_op(self, val):
        t = self.peek()
        if t.type != TOK_OP:
            return False
        return t.value in val if isinstance(val, tuple) else t.value == val

    def _opt_colon(self):
        """v3.4：块引导符可以是 `:`，也可以是 `then`/`则`（可省略）。"""
        if self._is_op(":"):
            self.consume()
        elif self.peek().type == TOK_ID and self.peek().value in ("then", "do"):
            self.consume()

    def _skip_semis(self):
        while self._is_op(";"):
            self.consume()

    def _skip_fill(self):
        """跳过换行/缩进残留（用于括号内续行、catch/finally 前的换行）。"""
        while self.peek().type in (TOK_NEWLINE, TOK_INDENT):
            self.consume()

    def _skip_type_annotation(self):
        """v7.3：读取类型注解并返回规范化文本（不再丢弃）。

        支持 var x: int / func f(a: int, b: str) -> int / 泛型 List<int> /
        nullable int? 这类写法。遇到 `=`、`,`、`)`、`;`、`{`、`}` 或行尾即停止。
        """
        parts = []
        depth = 0
        while True:
            t = self.peek()
            if t.type in (TOK_NEWLINE, TOK_EOF):
                break
            if t.type == TOK_OP and t.value in ("(", "[", "<"):
                depth += 1
            elif t.type == TOK_OP and t.value in (")", "]", ">"):
                if depth == 0:
                    break
                depth -= 1
            elif depth == 0 and t.type == TOK_OP and t.value in ("=", ",", ";", "{", "}", ":"):
                break
            parts.append(str(t.value))
            self.consume()
        return _normalize_type_text("".join(parts))


    # ---------- 表达式 ----------
    def expr(self):
        return self.ternary()

    def ternary(self):
        n = self.pipe_expr()
        if self._is_op("?"):
            ln, cl = self._peek_ln_col()
            self.consume()
            te = self.expr()
            self.expect(TOK_OP, ":")
            fe = self.expr()
            return TernaryExpr(n, te, fe, ln, cl)
        return n

    def pipe_expr(self):
        # v2.9 管道运算符：x |> f(1) == f(x, 1)；左结合，优先级低于 ?:、高于 or
        n = self.or_expr()
        while self._is_op("|>"):
            ln, cl = self._peek_ln_col()
            self.consume()
            n = Pipe(n, self.or_expr(), ln, cl)
        return n

    def or_expr(self):
        n = self.and_expr()
        while self._is_key("or") or self._is_op("||"):  # v3.4：|| 同 or
            ln, cl = self._peek_ln_col()
            self.consume()
            n = BinOp(n, "or", self.and_expr(), ln, cl)
        return n

    def and_expr(self):
        n = self.not_expr()
        while self._is_key("and") or self._is_op("&&"):  # v3.4：&& 同 and
            ln, cl = self._peek_ln_col()
            self.consume()
            n = BinOp(n, "and", self.not_expr(), ln, cl)
        return n

    def _await_has_operand(self):
        """v3.7.2：await 软关键字判定——后面确实接了表达式才按 await 解析。"""
        nxt = self.peek(1)
        if nxt.type in (TOK_ID, TOK_NUM, TOK_STR, TOK_FSTR, TOK_KEY):
            return True
        if nxt.type == TOK_OP and nxt.value in ("(", "[", "{", "!", "-", "+"):
            return True
        return False

    def not_expr(self):
        # v3.7.2：await 是软关键字；后面能接表达式才按 await 解析，否则按普通标识符。
        if self.peek().value == "await" and self._await_has_operand():
            ln, cl = self._peek_ln_col()
            self.consume()
            return Await(self.neg(), ln, cl)
        if self._is_key("not"):
            ln, cl = self._peek_ln_col()
            self.consume()
            return UnaryOp("not", self.neg(), ln, cl)
        if self._is_op("!"):
            ln, cl = self._peek_ln_col()
            self.consume()
            return UnaryOp("!", self.neg(), ln, cl)
        return self.neg()

    def neg(self):
        if self._is_op("-"):
            ln, cl = self._peek_ln_col()
            self.consume()
            return UnaryOp("-", self.neg(), ln, cl)
        if self._is_op("+"):
            ln, cl = self._peek_ln_col()
            self.consume()
            return UnaryOp("+", self.neg(), ln, cl)
        return self.cmp()

    def _rhs(self, lower):
        """二元/比较运算符右侧：支持一元正负号（语法宽松，如 a != -1、x * -2）。"""
        if self._is_op(("-", "+")):
            ln, cl = self._peek_ln_col()
            op = self.consume()
            return UnaryOp(op.value, self._rhs(lower), ln, cl)
        return lower()

    def cmp(self):
        n = self.add()
        while self._is_op((">", "<", ">=", "<=", "==", "!=", "<>")):
            op = self.consume()
            n = BinOp(n, "!=" if op.value == "<>" else op.value, self._rhs(self.add), op.line, op.col)
        return n

    def add(self):
        n = self.mul()
        while self._is_op(("+", "-")):
            op = self.consume()
            n = BinOp(n, op.value, self._rhs(self.mul), op.line, op.col)
        return n

    def mul(self):
        n = self.primary()
        while self._is_op(("*", "/", "%")):
            op = self.consume()
            n = BinOp(n, op.value, self._rhs(self.primary), op.line, op.col)
        return n

    def _parse_call_args(self):
        self.expect(TOK_OP, "(")
        args = []
        while not self._is_op(")"):
            args.append(self.expr())
            if self._is_op(","):
                self.consume()
        self.expect(TOK_OP, ")")
        return args

    def _parse_index_or_slice(self, node, ln, cl):
        """解析 [ ... ]：下标 a[i] 或切片 a[i:j] / a[:j] / a[i:j:k]。"""
        self.consume()  # [
        start = end = step = None
        if self._is_op(":"):
            self.consume()
        else:
            start = self.expr()
            if self._is_op(":"):
                self.consume()
            else:
                self.expect(TOK_OP, "]")
                return IndexGet(node, start, ln, cl)
        if not self._is_op("]") and not self._is_op(":"):
            end = self.expr()
        if self._is_op(":"):
            self.consume()
            if not self._is_op("]"):
                step = self.expr()
        self.expect(TOK_OP, "]")
        return Slice(node, start, end, step, ln, cl)

    def primary(self):
        t = self.peek()

        # v5.3：lambda (a, b): a + b；也接受 lambda x: x * 2
        # v3.7.1：lambda 解封后可作普通标识符；只有后接参数或 '(' 时才按 lambda 表达式解析。
        if (t.type in (TOK_ID, TOK_KEY) and t.value == "lambda"
                and (self.peek(1).type == TOK_ID
                     or (self.peek(1).type == TOK_OP and self.peek(1).value == "("))):
            ln, cl = t.line, t.col
            self.consume()
            if self._is_op("("):
                params = self._parse_params()
            else:
                params = [self.expect(TOK_ID).value]
            self.expect(TOK_OP, ":")
            return Lambda(params, self.expr(), ln, cl)

        if t.type == TOK_KEY and t.value == "new":
            ln, cl = t.line, t.col
            self.consume()
            cn = self.expect(TOK_ID).value
            args = self._parse_call_args() if self._is_op("(") else []  # v3.4：new A 可省括号
            return NewInstance(cn, args, ln, cl)

        if t.type == TOK_KEY and t.value == "super":
            ln, cl = t.line, t.col
            self.consume()
            # v3.4：super() 的括号可省略（super.方法() 等价 super().方法()）
            if self._is_op("("):
                self.consume()
                self._skip_fill()
                self.expect(TOK_OP, ")")
            node = SuperCall(ln)
            while self._is_op(("(", "[", ".")):
                if self._is_op("("):
                    args = self._parse_call_args()
                    node = Call(node, args, ln, cl)
                elif self._is_op("["):
                    sln, cl = self._peek_ln_col()
                    node = self._parse_index_or_slice(node, sln)
                else:
                    sln, cl = self._peek_ln_col()
                    self.consume()
                    attr = self.expect(TOK_ID).value
                    node = AttrGet(node, attr, sln)
            return node

        if t.type == TOK_NUM:
            self.consume()
            return Num(t.value, t.line, t.col)
        if t.type == TOK_STR:
            self.consume()
            node = Str(t.value, t.line, t.col)
            # v3.3：字符串字面量后允许下标（"你好"[1] 取字符），此前被误解析为 print 第二个参数
            while self._is_op("["):
                sln, scl = self._peek_ln_col()
                node = self._parse_index_or_slice(node, sln, scl)
            return node
        if t.type == TOK_FSTR:
            ln, cl = t.line, t.col
            self.consume()
            # v3.6.16：Tcl/Tk 的字体、颜色、布局常写 "{Segoe UI} 9"，
            # 花括号里是带空格的字面文本，不是 Impor 表达式。这里的规则是：
            # {…} 内容能按 Impor 表达式解析才算插值；解析失败就整段退化为
            # 字面文本（保留花括号）。这样 Tk 字体描述符不需要改写成
            # \{Segoe UI\}，也不会再把 {Segoe UI} 当变量而报“变量 Segoe 未定义”。
            parts = []
            exprs = []
            from .lexer import tokenize as _tk
            for kind, payload in t.value:
                if kind == "expr":
                    try:
                        sp = Parser(_tk(payload + "\n"))
                        sub = sp.expr()
                        # 关键：expr() 只吃一条表达式。"{Segoe UI}" 里
                        # expr() 会解析出 Segoe，把 UI 留在原地，旧代码
                        # 直接忽略残留，于是花括号被悄悄吃掉。这里要求
                        # 除换行/EOF 外不能有残留，否则按字面文本处理。
                        while sp.peek().type in (TOK_NEWLINE, TOK_DEDENT):
                            sp.consume()
                        if sp.peek().type != TOK_EOF:
                            raise ImporSyntaxError("插值段不是完整表达式", 1, 1)
                    except ImporSyntaxError:
                        # 不是合法表达式：把这一段的 {payload} 原样还给文本
                        parts.append(("text", "{" + payload + "}"))
                        continue
                    parts.append(("expr", payload))
                    exprs.append(sub)
                else:
                    parts.append((kind, payload))
            if not exprs:
                text = "".join(p for k, p in parts if k == "text")
                return Str(unescape_impor(text), ln, cl)
            node = FStr(parts, ln, cl)
            node.exprs = exprs
            return node
        if t.type == TOK_KEY and t.value == "true":
            self.consume()
            return Bool(True, t.line, t.col)
        if t.type == TOK_KEY and t.value == "false":
            self.consume()
            return Bool(False, t.line, t.col)
        # v2.18：空值字面量 none / nil / null
        if t.type == TOK_KEY and t.value in ("none", "nil", "null"):
            self.consume()
            return Null(t.line, t.col)
        # v2.18：this → self 别名；v3.7.2 改为软关键字，只有 this. 才按 self 解析，
        # 裸 this 可作普通变量。
        if (t.type in (TOK_ID, TOK_KEY) and t.value == "this"
                and self.peek(1).type == TOK_OP and self.peek(1).value == "."):
            self.consume()
            node = Var("self", t.line, t.col)
            while self._is_op(("(", "[", ".")):
                if self._is_op("("):
                    args = self._parse_call_args()
                    node = Call(node, args, t.line, t.col)
                elif self._is_op("["):
                    ln, cl = self._peek_ln_col()
                    node = self._parse_index_or_slice(node, ln, cl)
                else:
                    ln, cl = self._peek_ln_col()
                    self.consume()
                    attr = self.expect(TOK_ID).value
                    node = AttrGet(node, attr, ln, cl)
            return node

        if t.type == TOK_ID:
            name = self.consume().value
            node = Var(name, t.line, t.col)
            while self._is_op(("(", "[", ".")):
                if self._is_op("("):
                    args = self._parse_call_args()
                    node = Call(node, args, t.line, t.col)
                elif self._is_op("["):
                    ln, cl = self._peek_ln_col()
                    node = self._parse_index_or_slice(node, ln, cl)
                else:
                    ln, cl = self._peek_ln_col()
                    self.consume()
                    attr = self.expect(TOK_ID).value
                    node = self._enum_attr(node, attr, ln, cl)
            return node

        if t.type == TOK_OP and t.value == "(":
            self.consume()
            e = self.expr()
            self.expect(TOK_OP, ")")
            return e

        if t.type == TOK_OP and t.value == "[":
            ln, cl = t.line, t.col
            self.consume()
            items = []
            while not self._is_op("]"):
                items.append(self.expr())
                nxt = self.peek()
                if nxt.type == TOK_KEY and nxt.value == "for":
                    raise ImporSyntaxError(
                        "列表推导式尚未实现（v3.9 计划）", nxt.line, nxt.col,
                        self.filename, hint_for("stmt_start"))
                if self._is_op(","):
                    self.consume()
            self.expect(TOK_OP, "]")
            return ListLit(items, ln, cl)

        if t.type == TOK_OP and t.value == "{":
            ln, cl = t.line, t.col
            self.consume()
            kv = []
            while not self._is_op("}"):
                k = self.expr()
                self.expect(TOK_OP, ":")
                v = self.expr()
                kv.append((k, v))
                nxt = self.peek()
                if nxt.type == TOK_KEY and nxt.value == "for":
                    raise ImporSyntaxError(
                        "字典推导式尚未实现（v3.9 计划）", nxt.line, nxt.col,
                        self.filename, hint_for("stmt_start"))
                if self._is_op(","):
                    self.consume()
            self.expect(TOK_OP, "}")
            return DictLit(kv, ln, cl)

        if t.type in (TOK_NEWLINE, TOK_DEDENT, TOK_EOF):
            raise ImporSyntaxError("表达式缺失（该处需要一个值或表达式）", t.line, t.col, self.filename, hint_for("expr_missing"))
        raise ImporSyntaxError(f"无法解析的记号 {t.value!r}", t.line, t.col, self.filename, hint_for("stmt_start"))

    # ---------- 语句 ----------
    def _take_docs(self):
        """v3.5：收集紧邻下一条语句的 `##` 文档注释（连续多行合并，保留换行）。"""
        docs = []
        while self.peek().type == TOK_DOC:
            docs.append(self.consume().value)
        return docs

    def _stmt_with_doc(self):
        """解析一条语句，并把紧邻其前的文档注释挂到 node.doc（不参与三端分发）。"""
        docs = self._take_docs()
        while self.peek().type == TOK_NEWLINE:  # 文档注释后可能夹空行
            self.consume()
        node = self.stmt()
        if docs:
            try:
                node.doc = "\n".join(docs)
            except (AttributeError, TypeError):
                pass
        return node

    def block(self):
        st = []
        while self.peek().type not in (TOK_DEDENT, TOK_EOF):
            if self.peek().type in (TOK_NEWLINE, TOK_INDENT):
                self.consume()
                continue
            if self._is_op(";"):
                self.consume()
                continue
            if self.peek().type == TOK_KEY and self.peek().value in ("endwhile", "endfor"):
                break
            st.append(self._stmt_with_doc())
        return st

    def _quick_block(self):
        """v3.4：同一行的快捷块体，支持 `;` 分隔的多条语句：if x: a(); b()"""
        st = [self._stmt_with_doc()]
        while self._is_op(";"):
            self.consume()
            if self.peek().type in (TOK_NEWLINE, TOK_DEDENT, TOK_EOF) or self._is_op("}"):
                break
            st.append(self._stmt_with_doc())
        return st

    def _parse_block(self):
        """v2.10 代码块（三种形式，向后兼容）：
        1. 大括号块（新标准）：if x > 0 { print(x) }，块内缩进被忽略
        2. quick 单行块：if x > 0 print(x)（块体仅一条语句，省略大括号）
        3. 旧缩进块（兼容）：if x > 0\n    print(x)
        """
        if self._is_op("{"):
            bln, bcl = self._peek_ln_col()
            self.consume()
            return self._brace_block(bln)
        if self.peek().type == TOK_NEWLINE:
            self.consume()
            # v2.18：`{` 单独一行也算大括号块（省略缩进）
            if self._is_op("{"):
                bln, bcl = self._peek_ln_col()
                self.consume()
                return self._brace_block(bln)
            self.expect(TOK_INDENT)
            bd = self.block()
            self.expect(TOK_DEDENT)
            return bd
        # quick 单行：同一行剩余内容作为语句块体（v3.4 支持 ; 分隔多条）
        return self._quick_block()

    def _reject_defer_control(self, body, ln, cl):
        """defer 体是退出时执行的清理块，不能 return/break/continue 到外层。"""
        stack = list(body or [])
        while stack:
            s = stack.pop()
            if isinstance(s, (Def, ClassDef, ExceptionDef)):
                continue
            if isinstance(s, (Return, Break, Continue)):
                raise ImporSyntaxError(
                    "defer 体内不能使用 return / break / continue",
                    getattr(s, "lineno", ln), cl, self.filename,
                    hint_for("stmt_start"))
            if isinstance(s, Block):
                stack.extend(s.stmts)
            elif isinstance(s, If):
                stack.extend(s.body)
                for _, eb in s.elseifs:
                    stack.extend(eb)
                if s.elseb:
                    stack.extend(s.elseb)
            elif isinstance(s, (While, For)):
                stack.extend(s.body)
            elif isinstance(s, TryCatch):
                stack.extend(s.body)
                stack.extend(s.catch_body)
                if s.finally_body:
                    stack.extend(s.finally_body)

    def _enum_const(self, expr, name, member, ln, cl):
        """enum 显式值只接受整数字面量（含 + / - 号），保证自增可静态计算。"""
        if isinstance(expr, Num) and type(expr.val) is int:
            return expr.val
        if isinstance(expr, UnaryOp) and isinstance(expr.child, Num) \
                and type(expr.child.val) is int and expr.op in ("+", "-"):
            return expr.child.val if expr.op == "+" else -expr.child.val
        raise ImporSyntaxError(
            f"enum {name} 成员 {member!r} 的值必须是整数字面量",
            ln, cl, self.filename, hint_for("stmt_start"))

    def _parse_enum_member(self, ename, seen, ln, cl):
        tok = self.expect(TOK_ID)
        member = tok.value
        if member in seen:
            raise ImporSyntaxError(
                f"enum {ename} 成员 {member!r} 重复声明",
                tok.line, tok.col, self.filename, hint_for("stmt_start"))
        fields = []
        if self._is_op("("):
            # 3.2.1 带载荷变体：Ok(value) / Point(x, y)
            self.consume()
            while not self._is_op(")"):
                if self.peek().type == TOK_EOF:
                    raise ImporSyntaxError(
                        f"enum {ename}.{member} 的载荷字段缺少 ) 闭合",
                        tok.line, tok.col, self.filename, hint_for("unclosed_block"))
                fld = self.expect(TOK_ID).value
                if fld in fields:
                    raise ImporSyntaxError(
                        f"enum {ename}.{member} 的载荷字段 {fld!r} 重复",
                        tok.line, tok.col, self.filename, hint_for("stmt_start"))
                fields.append(fld)
                if self._is_op(":"):
                    # 可选字段类型标注，当前仅用于文档；载荷本身动态。
                    self.consume()
                    self._skip_type_annotation()
                if self._is_op(","):
                    self.consume()
            self.expect(TOK_OP, ")")
        expr = None
        if self._is_op("="):
            if fields:
                raise ImporSyntaxError(
                    f"带载荷的 enum 成员 {ename}.{member} 不能指定整数值",
                    tok.line, tok.col, self.filename, hint_for("stmt_start"))
            self.consume()
            expr = self.expr()
        return member, expr, fields, tok.line, tok.col

    def _parse_enum(self, t):
        """enum 声明 → 整型常量或带载荷 ADT（v3.8 / v3.2.1）。

        无载荷（兼容 3.1）：
            enum Color { RED, GREEN = 5, BLUE }
        带载荷：
            enum Result { Ok(value), Err(message) }
            enum Option { Some(value), None }

        无载荷变体脱糖为 `__enum_类型_成员` 整型常量；带载荷变体脱糖为
        `__adt_类型_成员(字段...)` 构造函数，返回
        `{"__adt": "类型.成员", "字段": 值}`。因此解释器、C 后端和自举
        编译器仍共享普通变量/字典语义。
        """
        ln, cl = t.line, t.col
        self.consume()  # enum
        ename = self.expect(TOK_ID).value
        self._opt_colon()

        brace = False
        if self._is_op("{"):
            brace = True
            self.consume()
        elif self.peek().type == TOK_NEWLINE:
            self.consume()
            if self._is_op("{"):
                brace = True
                self.consume()
            else:
                self.expect(TOK_INDENT)
        else:
            raise ImporSyntaxError(
                "enum 需要大括号块或缩进块", ln, cl, self.filename,
                hint_for("stmt_start"))

        members = []
        seen = set()
        auto = 0
        closed = False
        while True:
            if brace and self._is_op("}"):
                self.consume()
                closed = True
                break
            if not brace and self.peek().type == TOK_DEDENT:
                self.consume()
                closed = True
                break
            if self.peek().type in (TOK_NEWLINE, TOK_INDENT, TOK_DEDENT):
                self.consume()
                continue
            if self._is_op(";") or self._is_op(","):
                self.consume()
                continue
            if self.peek().type == TOK_EOF:
                break
            member, expr, fields, mln, mcl = self._parse_enum_member(ename, seen, ln, cl)
            value = auto if expr is None else self._enum_const(expr, ename, member, mln, mcl)
            seen.add(member)
            members.append((member, value, fields, mln, mcl))
            auto = value + 1

        if not closed:
            raise ImporSyntaxError(
                ("enum 缺少 } 闭合" if brace else "enum 缺少缩进块结束"),
                ln, cl, self.filename, hint_for("unclosed_block"))
        if not members:
            raise ImporSyntaxError(
                f"enum {ename} 至少需要一个成员", ln, cl, self.filename,
                hint_for("stmt_start"))

        has_payload = any(fields for (_m, _v, fields, _mln, _mcl) in members)
        mapping = {}
        stmts = []
        for member, value, fields, mln, mcl in members:
            vname = "__enum_%s_%s" % (ename, member)
            mapping[member] = vname
            tag = "%s.%s" % (ename, member)
            if has_payload:
                if fields:
                    cname = "__adt_%s_%s" % (ename, member)
                    mapping[member] = cname
                    body = [Return(
                        DictLit(
                            [(Str("__adt", mln, mcl), Str(tag, mln, mcl))] +
                            [(Str(f, mln, mcl), Var(f, mln, mcl)) for f in fields],
                            mln, mcl),
                        mln, mcl)]
                    stmt = Def(cname, list(fields), body, mln, mcl)
                    stmt.is_async = False
                    stmt.is_adt = True
                    stmt.is_enum = True
                    stmt.enum_type = ename
                    stmt.enum_member = member
                    stmt.payload_fields = list(fields)
                    stmt.visibility = "public"
                else:
                    stmt = Assign(
                        vname,
                        DictLit([(Str("__adt", mln, mcl), Str(tag, mln, mcl))], mln, mcl),
                        "global", mln, mcl)
                    stmt.is_enum = True
                    stmt.enum_type = ename
                    stmt.enum_member = member
                    stmt.is_adt = True
                    stmt.payload_fields = []
            else:
                stmt = Assign(vname, Num(value, mln, mcl), "global", mln, mcl)
                stmt.is_enum = True
                stmt.enum_type = ename
                stmt.enum_member = member
            stmts.append(stmt)
        self._enum_members[ename] = mapping
        self._enum_order[ename] = list(mapping.keys())
        if has_payload:
            self._adt_types[ename] = {m: list(f) for (m, _v, f, _ln, _cl) in members}
        return Block(stmts, ln, cl)

    def _enum_attr(self, node, attr, ln, cl):
        """把 `枚举.成员` 改写为脱糖变量/构造器；非枚举保持普通属性访问。"""
        if isinstance(node, Var) and node.name in self._enum_members:
            mapping = self._enum_members[node.name]
            vname = mapping.get(attr)
            if vname is None:
                raise ImporSyntaxError(
                    f"enum {node.name} 没有成员 {attr!r}",
                    ln, cl, self.filename, hint_for("attr"))
            return Var(vname, node.lineno, node.col)
        return AttrGet(node, attr, ln, cl)

    def _parse_struct(self, t):
        """struct 声明 → ClassDef + 自动 __init__（v3.7）。

        语法（字段必须带默认值，字段名即构造参数名）：
            struct Point { var x = 0; var y = 0 }
            struct Point
                var x = 0
                var y = 0
        生成的 __init__ 等价于：
            def __init__(x, y)
                set self.x = x
                set self.y = y
        """
        ln, cl = t.line, t.col
        self.consume()  # struct
        name = self.expect(TOK_ID).value
        self._opt_colon()
        body = self._parse_block()

        fields = []
        for s in body:
            if not isinstance(s, Assign):
                raise ImporSyntaxError(
                    "struct 体内只能声明字段（var 字段名 = 默认值）",
                    getattr(s, "lineno", ln), getattr(s, "col", cl),
                    self.filename, hint_for("stmt_start"))
            if s.name in fields:
                raise ImporSyntaxError(
                    f"struct {name} 字段 {s.name!r} 重复声明",
                    s.lineno, s.col, self.filename, hint_for("stmt_start"))
            fields.append(s.name)
        if not fields:
            raise ImporSyntaxError(
                f"struct {name} 至少需要一个字段",
                ln, cl, self.filename, hint_for("stmt_start"))

        init_body = []
        for f in fields:
            init_body.append(AttrSet(Var("self", ln, cl), f, Var(f, ln, cl), ln, cl))
        init = Def("__init__", list(fields), init_body, ln, cl)
        cd = ClassDef(name, None, list(body) + [init], ln, cl)
        cd.is_struct = True
        cd.struct_fields = list(fields)
        return cd

    def _brace_block(self, bln=0):
        """大括号块体：跳过缩进/换行 token，解析语句直到 } 闭合。
        bln：大括号起始行（缺 } 时用于报错定位）。"""
        st = []
        pending_docs = []
        while True:
            pending_docs.extend(self._take_docs())
            t = self.peek()
            if t.type == TOK_OP and t.value == "}":
                self.consume()
                break
            if t.type in (TOK_NEWLINE, TOK_INDENT, TOK_DEDENT):
                self.consume()
                continue
            if t.type == TOK_OP and t.value == ";":
                self.consume()
                continue
            if t.type == TOK_EOF:
                raise ImporSyntaxError(
                    "代码块缺少 } 闭合（检查大括号是否成对）", bln or t.line, t.col,
                    self.filename, hint_for("unclosed_block"))
            node = self.stmt()
            if pending_docs:
                try:
                    node.doc = "\n".join(pending_docs)
                except (AttributeError, TypeError):
                    pass
                pending_docs = []
            st.append(node)
        return st

    def _parse_c_for(self, ln, cl):
        """v3.4：C 风格计数循环 —— for i = 0; i < 3; i++ { }
        仅支持「i++ / i-- 自增自减 + < / <= / > / >= 单调结束」；降级成 for i in range(...)。"""
        var = self.expect(TOK_ID).value
        self.consume()  # = / :=
        start = self.expr()
        self.expect(TOK_OP, ";")
        cvar = self.expect(TOK_ID).value
        op = self.expect(TOK_OP).value
        if op not in ("<", "<=", ">", ">="):
            raise ImporSyntaxError("for 条件只支持 < / <= / > / >=", ln, cl,
                                   self.filename, hint_for("expect"))
        bound = self.expr()
        self.expect(TOK_OP, ";")
        svar = self.expect(TOK_ID).value
        step = self.expect(TOK_OP).value
        if step not in ("++", "--"):
            raise ImporSyntaxError("for 步进只支持 i++ / i--", ln, cl,
                                   self.filename, hint_for("expect"))
        if cvar != var or svar != var:
            raise ImporSyntaxError("for 头部必须是同一个计数器变量", ln, cl,
                                   self.filename, hint_for("lvalue"))
        one = Num(1, ln, cl)
        if op in ("<", "<=") and step == "--":
            raise ImporSyntaxError(
                "递减步进 i-- 不能配 < / <= 条件（循环不会终止），请改用 > / >=",
                ln, cl, self.filename, hint_for("stmt_start"))
        if op in (">", ">=") and step == "++":
            raise ImporSyntaxError(
                "递增步进 i++ 不能配 > / >= 条件（循环不会终止），请改用 < / <=",
                ln, cl, self.filename, hint_for("stmt_start"))
        if op == "<":
            args = [start, bound]
        elif op == "<=":
            args = [start, BinOp(bound, "+", one, ln, cl)]
        elif op == ">":
            # i > M：range(start, M, -1) 取到 M+1 为止，正好不含 M。
            args = [start, bound, Num(-1, ln, cl)]
        else:
            # i >= M：range(start, M - 1, -1) 会包含 M。
            args = [start, BinOp(bound, "-", one, ln, cl), Num(-1, ln, cl)]
        self._opt_colon()
        bd = self._parse_block()
        return For(var, Call(Var("range", ln, cl), args, ln, cl), bd, ln, cl)

    def _parse_params(self):
        """v3.4 参数列表四种写法都接受：
        func f(a, b) / func f(a: int, b: str) / func f a, b / func f
        v3.8.4：默认参数 / 变参尚未实现，语法阶段明确拒绝。
        """
        params = []
        self._last_param_types = []
        if self._is_op("("):
            self.consume()
            self._skip_fill()
            while not self._is_op(")"):
                params.append(self._param_name())
                self._reject_unsupported_param()
                self._skip_fill()
                if self._is_op(","):
                    self.consume()
                    self._skip_fill()
                    continue
                if self.peek().type == TOK_ID:  # 省略逗号
                    continue
                break
            self._skip_fill()
            self.expect(TOK_OP, ")")
            return params
        # 无括号形式
        if self.peek().type == TOK_ID and self.peek().value not in ("then", "do"):
            params.append(self._param_name())
            self._reject_unsupported_param()
            while self._is_op(","):
                self.consume()
                self._skip_fill()
                params.append(self._param_name())
                self._reject_unsupported_param()
        return params

    def _reject_unsupported_param(self):
        t = self.peek()
        if t.type == TOK_OP and t.value in ("=", "...", "?"):
            name = {"=": "默认参数", "...": "变参", "?": "可选参数"}[t.value]
            raise ImporSyntaxError(
                f"{name}尚未实现（v3.9 计划）", t.line, t.col,
                self.filename, hint_for("stmt_start"))

    def _param_name(self):
        name = self.expect(TOK_ID).value
        if self._is_op(":"):
            self.consume()
            self._last_param_types.append(self._skip_type_annotation())
        else:
            self._last_param_types.append("")
        return name

    def _unimplemented(self, t, name):
        raise ImporSyntaxError(
            f"{name} 尚未实现（v3.9 计划）；v3.8.4 不再把它当成 null 静默执行",
            t.line, t.col, self.filename, hint_for("stmt_start"))

    def stmt(self):
        t = self.peek()

        # v3.8.4：语义诚实——保留关键字未实现时必须在语法阶段拒绝。
        if t.type == TOK_KEY and t.value in ("yield", "with"):
            self._unimplemented(t, t.value)

        # v2.18：顶层大括号块 `{ ... }`（省略缩进的独立代码块）
        if t.type == TOK_OP and t.value == "{":
            bln, bcl = self._peek_ln_col()
            self.consume()
            bd = self._brace_block(bln)
            return Block(bd, bln)

        # 作用域修饰：nonlocal set / global set
        if t.type == TOK_KEY and t.value in ("nonlocal", "global"):
            scope = t.value
            ln, cl = t.line, t.col
            self.consume()
            self.expect(TOK_KEY, "set")
            vn = self.expect(TOK_ID).value
            self.expect(TOK_OP, "=")
            exp = self.expr()
            return Assign(vn, exp, scope, ln, cl)

        # const / static / readonly 只读常量声明（v2.17/v2.18）；v3.7.2 后两者为软关键字，
        # 只有后接声明名时才按修饰符解析，`static = 1` 仍可作普通赋值。
        if (t.type in (TOK_ID, TOK_KEY) and t.value in ("const", "static", "readonly")
                and self.peek(1).type in (TOK_ID, TOK_KEY)
                and not (self.peek(1).type == TOK_OP and self.peek(1).value == "=")):
            ln, cl = t.line, t.col
            self.consume()
            vn = self.expect(TOK_ID).value
            ann = ""
            if self._is_op(":"):
                self.consume()
                ann = self._skip_type_annotation()
            exp = Null(ln, cl)
            has_init = False
            if self._is_op(("=", ":=")):
                self.consume()
                exp = self.expr()
                has_init = True
            node = Assign(vn, exp, "const", ln, cl)
            node.type_ann = ann
            node.has_init = has_init
            return node

        # var / let / local / set 声明（支持 set 对象.属性 = 值 / set 数组[下标] = 值）
        if t.type in (TOK_ID, TOK_KEY) and t.value in ("var", "set", "let", "local"):
            ln, cl = t.line, t.col
            self.consume()
            vn = self.expect(TOK_ID).value
            node = Var(vn, ln, cl)
            ann = ""
            if self._is_op(":"):
                self.consume()
                ann = self._skip_type_annotation()
            if self._is_op("."):
                self.consume()
                attr = self.expect(TOK_ID).value
                if self._is_op(("=", ":=")):
                    self.consume()
                exp = self.expr()
                return AttrSet(Var(vn, ln, cl), attr, exp, ln, cl)
            if self._is_op("["):
                self.consume()
                idx = self.expr()
                self.expect(TOK_OP, "]")
                if self._is_op(("=", ":=")):
                    self.consume()
                exp = self.expr()
                return IndexSet(Var(vn, ln, cl), idx, exp, ln, cl)
            # v3.4：初始化可省略（var x / var x: int），未初始化即空值
            exp = Null(ln, cl)
            has_init = False
            if self._is_op(("=", ":=")):
                self.consume()
                exp = self.expr()
                has_init = True
            # v2.10 作用域规范：var 声明局部；set 就近更新（闭包内修改外层变量）
            node = Assign(vn, exp, 'update' if t.value == "set" else 'local', ln, cl)
            node.type_ann = ann
            node.has_init = has_init
            return node

        # v3.7.8：defer 软关键字。只有明确的块引导（{ / : / 换行+缩进）才按延迟块解析，
        # `var defer = 1`、`defer()` 仍可作普通标识符。
        if (t.type in (TOK_ID, TOK_KEY) and t.value == "defer"
                and ((self.peek(1).type == TOK_OP and self.peek(1).value in ("{", ":"))
                     or (self.peek(1).type == TOK_NEWLINE
                         and self.peek(2).type == TOK_INDENT))):
            ln, cl = t.line, t.col
            self.consume()
            if self._is_op(":"):
                self.consume()
            body = self._parse_block()
            self._reject_defer_control(body, ln, cl)
            return Defer(body, ln, cl)

        if t.type == TOK_KEY and t.value == "exception":
            ln, cl = t.line, t.col
            self.consume()
            exc_name = self.expect(TOK_ID).value
            return ExceptionDef(exc_name, ln, cl)

        if t.type == TOK_KEY and t.value == "raise":
            ln, cl = t.line, t.col
            self.consume()
            exc_typename = self.expect(TOK_ID).value
            self.expect(TOK_OP, "(")
            msg_expr = self.expr()
            cause_expr = None
            if self._is_op(",") and self.peek(1).type == TOK_KEY and self.peek(1).value == "from":
                self.consume()  # ,
                self.consume()  # from
                cause_expr = self.expr()
            self.expect(TOK_OP, ")")
            if cause_expr is None and self._is_key("from"):
                self.consume()
                cause_expr = self.expr()
            return Raise(exc_typename, msg_expr, ln, cause_expr, cl)

        # match 是软关键字（不占用普通标识符）：只有语句位置出现才作模式匹配
        if t.type in (TOK_ID, TOK_KEY) and t.value in ("match", "switch"):
            return self._parse_match()

        if t.type == TOK_KEY and t.value == "exit":
            ln, cl = t.line, t.col
            self.consume()
            # v3.8.1：exit(code) 作为带退出码的内建调用；裸 exit 仍是结束脚本语句。
            if self._is_op("("):
                args = self._parse_call_args()
                return ExprStmt(Call(Var("exit", ln, cl), args, ln, cl), ln, cl)
            return ExitScript(ln)

        if t.type == TOK_KEY and t.value == "cls":
            n = ClsOut(t.line)
            self.consume()
            return n

        # v3.4：print 可省略括号 —— print "hi", 名字；也接受 print(...)（走内建调用）
        if t.type in (TOK_ID, TOK_KEY) and t.value == "print" and not (
                self.peek(1).type == TOK_OP and self.peek(1).value == "("):
            ln, cl = t.line, t.col
            self.consume()
            items = []
            while (self.peek().type not in (TOK_NEWLINE, TOK_EOF, TOK_DEDENT)
                   and not self._is_op((";", "}"))):
                items.append(self.expr())
                if self._is_op(","):
                    self.consume()
                    continue
                if self.peek().type in (TOK_NEWLINE, TOK_EOF, TOK_DEDENT):
                    break
                break
            return Print(items, ln, cl)

        if t.type == TOK_KEY and t.value == "print":
            ln, cl = t.line, t.col
            self.consume()
            self.expect(TOK_OP, "(")
            items = []
            while not self._is_op(")"):
                items.append(self.expr())
                if self._is_op(","):
                    self.consume()
            self.expect(TOK_OP, ")")
            return Print(items, ln, cl)

        if t.type == TOK_KEY and t.value == "break":
            n = Break(t.line)
            self.consume()
            return n

        if t.type == TOK_KEY and t.value == "continue":
            n = Continue(t.line)
            self.consume()
            return n

        # v3.4：unless 条件 { } —— if 取反写法
        if t.type in (TOK_ID, TOK_KEY) and t.value == "unless":
            ln, cl = t.line, t.col
            self.consume()
            cond = self.expr()
            self._opt_colon()
            bd = self._parse_block()
            return If(UnaryOp("not", cond, ln, cl), bd, [], [], ln, cl)

        # v3.4：do { } until 条件 —— 先执行一次再判断（也接受 do { } while 条件）
        if t.type in (TOK_ID, TOK_KEY) and t.value == "do":
            ln, cl = t.line, t.col
            nxt0 = self.peek(1)
            if nxt0.type == TOK_OP and nxt0.value in ("{", ":"):
                self.consume()
                self._opt_colon()
                bd = self._parse_block()
                negate = True
                nde = self.peek()
                self._skip_fill()
                nde = self.peek()
                if nde.type in (TOK_ID, TOK_KEY) and nde.value == "while":
                    self.consume()
                    negate = False
                elif nde.type in (TOK_ID, TOK_KEY) and nde.value == "until":
                    self.consume()
                else:
                    raise ImporSyntaxError(
                        "do 代码块之后需要 until / while 条件", nde.line, nde.col,
                        self.filename, hint_for("expect"))
                cond = self.expr()
                if negate:
                    cond = UnaryOp("not", cond, ln, cl)
                seed = getattr(self, "_do_seed", 0) + 1
                self._do_seed = seed
                flag = "__do%d" % seed
                first = Assign(flag, Bool(True, ln, cl), 'local', ln, cl)
                clear = Assign(flag, Bool(False, ln, cl), 'local', ln, cl)
                check = BinOp(Var(flag, ln, cl), "or", cond, ln, cl)
                return Block([first, While(check, [clear] + bd, ln, cl)], ln)

        if t.type == TOK_KEY and t.value == "if":
            ln, cl = t.line, t.col
            self.consume()
            cond = self.expr()
            self._opt_colon()
            bd = self._parse_block()
            eif_list = []
            while self._is_key(("elseif", "elif")):
                eln, cl = self._peek_ln_col()
                self.consume()
                ec = self.expr()
                self._opt_colon()
                ebd = self._parse_block()
                eif_list.append((ec, ebd))
            elsbd = []
            if self._is_key("else"):
                self.consume()
                self._opt_colon()
                elsbd = self._parse_block()
            return If(cond, bd, eif_list, elsbd, ln, cl)

        if t.type == TOK_KEY and t.value == "while":
            ln, cl = t.line, t.col
            self.consume()
            cond = self.expr()
            self._opt_colon()
            bd = self._parse_block()
            return While(cond, bd, ln, cl)

        if t.type == TOK_KEY and t.value == "loop":
            ln, cl = t.line, t.col
            self.consume()
            self._opt_colon()
            bd = self._parse_block()
            return While(Bool(True, ln, cl), bd, ln, cl)

        if t.type in (TOK_ID, TOK_KEY) and t.value in ("for", "repeat", "each"):
            ln, cl = t.line, t.col
            self.consume()
            # v3.4：C 风格计数循环 for i = 0; i < 3; i++ { }
            if (t.value == "for" and self.peek().type == TOK_ID
                    and self.peek(1).type == TOK_OP and self.peek(1).value in ("=", ":=")):
                return self._parse_c_for(ln, cl)
            # v3.4：for each x in 表 —— each 可省略的引导词
            if self.peek().type in (TOK_ID, TOK_KEY) and self.peek().value == "each":
                self.consume()
            # v2.18：repeat N { } 按次数重复（等价 for __r in range(N)）；repeat/each x in 集合 为 for 别名
            is_for_in = (self.peek().type == TOK_ID
                         and self.peek(1).type in (TOK_ID, TOK_KEY)
                         and self.peek(1).value in ("in", "of"))
            if t.value == "repeat" and not is_for_in:
                count = self.expr()
                self._opt_colon()
                bd = self._parse_block()
                return For("__r", Call(Var("range", ln, cl), [count], ln, cl), bd, ln, cl)
            var = self.expect(TOK_ID).value
            # v3.4：of 也可作 in 的别名（for x of 表）
            if self.peek().type == TOK_KEY and self.peek().value == "in":
                self.consume()
            elif self.peek().type in (TOK_ID, TOK_KEY) and self.peek().value in ("in", "of"):
                self.consume()
            else:
                self.expect(TOK_KEY, "in")
            ite = self.expr()
            # v3.4：区间写法 for i in 1..5（闭区间，等价 range(1, 5 + 1)）
            if self._is_op(".."):
                self.consume()
                end = self.expr()
                one = Num(1, ln, cl)
                ite = Call(Var("range", ln, cl),
                           [ite, BinOp(end, "+", one, ln, cl)], ln, cl)
            self._opt_colon()
            bd = self._parse_block()
            return For(var, ite, bd, ln, cl)

        if (t.type in (TOK_ID, TOK_KEY) and t.value == "async"
                and self.peek(1).type in (TOK_ID, TOK_KEY)
                and self.peek(1).value in ("def", "func", "fn")):
            ln, cl = t.line, t.col
            self.consume()
            if not (self.peek().type in (TOK_ID, TOK_KEY)
                    and self.peek().value in ("def", "func", "fn")):
                raise ImporSyntaxError(
                    "async 只能修饰函数声明（async func 名称(...)）",
                    ln, cl, self.filename, hint_for("stmt_start"))
            self.consume()  # def/func/fn
            name = self.expect(TOK_ID).value
            params = self._parse_params()
            ptypes = list(getattr(self, "_last_param_types", []))
            ret_type = ""
            if self._is_op("->"):
                self.consume()
                ret_type = self._skip_type_annotation()
            self._opt_colon()
            bd = self._parse_block()
            node = Def(name, params, bd, ln, cl)
            node.param_types = ptypes
            node.ret_type = ret_type
            node.is_async = True
            return node

        if t.type in (TOK_ID, TOK_KEY) and t.value in ("def", "func", "fn"):
            ln, cl = t.line, t.col
            self.consume()
            name = self.expect(TOK_ID).value
            params = self._parse_params()
            ptypes = list(getattr(self, "_last_param_types", []))
            ret_type = ""
            if self._is_op("->"):  # v7.3：返回类型进入 AST，供类型检查
                self.consume()
                ret_type = self._skip_type_annotation()
            self._opt_colon()
            bd = self._parse_block()
            node = Def(name, params, bd, ln, cl)
            node.param_types = ptypes
            node.ret_type = ret_type
            return node

        if t.type == TOK_KEY and t.value == "return":
            ln, cl = t.line, t.col
            self.consume()
            if self.peek().type in (TOK_NEWLINE, TOK_DEDENT, TOK_EOF):
                return Return(None, ln, cl)
            e = self.expr()
            return Return(e, ln, cl)

        # v2.18：预留关键字（已保留为关键字，语义后续版本实现）
        if t.type == TOK_KEY and t.value == "until":
            raise ImporSyntaxError(
                f"关键字 {t.value!r} 已保留（v2.18 预留，语义将在后续版本实现）",
                t.line, t.col, self.filename, hint_for("stmt_start"))

        if t.type == TOK_KEY and t.value == "import":
            return self._parse_import()

        if t.type == TOK_KEY and t.value == "from":
            return self._parse_from_import()

        if t.type == TOK_KEY and t.value == "use":
            # v2.12 use 模块：把模块全部导出符号注入当前作用域（便捷星导入）
            ln, cl = t.line, t.col
            self.consume()
            mod = self._parse_dotted_name()
            self._opt_colon()
            return FromImportNode(mod, 0, [], True, ln, cl)

        if t.type == TOK_KEY and t.value == "export":
            return self._parse_export()

        if t.type == TOK_KEY and t.value == "assert":
            ln, cl = t.line, t.col
            col = t.col
            self.consume()
            cond = self.expr()
            self._opt_colon()
            return AssertNode(cond, ln, col)

        if t.type == TOK_KEY and t.value == "try":
            ln, cl = t.line, t.col
            self.consume()
            self._opt_colon()
            trybd = self._parse_block()
            self._skip_fill()
            self.expect(TOK_KEY, "catch")
            et, ev = None, None
            self._skip_fill()
            # v3.4：catch 后的变量可省略，也可加括号：catch { } / catch (e) { }
            if self._is_op("("):
                self.consume()
                self._skip_fill()
                first = self.expect(TOK_ID).value
                self._skip_fill()
                if self._is_op(","):
                    self.consume()
                    self._skip_fill()
                    et = first
                    ev = self.expect(TOK_ID).value
                else:
                    ev = first
                self._skip_fill()
                self.expect(TOK_OP, ")")
            else:
                nxt1 = self.peek()
                if nxt1.type == TOK_ID:
                    nxt2 = self.peek(1)
                    if nxt2.type == TOK_ID:
                        et = self.expect(TOK_ID).value
                        ev = self.expect(TOK_ID).value
                    elif nxt2.type == TOK_OP and nxt2.value == ",":
                        et = self.expect(TOK_ID).value
                        self.expect(TOK_OP, ",")
                        ev = self.expect(TOK_ID).value
                    else:
                        ev = self.expect(TOK_ID).value
            ev = ev or "__err"  # 省略变量时用内部名接收异常，catch 体照常执行
            self._opt_colon()
            catchbd = self._parse_block()
            finallybd = []
            self._skip_fill()
            if self._is_key("finally"):
                self.consume()
                self._opt_colon()
                finallybd = self._parse_block()
            return TryCatch(trybd, et, ev, catchbd, finallybd, ln, cl)

        # v3.7：struct 是“显式字段记录”的语法糖：字段仍按声明顺序初始化，
        # 构造参数即字段名（struct Point { var x = 0 var y = 0 } → new Point(3, 4)）。
        # 作为软关键字识别，不改变关键字总量；只认 `struct 名字 {` / `struct 名字` 换行。
        if t.type == TOK_ID and t.value == "struct" and self.peek(1).type == TOK_ID:
            return self._parse_struct(t)

        # v3.8：enum 同为软关键字，只认 `enum 名字 {` / `enum 名字:` 换行，
        # 避免把名为 enum 的普通函数调用（enum foo()）误判成声明。
        if t.type == TOK_ID and t.value == "enum" and self.peek(1).type == TOK_ID:
            nxt = self.peek(2)
            if (nxt.type == TOK_OP and nxt.value in ("{", ":")) or nxt.type == TOK_NEWLINE:
                return self._parse_enum(t)

        # v5.3：修饰符与结构别名；v7.3：保留可见性/只读语义
        if t.type in (TOK_ID, TOK_KEY) and t.value in ("public", "private", "protected", "abstract",
                                             "final", "sealed", "override", "static", "readonly"):
            mod = t.value
            self.consume()
            node = self.stmt()
            if mod in ("public", "private", "protected"):
                try:
                    node.visibility = mod
                except Exception:
                    pass
            if mod in ("static", "readonly"):
                try:
                    node.is_static = True
                except Exception:
                    pass
            if mod == "readonly":
                try:
                    node.is_readonly = True
                    if getattr(node, "scope", "") == "local":
                        node.scope = "const"
                except Exception:
                    pass
            if mod == "abstract":
                try:
                    node.is_abstract = True
                except Exception:
                    pass
            return node

        if t.type in (TOK_ID, TOK_KEY) and t.value in ("interface", "record", "namespace"):
            ln, cl = t.line, t.col
            kind = t.value
            if kind == "record":
                t.value = "struct"
                node = self._parse_struct(t)
                t.value = kind
                return node
            self.consume()
            cn = self.expect(TOK_ID).value
            bn = None
            if kind == "interface" and self._is_op("("):
                self.consume(); self._skip_fill(); bn = self.expect(TOK_ID).value; self._skip_fill(); self.expect(TOK_OP, ")")
            elif self.peek().type in (TOK_ID, TOK_KEY) and self.peek().value == "extends":
                self.consume(); bn = self.expect(TOK_ID).value
            impls = []
            if self.peek().type in (TOK_ID, TOK_KEY) and self.peek().value == "implements":
                self.consume()
                while True:
                    impls.append(self.expect(TOK_ID).value)
                    if self._is_op(","):
                        self.consume(); continue
                    break
            self._opt_colon()
            bd = self._parse_block()
            return ClassDef(cn, bn, bd, ln, cl, impls)

        if t.type == TOK_KEY and t.value == "class":
            ln, cl = t.line, t.col
            self.consume()
            cn = self.expect(TOK_ID).value
            bn = None
            if self._is_op("("):
                self.consume()
                self._skip_fill()
                bn = self.expect(TOK_ID).value
                self._skip_fill()
                self.expect(TOK_OP, ")")
            elif self.peek().type in (TOK_ID, TOK_KEY) and self.peek().value == "extends":
                # v3.4：class A extends B { }
                self.consume()
                bn = self.expect(TOK_ID).value
            impls = []
            if self.peek().type in (TOK_ID, TOK_KEY) and self.peek().value == "implements":
                self.consume()
                while True:
                    impls.append(self.expect(TOK_ID).value)
                    if self._is_op(","):
                        self.consume(); continue
                    break
            self._opt_colon()
            bd = self._parse_block()
            return ClassDef(cn, bn, bd, ln, cl, impls)

        # 表达式语句：赋值 / 复合赋值 / 调用
        # v3.4：前缀 ++/--：++x / --obj.n / ++arr[i]
        if self._is_op(("++", "--")):
            op = self.consume()
            target = self.expr()
            return self._incdec(target, op.value[0] + "=", op.line, op.col)

        expr_node = self.expr()

        # v3.4：后缀 ++/--：x++ / arr[i]--
        if self._is_op(("++", "--")):
            op = self.consume()
            return self._incdec(expr_node, op.value[0] + "=", expr_node.lineno, expr_node.col)

        if self._is_op(("=", ":=")):
            self.consume()
            rhs = self.expr()
            if isinstance(expr_node, Var):
                return Assign(expr_node.name, rhs, 'local', expr_node.lineno, expr_node.col)
            if isinstance(expr_node, IndexGet):
                return IndexSet(expr_node.container, expr_node.idx, rhs, expr_node.lineno, expr_node.col)
            if isinstance(expr_node, AttrGet):
                return AttrSet(expr_node.obj_expr, expr_node.attr_name, rhs, expr_node.lineno, expr_node.col)
            raise ImporSyntaxError("不支持该左值赋值（赋值左侧必须是变量、下标或属性）", expr_node.lineno, expr_node.col, self.filename, hint_for("lvalue"))

        if self._is_op(("+=", "-=", "*=", "/=", "%=")):
            op = self.consume().value
            rhs = self.expr()
            if isinstance(expr_node, Var):
                return CompoundAssign(expr_node.name, op, rhs, 'local', expr_node.lineno, expr_node.col)
            if isinstance(expr_node, IndexGet):
                return IndexSet(expr_node.container, expr_node.idx,
                                BinOp(expr_node, op[0], rhs, expr_node.lineno, expr_node.col), expr_node.lineno, expr_node.col)
            if isinstance(expr_node, AttrGet):
                return AttrSet(expr_node.obj_expr, expr_node.attr_name,
                               BinOp(expr_node, op[0], rhs, expr_node.lineno, expr_node.col), expr_node.lineno, expr_node.col)
            raise ImporSyntaxError("不支持该左值复合赋值", expr_node.lineno, expr_node.col, self.filename, hint_for("lvalue"))

        if isinstance(expr_node, Call):
            return expr_node
        # v2.9 裸表达式语句：任何表达式可独立成行（求值丢弃；函数末行隐式返回）
        return ExprStmt(expr_node, expr_node.lineno, expr_node.col)

        raise ImporSyntaxError(f"无法开始的语句 {t.value!r}", t.line, t.col, self.filename, hint_for("stmt_start"))

    def _incdec(self, target, op, ln, cl):
        """v3.4：把 x++ / x-- / ++x / --x 降级成既有节点（零改动三端）。"""
        one = Num(1, ln, cl)
        if isinstance(target, Var):
            return CompoundAssign(target.name, op, one, 'local', ln, cl)
        if isinstance(target, IndexGet):
            return IndexSet(target.container, target.idx,
                            BinOp(target, op[0], one, ln, cl), ln, cl)
        if isinstance(target, AttrGet):
            return AttrSet(target.obj_expr, target.attr_name,
                           BinOp(target, op[0], one, ln, cl), ln, cl)
        raise ImporSyntaxError("++/-- 只能用于变量、下标或属性", ln, cl,
                               self.filename, hint_for("lvalue"))

    def _parse_import(self):
        ln, cl = self._peek_ln_col()
        self.expect(TOK_KEY, "import")
        t = self.peek()

        if t.type == TOK_ID and t.value == "important" and self.peek(1).type == TOK_OP and self.peek(1).value == ".":
            self.consume()  # important
            self.consume()  # .
            mod = self._parse_dotted_name()
            alias = None
            if self._is_key("as"):
                self.consume()
                alias = self.expect(TOK_ID).value
            return ImportNode(mod, alias, True, ln, cl)

        mod = self._parse_dotted_name()
        alias = None
        if self._is_key("as"):
            self.consume()
            alias = self.expect(TOK_ID).value
        return ImportNode(mod, alias, False, ln, cl)

    def _parse_dotted_name(self):
        parts = [self.expect(TOK_ID).value]
        while self._is_op("."):
            self.consume()
            parts.append(self.expect(TOK_ID).value)
        return ".".join(parts)

    def _parse_from_import(self):
        """from [.  ..] 模块 import 名字 [as 别名], ... | from 模块 import *"""
        ln, cl = self._peek_ln_col()
        self.expect(TOK_KEY, "from")
        rel_level = 0
        # v3.4：`..` 现在是一个完整 token（区间运算符），相对导入要按两级父目录计
        while True:
            if self._is_op(".."):
                self.consume()
                rel_level += 2
            elif self._is_op("."):
                self.consume()
                rel_level += 1
            else:
                break
        if self.peek().type == TOK_ID:
            mod = self._parse_dotted_name()
        else:
            if rel_level == 0:
                raise ImporSyntaxError("from 后需要模块名", ln, cl, self.filename, hint_for("expect"))
            mod = ""
        self.expect(TOK_KEY, "import")
        if self._is_op("*"):
            self.consume()
            return FromImportNode(mod, rel_level, [], True, ln, cl)
        names = []
        while True:
            name = self.expect(TOK_ID).value
            alias = None
            if self._is_key("as"):
                self.consume()
                alias = self.expect(TOK_ID).value
            names.append((name, alias))
            if self._is_op(","):
                self.consume()
                continue
            break
        return FromImportNode(mod, rel_level, names, False, ln, cl)

    def _parse_export(self):
        """export 名字 | export 名字1, 名字2 | export { 名字1, 名字2 }"""
        ln, cl = self._peek_ln_col()
        self.expect(TOK_KEY, "export")
        t = self.peek()
        if t.type in (TOK_ID, TOK_KEY) and t.value in ("def", "func", "fn"):
            ln, cl = t.line, t.col
            self.consume()
            name = self.expect(TOK_ID).value
            params = self._parse_params()
            ptypes = list(getattr(self, "_last_param_types", []))
            ret_type = ""
            if self._is_op("->"):
                self.consume()
                ret_type = self._skip_type_annotation()
            self._opt_colon()
            bd = self._parse_block()
            node = Def(name, params, bd, ln, cl)
            node.param_types = ptypes
            node.ret_type = ret_type
            node.exported = True
            return node
        names = []
        if self._is_op("{"):
            self.consume()
            while True:
                names.append(self.expect(TOK_ID).value)
                if self._is_op(","):
                    self.consume()
                    continue
                break
            self.expect(TOK_OP, "}")
        else:
            names.append(self.expect(TOK_ID).value)
            while self._is_op(","):
                self.consume()
                names.append(self.expect(TOK_ID).value)
        return ExportNode(names, ln, cl)

    def _parse_match(self):
        """match 表达式:
            case 模式:
                语句
        ...
        模式：字面量（数字/字符串/布尔）| _ 通配符 | 名字（绑定任意值）。
        """
        _t0 = self.consume(); ln, cl = _t0.line, _t0.col  # match
        subject = self.expr()
        self._opt_colon()
        in_brace = False  # v2.10：match x { case ... } 大括号形式
        if self._is_op("{"):
            self.consume()
            in_brace = True
        else:
            self.expect(TOK_NEWLINE)
            self.expect(TOK_INDENT)
        cases = []
        elseb = None
        while True:
            t = self.peek()
            if in_brace and t.type in (TOK_NEWLINE, TOK_INDENT, TOK_DEDENT):
                self.consume()
                continue
            if in_brace and t.type == TOK_OP and t.value == "}":
                self.consume()
                break
            if t.type == TOK_DEDENT:
                break
            if (t.type in (TOK_ID, TOK_KEY) and t.value == "case"):
                self.consume()
                pattern = self._parse_pattern()
                self._opt_colon()
                body = self._parse_block()
                cases.append((pattern, body))
                continue
            # 兜底分支：else: / default:（必须位于最后一个分支）
            if (t.type == TOK_KEY and t.value == "else") or \
               (t.type in (TOK_ID, TOK_KEY) and t.value == "default"):
                self.consume()
                self._opt_colon()
                elseb = self._parse_block()
                break
            raise ImporSyntaxError("match 块内需要 case 分支", t.line, t.col, self.filename, hint_for("stmt_start"))
        if not in_brace:
            self.expect(TOK_DEDENT)
        else:
            # else/default 分支 break 后仍需闭合 match 的大括号（跳过换行/缩进残留）
            while self.peek().type in (TOK_NEWLINE, TOK_INDENT, TOK_DEDENT):
                self.consume()
            self.expect(TOK_OP, "}")
        return MatchNode(subject, cases, ln, elseb, cl)

    def _parse_pattern(self):
        t = self.peek()
        if t.type == TOK_ID:
            # v3.8：case Color.RED —— 枚举成员作为整型常量模式（不是绑定变量）
            if t.value in self._enum_members and self.peek(1).type == TOK_OP \
                    and self.peek(1).value == ".":
                ename = t.value
                self.consume()  # Color
                self.consume()  # .
                mt = self.expect(TOK_ID)
                vname = self._enum_members[ename].get(mt.value)
                if vname is None:
                    raise ImporSyntaxError(
                        f"enum {ename} 没有成员 {mt.value!r}",
                        mt.line, mt.col, self.filename, hint_for("attr"))
                adt = self._adt_types.get(ename)
                if adt is None:
                    return Var(vname, mt.line, mt.col)
                # 3.2.1 带载荷 ADT：Type.Member(a, b)；不带括号则只匹配标签。
                fields = list(adt.get(mt.value) or [])
                binds = [None] * len(fields)
                if self._is_op("("):
                    self.consume()
                    binds = []
                    while not self._is_op(")"):
                        if self.peek().type == TOK_EOF:
                            raise ImporSyntaxError(
                                f"模式 {ename}.{mt.value} 缺少 ) 闭合",
                                mt.line, mt.col, self.filename, hint_for("unclosed_block"))
                        bt = self.expect(TOK_ID).value
                        binds.append(None if bt == "_" else bt)
                        if self._is_op(","):
                            self.consume()
                    self.expect(TOK_OP, ")")
                    if len(binds) != len(fields):
                        raise ImporSyntaxError(
                            "模式 %s.%s 需要 %d 个字段绑定，给了 %d 个" % (
                                ename, mt.value, len(fields), len(binds)),
                            mt.line, mt.col, self.filename, hint_for("stmt_start"))
                return AdtPattern(ename, mt.value, binds, mt.line, mt.col, fields)
            self.consume()
            if t.value == "_":
                return BindingPattern(None, t.line, t.col)
            return BindingPattern(t.value, t.line, t.col)
        if t.type in (TOK_NUM, TOK_STR):
            self.consume()
            return Num(t.value, t.line, t.col) if t.type == TOK_NUM else Str(t.value, t.line, t.col)
        if t.type == TOK_KEY and t.value in ("true", "false"):
            self.consume()
            return Bool(t.value == "true", t.line, t.col)
        if self._is_op("-"):
            self.consume()
            num = self.expect(TOK_NUM)
            return UnaryOp("-", Num(num.value, num.line, num.col), num.line, num.col)
        raise ImporSyntaxError(f"不支持的匹配模式 {t.value!r}", t.line, t.col, self.filename, hint_for("stmt_start"))

    def program(self):
        st = []
        while self.peek().type != TOK_EOF:
            if self.peek().type in (TOK_NEWLINE, TOK_DEDENT):
                self.consume()
                continue
            if self._is_op(";"):  # v3.4：分号可作语句分隔（多余分号直接忽略）
                self.consume()
                continue
            st.append(self._stmt_with_doc())
        return Block(st, 0)
