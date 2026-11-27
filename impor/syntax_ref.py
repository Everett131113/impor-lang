# -*- coding: utf-8 -*-
"""Impor 语法查询数据（v3.6.6）。

每个条目回答四件事：
  意思：这个关键字/内建函数做什么
  格式：怎么写
  例子：可直接抄的最小示例
  解释：例子逐行说明，以及常见误用

数据是查询用的说明，不参与解析；解析器仍以 lexer/parser 为准。
"""
from .lexer import KEYWORDS

ENTRIES = {}


def _add(names, meaning, fmt, example, explain, kind="关键字", status="active"):
    if isinstance(names, str):
        names = [names]
    for name in names:
        ENTRIES[name] = {
            "name": name,
            "kind": kind,
            "meaning": meaning,
            "format": fmt,
            "example": example,
            "explain": explain,
            "status": status,
        }


# ---------- 声明与变量 ----------
_add("var", "声明一个变量并赋值。", "var 名字 = 值",
     'var name = "Impor"\nprint(name)',
     "`var name = \"Impor\"` 创建变量 name；`print(name)` 输出变量里的值，所以输出 `Impor`。")
_add("let", "声明局部变量（let 是 var 的别名，常用于较小作用域）。", "let 名字 = 值",
     'let x = 3\nprint(x)',
     "`let x = 3` 创建变量 x；print 输出 3。和 var 的差别主要是写法，当前版本二者都可声明变量。")
_add("local", "在函数内声明局部变量，不污染外层。", "local 名字 = 值",
     'def f()\n    local n = 2\n    return n\nprint(f())',
     "函数 f 内 `local n = 2` 只在 f 内有效；调用 f() 返回 2，print 输出 2。")
_add("set", "给已有变量或对象属性赋值。", "set 名字 = 值  /  set self.属性 = 值",
     'var a = 1\nset a = 2\nprint(a)',
     "`var a = 1` 先创建 a；`set a = 2` 改写 a；print 输出 2。`set` 不创建新变量，变量不存在会报错。")
_add(["const", "readonly"], "声明只读常量，之后不能再用 set 改写。", "const 名字 = 值",
     "const PI = 3.14\nprint(PI)",
     "`const PI = 3.14` 创建只读常量；print 输出 3.14。若再写 `set PI = 3` 会报错。")
_add("static", "声明类级只读常量/静态成员（写法与 const 类似，直接写名字）。", "static 名字 = 值",
     "class Counter\n    static total = 0\nprint(Counter.total)",
     "`static total = 0` 属于 Counter 类；`Counter.total` 直接访问，不创建实例也能读。")

# ---------- 条件 ----------
_add("if", "条件为真时执行缩进块。", "if 条件:\n    语句",
     'var a = 1\nif a > 0:\n    print("正数")',
     "a 是 1，`a > 0` 为真，所以执行缩进里的 print，输出 `正数`；条件为假时整块跳过。")
_add("else", "if 条件不成立时执行。", "if 条件:\n    语句\nelse:\n    语句",
     'var a = 0\nif a > 0:\n    print("正")\nelse:\n    print("非正")',
     "a 为 0，`a > 0` 不成立，跳过 if 块，进入 else，输出 `非正`。")
_add(["elseif", "elif"], "多分支条件：前面的 if/elseif 都不成立时再判断。", "elseif 条件:\n    语句",
     'var n = 2\nif n == 1:\n    print("一")\nelseif n == 2:\n    print("二")\nelse:\n    print("其他")',
     "n 为 2；第一个条件 n == 1 不成立，第二个 elseif n == 2 成立，输出 `二`。elif 是 elseif 的别名。")

# ---------- 循环 ----------
_add("while", "条件为真时反复执行。", "while 条件:\n    语句",
     'var i = 0\nwhile i < 3:\n    print(i)\n    set i = i + 1',
     "i 从 0 开始；每轮先判断 i < 3，成立就打印并加 1。输出 0、1、2；i 到 3 时条件不成立，循环结束。")
_add("until", "do-until 循环的结束条件：先执行一次，条件为真时停止。", "do {\n    语句\n} until 条件",
     'var i = 0\ndo {\n    print(i)\n    set i = i + 1\n} until i >= 3',
     "先执行一次循环体，打印 0、i 变 1；再判断 i >= 3。重复到 i 为 3 时停止，所以输出 0、1、2。until 不能单独当 while 用。")
_add("for", "遍历区间或数组。", "for 变量 in 序列:\n    语句",
     'for i in 0..3:\n    print(i)',
     "`0..3` 是范围；i 依次取 0、1、2，每取一次执行 print，输出 0、1、2。")
_add("in", "for 循环里连接变量和序列。", "for 变量 in 序列:",
     'for ch in ["a", "b"]:\n    print(ch)',
     "ch 依次取数组里的 a、b，print 分别输出 a 和 b。in 也可以换成 of。")
_add("of", "for 循环里 in 的别名。", "for 变量 of 序列:",
     'for n of [1, 2]:\n    print(n)',
     "n 依次取 1、2，输出 1、2。of 与 in 在这里等价。")
_add("each", "for-each 写法：遍历序列。", "for each 变量 in 序列:",
     'for each x in [10, 20]:\n    print(x)',
     "each 强调逐个取；x 依次为 10、20，输出 10、20。")
_add("repeat", "重复执行固定次数或遍历序列。", "repeat 次数:\n    语句",
     'repeat 3:\n    print("hi")',
     "循环体执行 3 次，输出三行 hi。repeat 不是无限循环。")
_add("loop", "无限循环，靠 break 退出。", "loop:\n    语句",
     'var i = 0\nloop:\n    print(i)\n    set i = i + 1\n    if i >= 2:\n        break',
     "loop 没有条件，会一直执行；i 到 2 时 break 跳出，所以输出 0、1。没有 break 会一直循环。")
_add("break", "立即跳出当前循环。", "break",
     'for i in 0..5:\n    if i == 2:\n        break\n    print(i)',
     "i 为 0、1 时打印；i 到 2 时 break 直接跳出整个 for，所以只输出 0、1，不会打印 2。")
_add("continue", "跳过本轮剩下的语句，进入下一轮。", "continue",
     'for i in 0..4:\n    if i == 2:\n        continue\n    print(i)',
     "i 为 2 时 continue 跳过 print，直接下一轮；输出 0、1、3。")

# ---------- 函数 ----------
_add(["def", "func", "fn"], "定义一个函数。", "def 名字(参数):\n    语句",
     'def add(a, b):\n    return a + b\nprint(add(1, 2))',
     "定义 add，接收 a、b；`return a + b` 返回 3；print 输出 3。func、fn 是 def 的别名。")
_add("return", "从函数返回一个值并结束函数。", "return 值",
     'def f(x):\n    return x * 2\nprint(f(3))',
     "f(3) 返回 6；print 输出 6。return 后面的语句不会再执行。")
_add("lambda", "写一个匿名函数表达式。", "lambda (参数): 表达式  /  lambda 参数: 表达式",
     'var double = lambda x: x * 2\nprint(double(4))',
     "lambda 创建匿名函数，x 是参数，表达式 x * 2 是返回值；double(4) 得到 8。")

# ---------- 布尔与逻辑 ----------
_add("true", "布尔真值。", "true",
     'var ok = true\nif ok:\n    print("yes")',
     "ok 为真，if 条件成立，输出 yes。")
_add("false", "布尔假值。", "false",
     'var ok = false\nif ok:\n    print("yes")\nelse:\n    print("no")',
     "ok 为假，if 不成立，走 else，输出 no。")
_add(["and", "or", "not"], "逻辑与 / 逻辑或 / 逻辑非。", "a and b  /  a or b  /  not a",
     'var a = true\nvar b = false\nprint(a and b)\nprint(a or b)\nprint(not a)',
     "a and b 只有两边都真才真，输出 false；a or b 只要一边真就真，输出 true；not a 取反，输出 false。")

# ---------- 模块 ----------
_add("import", "导入一个模块，用 模块名.符号 访问。", "import 模块名",
     "import mathx\nprint(mathx.sqrt(9))",
     "导入 mathx 后，用 mathx.sqrt(9) 调用模块里的函数，输出 3。")
_add("as", "导入时给模块起别名。", "import 模块名 as 别名",
     "import mathx as m\nprint(m.sqrt(16))",
     "把 mathx 命名为 m；m.sqrt(16) 输出 4。")
_add("from", "从模块里只导入指定符号。", "from 模块名 import 符号",
     "from mathx import sqrt\nprint(sqrt(25))",
     "只导入 sqrt，调用时不用写模块名前缀；sqrt(25) 输出 5。")
_add("export", "把当前文件已有的函数/变量名字导出给其他文件（export 只写名字，不重新声明）。", "export 名字  /  export 名字1, 名字2",
     'def hello()\n    return "hi"\nexport hello\nprint(hello())',
     "先定义 hello，再用 `export hello` 标记导出；本文件内直接调用输出 hi，其他文件 import 后也能用。")
_add("use", "把模块的全部导出符号直接注入当前作用域。", "use 模块名",
     "use mathx\nprint(sqrt(36))",
     "use 相当于星号导入；sqrt 可以直接写，不必加 mathx. 前缀。")

# ---------- 异常 ----------
_add("try", "捕获可能出错的代码块。", "try:\n    语句\ncatch 异常名:\n    处理",
     'try:\n    raise MyErr("坏了")\ncatch MyErr:\n    print("捕获")',
     "try 里主动 raise MyErr；catch MyErr 接住，输出 `捕获`。没有 catch 的错误会中断程序。")
_add("catch", "接住 try 里抛出的指定异常。", "catch 异常名:",
     'try:\n    var x = 1 / 0\ncatch ZeroDivisionError:\n    print("除零")',
     "1 / 0 抛出 ZeroDivisionError；catch 接住后输出 `除零`，程序继续运行。")
_add("finally", "无论是否出错都执行的收尾块；必须跟在 try/catch 后面。", "try:\n    语句\ncatch 异常名:\n    处理\nfinally:\n    语句",
     'exception E\ntry:\n    print("try")\ncatch E:\n    print("catch")\nfinally:\n    print("done")',
     "try 打印 try；没有异常就不进 catch；无论有没有异常，finally 都会打印 done。finally 不能单独使用。")
_add("raise", "主动抛出一个异常。", 'raise 异常名("消息")',
     'exception MyErr\nraise MyErr("出错了")',
     "`exception MyErr` 先定义异常类型；raise MyErr(\"出错了\") 抛出它，未被 catch 时程序报错并显示消息。")
_add("exception", "定义一个自定义异常类型。", "exception 异常名",
     "exception MyErr\ntry:\n    raise MyErr(\"x\")\ncatch MyErr:\n    print(\"ok\")",
     "exception MyErr 创建异常类型；raise 抛它，catch MyErr 接住，输出 ok。")
_add("global", "在函数内声明要修改全局变量；必须配合 set 使用。", "global set 名字 = 新值",
     'var total = 0\ndef add(n)\n    global set total = total + n\nadd(3)\nprint(total)',
     "total 在函数外；函数内 `global set total = ...` 声明要改全局变量，add(3) 后 total 变成 3。")
_add("nonlocal", "在嵌套函数内声明要修改外层函数的局部变量；必须配合 set 使用。", "nonlocal set 名字 = 新值",
     'def outer()\n    var n = 1\n    def inner()\n        nonlocal set n = n + 1\n    inner()\n    return n\nprint(outer())',
     "n 是 outer 的局部变量；inner 里 nonlocal set n = n + 1 改的是外层那个 n，outer 返回 2。")

# ---------- 类与对象 ----------
_add("class", "定义一个类。", "class 类名(父类):\n    语句",
     'class Dog\n    def bark()\n        print("汪")\nvar d = new Dog()\nd.bark()',
     "class Dog 定义类；new Dog() 创建实例 d；d.bark() 调用方法，输出 汪。")
_add("new", "创建类的实例。", "new 类名(参数)",
     'class Point\n    var x = 0\nvar p = new Point()\nprint(p.x)',
     "new Point() 创建实例 p；p.x 是字段，初始 0，输出 0。")
_add("super", "在子类里取得父类实例，用来调用父类方法。", "var 父 = super()\n父.方法()",
     'class A\n    def hi()\n        print("A")\nclass B(A)\n    def hi()\n        var p = super()\n        p.hi()\n        print("B")\nvar b = new B()\nb.hi()',
     "B 继承 A；B.hi 里 super() 取得父类实例 p，p.hi() 打印 A，再打印 B。不能直接写 super().hi()，当前版本会解析失败。")
_add("this", "在方法里指当前实例（self 的别名）。", "this.属性",
     'class Box\n    var n = 1\n    def show()\n        print(this.n)\nvar b = new Box()\nb.show()',
     "this 指当前实例；show 里 this.n 读取字段 n，输出 1。new Box() 要先赋给变量，才能继续 .show()。")
_add("cls", "清屏（清空终端/输出窗口），不是类引用。", "cls",
     'print("第一行")\ncls\nprint("清屏后")',
     "cls 清空终端画面，再打印清屏后。它和 Python 的 cls 类参数无关。")
_add("exit", "立即结束脚本。", "exit",
     'print("开始")\nexit\nprint("不会执行")',
     "print 开始后 exit 结束程序，后面的 print 不会执行。")

# ---------- 断言 ----------
_add("assert", "断言条件为真；为假时抛断言错误。", "assert 条件",
     "var x = 2\nassert x == 2\nprint(\"通过\")",
     "x == 2 为真，assert 通过，继续打印 通过；若 x 不是 2，会抛 AssertionError 并中断。")

# ---------- 模式匹配 ----------
_add(["switch", "match"], "按值/模式分支，类似 C 的 switch。", "switch 值:\n    case 模式:\n        语句\n    default:\n        语句",
     'var n = 2\nswitch n:\n    case 1:\n        print("一")\n    case 2:\n        print("二")\n    default:\n        print("其他")',
     "n 为 2；case 1 不匹配，case 2 匹配，输出 二；default 是兜底。match 是 switch 的别名。")
_add("case", "模式匹配里的一个分支。", "case 模式:",
     'var n = 1\nswitch n:\n    case 1:\n        print("one")',
     "case 1 只在 n 等于 1 时执行；这里输出 one。")
_add("default", "模式匹配的兜底分支。", "default:",
     'var n = 9\nswitch n:\n    case 1:\n        print("一")\n    default:\n        print("其他")',
     "n 为 9，没有 case 匹配，走 default，输出 其他。default 必须放在最后。")

# ---------- 空值 ----------
_add(["nil", "null", "none"], "表示空值/没有值。", "nil",
     'var x = nil\nif x == nil:\n    print("空")',
     "x 被赋为空值；x == nil 成立，输出 空。nil、null、none 是同一个空值的三种写法。")

# ---------- 异步 ----------
_add("async", "把函数标记为异步任务。", "async def 名字(...):\n    语句",
     'async def work()\n    await sleep(1)\n    print("done")',
     "async 定义异步函数 work；await sleep(1) 等待 1 秒，再打印 done。当前是单线程任务，不是多线程并行。")
_add("await", "等待一个异步任务完成。", "await 异步调用",
     'async def work()\n    await sleep(1)\nprint("start")',
     "await sleep(1) 暂停当前异步流程，等 1 秒；这不会创建新线程。")

# ---------- 延迟清理 ----------
_add("defer", "注册退出当前函数/模块时执行的清理块，按后进先出执行。", "defer {\n    语句\n}",
     'def f()\n    defer { print("clean") }\n    print("body")\nf()',
     "进入 f 后注册清理块；f 正常返回或异常退出时都会执行，输出 body 后输出 clean。C 后端暂不支持 defer。")

# ---------- 结构与枚举 ----------
_add("struct", "定义值对象/结构体，字段用 var 声明。", "struct 名字\n    var 字段 = 初值",
     'struct Point\n    var x = 0\n    var y = 0\nvar p = new Point()\nprint(p.x)',
     "struct Point 定义两个字段 x、y，默认 0；new Point() 创建实例，p.x 输出 0。")
_add("enum", "定义枚举，成员是命名常量。", "enum 名字 {\n    成员1,\n    成员2\n}",
     'enum Color {\n    RED,\n    GREEN\n}\nprint(Color.RED)',
     "enum Color 定义 RED、GREEN；Color.RED 是其中一个常量，print 输出它的值。")
_add("record", "record 是 struct 的别名，定义字段记录。", "record 名字\n    var 字段 = 初值",
     'record User\n    var name = ""\nvar u = new User()\nprint(u.name)',
     "record 会按 struct 处理；User 有 name 字段，new User() 后 u.name 是空字符串。")
_add("interface", "定义接口/抽象类容器，方法用 def 声明。", "interface 名字:\n    def 方法()\n        return 0",
     'interface Greet\n    def hello()\n        return 0\nclass A implements Greet\n    def hello()\n        print("hi")\nvar a = new A()\na.hello()',
     "interface 声明方法形状；class A implements Greet 表示实现它，a.hello() 打印 hi。接口方法也要有缩进块。")
_add("implements", "声明类实现某个接口。", "class 类名 implements 接口名:",
     'interface I\n    def f()\n        return 0\nclass C implements I\n    def f()\n        print("ok")\nvar c = new C()\nc.f()',
     "C 声明实现 I；实现后可以像普通类一样创建 c 并调用 c.f()，输出 ok。")
_add("namespace", "定义命名空间/容器类（当前实现为类容器）。", "namespace 名字:\n    语句",
     'namespace Util\n    def twice(x)\n        return x * 2\nprint(Util.twice(3))',
     "namespace Util 把 twice 收在 Util 下；Util.twice(3) 返回 6。")
_add("sealed", "修饰类，表示密封类（当前主要作为标记保留）。", "sealed class 类名:",
     'sealed class FinalBox\n    var n = 1\nvar f = new FinalBox()\nprint(f.n)',
     "sealed 写在 class 前；FinalBox 正常可用，sealed 表示设计上不再扩展。当前版本以标记为主。")
_add("abstract", "修饰类/方法，表示抽象。", "abstract class 类名:",
     'abstract class Base\n    def f()\n        print("base")\nvar b = new Base()\nb.f()',
     "abstract 标记 Base 为抽象类；当前版本仍可实例化，主要用于设计标记。")
_add("final", "修饰类，表示最终类（当前主要作为标记保留）。", "final class 类名:",
     'final class F\n    var n = 1\nvar f = new F()\nprint(f.n)',
     "final 写在 class 前，表示设计上不再被继承；当前版本以标记为主。")
_add("override", "修饰方法，表示覆盖父类方法。", "override def 方法():",
     'class A\n    def f()\n        print("A")\nclass B(A)\n    override def f()\n        print("B")\nvar b = new B()\nb.f()',
     "B 覆盖 A 的 f；b.f() 调用 B 的版本，输出 B。")
_add(["public", "private", "protected"], "成员可见性修饰符。", "public def 方法():",
     'class C\n    private var secret = 1\n    public def get()\n        return this.secret\nvar c = new C()\nprint(c.get())',
     "secret 标记 private，get 标记 public；类内部 get 仍能读 secret，返回 1。当前可见性主要作为设计标记。")

# ---------- 软关键字 ----------
_add("unless", "if 的反向写法：条件为假时执行。", "unless 条件:\n    语句",
     'var ok = false\nunless ok:\n    print("执行")',
     "unless ok 等价于 if not ok；ok 为假，所以执行 print，输出 执行。")
_add("do", "do-until / do-while 循环的开头：先执行一次。", "do {\n    语句\n} until 条件",
     'var i = 0\ndo {\n    print(i)\n    set i = i + 1\n} until i >= 2',
     "先执行循环体，打印 0、i 变 1；判断 i >= 2 不成立，再执行一次打印 1、i 变 2；再判断成立，停止。")

# ---------- 预留关键字 ----------
for _name, _meaning in {
    "with": "预留：资源自动管理/上下文块（当前未实现）。",
    "yield": "预留：生成器产出（当前未实现）。",
    "macro": "预留：宏展开（当前未实现）。",
    "template": "预留：模板（当前未实现）。",
}.items():
    _add(_name, _meaning,
         "（当前无可用语法）",
         "# 当前版本写 " + _name + " 会触发“关键字已保留”语法错误",
         "它已经被 lexer 保留，避免未来特性与普通变量名冲突；现阶段不要使用。",
         status="reserved")

# 兜底：KEYWORDS 里任何没有单独条目的名字，都明确标成保留字，绝不假装有语法。
for _name in sorted(KEYWORDS):
    if _name not in ENTRIES:
        _add(_name, "Impor 保留字；当前版本尚未实现独立语法。",
             "（当前无可用语法）",
             "# 当前版本写 " + _name + " 会触发语法错误",
             "保留字用于占位，避免未来特性与用户标识符冲突。",
             status="reserved")

# ---------- 内建函数 ----------
_add(["print"], "打印：把括号里的内容输出到屏幕，末尾换行。", 'print("xxx")  /  print(变量)',
     'var a = 1\nprint("1")\nprint(a)\nprint("a")',
     "`print(\"1\")` 括号里是字符串 1，输出 1；`print(a)` 括号里是变量 a，输出 a 保存的值 1；`print(\"a\")` 括号里是字符串 a，输出 a。引号只表示字符串，不会把引号一起输出。",
     kind="内建函数")
_add(["input"], "读取一行输入并尝试转成数值。", "input(提示)",
     'var n = input("数字：")\nprint(n + 1)',
     "提示后读取一行；输入 4 会转成数值 4，print(n + 1) 输出 5。无法转成数值会报错。",
     kind="内建函数")
_add(["inputstr"], "读取一行输入，保持字符串。", "inputstr(提示)",
     'var s = inputstr("名字：")\nprint("你好", s)',
     "输入的内容不会转数值；输入 Impor 后输出 你好 Impor。",
     kind="内建函数")
_add(["len"], "返回字符串长度或数组元素个数。", "len(值)",
     'print(len("abc"))\nprint(len([1, 2, 3]))',
     "字符串 abc 长度 3，输出 3；数组 3 个元素，输出 3。",
     kind="内建函数")
_add(["range"], "生成整数序列，配合 for。", "range(起点, 终点[, 步长])",
     'for i in range(0, 3):\n    print(i)',
     "i 依次取 0、1、2（不包含终点 3），输出 0、1、2。",
     kind="内建函数")
_add(["type", "type_of"], "返回值的类型名。", "type(值)",
     'print(type(1))\nprint(type("a"))',
     "1 是整数，输出 int；\"a\" 是字符串，输出 str。",
     kind="内建函数")
_add(["to_num", "int"], "把值转成数值/整数。", "int(值)",
     'print(int("12") + 1)',
     "字符串 12 转成整数 12，再加 1，输出 13。",
     kind="内建函数")
_add(["to_str", "str"], "把值转成字符串。", "str(值)",
     'print(str(12) + "!")',
     "12 转成字符串 12，再拼接 !，输出 12!。",
     kind="内建函数")
_add(["abs"], "返回绝对值。", "abs(数值)",
     'print(abs(-3))',
     "绝对值把 -3 变成 3，输出 3。",
     kind="内建函数")
_add(["min", "max"], "取最小值 / 最大值。", "min(a, b, ...)",
     'print(min(2, 5))\nprint(max(2, 5))',
     "min 输出 2，max 输出 5。",
     kind="内建函数")
_add(["clamp"], "把数值限制在区间内。", "clamp(x, 下界, 上界)",
     'print(clamp(10, 0, 5))',
     "10 超过上界 5，被限制为 5，输出 5。",
     kind="内建函数")
_add(["trim", "upper", "lower"], "去首尾空白 / 转大写 / 转小写。", "upper(字符串)",
     'print(upper("abc"))\nprint(trim("  hi  "))',
     "upper 输出 ABC；trim 去掉两边空格，输出 hi。",
     kind="内建函数")
_add(["strjoin"], "把数组拼成一个字符串。", "strjoin(数组[, 分隔符])",
     'print(strjoin(["a", "b"], "-"))',
     "用 - 连接 a、b，输出 a-b。",
     kind="内建函数")
_add(["randi", "randstr"], "随机整数 / 随机字符串。", "randi(a, b)",
     'print(randi(1, 3))',
     "在 1 到 3 之间随机取一个整数；每次运行结果可能不同。",
     kind="内建函数")
_add(["now", "clock", "sleep", "wait"], "当前时间 / CPU 时间 / 暂停。", "sleep(秒)",
     'print("start")\nsleep(1)\nprint("end")',
     "先输出 start，暂停 1 秒，再输出 end。wait 是 sleep 的别名。",
     kind="内建函数")
_add(["fileread", "filewrite", "fileexists"], "读文件 / 覆盖写文件 / 判断文件是否存在。", 'filewrite("a.txt", "hi")',
     'filewrite("a.txt", "hi")\nprint(fileread("a.txt"))',
     "filewrite 写入 hi；fileread 读回来，print 输出 hi。",
     kind="内建函数")
_add(["json_encode", "json_decode"], "JSON 序列化 / 解析。", "json_encode(值)",
     'var s = json_encode({"a": 1})\nprint(s)\nprint(json_decode(s)["a"])',
     "json_encode 把字典变成 JSON 字符串；json_decode 再解析回来，[\"a\"] 取值 1。",
     kind="内建函数")
_add(["regex_match", "regex_find", "regex_replace"], "正则整串匹配 / 查找 / 替换。", "regex_match(文本, 模式)",
     'print(regex_match("abc", "a.*"))',
     "模式 a.* 匹配整串 abc，返回 true。",
     kind="内建函数")
_add(["mem", "gc", "memstats"], "查看自动内存 / 触发回收 / 取内存统计。", "gc()",
     'print(memstats())\nprint(gc())',
     "memstats 返回内存统计字典；gc 手动触发一次回收并返回清理对象数。",
     kind="内建函数")
_add(["ffi_open", "ffi_call"], "受控调用原生库函数。", 'ffi_open("库路径")',
     'var lib = ffi_open("C:\\\\Windows\\\\System32\\\\msvcrt.dll")\nprint(ffi_call(lib, "strlen", ["abc"], "int", ["cstr"]))',
     "先打开允许清单内的库；strlen 用 cstr 参数接收字符串，返回长度 3。需要先配置 FFI 允许清单。",
     kind="内建函数")
_add(["ffi_alloc", "ffi_alloc_str", "ffi_read_i64", "ffi_write_i64", "ffi_read_str"], "受控原生缓冲区读写。", "ffi_alloc(字节数)",
     'var b = ffi_alloc(8)\nffi_write_i64(b, 42)\nprint(ffi_read_i64(b))',
     "分配 8 字节缓冲区 b；写入整数 42；再读出来，输出 42。token 由运行时托管，不需要手动 free。",
     kind="内建函数")


def get(name):
    key = str(name).strip()
    if key not in ENTRIES:
        key = ALIASES.get(key, key)
    return ENTRIES.get(key)


def names():
    return sorted(ENTRIES)


def search(query):
    q = str(query or "").strip().lower()
    if not q:
        return [ENTRIES[n] for n in names()]
    out = []
    for n in names():
        e = ENTRIES[n]
        hay = " ".join([n, e["meaning"], e["format"], e["example"], e["explain"]]).lower()
        if q in hay:
            out.append(e)
    return out


def as_text(entry):
    e = entry
    status = ""
    if e.get("status") == "reserved":
        status = "  [预留，当前无语法]"
    return (
        f"{e['name']}（{e['kind']}）{status}\n"
        f"\n意思\n{e['meaning']}\n"
        f"\n格式\n{e['format']}\n"
        f"\n例子\n{e['example']}\n"
        f"\n例子说明\n{e['explain']}\n"
    )


def format_entry(name):
    e = get(name)
    if not e:
        return None
    return as_text(e)


def categories():
    groups = {}
    for e in ENTRIES.values():
        for kind in [e["kind"]] + list(e.get("extra_kinds", [])):
            groups.setdefault(kind, []).append(e["name"])
    return {k: sorted(v) for k, v in groups.items()}


# ---------- patch8b：语法表不再靠手写数字，与真实语言对齐 ----------
# 旧实现的毛病（用户实测撞到）：表里躺着 79 个「关键字」，其中 func/loop/let/local 等
# 12 个在 4.0 已被物理删除；内建只有 43 条，而语言实际有 101 条；连 `管道 |>`
# 这种印在语言规范里的差异化特性都查不到。真源是 tools/api_baseline.json
# （README 首屏的 55/101 也是它算的），这里直接对齐，避免两套数字打架。

ALIASES = {"管道": "|>", "管道运算符": "|>", "pipe": "|>"}

_add("|>", "管道运算符：把左侧的值作为右侧调用的第一个参数，专为链式数据流写法设计。",
     "值 |> 函数(其余参数)",
     'def add(a, b)\n    return a + b\nprint(3 |> add(4))',
     "`3 |> add(4)` 等价于 `add(3, 4)`，所以输出 7。管道在解释器与完整 C 后端一致（v3.7.8 起是普通调用）；"
     "自举编译器 imporc 的子集不含管道，遇到会明确报错，不会静默改语义。",
     kind="运算符")


def _api_baseline():
    """读 tools/api_baseline.json；读不到就退回 lexer 的 KEYWORDS（不静默编数字）。"""
    import json
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        with open(os.path.join(root, "tools", "api_baseline.json"), encoding="utf-8") as f:
            base = json.load(f)
        kws = [str(x) for x in base.get("keywords", [])]
        bis = {}
        for group, names in (base.get("builtins", {}) or {}).items():
            for n in names:
                bis[str(n)] = str(group)
        return kws, bis
    except Exception:
        return list(KEYWORDS), {}


def _align_with_language():
    kws, bis = _api_baseline()
    kwset = set(kws)
    # 1) 删掉语言里已经不存在的条目（4.0 物理删除的历史写法、以及被移除的内建）
    for name, e in list(ENTRIES.items()):
        if e.get("kind") == "关键字" and name not in kwset:
            del ENTRIES[name]
        elif e.get("kind") == "内建函数" and bis and name not in bis:
            del ENTRIES[name]
    # 2) 补齐真实存在但没写说明的关键字
    for kw in sorted(kwset - set(ENTRIES)):
        _add(kw, "语言关键字（自动收录：本表还没有为它写说明，先用 `impor explain` 或 docs/语言规范.md 查）。",
             "——", "——", "语法表自动收录项，仅用于让条目数与真实语言一致。", kind="关键字")
    # 3) 补齐真实存在但没写说明的内建
    for name in sorted(set(bis) - set(ENTRIES)):
        _add(name, "内建函数（分组：%s；自动收录，说明待补，`help(%s)` 可看内置帮助）。" % (bis[name], name),
             "%s(...)" % name, "——", "语法表自动收录项，调用约定以 `help(%s)` 为准。" % name,
             kind="内建函数")
    # 4) 双身份：exit 既是关键字又是内建函数，dict 键只能占一个身份。
    #    之前这里被吞掉，于是「内建 100」永远差基线 1 —— 现在显式记第二个身份。
    for name in sorted(kwset & set(bis)):
        e = ENTRIES.get(name)
        if e and "内建函数" not in e.get("extra_kinds", []):
            e["extra_kinds"] = list(e.get("extra_kinds", [])) + ["内建函数"]
            e["explain"] = (e.get("explain", "") +
                            "（它同时是内建函数：%s() 也是合法调用形式。）" % name)


_align_with_language()

