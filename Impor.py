# -*- coding: utf-8 -*-
"""
Impor (I++) — 真正的编程语言 v{__version__}

本文件是启动器，兼容旧用法：
    python Impor.py            打开图形化编辑器
    python Impor.py run 脚本.impor [参数...]   命令行运行
    python Impor.py run-dbg 脚本.impor        交互式调试（断点/单步/栈/变量）
    python Impor.py repl       交互式解释器
    python Impor.py compile 脚本.impor [-o 输出.py] [--exe]
    python Impor.py check 脚本.impor
    python Impor.py fmt 脚本.impor / check-fmt / lint
    python Impor.py lsp / list-modules / check-imports / reload / pkg
    python Impor.py new 名字.impor
    python Impor.py version

也可以安装后全局使用：pip install -e . ，然后直接敲 impor 命令。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def _read_version():
    """版本号唯一真源：仓库根 VERSION；冻结/缺失时退回构建基线。"""
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION"),
                  encoding="utf-8") as f:
            v = f.read().strip()
        if v:
            return v
    except OSError:
        pass
    return "4.3.2"


__version__ = _read_version()  # 唯一版本常量；升版只改 VERSION
__doc__ = __doc__.format(__version__=__version__)

# 全部可透传子命令由 impor.cli.SUPPORTED_COMMANDS 单一维护（见 cli.py 的 _COMMANDS），
# 新增/改名子命令只改一处，避免启动器与 CLI 命令集合漂移。

# 导入类异常集合：impor 包 / 依赖缺失（tkinter 等）导致导入失败时的可预期异常
_IMPORT_ERRORS = (ImportError, AttributeError, SyntaxError, OSError)


def _load_cli():
    """延迟导入 CLI；impor 包损坏或依赖缺失时给出友好提示而不是抛原始堆栈。"""
    try:
        from impor.cli import main, SUPPORTED_COMMANDS
        return main, SUPPORTED_COMMANDS
    except _IMPORT_ERRORS as exc:  # 只捕获导入相关异常，不吞掉解释器内部 bug
        print(f"Impor 初始化失败：{exc}", file=sys.stderr)
        print("请检查 impor 目录是否完整，或重新执行 pip install -e .", file=sys.stderr)
        return None, frozenset()


def _open_editor() -> int:
    """打开图形化编辑器；系统缺少 tkinter 时给出友好提示而不是抛原始导入堆栈。"""
    try:
        from impor.editor import main as editor_main
        return editor_main()
    except _IMPORT_ERRORS as exc:
        print(f"无法打开图形化编辑器：{exc}", file=sys.stderr)
        print("提示：图形编辑器依赖 tkinter，当前环境似乎未安装；", file=sys.stderr)
        print("可改用命令行：impor repl（交互式）或 impor run 脚本.impor（运行）。", file=sys.stderr)
        return 1


def _fast_version() -> int:
    """不导入 lexer/parser/interpreter 的 version 快路径。"""
    root = os.path.dirname(os.path.abspath(__file__))
    print(f"Impor (I++) {__version__}")
    print(f"  发布线: {__version__}（教学稳定线，语法已冻结；v6.x / 7.3.0 为历史实验线，不属于本发布）")
    print(f"  平台: {sys.platform} | Python {sys.version.split()[0]}")
    tcc = os.path.join(root, "impor", "tcc", "win32", "tcc.exe")
    print("  C 后端: " + ("可用（tcc.exe）" if os.path.isfile(tcc) else "缺失"))
    if os.path.isfile(os.path.join(root, "impor", "imporc.exe")):
        print("  自举: 原生 C 编译器 imporc.exe 可用")
        if os.path.isfile(os.path.join(root, "imporc_self.exe")):
            print("        自举产物 B（imporc_self.exe）已构建")
    return 0


def _main() -> int:
    argv = sys.argv[1:]

    # 无参数 → 图形化编辑器（不先加载 CLI/解释器，省掉编辑器启动的无用导入）
    if not argv:
        return _open_editor()

    # version 快路径：不导入整个语言后端
    if argv[0] in ("version", "--version", "-v"):
        return _fast_version()

    main, supported = _load_cli()
    if main is None:
        return 1

    # 已知子命令 → 透传 CLI（run 与其它命令统一走命令表，无特例）
    if argv[0] in supported:
        return main(argv)

    # 未知命令/直接给脚本文件 → 交给 CLI 处理（报错或按脚本运行），不再静默打开编辑器
    return main(argv)


if __name__ == "__main__":
    sys.exit(_main())
