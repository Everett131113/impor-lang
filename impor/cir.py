# -*- coding: utf-8 -*-
"""Impor (I++) 4.5.0 —— IR → C 下降后端（`impor ir --c` / `impor ir --build`）。

这一层是「后端从 IR 下降」的第一块落地：以前 cfast / ctrans 各自 walk AST，
两条路径各写一遍语义，`div` 那类缺陷就是从这里来的。本模块只接受
`impor/ir.py` 的 22 条指令，把它们机械地映射到 `c_runtime.c` 的运行时 API，
**不复用 ctrans 的 AST 遍历**，所以它能当 IR 的独立校验器：

  AST ──lower──▶ IR ──to_c──▶ C ──gcc──▶ exe

覆盖范围与 ir_lower 一致（类 / 异常 / 闭包 / 模块在 lowering 阶段就被拒绝）。
超出范围的指令抛 `CIRUnsupported`，由 CLI 报错，不静默降级。
"""

from .ir import Lit, IRModule
from .ctrans import BUILTINS, RUNTIME_C

__all__ = ["CIRUnsupported", "to_c"]


class CIRUnsupported(Exception):
    def __init__(self, msg, lineno=0):
        super().__init__("IR→C: " + msg)
        self.msg = msg
        self.lineno = lineno


# 二元运算 → 运行时函数
_BIN = {
    "+": "rt_add", "-": "rt_sub", "*": "rt_mul", "/": "rt_div",
    "%": "rt_mod", "**": "rt_pow_impor",
    "and": "rt_and", "or": "rt_or",
}
# 比较运算 → rt_cmp(a, b, "op")
_CMP = {"<", "<=", ">", ">=", "==", "!="}

# C 运行时里返回 void 的内建（调用点不能写成 `dst = rt_xxx(...)`）
VOID_C_BUILTINS = {"rt_seed", "rt_cls", "rt_assert"}


def _cstr(s):
    out = ['"']
    for ch in s:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20:
            out.append("\\x%02x" % ord(ch))
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _mangle(i):
    return "im_%d" % i


def _is_tmp(name):
    """IR 的临时量（t1、t2…）本身就是合法 C 标识符，不需要再改名。"""
    return (isinstance(name, str) and name[:1] == "t"
            and name[1:].isdigit())


def _lit_c(v):
    if v is None:
        return "v_null()"
    if v is True:
        return "v_bool(1)"
    if v is False:
        return "v_bool(0)"
    if isinstance(v, bool):
        return "v_bool(%d)" % (1 if v else 0)
    if isinstance(v, int):
        if v > 9223372036854775807 or v < -9223372036854775808:
            # 与解释器 / cfast / ctrans 同一错误码：交付层只有 int64 一套语义，
            # 超界常量在生成期即判死，绝不产出一个运行期才崩的产物。
            raise CIRUnsupported(
                "E1012：字面量 %d 超出 int64 范围 [%d, %d]，IR 后端拒绝生成"
                % (v, -9223372036854775808, 9223372036854775807))
        return "v_int(%dLL)" % v
    if isinstance(v, float):
        return "v_float(%.17g)" % v
    if isinstance(v, str):
        return "v_str_lit(%s)" % _cstr(v)
    raise CIRUnsupported("无法表示的字面量类型 %r" % type(v).__name__)


def _cid(s):
    """标签 → C 标识符片段（只保留 ASCII 字母数字下划线）。"""
    return "".join(c if (c.isascii() and (c.isalnum() or c == "_")) else "_"
                   for c in str(s))


class _FnGen:
    """单函数代码生成：把 IR 指令序列翻成 C 语句。"""

    def __init__(self, module, fn, names, globals_map, funcs, out_helpers):
        self.module = module
        self.fn = fn
        self.names = names            # impor 名 → C 标识符（参数/局部）
        self.globals_map = globals_map
        self.funcs = funcs            # impor 函数名 → (C 标识符, 参数个数)
        self.helpers = out_helpers    # 需要生成的函数值包装器（去重）
        self.lines = []
        self.temps = []               # 函数体内出现的临时量（按出现顺序）
        self.iter_pairs = []          # [(键容器标识, 游标标识)]
        self.try_meta = {}
        self._scan_tries()
        self._declare_temps()
        for t in self.temps:
            self.names.setdefault(t, t)

    # ---------- 名字解析 ----------
    def c_name(self, name):
        if name in self.names:
            return self.names[name]
        if _is_tmp(name):
            return name
        if name in self.globals_map:
            return self.globals_map[name]
        raise CIRUnsupported("变量 %s 未声明（IR 后端需要静态可解析的名字）" % name)

    def operand(self, x, lineno=0):
        if isinstance(x, Lit):
            return _lit_c(x.value)
        if x in self.names:
            return self.names[x]
        if _is_tmp(x):
            return x
        if x in self.globals_map:
            return self.globals_map[x]
        if x in self.funcs:
            cname, arity = self.funcs[x]
            wname = "fw_" + cname
            self.helpers.append((wname, cname, x, arity))
            return "rt_fn_new(%s, NULL, 0, %s, %d)" % (wname, _cstr(x), arity)
        if x in BUILTINS:
            raise CIRUnsupported(
                "内建 %s 作为值传递（IR 后端只支持直接调用，不支持取函数值）" % x, lineno)
        raise CIRUnsupported("操作数 %s 无法解析" % x, lineno)

    def _scan_tries(self):
        """TRY 结构元信息：标签 → setjmp 槽变量名 / 重抛变量名 / 是否有 catch。"""
        stack = []
        for ins in self.fn.body:
            if ins.op == "TRY_BEGIN":
                tag, rh = ins.args[0], ins.args[3]
                self.try_meta[tag] = {"idx": "_tji_" + _cid(tag),
                                      "rh": rh,
                                      "has_catch": False}
                stack.append(tag)
            elif ins.op == "CATCH_BEGIN" and ins.args[0] in self.try_meta:
                self.try_meta[ins.args[0]]["has_catch"] = True
            elif ins.op == "TRY_CLOSE" and stack:
                stack.pop()

    def _declare_temps(self):
        for ins in self.fn.body:
            for a in ins.args:
                if _is_tmp(a) and a not in self.names:
                    if a not in self.temps:
                        self.temps.append(a)

    def emit(self, text):
        self.lines.append("    " + text)

    # ---------- 指令 ----------
    def gen(self):
        self.emit("rt_set_line(0);")
        for ins in self.fn.body:
            self.instr(ins)
        return self.lines

    def instr(self, ins):
        op, a = ins.op, ins.args
        ln = ins.lineno
        if op == "LABEL":
            self.lines.append("L_%s: ;" % a[0])
            return
        if ln:
            self.emit("rt_set_line(%d);" % ln)
        if op == "CONST":
            self.emit("%s = %s;" % (self.c_name(a[0]), _lit_c(a[1].value)))
        elif op == "LOAD":
            self.emit("%s = %s;" % (self.names[a[0]], self.operand(a[1], ln)))
        elif op == "MOVE":
            self.emit("%s = %s;" % (self.names[a[0]], self.operand(a[1], ln)))
        elif op == "STORE":
            self.emit("%s = %s;" % (self.c_name(a[0]), self.operand(a[2], ln)))
        elif op == "BINOP":
            dst, bop, lhs, rhs = a
            o1 = self.operand(lhs, ln)
            o2 = self.operand(rhs, ln)
            if bop in _CMP:
                self.emit("%s = rt_cmp(%s, %s, %s);" % (self.names[dst], o1, o2, _cstr(bop)))
            elif bop in _BIN:
                self.emit("%s = %s(%s, %s);" % (self.names[dst], _BIN[bop], o1, o2))
            else:
                raise CIRUnsupported("二元运算符 %s" % bop, ln)
        elif op == "UNOP":
            dst, uop, val = a
            v = self.operand(val, ln)
            fn = {"-": "rt_neg", "+": "rt_pos_impor", "not": "rt_not"}.get(uop)
            if not fn:
                raise CIRUnsupported("一元运算符 %s" % uop, ln)
            self.emit("%s = %s(%s);" % (self.names[dst], fn, v))
        elif op == "CALL":
            self.emit(self._call_c(a, ln))
        elif op == "PRINT":
            for k, x in enumerate(a):
                if k:
                    self.emit("rt_print_sep();")
                self.emit("rt_print_val(%s);" % self.operand(x, ln))
            self.emit("rt_print_nl();")
        elif op == "LIST":
            dst = a[0]
            if len(a) == 1:
                self.emit("%s = rt_arr_of(0);" % self.names[dst])
            else:
                self.emit("%s = rt_arr_of(%d, %s);" % (
                    self.names[dst], len(a) - 1,
                    ", ".join(self.operand(x, ln) for x in a[1:])))
        elif op == "DICT":
            dst = a[0]
            if len(a) == 1:
                self.emit("%s = rt_dict_of(0);" % self.names[dst])
            else:
                self.emit("%s = rt_dict_of(%d, %s);" % (
                    self.names[dst], (len(a) - 1) // 2,
                    ", ".join(self.operand(x, ln) for x in a[1:])))
        elif op == "INDEX":
            self.emit("%s = rt_index(%s, %s);" % (
                self.names[a[0]], self.operand(a[1], ln), self.operand(a[2], ln)))
        elif op == "SETINDEX":
            self.emit("rt_setindex(%s, %s, %s);" % (
                self.operand(a[0], ln), self.operand(a[1], ln), self.operand(a[2], ln)))
        elif op == "SLICE":
            self.emit("%s = rt_slice(%s, %s, %s, %s);" % (
                self.names[a[0]], self.operand(a[1], ln), self.operand(a[2], ln),
                self.operand(a[3], ln), self.operand(a[4], ln)))
        elif op == "TOSTR":
            self.emit("%s = rt_tostr(%s);" % (self.names[a[0]], self.operand(a[1], ln)))
        elif op == "CONCAT":
            seed = 'v_str("")'
            for x in a[1:]:
                seed = "rt_add(%s, %s)" % (seed, self.operand(x, ln))
            self.emit("%s = %s;" % (self.names[a[0]], seed))
        elif op == "ITER":
            key = "irk_" + a[0]
            cur = "iri_" + a[0]
            self.iter_pairs.append((key, cur))
            self.emit("%s = rt_iter_keys(%s);" % (key, self.operand(a[1], ln)))
            self.emit("%s = 0;" % cur)
        elif op == "ITER_NEXT":
            item, it, end = a
            key = "irk_" + it
            cur = "iri_" + it
            if (key, cur) not in self.iter_pairs:
                raise CIRUnsupported("ITER_NEXT 缺少对应的 ITER：%s" % it, ln)
            self.emit("if (%s >= rt_iter_n(%s)) goto L_%s;" % (cur, key, end))
            self.emit("%s = rt_iter_at(%s, %s++);" % (self.names[item], key, cur))
        elif op == "JUMP":
            self.emit("goto L_%s;" % a[0])
        elif op == "JUMP_IF_FALSE":
            self.emit("if (!rt_truth(%s)) goto L_%s;" % (self.operand(a[0], ln), a[1]))
        elif op == "ASSERT":
            self.emit("if (!rt_truth(%s)) rt_abort(\"断言失败\");" % self.operand(a[0], ln))
        elif op == "NEW":
            dst = a[0]
            args = a[2:]
            clsname = a[1].value if isinstance(a[1], Lit) else str(a[1])
            tmp, n, decl = self._argv(args, ln, dst)
            self.emit("{ %s%s = rt_new(%s, %s, %d); }" % (
                decl, self.names[dst], _cstr(str(clsname)), tmp, n))
        elif op == "GETATTR":
            name = a[2].value if isinstance(a[2], Lit) else str(a[2])
            self.emit("%s = rt_getattr(%s, %s);" % (
                self.names[a[0]], self.operand(a[1], ln), _cstr(str(name))))
        elif op == "SETATTR":
            name = a[1].value if isinstance(a[1], Lit) else str(a[1])
            self.emit("rt_objset((%s).o, %s, %s);" % (
                self.operand(a[0], ln), _cstr(str(name)), self.operand(a[2], ln)))
        elif op == "CALLMETHOD":
            dst, obj, name = a[0], a[1], a[2]
            args = a[3:]
            tmp, n, decl = self._argv(args, ln, dst)
            self.emit("{ %s%s = rt_method(%s, %s, %s, %d); }" % (
                decl, self.names[dst], self.operand(obj, ln),
                _cstr(str(name.value if isinstance(name, Lit) else name)), tmp, n))
        elif op == "SUPERCALL":
            dst, from_cls, name = a[0], a[1], a[2]
            args = a[3:]
            tmp, n, decl = self._argv(args, ln, dst)
            self.emit("{ %s%s = rt_supercall((%s).o, %s, %s, %s, %d); }" % (
                decl, self.names[dst], self.c_name("self"),
                _cstr(str(from_cls.value if isinstance(from_cls, Lit) else from_cls)),
                _cstr(str(name.value if isinstance(name, Lit) else name)), tmp, n))
        elif op == "TRY_BEGIN":
            m = self.try_meta[a[0]]
            self.emit("{")
            self.emit("volatile int %s = g_jmp_n++;" % m["idx"])
            self.emit("%s = v_int(0);" % self.c_name(m["rh"]))
            # 4.6：走运行时宏 IMPOR_SETJMP（mingw x64 下等价 _setjmp(buf, NULL)），
            # 避免 setjmp 宏把 __builtin_frame_address(0) 存进 SEH 链导致 longjmp 崩。
            self.emit("if (IMPOR_SETJMP(g_jmp_stack[%s]) == 0) {" % m["idx"])
        elif op == "TRY_END":
            m = self.try_meta[a[0]]
            self.emit("g_jmp_n = %s;" % m["idx"])
            self.emit("goto %s_fin;" % m["idx"])
            self.emit("} else {")
            self.emit("g_jmp_n = %s;" % m["idx"])
            if not m["has_catch"]:
                self.emit("%s = v_int(1);" % self.c_name(m["rh"]))
                self.emit("}")
                self.emit("%s_fin: ;" % m["idx"])
        elif op == "CATCH_BEGIN":
            self.emit("%s = rt_err_obj();" % self.names[a[1]])
        elif op == "CATCH_END":
            m = self.try_meta[a[0]]
            self.emit("}")
            self.emit("%s_fin: ;" % m["idx"])
        elif op == "ERR_MATCH":
            typ = a[1].value if isinstance(a[1], Lit) else None
            if typ is None:
                raise CIRUnsupported("ERR_MATCH 的类型必须是字面量类型名", ln)
            self.emit("%s = v_bool(rt_err_matches(%s));" % (
                self.names[a[0]], _cstr(str(typ))))
        elif op == "RAISE":
            typ = a[0].value if isinstance(a[0], Lit) else None
            if typ is None:
                raise CIRUnsupported("RAISE 的类型必须是字面量类型名", ln)
            msg_op = a[1]
            if isinstance(msg_op, Lit):
                ms = _cstr("" if msg_op.value is None else str(msg_op.value))
            else:
                ms = "rt_str_of(rt_tostr(%s))" % self.operand(msg_op, ln)
            self.emit("rt_throw(%s, %s);" % (_cstr(str(typ)), ms))
        elif op == "TRY_CLOSE":
            m = self.try_meta[a[0]]
            self.emit("if (rt_truth(%s)) rt_rethrow(%s);" % (self.c_name(m["rh"]), m["idx"]))
            self.emit("}")
        elif op == "RETURN":
            self.emit("return %s;" % self.operand(a[0], ln))
        else:
            raise CIRUnsupported("未知 IR 指令 %s" % op, ln)

    def _argv(self, args, ln, tag):
        """把参数铺成 C 数组，返回 (数组名, 个数, 声明前缀)。"""
        n = len(args)
        tmp = "%s_a%d" % (tag, n)
        vals = ", ".join(self.operand(x, ln) for x in args)
        return tmp, n, "Val %s[%d] = {%s}; " % (tmp, n if n else 1, vals)

    def _call_c(self, a, ln):
        dst, callee, args = a[0], a[1], a[2:]
        cargs = ", ".join(self.operand(x, ln) for x in args)
        if isinstance(callee, str) and callee in self.funcs:
            cname = self.funcs[callee][0]
            return "%s = %s(%s);" % (self.names[dst], cname, cargs)
        if isinstance(callee, str) and callee == "range":
            if len(args) == 1:
                return "%s = rt_range(%s);" % (self.names[dst], cargs)
            if len(args) == 2:
                return "%s = rt_range3(%s, v_int(1LL));" % (self.names[dst], cargs)
            return "%s = rt_range3(%s);" % (self.names[dst], cargs)
        if isinstance(callee, str) and callee in BUILTINS:
            cfn = BUILTINS[callee]
            if cfn in VOID_C_BUILTINS:
                # C 侧是 `static void`（如 rt_seed / rt_cls）：不能赋给 Val 目的量。
                # 语句位置照常产生副作用，目的量按解释器语义置 null。
                return "(void)%s(%s); %s = v_null();" % (cfn, cargs, self.names[dst])
            return "%s = %s(%s);" % (self.names[dst], cfn, cargs)
        # 函数值 / 变量持有函数：走 rt_call，参数先落数组（C89 兼容的块作用域）
        target = self.operand(callee, ln)
        n = len(args)
        decl = " ; ".join([])
        arr = " ".join("")
        tmp = "%s_arg%d" % (dst, n)
        vals = ", ".join(self.operand(x, ln) for x in args)
        body = "Val %s[%d] = {%s}; %s = rt_call(%s, %s, %d);" % (
            tmp, n if n else 1, vals, self.names[dst], target, tmp, n)
        _ = (decl, arr)
        return "{ " + body + " }"


def _sig(n):
    """n 个 Val 参数的原型片段（0 个时写 void）。"""
    return ", ".join("Val p%d" % i for i in range(n)) if n else "void"


def _collect_names(fn, exclude=None):
    """参数 + STORE local/loop 的名字 → C 标识符（顺序稳定，便于产物 diff）。

    `exclude`：顶层（`__main__`）的 `var` 在解释器里是模块级变量、函数内可见，
    所以它们被提升成全局，不再作为 `f_main` 的局部量。
    """
    exclude = exclude or {}
    names = {}
    for p in fn.params:
        if p in exclude:
            continue
        names[p] = _mangle(len(names))
    for ins in fn.body:
        if ins.op == "STORE" and ins.args[1] in ("local", "loop"):
            name0 = ins.args[0]
            if name0 in exclude:
                continue
            # 注意：用户变量可能恰好叫 `t0`（`impor/std/channel.impor` 就是）。
            # STORE local 是「这是一个变量」的硬证据，所以优先按变量处理（会被 mangle），
            # 不能因为名字形状像临时量就跳过——否则声明侧会漏掉它。
            if name0 not in names:
                names[name0] = _mangle(len(names))
        elif ins.op == "CATCH_BEGIN":
            if ins.args[1] in exclude or ins.args[1] in names:
                continue
            names[ins.args[1]] = _mangle(len(names))
        elif ins.op == "CONST" and isinstance(ins.args[0], str):
            # 4.6：try 的重抛标志（`__rh_L_try_*`）可能只由 CONST 初始化、
            # 没有类型过滤分支时也不会出现 STORE。这里一并收进局部量，
            # 否则 c_name 会走到「变量未声明」。
            dst = ins.args[0]
            if dst in exclude or dst in names or _is_tmp(dst):
                continue
            names[dst] = _mangle(len(names))
    return names


def _collect_globals(module, local_names):
    """跨函数共享的名字（含「在函数里 update 一个顶层变量」这种隐式全局）。"""
    g = {}

    def add(name):
        if name not in g:
            g[name] = "g_%d" % len(g)

    for fn in [module.main] + module.funcs:
        locals_ = local_names[id(fn)]
        for ins in fn.body:
            if ins.op != "STORE":
                continue
            name, mode = ins.args[0], ins.args[1]
            if fn.is_main and mode != "loop":
                add(name)          # 顶层 var 即模块级变量，函数内可见
            elif mode == "global":
                add(name)
            elif mode in ("update", "const") and name not in locals_:
                add(name)
    return g


def to_c(module, source_name=None, emit_main=True):
    """把 IRModule 翻成一份可直接编译的 C 源码（含运行时）。"""
    if not isinstance(module, IRModule):
        raise CIRUnsupported("to_c 需要 IRModule")
    # 4.7：IR 上的三项优化（常量折叠 / 死代码 / 循环不变量外提）。
    # 求值器走同一套，所以优化前后的语义一致性由三路对拍兜住。
    from . import irop as _irop
    _irop.optimize_module(module)
    globals_map = _collect_globals(module, {id(fn): _collect_names(fn)
                                           for fn in [module.main] + module.funcs})
    funcs = {}
    for fn in module.funcs:
        funcs[fn.name] = ("f_%d" % len(funcs), len(fn.params))

    out = ["/* 由 Impor (I++) 4.5.0 IR 下降后端生成，勿手改 */",
           "/* 源：%s */" % (source_name or "<ir>"),
           "#include <stdio.h>",
           "#include <stdlib.h>",
           "#include <string.h>",
           "#include <math.h>",
           RUNTIME_C.rstrip("\n"),
           ""]

    helpers = []
    bodies = []

    # ---- 4.6 类：方法 impl / 字段初始化 impl ----
    cls_meta = []
    for ci_, c in enumerate(module.classes):
        methods = []
        for mi, m in enumerate(c.methods):
            methods.append({"name": m.name, "impl": "f_m%d_%d" % (ci_, mi),
                            "arity": len(m.params)})
        cls_meta.append({
            "cls": c, "cname": "ci_%d" % ci_, "mt": "mt_%d" % ci_,
            "fi": "fi_%d" % ci_,
            "impl_fi": ("f_fi%d" % ci_) if c.field_body else None,
            "methods": methods,
        })

    for fn in module.funcs:
        bodies.append(_gen_fn(module, fn, globals_map, funcs, helpers,
                              "static Val f_%s(%s)" % (
                                  funcs[fn.name][0].split("_")[1],
                                  ", ".join("Val p%d" % i for i in range(len(fn.params))))))
    for meta in cls_meta:
        c = meta["cls"]
        for mi, m in enumerate(c.methods):
            impl = meta["methods"][mi]["impl"]
            bodies.append(_gen_fn(module, m, globals_map, funcs, helpers,
                                  "static Val %s(%s)" % (impl, _sig(len(m.params)))))
        if c.field_body:
            ffn = IRFunction("__fields_%s" % c.name, ["self"], c.field_body)
            bodies.append(_gen_fn(module, ffn, globals_map, funcs, helpers,
                                  "static Val %s(Val p0)" % meta["impl_fi"]))

    main_sig = "static Val f_main(void)"
    if module.main.body:
        bodies.append(_gen_fn(module, module.main, globals_map, funcs, helpers, main_sig))

    # 原型（互相调用需要先声明）
    out.append("/* 函数原型 */")
    for fn in module.funcs:
        cname = funcs[fn.name][0]
        out.append("static Val %s(%s);" % (
            cname, ", ".join("Val p%d" % i for i in range(len(fn.params)))))
    out.append("static Val f_main(void);")
    for meta in cls_meta:
        for mm in meta["methods"]:
            out.append("static Val %s(%s);" % (mm["impl"], _sig(mm["arity"])))
        if meta["impl_fi"]:
            out.append("static Val %s(Val p0);" % meta["impl_fi"])
    seen_w = set()
    for (wname, cname, impor_name, arity) in helpers:
        if wname in seen_w:
            continue
        seen_w.add(wname)
        out.append("static Val %s(void *env, Val *a);" % wname)

    if globals_map:
        out.append("")
        out.append("/* 全局变量 */")
        for name, cname in sorted(globals_map.items(), key=lambda kv: kv[1]):
            out.append("static Val %s;" % cname)

    out.extend(bodies)

    # ---- 4.6 类段：方法包装 / 方法表 / 字段初始化 / ClassInfo ----
    for ci_, meta in enumerate(cls_meta):
        c = meta["cls"]
        out.append("")
        out.append("/* Impor 类：%s%s */" % (c.name, (" < " + c.base) if c.base else ""))
        for mi, m in enumerate(c.methods):
            mm = meta["methods"][mi]
            args = ", ".join("args[%d]" % i for i in range(mm["arity"] - 1))
            out.append("static Val mw_%d_%d(Obj *self, Val *args) { (void)args; return %s(v_obj(self)%s); }"
                       % (ci_, mi, mm["impl"], (", " + args) if args else ""))
        out.append("static const Method %s[] = {" % meta["mt"])
        for mi, m in enumerate(c.methods):
            out.append('    {"%s", mw_%d_%d},' % (m.name, ci_, mi))
        out.append("    {NULL, NULL},")
        out.append("};")
        if meta["impl_fi"]:
            out.append("static void %s(Obj *o) { Val s = v_obj(o); %s(s); }"
                       % (meta["fi"], meta["impl_fi"]))
        else:
            out.append("static void %s(Obj *o) { (void)o; }" % meta["fi"])
        base = ('"%s"' % c.base) if c.base else "NULL"
        out.append('static ClassInfo %s = {%s, %s, %s, %s, %s};'
                   % (meta["cname"], _cstr(c.name), _cstr(c.name), base,
                      meta["mt"], meta["fi"]))

    # 函数值包装器（去重）
    seen = set()
    wrappers = []
    for (wname, cname, impor_name, arity) in helpers:
        if wname in seen:
            continue
        seen.add(wname)
        call = ", ".join("a[%d]" % i for i in range(arity))
        wrappers.append("static Val %s(void *env, Val *a) { (void)env; (void)a; return %s(%s); }"
                        % (wname, cname, call))
    if wrappers:
        out.append("")
        out.append("/* 一等函数包装 */")
        out.extend(wrappers)

    if emit_main:
        out.append("")
        out.append("int main(int argc, char **argv) {")
        out.append("    rt_args_init(argc, argv);")
        for meta in cls_meta:
            out.append("    reg_class(&%s);" % meta["cname"])
        out.append("    (void)f_main();")
        out.append("    return 0;")
        out.append("}")
    return "\n".join(out) + "\n"


def _gen_fn(module, fn, globals_map, funcs, helpers, signature):
    names = _collect_names(fn, globals_map if fn.is_main else None)
    g = _FnGen(module, fn, names, globals_map, funcs, helpers)
    body = g.gen()
    lines = ["", "/* Impor %s */" % ("__main__" if fn.is_main else fn.name),
             signature + " {"]
    order = list(fn.params) + [n for n in names
                               if n not in fn.params and n not in g.temps]
    decls = []
    for n in order:
        decls.append("Val %s = v_null();" % names[n])
    for t in g.temps:
        decls.append("Val %s = v_null();" % t)
    for (key, cur) in g.iter_pairs:
        decls.append("Val %s = v_null();" % key)
        decls.append("long long %s = 0;" % cur)
    if decls:
        lines.append("    " + " ".join(decls))
    for i, p in enumerate(fn.params):
        lines.append("    %s = p%d;" % (names[p], i))
    lines.extend(body)
    lines.append("    return v_null();")
    lines.append("}")
    return "\n".join(lines)
