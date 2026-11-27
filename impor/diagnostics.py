# -*- coding: utf-8 -*-
"""统一诊断系统（v3.6）：四段式「哪里 / 是什么 / 为什么 / 修复」+ 彩色 + 上下文片段。

设计目标（语言宪法第 3 条）：
  - 不输出晦涩内部代号，直接说清「哪里出错、是什么问题、为什么不合法、怎么改」；
  - 支持彩色（自动识别终端；IMPOR_COLOR / NO_COLOR 可强制开关）；
  - 带上下文代码片段与波浪线，定位到行与列。

对外接口：
    render_diagnostic(...)  四段式诊断（CLI / REPL / LSP / 编辑器共用）
    explain(subject, msg)   返回 (为什么, 修复建议)，未命中规则时给通用兜底
    render_error(...)       旧接口（v2.8 起的「行:列 + 源码行 + 波浪线」），保持兼容
"""

import os
import re
import sys

# ---------------- 颜色 ----------------
_RESET = "\x1b[0m"


def _ansi(code):
    return "\x1b[%sm" % code


_C_KIND = "1;31"      # 错误类别：加粗红
_C_POS = "36"         # 位置：青
_C_CARET = "31"       # 波浪线：红
_C_GUTTER = "2"       # 行号槽：暗
_C_WHY = "1;33"       # 为什么：加粗黄
_C_FIX = "1;32"       # 修复：加粗绿
_C_DIM = "2"


def _enable_windows_ansi():
    """Windows 控制台启用 VT 序列（否则 ANSI 会原样显示）。"""
    if os.name != "nt":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-12)  # STDERR
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass


def color_enabled(mode=None, stream=None):
    """判断是否输出彩色。

    mode: None=按环境自动；"always"/"never" 强制。
    优先级：显式 mode > IMPOR_COLOR > NO_COLOR > TERM=dumb > stream.isatty()
    """
    if mode is None:
        mode = os.environ.get("IMPOR_COLOR")
    if mode:
        m = str(mode).strip().lower()
        if m in ("always", "1", "true", "yes", "on"):
            _enable_windows_ansi()
            return True
        if m in ("never", "0", "false", "no", "off"):
            return False
    if os.environ.get("NO_COLOR"):
        return False
    if (os.environ.get("TERM") or "").lower() == "dumb":
        return False
    try:
        stream = stream if stream is not None else sys.stderr
        ok = bool(stream.isatty())
        if ok:
            _enable_windows_ansi()
        return ok
    except Exception:
        return False


def _paint(text, code, on):
    return _ansi(code) + text + _RESET if on else text


# ---------------- 位置解析 ----------------
_POS_RE = re.compile(r"第(\d+)行(?: 列(\d+))?:\s*(.*)", re.S)


def parse_position(message):
    """从 parser 抛出的 SyntaxError 消息中提取 (行号, 列号, 剩余消息)。

    消息形如：第3行 列7: 期望 ID，得到 '='
    无法解析时返回 (None, None, message)。
    """
    m = _POS_RE.match((message or "").strip())
    if m:
        return int(m.group(1)), (int(m.group(2)) if m.group(2) else 0), m.group(3).strip()
    return None, None, message


# ---------------- 知识库：为什么 / 修复 ----------------
_GENERIC_WHY = "Impor 执行到这一步时遇到了未预期的情况。"
_GENERIC_FIX = "检查该行的输入与状态；若怀疑是编译器内部问题，设 IMPOR_DEBUG=1 查看完整调试栈。"

# v4.2：跨后端统一错误码（解释器 / C 后端 / imporc 同一套）
ERROR_CODES = {
    "ZeroDivisionError": "E1001",
    "TypeError": "E1002",
    "IndexError": "E1003",
    "KeyError": "E1004",
    "ValueError": "E1005",
    "AttributeError": "E1006",
    "AssertionError": "E1007",
    "NameError": "E1008",
    "ConstError": "E1009",
    "ImportError": "E1010",
    "ModuleNotFound": "E1015",
    "RecursionError": "E1011",
    "RecursionDeep": "E1011",
    "OverflowError": "E1012",
    "MemoryError": "E1013",
    "ContractError": "E1014",
    # 4.1 稳定性内核（资源闸门）：全部是「预算耗尽」，不是语法或类型问题
    "StepLimit": "E1020",
    "TimeLimit": "E1021",
    "MemoryLimit": "E1022",
    "OutputLimit": "E1023",
    "AllocLimit": "E1024",
    "RuntimeError": "E1099",
}

# 语法/编译期错误码：与运行错误分开编号，永不复用。
# 语法错误在进入运行前就被挡住，因此不需要 subject（异常名）也能附码。
SYNTAX_CODES = {
    "E2001": "未实现的关键字",
    "E2002": "无法解析的记号 / 期望其他记号",
    "E2003": "表达式缺失",
    "E2004": "括号、引号或代码块未闭合",
    "E2005": "缩进层级不一致",
    "E2006": "赋值左侧不可写",
    "E2007": "默认参数 / 变参写法不支持",
    "E2099": "其他语法错误",
}

_SYNTAX_CODE_RULES = [
    (r"已删除的历史写法", "E2002"),
    (r"默认参数|变参|可变参数|缺省参数", "E2007"),
    (r"尚未实现|当前版本不支持|v3\.x 计划|需要块写法", "E2001"),
    (r"表达式缺失|缺少表达式|需要一个值或表达式", "E2003"),
    (r"未闭合|unclosed|缺少闭合|括号.*闭合|缺少 ?}", "E2004"),
    (r"缩进|indent", "E2005"),
    (r"左值|lvalue|赋值左侧", "E2006"),
    (r"无法解析的记号", "E2002"),
    (r"期望 .*得到|期望 ?[A-Z_]+|期望 ID|期望 (数字|字符串|标识符)", "E2002"),
]

# v4.3：`impor explain` 的标题与最小示例（错误码 → 人话）
ERROR_TITLES = {
    "E1001": ("ZeroDivisionError", "除数为 0", "var x = 10 / 0"),
    "E1002": ("TypeError", "类型不匹配", 'var x = 1 + "a"'),
    "E1003": ("IndexError", "下标越界", "var a = [1, 2]\nprint(a[5])"),
    "E1004": ("KeyError", "字典键不存在", 'var d = {"a": 1}\nprint(d["b"])'),
    "E1005": ("ValueError", "值不合法", 'print(num("abc"))'),
    "E1006": ("AttributeError", "属性/方法不存在", "var x = 1\nprint(x.nope)"),
    "E1007": ("AssertionError", "断言失败", "expect(1, 2, \"one\")"),
    "E1008": ("NameError", "名字未定义", "print(nope)"),
    "E1009": ("ConstError", "修改了 const", "const A = 1\nA = 2"),
    "E1010": ("ImportError", "模块/名字导入失败", "import no_such_mod"),
    "E1011": ("RecursionError", "递归过深", "def f()\n    return f()\nf()"),
    "E1012": ("OverflowError", "整数溢出", "var x = 9223372036854775807 + 1"),
    "E1013": ("MemoryError", "内存不足", "var a = list(1000000000)"),
    "E1014": ("ContractError", "接口契约不满足", "interface S { def area() { return 0 } }\nclass A implements S { }"),
    "E1015": ("ModuleNotFound", "模块文件不存在", "import no_such_mod"),
    "E1020": ("StepLimit", "执行步数超过上限（疑似死循环）", "var i = 0\nwhile true\n    set i = i + 1"),
    "E1021": ("TimeLimit", "运行时间超过上限（卡住）", "while true\n    sleep(1)"),
    "E1022": ("MemoryLimit", "进程内存超过上限", "var keep = list()\nwhile true\n    arrappend(keep, arrfill(400000, 0))"),
    "E1023": ("OutputLimit", "输出量超过上限（刷屏）", "while true\n    print(1)"),
    "E1024": ("AllocLimit", "单次分配超过上限", "var a = arrfill(50000000, 0)"),
    "E1099": ("RuntimeError", "未分类运行错误", "print(1)"),
}


def code_info(name_or_code):
    """错误码或异常名 → dict(code,name,title,example,why,fix)；未知返回 None。"""
    key = str(name_or_code or "").strip()
    if key.upper().startswith("E") and key.upper() in SYNTAX_CODES:
        code = key.upper()
        title = SYNTAX_CODES[code]
        return {"code": code, "name": "SyntaxError", "title": title,
                "example": "", "why": "语法在编译期被拒绝；Impor 不会静默忽略不支持的写法。",
                "fix": "按提示改写为已支持的写法；完整清单见 docs/errors.md#%s。" % code}
    if key.upper().startswith("E") and key.upper() in ERROR_TITLES:
        code = key.upper()
        name, title, example = ERROR_TITLES[code]
    else:
        name = key
        code = ERROR_CODES.get(name)
        if not code:
            return None
        title, example = ERROR_TITLES.get(code, (name, ""))[1:]
    why, fix = explain(name, "")
    return {"code": code, "name": name, "title": title,
            "example": example, "why": why, "fix": fix}


def format_code_info(info):
    lines = ["%s  %s（%s）" % (info["code"], info["name"], info["title"]),
             "  为什么：" + info["why"],
             "  修复：" + info["fix"]]
    if info.get("example"):
        lines.append("  示例：")
        for src in info["example"].split("\n"):
            lines.append("    " + src)
    return "\n".join(lines)



def error_code(name):
    """异常名 → 统一错误码；未知统一为 E1099。"""
    return ERROR_CODES.get(str(name or ""), "E1099")


def syntax_code(message):
    """语法错误消息 → 稳定错误码（E2xxx）；无法分类时为 E2099。"""
    for pat, code in _SYNTAX_CODE_RULES:
        if re.search(pat, message or "", re.I):
            return code
    return "E2099"


# (正则, 为什么, 修复)；按顺序匹配第一条命中的规则
_RULES = [
    (r"StepLimit|步数达到上限|执行步数",
     "稳定模式按「语句条数」给预算，`while true` / 条件永不收敛的循环会先耗尽预算。"
     "这一层不判断程序对不对，只保证「有限预算内必停」，不会把终端永久占住。",
     "先检查循环条件是否真的会变（`set i = i + 1` 是否在会执行到的分支里）；"
     "长任务确实需要更多步数时显式放宽 `--max-steps`，并自担风险。"),
    (r"TimeLimit|运行时间 .* 超过上限",
     "墙钟闸门按真实时间计。脚本可能卡在等待（输入/网络/原生调用），"
     "也可能只是单步很慢；预算耗尽即熔断，不再无限等。",
     "确认是否在等外部资源；需要长时间运行的任务用 `--max-seconds` 放宽，"
     "或把长任务拆成多段执行。"),
    (r"MemoryLimit|进程内存 .* 达到上限|内存上限",
     "内存闸门采样进程工作集（RSS）。容器在不断累积（数组只增不减、缓存不清理）时，"
     "增长到上限附近就会被拒绝，而不是等操作系统直接杀进程。",
     "检查循环里是否每次都在新建容器而没有释放；把中间结果及时清空，"
     "或显式放宽 `--max-memory`（单位 MB）。"),
    (r"OutputLimit|输出已写",
     "输出闸门统计所有 `print` 落点。刷屏会把终端/日志/管道拖死，"
     "所以输出量本身也要有预算。",
     "删掉循环里的调试打印；确实要大量输出时写文件而不是刷终端，"
     "或用 `--max-output` 显式放宽（单位 KB）。"),
    (r"AllocLimit|单次分配",
     "分配闸门在真正分配之前检查元素数（O(1) 先算长度）。"
     "一次要一个巨型容器时，检查本身不会先把内存吃掉。",
     "分批处理（一次几万元素，处理完就释放），或改用迭代/流式写法；"
     "确需大容器时放宽 `--max-alloc`。"),
    (r"`print` 无括号|print 无括号",
     "print 是内建函数，不是语句关键字；无括号写法在 v4.0.0 已删除。继续放行会被拆成两条无副作用表达式，表现为 rc=0、零输出。",
     "改成唯一写法 print(表达式[, 表达式...])；例如 print(\"hello\") 或 print(x)。"),
    (r"已删除的历史写法",
     "这个写法属于 v3.x 的兼容层，v4.0.0 已在词法/语法层物理删除，不再降级为其他 AST。",
     "按错误里的提示改成唯一写法：func → def、let/local → var、loop → while true、then → 冒号或大括号块、unless → if not、裸 print → print(...)。"),
    (r"尚未实现（v3\.x|当前版本不支持|不再静默接受",
     "这个保留字/语法还没有在当前发布线实现。Impor 选择明确拒绝，而不是静默忽略后继续执行。",
     "删除该写法，改用已实现的等价写法；不要依赖它当前会产生任何效果。"),
    (r"无法解析的记号",
     "这个记号不允许出现在语句的这个位置，解析器无法把它接进当前语法结构。",
     "检查前一行是否已经写完整（是否漏了逗号、括号或运算符），必要时先把这一行注释掉再定位。"),
    (r"期望 .*得到|期望 ?[A-Z_]+|期望 ID|期望 (数字|字符串|标识符)",
     "语法在这一处要求出现指定的成分，但当前记号不是它。",
     "按提示把该处替换成要求的成分；常见原因是漏写逗号、括号未闭合或关键字拼错。"),
    (r"表达式缺失|需要一个值或表达式|缺少表达式",
     "该位置需要一个值或表达式，但语句在这里提前结束了。",
     "在 =、return、(、[ 之后补上变量、字面量或表达式；也可以先只声明变量，再单独赋值。"),
    (r"头部标记|FormatError|HEADER_MARK|头部",
     "文件缺少 Impor 的头部标记行（v2.3 起已可选，但显式格式检查仍会报）。",
     '在首行补上 important<<DAY<<english("English"):；如果只是想让脚本跑起来，可直接用 impor run。'),
    (r"未闭合|unclosed|缺少闭合|括号.*闭合|缺少 ?}",
     "括号、引号或代码块没有闭合，解析器读到文件末尾仍在等待结尾。",
     "补上缺少的 }、) 或结束引号；编辑器里可用括号匹配快速定位未闭合的位置。"),
    (r"缩进|indent",
     "同一代码块的缩进层级不一致（常见于空格与 Tab 混用）。",
     "同一代码块统一用 4 个空格缩进，并让同级语句左对齐。"),
    (r"未定义|NameError|未定义变量|找不到名字",
     "Impor 不做隐式全局：名字必须先用 var / const / def / import 声明，之后才能使用。",
     "先用 var x = ... 声明，或用 import 引入；同时检查拼写与大小写是否一致。"),
    (r"函数需要 .* 个参数|参数个数|CallError.*参数",
     "调用时传入的实参个数与函数定义不一致。",
     "按函数定义补齐或删减实参；确实需要可选参数时，在函数里给形参一个默认值。"),
    (r"不是可调用对象|不可调用",
     "这个表达式的结果不是函数或类，不能加括号调用。",
     "检查是否被同名变量覆盖（例如用内建名当变量名），或确认函数已经定义、导入成功。"),
    (r"OverflowError|整数溢出|E1012",
     "交付层把整数固定为 int64（−9223372036854775808 ~ 9223372036854775807）。"
     "加减乘越界时报错中止，而不是像 C 那样静默回绕成一个错误的值。",
     "缩小数值范围，或把大数拆成多步并检查边界；确实需要任意精度时用 "
     "`impor run 脚本.impor --big`（C 产物按 int64 编译，不提供大整数）。"),
    (r"ZeroDivisionError|除以 0|除数为 0|division by zero",
     "除数是 0，数学上没有定义，运行无法继续。",
     "在除法前判断除数（if b != 0 { ... }），或先检查数据来源是否可能为 0。"),
    (r"IndexError|下标|越界|索引",
     "下标超出了容器的有效范围（Impor 下标从 0 开始，-1 表示最后一项）。",
     "先用 len(容器) 检查边界，或改用 for x in 容器 / range(len(容器)) 遍历。"),
    (r"ContractError|实现 .* 缺少方法|接口 .* 未定义",
     "类声明了 implements，但接口要求的方法在类（含基类）里找不到。",
     "补齐接口里列出的方法，或从 implements 列表里移除该接口；只声明不实现现在会直接报错。"),
    (r"TypeError|类型不匹配|不能将|不支持 .*类型",
     "操作数的类型不匹配；Impor 不做隐式类型转换。",
     "用 str() / num() / int() / float() 显式转换后再运算，或修正其中一个操作数。"),
    (r"ConstError|常量 .* 不可修改|只读",
     "const 声明的名字是只读的，再次赋值会破坏它的不变式。",
     "确实需要修改就把它声明成 var；否则把赋值改成对另一个变量的计算。"),
    (r"AssertionError|断言失败",
     "assert 后面的条件求值为 false，说明代码假设不成立。",
     "检查断言的前置条件，必要时在断言前打印相关变量，确认哪一步和预期不一致。"),
    (r"递归|RecursionDeep|栈溢出",
     "递归层数超过了上限，继续下去会耗尽调用栈。",
     "检查递归的终止条件是否会到达；也可以把递归改写成循环。"),
    (r"FFIArchError",
     "解释器与目标库的位数不一致：64 位 Python 装不进 32 位 DLL，反之亦然。",
     "用 impor build 产出的 32 位 exe 调用 32 位库，或用 --c-source 以 cl / gcc -m64 编 64 位库；不要把它当成签名问题。"),
    (r"FFIAccessError",
     "原生库不在 FFI 允许清单里；允许清单在编译期烘焙，运行时不能临时扩展。",
     "在 .impor/ffi_allow.json 的 allow 数组登记库文件名（或设置 IMPOR_FFI_ALLOW，分号/逗号都可），然后重新 build。"),
    (r"FFIError",
     "原生库或函数不可用，或 FFI 参数/返回类型与函数签名不匹配。",
     "确认库文件存在、函数名拼写正确，并让 argtypes 与原生函数签名逐项一致；C 后端只支持字面量 argtypes。"),
    (r"AccessError|private 符号|不能跨文件",
     "模块顶层的 private 符号只在定义它的文件内可见，跨文件访问会被拒绝。",
     "把该符号改成 public（去掉 private），或在模块里提供一个公开函数转发；确实要跨文件共享就显式导出。"),
    (r"ValueError|值不合法|JSON 解析失败|正则无效|字节值必须", 
     "传入了无法转换成目标类型的值，或内容不符合该函数的取值范围。",
     "检查输入是否为合法数字 / JSON / 正则；转换前先判断空串和非法字符，不要直接假定输入可信。"),
    (r"ImportError|ModuleNotFound|模块 .* 未找到|导入.*失败",
     "导入的名字在模块搜索路径里找不到对应的文件或包。",
     "确认文件名（含 .impor 后缀）与相对路径正确；可用 impor list-modules 查看当前可发现的模块。"),
    (r"AttributeError|无属性|没有属性",
     "这个对象 / 类 / 实例上没有该属性。",
     "检查属性名拼写；实例属性要在 __init__ 里先赋值，再去读取。"),
    (r"ScopeError|super\(\)|nonlocal",
     "这个名字或调用在当前作用域里不可用。",
     "把代码放到允许的作用域里（例如 super() 只能写在类方法中），或改用显式传参。"),
    (r"OpError|未知运算符|未知单目",
     "这个运算符不适用于当前的操作数，或运算符本身写错了。",
     "检查运算符拼写与两侧类型；逻辑运算请用 and / or / not。"),
    (r"lvalue|左值|赋值左侧",
     "赋值左侧必须是可写目标：变量、下标或属性。",
     "把左侧改成已声明的变量，或检查下标 / 属性表达式是否写错。"),
    (r"C 编译错误|tcc|C 后端",
     "C 后端没能接受生成的 C 代码，通常是某个 Impor 语法还没有对应的 C 生成规则。",
     "先用 impor run 得到解释器级诊断；确认没有超出 C 后端支持的语法子集。"),
    # 3.2.2：Quick Fix 扩面——补齐常见解析 / 类型 / 访问错误
    (r"缺少 return|没有返回|must return|返回值缺失",
     "函数声明了返回类型或调用方需要结果，但执行路径没有 return，得到的是 null。",
     "在函数末尾补 return 表达式；有分支的函数要保证每条分支都返回，或在末尾给默认返回值。"),
    (r"不穷尽|non-exhaustive|match .* 缺少|未覆盖.*分支|缺少分支",
     "match 没有覆盖 ADT/枚举的所有构造子，运行时可能落到没有分支处理的值。",
     "补齐缺少的分支；暂时无法处理时显式写 else / _ 分支，并在分支里 raise 或返回安全值。"),
    (r"重复定义|already defined|重复声明|重名",
     "同一作用域里同名符号被声明了两次，后一个会覆盖前一个，通常不是本意。",
     "改掉其中一个名字；确实需要覆盖时先删除旧声明，或把第二个放进独立作用域。"),
    (r"类型不匹配|期望类型|期望 .* 得到 .*|type mismatch",
     "静态检查发现表达式类型与声明/参数/返回值要求的类型不一致，Impor 不做隐式转换。",
     "按提示改成目标类型（num/str/bool/list/dict），或修正变量声明与函数签名。"),
    (r"await|异步函数|async",
     "async 函数返回的是任务对象，不 await 就直接使用会拿到任务而不是结果。",
     "在调用前加 await，或把调用放进 run_tasks / 事件循环；同步上下文里不要直接 await。"),
    (r"载荷|ADT|构造子|variant|字段数量",
     "ADT 构造子的载荷字段与声明不一致（数量、顺序或类型不对）。",
     "按 ADT 声明补齐载荷；用 match 解构时字段顺序要与声明一致。"),
    (r"FFI|外部函数|native|签名",
     "FFI 调用的参数或返回类型与原生函数签名不匹配，或该后端不支持 FFI。",
     "对照原生函数签名检查参数个数与类型；C 后端/自举目前只保证解释器侧的 FFI。"),
]


def explain(subject, message):
    """返回 (为什么, 修复建议)。subject 为错误类别或异常名，message 为原始信息。"""
    text = "%s: %s" % (subject or "", message or "")
    for pat, why, fix in _RULES:
        if re.search(pat, text, re.I):
            return why, fix
    return _GENERIC_WHY, _GENERIC_FIX


def fix_for(subject, message):
    """只要修复建议（编辑器 / REPL 这类紧凑场景用）。"""
    return explain(subject, message)[1]


_ISSUE_FIX = {
    "undefined-name": "先用 var x = ... 声明，或用 import 引入；也检查拼写与大小写。",
    "unused-variable": "删除它，或把它用在后续逻辑里；仅作调试用途时加 # impor-lint-ignore: unused-variable。",
    "unused-import": "删除这个导入，或在使用处引用它。",
    "unreachable": "删除不可达代码，或检查前面的 return / raise / 提前退出条件。",
    "type-mismatch": "按声明的类型显式转换（num/str/bool），或修正声明与函数签名。",
    "enum-nonexhaustive": "补齐缺少的枚举/ADT 分支，或显式写 else / _ 兜底。",
    "duplicate-def": "改掉重复的名字，或删除旧声明；需要覆盖时放进独立作用域。",
    "private-access": "把符号改为 public，或在定义文件里提供公开转发函数。",
    "missing-return": "在函数末尾补 return；有分支时保证每条分支都返回。",
    "async-without-await": "在调用前加 await，或把调用放进 run_tasks。",
}


def issue_fix(code, message):
    """静态检查（lint / LSP）用的修复建议；风格项已把推荐写法写在消息里，返回空串。"""
    if code and str(code).startswith("style-"):
        return ""
    return _ISSUE_FIX.get(code) or explain(code, message)[1]


# ---------------- 渲染 ----------------
def _display_width(text):
    w = 0
    for ch in text:
        o = ord(ch)
        if o == 0xFEFF or o < 32:   # BOM / 控制字符不占显示宽度
            continue
        w += 2 if o > 0x2E80 else 1
    return w


def _snippet(source_lines, line, col, context, color):
    if not source_lines or not line or not (1 <= line <= len(source_lines)):
        return []
    n = len(source_lines)
    lo = max(1, line - max(0, context))
    hi = min(n, line + max(0, context))
    gw = len(str(hi))
    out = []
    for i in range(lo, hi + 1):
        src = source_lines[i - 1]
        gutter = _paint(str(i).rjust(gw), _C_GUTTER, color)
        if i == line:
            out.append("  %s │ %s" % (gutter, src))
            col1 = max(col or 1, 1)
            disp = _display_width(src[:col1 - 1])
            carets = "^" * max(1, min(len(src) - col1 + 1, 8))
            out.append("  %s │ %s%s" % (" " * gw, " " * disp,
                                       _paint(carets, _C_CARET, color)))
        else:
            out.append("  %s │ %s" % (gutter, src))
    return out


def render_diagnostic(kind, message, line=0, col=0, source_lines=None,
                      filename=None, subject=None, hint=None,
                      why=None, fix=None, color=None, context=1,
                      code=None):
    """四段式诊断渲染。

        <kind>: 第N行 列M: <message>            ← 哪里 + 是什么
          2 │ 上文
          3 │ 出错行
            │      ^
          4 │ 下文
          为什么：...
          修复：...

    hint 传入时作为「修复」的首选（语法层已有的精准建议优先于通用建议）。
    """
    on = color_enabled(color) if not isinstance(color, bool) else color
    if why is None or fix is None:
        w, f = explain(subject or kind, message)
        why = why or w
        fix = fix or f
    if hint:
        fix = hint
    if subject and str(kind) in ("运行错误", "测试失败", "测试失败·语法错误"):
        code = error_code(subject)
    elif code is None and str(kind).startswith("语法"):
        code = syntax_code(message)
    if code:
        tag = "[%s]" % code
        if not str(message).rstrip().endswith(tag):
            lines = str(message).split("\n")
            lines[0] = "%s %s" % (lines[0], tag)
            message = "\n".join(lines)
    head = "%s: " % _paint(str(kind), _C_KIND, on)
    if line:
        pos = "第%d行" % line + (" 列%d" % col if col else "")
        head += _paint(pos, _C_POS, on) + ": "
    msg_lines = str(message).split("\n")
    head += msg_lines[0]
    if filename:
        head += "  " + _paint("· " + str(filename), _C_DIM, on)
    out = [head]
    for extra in msg_lines[1:]:
        if extra.strip():
            out.append("    " + extra.rstrip())
    out.extend(_snippet(source_lines, line, col, context, on))
    out.append("  %s %s" % (_paint("为什么：", _C_WHY, on), why))
    out.append("  %s %s" % (_paint("修复：", _C_FIX, on), fix))
    if code:
        out.append("  %s docs/errors.md#%s" % (_paint("文档：", _C_DIM, on), code))
    return "\n".join(out)


def render_error(source_lines, line, col, kind, message):
    """旧接口（v2.8）：保持「行:列 + 源码行 + 波浪线」格式，供既有调用方使用。

    等价于 render_diagnostic(..., context=0, color=False)，但只输出三段。
    """
    on = False
    head = "%s: 第%d行%s: %s" % (kind, line, (" 列%d" % col if col else ""), message) \
        if line else "%s: %s" % (kind, message)
    out = [head]
    out.extend(_snippet(source_lines, line, col, 0, on))
    return "\n".join(out)
