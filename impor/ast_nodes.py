# -*- coding: utf-8 -*-
"""
Impor (I++) AST 节点定义。
所有节点均带 lineno 用于报错定位。
"""

class AST:
    def __init__(self):
        self.lineno = 0
        self.col = 0          # v2.10 源码位置：列号（1 起）
        self.file = None      # v2.10 源码位置：文件名

class Num(AST):
    def __init__(self, v, lineno=0, col=0):
        super().__init__()
        self.val = v          # int 或 float
        self.lineno = lineno
        self.col = col

class Str(AST):
    def __init__(self, v, lineno=0, col=0):
        super().__init__()
        self.val = v
        self.lineno = lineno
        self.col = col

class FStr(AST):
    """v2.9 字符串插值："x = {expr}"。
    parts: [('text', 原始文本(含转义)), ('expr', 表达式源码文本), ...]"""
    def __init__(self, parts, lineno=0, col=0):
        super().__init__()
        self.parts = parts
        self.exprs = []   # 解析后的表达式 AST（parser 填充）
        self.lineno = lineno
        self.col = col

class Bool(AST):
    def __init__(self, v, lineno=0, col=0):
        super().__init__()
        self.val = v
        self.lineno = lineno
        self.col = col

class Null(AST):
    """v2.18：none / nil / null 空值字面量。"""
    def __init__(self, lineno=0, col=0):
        super().__init__()
        self.lineno = lineno
        self.col = col

class Var(AST):
    def __init__(self, n, lineno=0, col=0):
        super().__init__()
        self.name = n
        self.lineno = lineno
        self.col = col

class AttrGet(AST):
    def __init__(self, obj_expr, attr_name, lineno=0, col=0):
        super().__init__()
        self.obj_expr = obj_expr
        self.attr_name = attr_name
        self.lineno = lineno
        self.col = col

class AttrSet(AST):
    def __init__(self, obj_expr, attr_name, value, lineno=0, col=0):
        super().__init__()
        self.obj_expr = obj_expr
        self.attr_name = attr_name
        self.value = value
        self.lineno = lineno
        self.col = col

class BinOp(AST):
    def __init__(self, l, op, r, lineno=0, col=0):
        super().__init__()
        self.left = l
        self.op = op
        self.right = r
        self.lineno = lineno
        self.col = col

class Pipe(AST):
    """v2.9 管道运算符：x |> f(1)  ==  f(x, 1)；x |> f  ==  f(x)"""
    def __init__(self, l, r, lineno=0, col=0):
        super().__init__()
        self.left = l
        self.right = r
        self.lineno = lineno
        self.col = col

class UnaryOp(AST):
    def __init__(self, op, c, lineno=0, col=0):
        super().__init__()
        self.op = op
        self.child = c
        self.lineno = lineno
        self.col = col

class TernaryExpr(AST):
    def __init__(self, cond, te, fe, lineno=0, col=0):
        super().__init__()
        self.cond = cond
        self.true_expr = te
        self.false_expr = fe
        self.lineno = lineno
        self.col = col

# scope: 'local' | 'global' | 'nonlocal'
class Assign(AST):
    def __init__(self, n, e, scope='local', lineno=0, col=0):
        super().__init__()
        self.name = n
        self.expr = e
        self.scope = scope
        self.lineno = lineno
        self.col = col
        # 7.3：保留显式类型标注（var x: int），供 typecheck / 编辑器消费。
        self.type_ann = ""
        self.visibility = ""
        self.has_init = True

class CompoundAssign(AST):
    def __init__(self, n, op, e, scope='local', lineno=0, col=0):
        super().__init__()
        self.name = n
        self.op = op
        self.expr = e
        self.scope = scope
        self.lineno = lineno
        self.col = col

class Print(AST):
    def __init__(self, items, lineno=0, col=0):
        super().__init__()
        self.items = items
        self.lineno = lineno
        self.col = col

class ExprStmt(AST):
    """v2.9 裸表达式语句：`x + 1` 独立成行（求值丢弃）；函数末行自动返回其值"""
    def __init__(self, expr, lineno=0, col=0):
        super().__init__()
        self.expr = expr
        self.lineno = lineno
        self.col = col

class If(AST):
    def __init__(self, cond, body, eif, els, lineno=0, col=0):
        super().__init__()
        self.cond = cond
        self.body = body
        self.elseifs = eif      # [(cond, body), ...]
        self.elseb = els
        self.lineno = lineno
        self.col = col

class While(AST):
    def __init__(self, cond, body, lineno=0, col=0):
        super().__init__()
        self.cond = cond
        self.body = body
        self.lineno = lineno
        self.col = col

class For(AST):
    def __init__(self, var, it, body, lineno=0, col=0):
        super().__init__()
        self.var = var
        self.iter_expr = it
        self.body = body
        self.lineno = lineno
        self.col = col

class Lambda(AST):
    """lambda 表达式：params 参数名列表，expr 单个表达式（隐式返回）。"""
    def __init__(self, params, expr, lineno=0, col=0):
        super().__init__()
        self.params = params
        self.expr = expr
        self.lineno = lineno
        self.col = col


class Await(AST):
    """await 表达式：单线程调度器里等待 Task 完成并取结果。"""
    def __init__(self, expr, lineno=0, col=0):
        super().__init__()
        self.expr = expr
        self.lineno = lineno
        self.col = col


class Def(AST):
    def __init__(self, n, p, body, lineno=0, col=0):
        super().__init__()
        self.name = n
        self.params = p
        self.body = body
        self.lineno = lineno
        self.col = col
        # 7.3：参数/返回类型与可见性修饰符（空字符串 = 未标注 / public）
        self.param_types = []
        self.ret_type = ""
        self.visibility = ""
        self.is_async = False
        self.exported = False

class Return(AST):
    def __init__(self, e, lineno=0, col=0):
        super().__init__()
        self.expr = e
        self.lineno = lineno
        self.col = col

class Call(AST):
    def __init__(self, cal, args, lineno=0, col=0):
        super().__init__()
        self.callee = cal
        self.args = args
        self.lineno = lineno
        self.col = col

class NewInstance(AST):
    def __init__(self, cn, args, lineno=0, col=0):
        super().__init__()
        self.cls_name = cn
        self.args = args
        self.lineno = lineno
        self.col = col

class SuperCall(AST):
    def __init__(self, lineno=0, col=0):
        super().__init__()
        self.lineno = lineno
        self.col = col

class Break(AST):
    def __init__(self, lineno=0, col=0):
        super().__init__()
        self.lineno = lineno
        self.col = col

class Continue(AST):
    def __init__(self, lineno=0, col=0):
        super().__init__()
        self.lineno = lineno
        self.col = col

class ExitScript(AST):
    def __init__(self, lineno=0, col=0):
        super().__init__()
        self.lineno = lineno
        self.col = col

class ClsOut(AST):
    def __init__(self, lineno=0, col=0):
        super().__init__()
        self.lineno = lineno
        self.col = col

class ListLit(AST):
    def __init__(self, items, lineno=0, col=0):
        super().__init__()
        self.items = items
        self.lineno = lineno
        self.col = col

class DictLit(AST):
    def __init__(self, kv, lineno=0, col=0):
        super().__init__()
        self.kv = kv
        self.lineno = lineno
        self.col = col

class IndexGet(AST):
    def __init__(self, c, idx, lineno=0, col=0):
        super().__init__()
        self.container = c
        self.idx = idx
        self.lineno = lineno
        self.col = col

class Slice(AST):
    """切片 a[i:j] / a[:j] / a[i:] / a[i:j:k]。start/end/step 可为 None。"""
    def __init__(self, c, start, end, step, lineno=0, col=0):
        super().__init__()
        self.container = c
        self.start = start
        self.end = end
        self.step = step
        self.lineno = lineno
        self.col = col

class IndexSet(AST):
    def __init__(self, c, idx, v, lineno=0, col=0):
        super().__init__()
        self.container = c
        self.idx = idx
        self.val = v
        self.lineno = lineno
        self.col = col

class ImportNode(AST):
    def __init__(self, mn, alias, is_python, lineno=0, col=0):
        super().__init__()
        self.modname = mn
        self.alias = alias
        self.is_python = is_python
        self.lineno = lineno
        self.col = col

class FromImportNode(AST):
    """from [.  ..] 模块 import 名字 [as 别名], ...  /  from 模块 import *

    rel_level: 0=绝对导入；1=当前包；2=父包……（相对导入仅允许包内使用）
    names: [(原名, 别名或None), ...]；star=True 表示 from 模块 import *
    """
    def __init__(self, mn, rel_level, names, star, lineno=0, col=0):
        super().__init__()
        self.modname = mn
        self.rel_level = rel_level
        self.names = names
        self.star = star
        self.lineno = lineno
        self.col = col

class ExportNode(AST):
    """export 名字 / export 名字1, 名字2 / export { 名字1, 名字2 }"""
    def __init__(self, names, lineno=0, col=0):
        super().__init__()
        self.names = names
        self.lineno = lineno
        self.col = col

class Block(AST):
    def __init__(self, stmts, lineno=0, col=0):
        super().__init__()
        self.stmts = stmts
        self.lineno = lineno
        self.col = col

class Defer(AST):
    """v3.7.8：函数/模块作用域的延迟块；退出作用域时按 LIFO 执行。"""
    def __init__(self, body, lineno=0, col=0):
        super().__init__()
        self.body = body
        self.lineno = lineno
        self.col = col

class TryCatch(AST):
    def __init__(self, b, et, ev, cb, fb, lineno=0, col=0):
        super().__init__()
        self.body = b
        self.err_type = et
        self.err_var = ev
        self.catch_body = cb
        self.finally_body = fb
        self.lineno = lineno
        self.col = col

class ClassDef(AST):
    def __init__(self, cn, bn, body, lineno=0, col=0, interfaces=None):
        super().__init__()
        self.cls_name = cn
        self.base_name = bn
        self.body = body
        self.interfaces = interfaces or []
        self.lineno = lineno
        self.col = col
        self.visibility = ""

class ExceptionDef(AST):
    def __init__(self, exc_name, lineno=0, col=0):
        super().__init__()
        self.exc_name = exc_name
        self.lineno = lineno
        self.col = col

class AssertNode(AST):
    """assert 表达式：条件为假时抛 AssertionError（v2.8）。"""
    def __init__(self, cond, lineno=0, col=0):
        super().__init__()
        self.cond = cond
        self.lineno = lineno
        self.col = col
        self.col = col

class Raise(AST):
    """raise 异常类型("消息") [from 原异常]"""
    def __init__(self, exc_type_name, msg_expr, lineno=0, cause_expr=None, col=0):
        super().__init__()
        self.exc_type_name = exc_type_name
        self.msg_expr = msg_expr
        self.cause_expr = cause_expr
        self.lineno = lineno
        self.col = col


class BindingPattern(AST):
    """match-case 模式：绑定名（匹配任意值并绑定）；name=None 表示通配符 _。"""
    def __init__(self, name, lineno=0, col=0):
        super().__init__()
        self.name = name
        self.lineno = lineno
        self.col = col


class AdtPattern(AST):
    """带载荷 ADT 模式：Type.Member(f1, f2)。

    fields 为字段绑定名列表；None 表示该位置是通配符 _。tag 为 `Type.Member`，
    运行时值用 `{"__adt": tag, "字段名": 值}` 表示。
    """
    def __init__(self, adt_type, member, fields, lineno=0, col=0, field_names=None):
        super().__init__()
        self.adt_type = adt_type
        self.member = member
        self.fields = list(fields or [])
        self.field_names = list(field_names if field_names is not None else (fields or []))
        self.lineno = lineno
        self.col = col

    @property
    def tag(self):
        return "%s.%s" % (self.adt_type, self.member)


class MatchNode(AST):
    """match 表达式: case 模式: 块 ... [else:/default: 块]；cases: [(pattern, body), ...]。"""
    def __init__(self, subject, cases, lineno=0, elseb=None, col=0):
        super().__init__()
        self.subject = subject
        self.cases = cases
        self.elseb = elseb
        # 7.3：subject 是枚举绑定时记录枚举名与成员，供穷尽性检查。
        self.enum_name = None
        self.enum_members = []
        self.lineno = lineno
        self.col = col
