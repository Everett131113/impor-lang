# -*- coding: utf-8 -*-
"""
Impor (I++) — 真正的编程语言

v2.3 特性：
  - 完整语法：var/func/loop/复合赋值/冒号可省略/try-catch-finally/super()
  - 模块系统：from-import / as 别名 / 相对导入 / export 私有导出 / 目录包 /
    模块缓存与安全循环导入 / reload / 模块元信息
  - 项目配置：impor.toml；CLI：run / repl / compile / check / new /
    list-modules / reload / check-imports / pkg init
  - 调试系统：断点 / 单步(步入/步过/步出) / 调用栈与局部变量 / CLI run-dbg /
    GUI 调试面板 / 异常携带完整调用栈（catch 可访问 err.stack）
  - 语法扩展：原始字符串 r""（关闭转义）/ match-case 模式匹配 /
    复合赋值 += -= *= /= %= 支持对象属性与下标 / 异常链 raise ... from ...
  - 工具链：LSP 语言服务器（语法诊断/跳转定义/悬停/文档符号，impor lsp）、
    impor fmt / check-fmt 格式化、impor lint 静态检查（未定义/未使用/死代码）、
    impor pkg 包管理器（本地/git 源、lock 锁文件、upgrade/remove，缓存 ~/.impor/pkg）
  - v2.3.1 体验优化：文件头部标记改为可选（缺失仅警告）、转译产物异常显示
    源码行号（不再泄露 Python 内部栈）、pkg 缓存目录 Linux/macOS 遵循 XDG、
    lint 支持 # impor-lint-ignore 与 impor.toml [lint] disable、REPL/CLI 屏蔽
    底层异常（IMPOR_DEBUG=1 可看完整栈）
  - v2.4 语法宽松与稳定性：一元正负号允许出现在二元/比较运算符右侧
    （a != -1、x * -2）；match-case 新增 else:/default: 兜底分支；lint 消除
    模块级前向引用误报（后声明函数/变量可安全前向引用）；修复转译器
    global/nonlocal 重复声明导致的编译产物 SyntaxError（含 global 脚本现可
    可靠 compile）；GUI 编辑器支持 tkinter 游戏（examples/snake.impor 贪吃蛇）
  - v2.4.1 启动器重构：子命令集合收敛到 cli._COMMANDS 单一派发表，
    Impor.py 直接引用 SUPPORTED_COMMANDS（删除本地硬编码元组与 run 特例），
    顶层捕获导入异常给友好提示，新增启动器入口单元测试（tests/test_launcher.py）
  - v2.5 编辑器与调试体验增强：LSP 自动补全 / 符号重命名 / QuickFix（删除
    未使用导入、未使用变量加 _ 前缀）并修复带头部标记文件的 LSP 行号偏移；
    impor fmt 支持 impor.toml [fmt] 配置（indent_width / max_line_length）
    与 CLI --indent/--max-line 覆盖；CLI 调试器支持条件断点（b 6 if x>10）
    与任意表达式监视（p a+b），并修复带头部文件断点/报错行号偏移；
    lint 新增函数调用参数数量不匹配检查（用户函数 + 内建签名）；
    报错提示追加“设 IMPOR_DEBUG=1 查看完整调试栈”；impor new 支持
    script / lib / game 三种模板；impor help <子命令> 与 <子命令> --help；
    REPL 输入历史持久化到 ~/.impor_history
  - v2.5.1 启动器健壮性小修：无参数打开图形化编辑器时捕获 tkinter 等
    导入类异常并给友好降级提示（不再抛原始导入堆栈）；_load_cli 异常捕获
    从宽泛 except Exception 收窄为导入相关异常（ImportError/AttributeError/
    SyntaxError/OSError，不吞解释器内部 bug）；Impor.py 新增唯一版本常量
    __version__，文档字符串版本号自动注入（升版只改一处）
  - v2.7 新手教程：新增 docs/新手教程.md（30 分钟从零入门，16 个章节；
    全部示例已在 v2.6 解释器实测通过，覆盖变量/字符串/列表字典/流程控制/
    函数/类/异常/模块/game 工具/调试工具链/综合练习）
  - v2.6 测试与工程化：新增模糊测试（tests/test_fuzz.py——随机字符/结构化
    源码对 lexer/parser/transpiler 做崩溃与死循环检测，随机有界小程序做
    超时执行检测，迭代数可用环境变量 IMPOR_FUZZ_ITERS 调节，CI 用快速模式）；
    新增性能基准（benchmarks/bench_impor.py——对比树遍历解释器与转译 Python
    后端的 6 类负载耗时与加速比，--quick / --json / --only 可配）；
    新增 GitHub Actions CI 模板（.github/workflows/ci.yml——编译零警告检查 /
    单元测试 / fuzz 快速 / check-fmt / lint / benchmark，双平台双 Python）
  - v2.8 语法诊断与测试体系：全量语法报错携带行列号，CLI/REPL 输出源码行与
    波浪线（^）指位（impor/diagnostics.py 统一渲染，LSP 诊断同步带列号）；
    新增 assert 断言语句（条件为假抛 AssertionError，可被 try/catch 捕获，
    解释器与转译产物行为一致）；新增 impor test 单元测试子命令（扫描 tests/
    下文件名含 test 的 .impor 文件，断言失败/异常计失败，输出摘要与退出码）；
    Token 携带 1 起列号（lexer/parser 全链路）；impor pkg install 新增依赖
    冲突检测（新包声明的依赖与项目已有声明不一致时报冲突并指明来源）；
    pyproject.toml 补齐发布元数据（classifiers/urls，version 升至 2.8.0）；
    新增示例项目 examples/hello.impor、guess.impor、grade.impor 与
    examples/test_math.impor、test_fail_demo.impor（impor test 演示）；
    '表达式缺失'友好报错（NEWLINE/EOF 处不再显示无法解析的记号）
  - v2.9 差异化语言特性与性能：新增中文关键字系统（变量/函数/如果/否则/
    否则如果/循环/对于/在/返回/类/新/真/假/并且/或者/非/尝试/捕获/抛出/
    断言/导入/导出…，lexer 层映射为英文关键字 token，下游零改动，中英可混写）；
    新增字符串插值 "x = {expr}"（解释器与转译产物一致，转义花括号为字面量）；
    新增管道运算符 |>（x |> f(1) == f(x,1)，支持链式与内建函数，双后端一致）；
    新增隐式返回（函数末行为表达式语句时自动返回其值，Ruby/Julia 风格）；
    新增裸表达式语句（任意表达式可独立成行求值丢弃）；解释器 BinOp 数字
    字面量快速路径；benchmarks 新增 --inproc 模式（编译产物进程内 exec，
    剔除 Python 启动开销，实测编译后端纯性能为解释器约 8 倍）
  - 编译到 Python（impor compile，可再接 PyInstaller 打包 exe）
  - 标准库导入：import math / random / json / os ...
  - 游戏工具库：vec2 / rgb / clamp / lerp / remap / sign / distance / angle / frame / fps
  - v2.10 词法/语法/运行时核心重构：代码块与 Python 缩进剥离——统一大括号
    {} 块（新标准）、quick 单行省略大括号、旧缩进块完全兼容；每个 AST 节点
    携带文件名/行号/列号（友好报错/调试/LSP 的基础）；自定义语法异常
    ImporSyntaxError（源码片段 + 波浪线 + 修复建议，不抛 Python 原生异常）；
    运行时异常统一包装 ImporRuntimeError（隐藏 Python 内部堆栈，携带源码
    位置与 cause，IMPOR_DEBUG=1 查看完整调试栈）；规范作用域系统（var 声明
    局部、set 就近更新——闭包可修改外层变量、nonlocal/global set 显式作用域）；
    ImporException 全量携带列号；fmt 支持大括号块空格排版
  - v2.11 异常体系全覆盖：解释器/转译器/CLI 统一捕获 Python 底层异常，包装为
    Impor 自定义异常（语法/运行时/类型/导入错误），全部携带 filename/line/col；
    编译产物（impor compile 生成的 .py）运行时报错同样输出 Impor 风格——
    源码片段 + ^ 波浪线 + 修复提示，屏蔽 Python 内部堆栈（IMPOR_DEBUG=1 才打印
    完整 Traceback 供开发者调试）；runtime.py 异常携带 col；变量读取/复合赋值/
    new/class 继承/raise 的 NameError 自动补齐源码位置；CLI main 顶层统一捕获
    未包装异常（内部 bug 不再泄露 Python 栈）
  - v2.12 调试/工具链/模块/脚手架/高亮：CLI 调试器新增断点列表（b 无参数）与
    删除命令（del/d，可删单个或全部）；impor check 聚合四段检查（语法 + 导入 +
    lint + 格式，任一失败返回非 0，可接 CI）；模块系统新增 use 关键字（等价
    from ... import *，中文写法“用”）与内置标准库 std.text / std.mathx /
    std.collections（纯 Impor 编写，use 注入符号 lint 静态解析不误报）；
    from . import 子模块语义修复（包 store 无该名时回退加载包内子模块）、严格
    export 模式下模块内私有辅助函数互相调用可见；impor new 新增 --template pkg
    目录包项目骨架（impor.toml + main.impor + lib/NAME_lib/ 聚合导出包，整链路
    验证：new → run 输出“你好, Impor”）；VS Code 语法高亮增强（全部中文关键字、
    use、管道 |>,、字符串插值 {expr}）
"""

from .version import __version__

from .lexer import tokenize, KEYWORDS, BUILTIN_NAMES
from .parser import Parser
from .interpreter import (
    Interpreter, ImporException, ImporClass, ImporInstance, Closure,
    Vec2, Color, UserExceptionType, typename, strip_header, parse_source,
)
from .ast_nodes import Block

__all__ = [
    "tokenize", "Parser", "Interpreter", "ImporException", "ImporClass",
    "ImporInstance", "Closure", "Vec2", "Color", "UserExceptionType",
    "typename", "strip_header", "parse_source", "Block", "KEYWORDS",
    "BUILTIN_NAMES", "__version__",
]

