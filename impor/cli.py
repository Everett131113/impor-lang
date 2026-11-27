# -*- coding: utf-8 -*-
"""
Impor (I++) 命令行入口。

用法：
  impor run FILE [参数...]   运行 Impor 脚本
  impor run-dbg FILE [参数...]   命令行交互式调试（单步/断点/调用栈）
  impor repl                 启动交互式解释器
  impor compile FILE [-o 输出.py] [--exe]   编译为 Python（--exe 尝试打包 exe）
  impor check FILE           语法检查
  impor ir FILE [--run] [-o OUT] [--stats]
                             打印三地址 IR（4.4）：AST 降下来的中间表示；
                             --run 用 IR 参考求值器执行，-o 落盘便于 diff
  impor fmt FILE [-o OUT]    格式化代码（缩进/空格/换行）
  impor check-fmt FILE       检查是否已格式化（CI 可用）
  impor lint FILE [FILE...]  静态检查：未定义变量/未使用变量/未使用导入/死代码
  impor lint --style FILE    额外报告「不推荐写法」与可读性提示（命名/魔法数字/函数过长）（v3.8）
  impor lint --fix FILE      自动修复未使用导入/未使用变量前缀（v3.9）
  impor doc FILE [-o OUT.md] 从 `##` 文档注释生成 Markdown API 文档（v3.5）
  impor lsp                  启动 LSP 语言服务器（stdio，供 VS Code 等客户端连接）
  impor new NAME.impor       新建脚本模板
  impor list-modules         列出可发现模块
  impor reload MOD           加载/重新加载模块（开发调试）
  impor check-imports FILE   静态检查导入（缺失模块、循环导入）
  impor pkg init [目录]      生成项目骨架（impor.toml）
  impor pkg install|install --frozen|remove|list|search|lock|check|upgrade|registry
                              包管理器（本地/git/私有源；锁文件复现）
  impor mem [FILE]           自动内存管理概览 / 运行脚本并显示内存变化
  impor explain [E1001]      解释错误码：原因 / 修复 / 示例
  impor syntax [关键字]      语法查询：意思 / 格式 / 例子 / 例子说明
  impor version              版本信息

退出码：0 成功 / 1 运行错误 / 2 语法或文件错误 / 3 用法错误
"""

import os
import re
import shutil
import sys
import time

from . import __version__
from .lexer import tokenize
from .parser import Parser
from .ast_nodes import AST, Num, UnaryOp, ImportNode, FromImportNode
from .interpreter import Interpreter, ImporException, ImporRuntimeError, strip_header, parse_source
from .errors import ImporSyntaxError
from .diagnostics import (parse_position, render_diagnostic, issue_fix,
                          color_enabled, code_info, format_code_info,
                          ERROR_TITLES)
from .observability import note_swallowed

_PKG_ROOT = os.path.dirname(os.path.abspath(__file__))

EXIT_OK = 0
EXIT_RUNTIME = 1
EXIT_SYNTAX = 2
EXIT_USAGE = 3
EXIT_BUILD = 4          # 4.5：原生/IR 后端构建失败（与语法错误、运行期错误分开）


# ---- v4.1：64 位工具链自动发现 ----
# 内置 tcc 只能出 i386，64 位宿主（64 位 Python / 64 位 C++）无法加载。
# 这里按“环境变量 → PATH → 常见安装位置”的顺序找 x86_64 gcc，找到就用它出 64 位库。
_GCC64_HINTS = (
    r"C:\Program Files (x86)\Dev-Cpp\MinGW64\bin\gcc.exe",
    r"C:\msys64\mingw64\bin\gcc.exe",
    r"C:\TDM-GCC-64\bin\gcc.exe",
    r"C:\mingw64\bin\gcc.exe",
)


def _gcc_is_x64(cc):
    """gcc -dumpmachine 里带 x86_64 才算 64 位，避免误用 32 位 MinGW。"""
    import subprocess
    try:
        p = subprocess.run([cc, "-dumpmachine"], capture_output=True, text=True, timeout=20)
        return "x86_64" in (p.stdout or "").lower()
    except Exception:
        return False


def find_gcc64():
    """返回可用的 x86_64 gcc 路径；找不到返回 None。"""
    env = os.environ.get("IMPOR_CC")
    if env and os.path.isfile(env):
        return env if _gcc_is_x64(env) else None
    found = shutil.which("gcc") or shutil.which("x86_64-w64-mingw32-gcc")
    if found and _gcc_is_x64(found):
        return found
    import glob
    for path in _GCC64_HINTS:
        if os.path.isfile(path) and _gcc_is_x64(path):
            return path
    for path in glob.glob(r"C:\Program Files\mingw-w64*\*\bin\gcc.exe"):
        if _gcc_is_x64(path):
            return path
    return None


def _cc_version(cc):
    """取编译器版本首行，用于构建摘要（失败返回空串）。"""
    try:
        p = subprocess.run([cc, "--version"], capture_output=True, text=True, timeout=20,
                           env=_cc_env(cc))
        first = (p.stdout or "").strip().splitlines()
        return first[0].strip() if first else ""
    except Exception:
        return ""


def _resolve_build_cc(choice, tcc, opt):
    """决定 build 用哪个 C 编译器。

    返回 (path, kind, opt)；kind ∈ {"tcc", "gcc"}。
    优先级：--cc 显式指定 > IMPOR_BUILD_CC 环境变量 > auto。
    auto 的规则：找得到 x86_64 gcc 就用 gcc（优化能力远好于内置 tcc），
    否则退回内置 tcc（便携、6.6 KB 产物）。
    """
    choice = choice or os.environ.get("IMPOR_BUILD_CC") or "auto"
    choice = choice.strip()
    low = choice.lower()
    if low in ("auto", "default"):
        gcc = find_gcc64()
        if gcc:
            return gcc, "gcc", opt
        return tcc, "tcc", 2
    if low == "tcc":
        return tcc, "tcc", 2
    # 其余情况当作「gcc 名字或路径」
    if low in ("gcc", "cc", "clang", "g++"):
        gcc = find_gcc64()
        if not gcc:
            gcc = shutil.which(choice)
        return gcc, "gcc", opt
    path = choice if os.path.isfile(choice) else shutil.which(choice)
    return path, "gcc", opt


def _cc_build_cmd(cc_path, kind, cpath, out_abs, opt, lib=False, strip=False, lto=True,
                  static=False):
    """生成 C 编译器命令行；gcc 需要 -std=gnu99（TDM 4.8.1 默认 gnu89）。

    4.7.1：gcc 默认加 `-flto`（跨翻译单元内联；本项目的 C 是单文件，但 LTO 仍会
    把内联阈值放宽，实测 log 体积 248832→251392 字节、输出不变）。tcc 不支持 LTO，
    lib 产物也不加（共享库符号要保真）。`static=True` 时加 `-static`（Linux musl）。
    """
    if kind == "tcc":
        cmd = [cc_path, cpath, "-o", out_abs]
        if lib:
            cmd.insert(1, "-shared")
        cmd.insert(1, "-O2")
        return cmd
    cmd = [cc_path, "-std=gnu99", "-O%d" % opt]
    if lto and not lib and opt > 0:
        cmd.append("-flto")
    if static and not lib:
        cmd.append("-static")
    if lib:
        cmd.append("-shared")
    if strip:
        cmd.append("-s")
    cmd += [cpath, "-o", out_abs, "-lm"]
    return cmd


def _cc_env(cc_path):
    """gcc/clang 的子进程环境：把自己所在的 bin 目录放进 PATH。

    Dev-Cpp / TDM 的 gcc 找不到 cc1、as、ld 时会以退出码 1 结束且 stderr 为空，
    表现为「编译失败但没有任何报错」，必须先把 bin 目录补进 PATH。
    """
    env_cc = os.environ.copy()
    env_cc["PATH"] = os.path.dirname(os.path.abspath(cc_path)) + os.pathsep + env_cc.get("PATH", "")
    return env_cc

NEW_TEMPLATE = ('// Impor (I++) 脚本\n'
                'var name = "世界"\n'
                'print("你好,", name)\n')

NEW_LIB_TEMPLATE = ('// Impor (I++) 库模块模板：函数默认导出，外部可 import\n'
                    'def greet(name)\n'
                    '    return "你好, " + name\n'
                    '\n'
                    'def add(a, b)\n'
                    '    return a + b\n')

NEW_PKG_FILES = {
    "impor.toml": ('[project]\n'
                   'name = "{name}"\n'
                   'version = "0.1.0"\n'
                   'entry = "main.impor"\n'
                   'sandbox_default = false\n'
                   '\n'
                   '[module_search_paths]\n'
                   'paths = ["./lib"]\n'
                   '\n'
                   '[dependencies]\n'),
    "main.impor": ('// {name} 项目入口\n'
                   'import {name}_lib\n'
                   'print({name}_lib.greet("Impor"))\n'),
    "lib/{name}_lib/__pkg__.impor": ('// {name} 库包入口（相对导入，聚合导出对外 API）\n'
                                     'from .greet import greet\n'
                                     'from .add import add\n'
                                     'export greet\n'
                                     'export add\n'),
    "lib/{name}_lib/greet.impor": ('// {name} 库模块：greet\n'
                                   'def greet(name)\n'
                                   '    return "你好, " + name\n'
                                   'export greet\n'),
    "lib/{name}_lib/add.impor": ('// {name} 库模块：add\n'
                                 'def add(a, b)\n'
                                 '    return a + b\n'
                                 'export add\n'),
    "README.md": ('# {name}\n\n'
                  'Impor (I++) 项目骨架（v2.12 pkg 模板）。\n\n'
                  '## 运行\n\n'
                  '```\nimpor run main.impor\n'
                  '```\n\n'
                  '## 目录结构\n\n'
                  '- `main.impor` 项目入口\n'
                  '- `lib/{name}_lib/` 库包（含 `__pkg__.impor` 聚合导出）\n'
                  '- `impor.toml` 项目配置\n'),
}

NEW_GAME_TEMPLATE = ('// Impor (I++) 游戏模板（Tk 画布示例）\n'
                     'var x = 100\n'
                     'var y = 100\n'
                     'var vx = 3\n'
                     'var vy = 2\n'
                     '\n'
                     'def step()\n'
                     '    x += vx\n'
                     '    y += vy\n'
                     '    if x < 10 or x > 390\n'
                     '        vx = -vx\n'
                     '    if y < 10 or y > 390\n'
                     '        vy = -vy\n'
                     '    canvas.itemconfig("ball", {"fill": "#4C9AFF"})\n'
                     '    canvas.coords("ball", x - 10, y - 10, x + 10, y + 10)\n'
                     '    frame()\n'
                     '\n'
                     'canvas = canvas_create(400, 400)\n'
                     'canvas.create_oval(90, 90, 110, 110, {"tags": "ball", "fill": "#4C9AFF"})\n'
                     'start(step, 16)\n')


def _err(msg):
    print(f"impor: {msg}", file=sys.stderr)


def _print_runtime_stack(e, head_skip=0):
    """v4.2：未捕获运行错误时打印调用栈（与 C 后端同一格式）。"""
    stack = getattr(e, "stack", None)
    print("调用栈（自顶向下）:", file=sys.stderr)
    if not stack:
        print("  <main>", file=sys.stderr)
        return
    for i, fr in enumerate(reversed(stack)):
        arrow = " ->" if i == 0 else "   "
        print(f"{arrow} {fr.name}（第{fr.lineno + head_skip}行）", file=sys.stderr)


def _render_exc(e, src_lines=None, head_skip=0, filename=None):
    """统一异常渲染（v3.6）：四段式「位置 / 问题 / 为什么 / 修复」+ 彩色 + 上下文片段。

    屏蔽 Python 内部堆栈；IMPOR_DEBUG=1 时才附加完整调试提示。
    返回渲染后的字符串。
    """
    from .errors import ImporSyntaxError
    dbg = os.environ.get("IMPOR_DEBUG")
    src_lines = src_lines or []
    if isinstance(e, ImporSyntaxError):
        ln = (e.lineno or 0) + head_skip
        return render_diagnostic("语法错误", e.msg, ln, e.col or 0, src_lines,
                                 filename=filename, subject="语法错误")
    if isinstance(e, ImporException):
        ln = (e.lineno or 0) + head_skip
        col = getattr(e, "col", 0) or 0
        msg = f"{e.name}: {e.msg}" + ("" if dbg else "\n（设 IMPOR_DEBUG=1 可查看完整调试栈）")
        return render_diagnostic("运行错误", msg, ln, col, src_lines,
                                 filename=filename or getattr(e, "filename", None),
                                 subject=e.name, context=1)
    # 其他 Python 异常（内部 bug / 未包装）：不泄露内部堆栈
    hint = "" if dbg else "（设 IMPOR_DEBUG=1 可查看完整调试栈）"
    return f"内部错误: {type(e).__name__}: {e}{hint}"


def _read_source(path):
    if not os.path.exists(path):
        raise FileNotFoundError(f"文件不存在: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _strict_types_on():
    return os.environ.get("IMPOR_STRICT_TYPES", "").strip().lower() in ("1", "true", "yes", "on")


def _type_gate(src, fname, stage=""):
    """v3.8.1 起的入口类型门禁；4.7.0 起合并 semantic 层。

    分工：
      - `impor/typecheck.py`：既有诊断，severity=error，行为完全不变（硬拦）；
      - `impor/semantic.py`：算术混型 / int64 字面量越界 / 缺失 return。
        默认 severity=warn，只提示不中止；`--strict-types` 时升为 error。
    这样老脚本的宽松手感保留，而想收紧的人有一条明确的开关。
    """
    strict = _strict_types_on()
    try:
        from .semantic import check
        issues = check(src, fname, strict=strict)
    except Exception:
        try:
            from .typecheck import check_source
            issues = check_source(src, None, fname)
        except Exception as e:
            _err(f"  [FAIL] 类型检查异常: {e}")
            return False
    errors = [i for i in issues if getattr(i, "severity", "error") == "error"]
    warns = [i for i in issues if getattr(i, "severity", "error") != "error"]
    for tip in warns[:5]:
        _err(f"  [WARN] 类型 {fname}:{tip.line}:{tip.col} {tip.msg}")
        if getattr(tip, "why", None):
            _err(f"         为什么：{tip.why}")
        if getattr(tip, "fix", None):
            _err(f"         怎么改：{tip.fix}")
    if len(warns) > 5:
        _err(f"  [WARN] 类型 另有 {len(warns) - 5} 条同类提示（--strict-types 可让它们拦下来）")
    if not errors:
        return True
    for tip in errors:
        _err(f"  [FAIL] 类型 {fname}:{tip.line}:{tip.col} {tip.msg}")
        if getattr(tip, "why", None):
            _err(f"         为什么：{tip.why}")
        if getattr(tip, "fix", None):
            _err(f"         怎么改：{tip.fix}")
    if stage:
        _err(f"{stage} 已中止：先修正上面的类型错误（运行时不会强制注解，"
             f"但编译/构建前会检查；语义层提示加 --strict-types 也会拦）。")
    return False


def _decl_gate(ast, src, fname, stage):
    """v3.13：编译/转译前拦住“给未声明变量赋值”。

    解释器运行时也会拦，但 Python/C 直译会把 `set q = 5` 变成隐式声明；
    这里用同一份符号表在入口处先拒绝，保证三端语义一致。
    """
    try:
        from .analysis import Analyzer
        an = Analyzer(ast, src.splitlines(),
                      script_dir=os.path.dirname(os.path.abspath(fname))).run()
        bad = [i for i in an.issues if i.code == "undeclared-assign"]
    except Exception as e:
        _err(f"  [FAIL] 声明检查异常: {e}")
        return False
    if not bad:
        return True
    for tip in bad:
        _err(f"  [FAIL] 声明 {fname}:{tip.line}:{tip.col} {tip.msg}")
    if stage:
        _err(f"{stage} 已中止：先声明变量，再赋值。")
    return False


RUN_USAGE = ("run 需要脚本文件：impor run [FILE] [参数...]\n"
             "  默认已走「透明编译 + 缓存」的原生路径（patch7）；要强制解释器加 --no-native，"
             "要指定编译器/优化级加 --cc gcc|tcc / -O0..-O3。\n"
             "  后端对齐自检：--int64（别名 --strict-int）强制解释器按 C 后端 int64 语义运行，\n"
             "  加减乘/字面量越界报 E1012 整数溢出；--max-depth N 调整递归上限（默认 3200）。\n"
             "  也可以写进 impor.toml 的 [project] int_mode = \"int64\"，本工程每次 run 都按同一语义；\n"
             "  --big（别名 --bignum）压过该配置，改回任意精度。\n"
             "  稳定模式：--stable（或 --stable=strict / IMPOR_STABLE=1），额度可用\n"
             "  --max-steps / --max-seconds / --max-memory(MB) / --max-output(KB) / --max-alloc 单项覆盖。")


def _native_cache_key(fname, src, opt, backend="fast"):
    """原生 run 缓存键：不含“编译器是谁”。

    热执行的价值全在“直接跑已有产物”，而解析编译器（`gcc --version` 一次 147 ms）
    比产物本身还贵。所以键只覆盖会改变产物的东西：源码、工具链源码文件指纹、
    版本串、优化级、后端。换编译器后想强制重编，删掉 ~/.impor/run-cache 即可。
    """
    import hashlib
    parts = [os.path.abspath(fname), src, str(opt), "run-native", backend]
    for rel in ("cfast.py", "ctrans.py", "c_runtime.c", "cli.py"):
        p = os.path.join(_PKG_ROOT, rel)
        try:
            st = os.stat(p)
            parts.append("%s:%d:%d" % (rel, st.st_size, int(st.st_mtime)))
        except OSError:
            parts.append(rel + ":missing")
    parts.append("v" + __version__)
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]


def _transpile_for_native(ast, src, fname, backend):
    """把 AST 转成 C 源码。backend='fast' 走数值快路径，'full' 走完整 Val 后端。

    抛 FastUnsupported / UnsupportedFeature 表示这个脚本这个后端承载不了，由调用方决定下一档。
    """
    if backend == "fast":
        from .cfast import c_fast_transpile, FastUnsupported, _FastCompiler
        try:
            return c_fast_transpile(ast)
        except FastUnsupported:
            raise
    from .ctrans import c_transpile
    return c_transpile(ast, source_name=fname, stats={},
                       source_lines=src.splitlines(), emit_main=True)


def _native_dll(fname, src, ast, opt, verbose=False):
    """patch8：编译（或复用）快路径 DLL，返回路径；承载不了返回 None。给 `impor serve` 用。"""
    import subprocess
    from .cfast import c_fast_transpile, FastUnsupported
    try:
        csrc = c_fast_transpile(ast, entry="impor_entry")
    except FastUnsupported:
        return None
    root = os.environ.get("IMPOR_HOME") or os.path.expanduser("~/.impor")
    cache_dir = os.path.join(root, "run-cache")
    dll = os.path.join(cache_dir, _native_cache_key(fname, src, opt, "fast") + ".dll")
    if os.path.isfile(dll):
        return dll
    cc_path, kind, cc_opt = _resolve_build_cc(None, _find_tcc(), opt)
    if not cc_path or kind == "tcc":
        return None
    try:
        os.makedirs(cache_dir, exist_ok=True)
    except OSError:
        pass
    cpath = dll + ".c"
    try:
        with open(cpath, "w", encoding="utf-8", newline="\n") as f:
            f.write(csrc)
        p = subprocess.run(_cc_build_cmd(cc_path, kind, cpath, dll, cc_opt,
                                         lib=True, strip=True),
                           capture_output=True, text=True, timeout=300,
                           env=_cc_env(cc_path))
    except Exception:
        note_swallowed("serve dll build")
        return None
    finally:
        try:
            if os.path.isfile(cpath):
                os.unlink(cpath)
        except OSError:
            note_swallowed("serve dll c cleanup")
    if p.returncode != 0 or not os.path.isfile(dll):
        return None
    if verbose:
        _err("[serve] 已编译快路径 DLL：%s" % dll)
    return dll


def _native_cache_dir():
    root = os.environ.get("IMPOR_HOME") or os.path.expanduser("~/.impor")
    return os.path.join(root, "run-cache")


def _spawn_bg_upgrade(cc_path, kind, cpath, out_exe, opt):
    """4.8.0：后台把快速档的 .c 重编成满档产物。

    快速档（-O1、无 LTO）让脚本先跑起来；满档（原 opt + LTO）在后台编译，
    落到**另一个文件名**上（不覆盖正在执行的产物），下次运行自动优先用它。
    升级失败不重试、不报错、不影响前台结果，也不拦程序退出。
    """
    import subprocess
    try:
        if not os.path.isfile(cpath):
            return False
        helper = os.path.join(os.path.dirname(os.path.abspath(__file__)), "native_upgrade.py")
        if not os.path.isfile(helper):
            return False
        flags = 0
        if os.name == "nt":
            flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
                     | getattr(subprocess, "CREATE_NO_WINDOW", 0))
        subprocess.Popen([sys.executable, helper, cc_path, kind, cpath, out_exe, str(opt)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         env=_cc_env(cc_path), creationflags=flags)
        return True
    except Exception:
        note_swallowed("native bg upgrade")
        return False


def _native_product(fname, src, ast, opt, backend="fast", cc_choice=None, verbose=False):
    """产物路径（必要时编译并缓存）；这个后端承载不了返回 None。

    4.8.0 两级编译：默认先出「快速档」（-O1、无 LTO），同一份 .c 交给后台编
    「满档」（opt + LTO）。把首次运行的编译等待从满档的 1.2–1.5 s 压到 0.4 s 量级，
    且满档产物就绪后下次运行自动切换，语义两边逐字节一致（同一份 C 源码）。
    tcc（-O2、无 LTO、本就极快）与显式 -O0/-O1 仍走单级，不做无意义的后台任务。
    """
    import subprocess
    try:
        csrc = _transpile_for_native(ast, src, fname, backend)
    except Exception:
        return None
    cache_dir = _native_cache_dir()
    try:
        cc_path, kind, cc_opt = _resolve_build_cc(cc_choice, _find_tcc(), opt)
    except Exception:
        return None
    if not cc_path or not os.path.isfile(cc_path):
        return None

    two_stage = kind == "gcc" and opt >= 2
    full_exe = os.path.join(cache_dir, _native_cache_key(fname, src, opt, backend) + ".exe")
    if os.path.isfile(full_exe):
        return full_exe
    if not two_stage:
        target, build_opt, use_lto = full_exe, cc_opt, True
    else:
        target = os.path.join(cache_dir,
                              _native_cache_key(fname, src, 1, backend) + ".exe")
        build_opt, use_lto = 1, False
        if os.path.isfile(target):
            # 快速档已就绪、满档还没成：确保后台升级在跑，立刻返回快速档。
            _spawn_bg_upgrade(cc_path, kind, target + ".c", full_exe, opt)
            if verbose:
                _err("[native] 复用快速档产物（满档后台升级中）：%s" % target)
            return target

    try:
        os.makedirs(cache_dir, exist_ok=True)
    except OSError:
        note_swallowed("native cache mkdir")
    cpath = target + ".c"
    keep_c = two_stage
    try:
        with open(cpath, "w", encoding="utf-8", newline="\n") as f:
            f.write(csrc)
        p = subprocess.run(_cc_build_cmd(cc_path, kind, cpath, target, build_opt, lto=use_lto),
                           capture_output=True, text=True, timeout=300,
                           env=_cc_env(cc_path))
    except Exception as e:
        _err("[native] 编译失败（%s 后端），改用下一档：%s" % (backend, e))
        keep_c = False
        return None
    finally:
        if not keep_c:
            try:
                if os.path.isfile(cpath):
                    os.unlink(cpath)
            except OSError:
                note_swallowed("native product c cleanup")
    if p.returncode != 0 or not os.path.isfile(target):
        first = ((p.stdout or "") + (p.stderr or "")).strip().splitlines()
        _err("[native] 编译失败（%s 后端），改用下一档：%s"
             % (backend, first[0] if first else "退出码 %d" % p.returncode))
        if keep_c:
            try:
                os.unlink(cpath)
            except OSError:
                note_swallowed("native product c cleanup after fail")
        return None
    if two_stage:
        _spawn_bg_upgrade(cc_path, kind, cpath, full_exe, opt)
    if verbose:
        _err("[native] 已用 %s -O%d%s 编译（%s 后端）并缓存%s"
             % (os.path.basename(cc_path), build_opt,
                "" if not use_lto else " + LTO", backend,
                "，满档后台升级中" if two_stage else "，后续执行直接跑产物"))
    return target


def _has_module_import(ast):
    """脚本里有没有 use/import 加载 std 模块（完整后端的模块支持不完整，见到就绕开）。"""
    try:
        from .ast_nodes import ImportNode, FromImportNode
    except Exception:
        return True                     # 拿不到节点类型就保守地不进完整后端
    stack = [ast]
    while stack:
        node = stack.pop()
        if isinstance(node, (ImportNode, FromImportNode)):
            return True
        for attr in ("body", "stmts", "then_body", "else_body", "orelse", "handlers",
                     "final_body", "cases"):
            child = getattr(node, attr, None)
            if isinstance(child, list):
                stack.extend(child)
            elif child is not None and hasattr(child, "__dict__"):
                stack.append(child)
    return False


def _native_run(fname, src, ast, script_args, cc_choice, opt, verbose=False):
    """v4.2：把脚本编译成原生 exe 后执行，返回退出码；返回 None 表示得回落解释器。

    与解释器共用同一份源码与同一套门禁，stdout 由产物直接写出（不重排、不加前缀），
    因此对拍解释器时输出逐字节一致。

    三档（patch8）：数值快路径（cfast，微秒级内核）→ **完整 Val 后端（ctrans）** →
    解释器。第二档是本轮 P3 的落点：字符串/列表/函数/异常这些“快路径不认”的脚本，
    此前一律回落解释器（百万循环 2132 ms），现在编成原生 C 产物执行（同脚本 20–40 ms）。
    """
    import subprocess
    # const 只读是语义契约，快路径不跟踪它：有 const 改写就交给解释器，
    # 由解释器给出带位置/为什么/修复的完整诊断（patch7 起 run 默认走原生，
    # 这道门必须在这里挡住，否则默认路径会静默允许改 const）。
    try:
        from .ctrans import UnsupportedFeature, _reject_const_mutation
        _reject_const_mutation(ast)
    except UnsupportedFeature:
        return None
    except Exception:
        note_swallowed("run native const gate")
        return None

    exe = _native_product(fname, src, ast, opt, "fast", cc_choice, verbose)
    if exe is None and not _has_module_import(ast):
        # 带 use/import std 模块的脚本不进完整后端：ctrans 的模块支持还不完整
        # （实测 demo_19_split_join 在完整后端里报「字符串无此方法」），
        # 让它接管等于把语义交给一个做不到的后端。这类脚本一律回落解释器，
        # 与 patch7 的行为一致；纯本地脚本（栈/循环/函数/字符串）才走 ctrans。
        exe = _native_product(fname, src, ast, opt, "full", cc_choice, verbose)
    if exe is None:
        return None
    workdir = os.path.dirname(os.path.abspath(fname)) or None
    try:
        r = subprocess.run([exe] + list(script_args), cwd=workdir)
    except Exception as e:
        _err("[native] 无法启动原生产物，改用解释器：%s" % e)
        return None
    return r.returncode


def cmd_run(args):
    # patch7：默认就走原生路径。解释器本体有 39–186 ms 的 Python 启动地板，
    # 把它当默认等于每个脚本都先付一次导入 impor 的钱；能静态编译的脚本直接
    # 跑产物（16–20 ms），编不动的自动回落到解释器，语义完全不变。
    env_flag = os.environ.get("IMPOR_RUN_NATIVE", "").strip().lower()
    native = env_flag not in ("0", "false", "no", "off")
    verbose = env_flag in ("1", "true", "yes", "on")
    cc_choice = None
    cc_opt = 2
    fname = None
    script_args = []
    stable = False
    stable_strict = False
    limit_over = {}
    # 4.0.1 定案：交付层（impor run / build / repl / serve）默认只有一种整数语义 = int64，
    # 与 C 后端逐位一致；要任意精度用 --big（只在解释器树遍历里可用）。
    int64_mode = True
    force_big = False
    big_notice = False
    no_fast = False
    max_depth = None
    from .stability import Limits as _Limits, DEFAULT_LIMITS as _DEFAULT_LIMITS, \
        STRICT_LIMITS as _STRICT_LIMITS, stable_from_env as _stable_from_env
    if _stable_from_env():
        stable = True
    i = 0
    while i < len(args):
        a = args[i]
        if fname is None:
            if a in ("--stable", "--stable=strict"):
                stable = True
                stable_strict = a.endswith("=strict")
                i += 1
                continue
            _m = re.match(r"--max-(steps|seconds|memory|output|alloc)(?:=(.+))?$", a)
            if _m:
                key = _m.group(1)
                val = _m.group(2)
                if val is None:
                    if i + 1 >= len(args):
                        _err("--max-%s 需要一个数值" % key)
                        return EXIT_USAGE
                    val = args[i + 1]
                    i += 2
                else:
                    i += 1
                limit_over[key] = val
                stable = True
                continue
            if a in ("--int64", "--strict-int"):
                # 后端对齐自检：原生产物在本进程外执行，计量不可见，故强制解释器。
                int64_mode = True
                force_big = False
                native = False
                i += 1
                continue
            if a in ("--big", "--bignum"):
                # 显式压过默认 int64 与 impor.toml 的 int_mode = "int64"。
                # 4.5.1 定案：任意精度只作为「解释器调试档」存在，不是第二套交付语义；
                # 产物（C / IR→C / DLL）一律 int64，超出即 E1012。故此处必须显式告警，
                # 让用户知道正在离开可交付口径。
                int64_mode = False
                force_big = True
                native = False
                no_fast = True
                if not big_notice:
                    _err("提示：--big 是仅解释器可用的调试档（任意精度），产物后端一律 int64；"
                         "要判定脚本能否按 int64 交付，用 `impor check-int FILE`。")
                    big_notice = True
                i += 1
                continue
            if a == "--max-depth":
                if i + 1 >= len(args):
                    _err("--max-depth 需要一个正整数")
                    return EXIT_USAGE
                _raw_depth = args[i + 1].strip()
                if not _raw_depth.isdigit() or int(_raw_depth) <= 0:
                    _err("--max-depth 的取值不是正整数：%r" % _raw_depth)
                    return EXIT_USAGE
                max_depth = int(_raw_depth)
                native = False
                i += 2
                continue
            if a in ("--native", "--fast-run"):
                native = True
                verbose = True
                i += 1
                continue
            if a == "--no-native":
                native = False
                i += 1
                continue
            if a == "--cc" and i + 1 < len(args):
                cc_choice = args[i + 1]
                native = True
                verbose = True
                i += 2
                continue
            if re.match(r"^-O[0-3]$", a):
                cc_opt = int(a[2])
                native = True
                verbose = True
                i += 1
                continue
            fname = a
        else:
            script_args.append(a)
        i += 1
    if fname is None:
        _err(RUN_USAGE)
        return EXIT_USAGE

    # 项目级整数语义：impor.toml [project] int_mode = "int64" / "big"。
    # 默认已是 int64；声明 "big" 等于自动加 --big（只走解释器树遍历）。
    if not force_big:
        try:
            from .config import find_config, int_mode_from_config
            _found = find_config(os.path.dirname(os.path.abspath(fname)))
            _mode = int_mode_from_config(_found[1]) if _found else None
            if _mode == "int64":
                int64_mode = True
            elif _mode == "big":
                int64_mode = False
                native = False
                no_fast = True
        except Exception:
            pass

    limits = None
    if stable:
        # from_env 同时接受 IMPOR_LIMIT_*（编辑器/CI/自检/围栏都靠这条统一口径）
        limits = _Limits.from_env(base=_STRICT_LIMITS if stable_strict else _DEFAULT_LIMITS)
        for _key, _raw in limit_over.items():
            try:
                _v = float(str(_raw).strip())
                if _key == "seconds":
                    limits.seconds = _v
                elif _key == "memory":
                    limits.memory_mb = int(_v)
                elif _key == "output":
                    limits.output_kb = int(_v)
                elif _key == "steps":
                    limits.steps = int(_v) if _v < 1e15 else 10 ** 15
                elif _key == "alloc":
                    limits.alloc = int(_v)
            except ValueError:
                _err("--max-%s 的取值不是数字：%r" % (_key, _raw))
                return EXIT_USAGE
        # 稳定模式必须走解释器：原生产物不在本进程里，计量器看不到它的语句
        native = False
    try:
        src = _read_source(fname)
    except FileNotFoundError as e:
        _err(str(e))
        return EXIT_SYNTAX
    except Exception as e:
        _err(f"读取失败: {e}")
        return EXIT_SYNTAX

    try:
        body = strip_header(src)
    except ImporException as e:
        _err(render_diagnostic("运行错误", f"{e.name}: {e.msg}", e.lineno,
                               getattr(e, "col", 0) or 0, src.splitlines(),
                               filename=fname, subject=e.name))
        return EXIT_SYNTAX
    except Exception as e:
        _err(f"解析失败: {e}")
        return EXIT_SYNTAX
    head_skip = len(src.splitlines()) - len(body.splitlines())

    try:
        ast = parse_source(body, filename=fname)
    except SyntaxError as e:
        # 语法错误的「为什么 / 修复」由 diagnostics 知识库统一给出（旧 errors.HINTS 仅留作兼容 API）
        if isinstance(e, ImporSyntaxError):
            _line, _col, _msg = e.lineno, e.col or 0, e.msg
        else:
            _line, _col, _msg = parse_position(str(e))
        src_lines = src.splitlines()
        if _line is not None and head_skip:
            _line += head_skip
        _err(render_diagnostic("语法错误", _msg, _line or 0, _col or 0, src_lines,
                               filename=fname, subject="语法错误"))
        return EXIT_SYNTAX
    except Exception as e:
        _err(f"解析失败: {e}")
        return EXIT_SYNTAX

    if not _type_gate(src, fname, "run"):
        return EXIT_SYNTAX
    if not _decl_gate(ast, body, fname, "run"):
        return EXIT_SYNTAX

    # --smoke 只在解释器里实现（input 自动返回）；原生产物不认这个开关，
    # 走到 native 就会真的去读 stdin 并挂住，所以带 --smoke 一律强制解释器。
    if "--smoke" in script_args:
        native = False

    if native:
        rc = _native_run(fname, src, ast, script_args, cc_choice, cc_opt, verbose=verbose)
        if rc is not None:
            return EXIT_OK if rc == 0 else EXIT_RUNTIME
        if verbose:
            _err("[native] 脚本含静态编译子集之外的内容（字符串/容器/类/模块/浮点函数等），已改用解释器执行。")

    interp = Interpreter(log_cb=print, argv=script_args, filename=fname,
                         fast_mode=not no_fast,
                         int64_mode=int64_mode, max_depth=max_depth)
    interp.smoke_mode = "--smoke" in script_args  # 冒烟：input 自动返回，脚本可用 args 自查
    if limits is not None:
        interp.enable_stability(limits)
    from .config import apply_config
    apply_config(interp, os.path.dirname(os.path.abspath(fname)))
    try:
        interp.run(ast)
    except ImporException as e:
        if getattr(e, "filename", None) and e.filename != fname:
            # 模块/包内部错误：定位到模块文件，不渲染主程序源码行
            ln = (e.lineno or 0) + head_skip
            _err(render_diagnostic("运行错误", f"{e.name}: {e.msg}", ln,
                                   getattr(e, "col", 0) or 0, None,
                                   filename=e.filename, subject=e.name))
            _print_runtime_stack(e, head_skip)
            return EXIT_RUNTIME
        src_lines = src.splitlines()
        _err(_render_exc(e, src_lines, head_skip, filename=fname))
        _print_runtime_stack(e, head_skip)
        return EXIT_RUNTIME
    except Exception as e:
        import traceback as _tb
        from .stability import write_crash_log as _crash
        path = _crash(e, where="cli.run", extra={"script": fname})
        _err(f"内部错误: {type(e).__name__}: {e}")
        if path:
            _err(f"现场已写入 {path}（可把它附在问题反馈里）")
        if os.environ.get("IMPOR_DEBUG"):
            _tb.print_exc()
        return EXIT_RUNTIME
    return EXIT_OK


class _DbgQuit(BaseException):
    """用户按 q 退出调试。"""


DBG_HELP = """命令：
  n                 步过（不进入函数）
  s                 步入（进入函数体）
  o                 步出（运行到当前函数返回）
  c                 继续（直到下一个断点）
  b [行号]          设置断点；b 6 if x > 10 设置条件断点（无行号列出全部）
  d 行号            删除断点
  p 表达式          求值并打印任意表达式（如 p a + b）
  bt                查看调用栈
  l                 查看当前帧局部变量
  q                 退出调试
"""


def cmd_run_dbg(args):
    if not args:
        _err("run-dbg 需要脚本文件：impor run-dbg FILE [参数...]")
        return EXIT_USAGE
    fname = args[0]
    script_args = args[1:]
    try:
        src = _read_source(fname)
    except FileNotFoundError as e:
        _err(str(e))
        return EXIT_SYNTAX
    except Exception as e:
        _err(f"读取失败: {e}")
        return EXIT_SYNTAX

    try:
        body = strip_header(src)
        ast = parse_source(body)
    except ImporException as e:
        _err(render_diagnostic("运行错误", f"{e.name}: {e.msg}", e.lineno,
                               getattr(e, "col", 0) or 0, src.splitlines(),
                               filename=fname, subject=e.name))
        return EXIT_SYNTAX
    except SyntaxError as e:
        if isinstance(e, ImporSyntaxError):
            _ln, _col, _msg = e.lineno, e.col or 0, e.msg
        else:
            _ln, _col, _msg = parse_position(str(e))
        _err(render_diagnostic("语法错误", _msg, _ln or 0, _col or 0, src.splitlines(),
                               filename=fname, subject="语法错误"))
        return EXIT_SYNTAX
    except Exception as e:
        _err(f"解析失败: {e}")
        return EXIT_SYNTAX

    source_lines = src.splitlines()
    head_skip = len(source_lines) - len(body.splitlines())
    if not _type_gate(src, fname, "run-dbg"):
        return EXIT_SYNTAX
    interp = Interpreter(log_cb=print, argv=script_args, debug_mode=True)
    from .config import apply_config
    apply_config(interp, os.path.dirname(os.path.abspath(fname)))

    def show_line(ln):
        ln += head_skip
        if 1 <= ln <= len(source_lines):
            print(f"  {ln:>4} | {source_lines[ln - 1].rstrip()}")
        else:
            print(f"  （第 {ln} 行，不在源码范围）")

    def print_stack():
        print("调用栈（自顶向下）:")
        if not interp.call_stack:
            print("  （空）")
            return
        for i, fr in enumerate(reversed(interp.call_stack)):
            arrow = " ->" if i == 0 else "   "
            print(f"{arrow} {fr.name}（第{fr.lineno + head_skip}行）")

    def print_locals():
        env = interp.cur_env
        items = {k: v for k, v in env.store.items() if not k.startswith("__")}
        if not items:
            print("  （无局部变量）")
            return
        for k in sorted(items):
            print(f"  {k} = {items[k]!r}")

    def on_pause(ln, env):
        print(f"\n[暂停] 第 {ln + head_skip} 行")
        show_line(ln)
        while True:
            try:
                line = input("(impor-dbg) ").strip()
            except EOFError:
                print("[调试结束]")
                raise _DbgQuit()
            except KeyboardInterrupt:
                print()
                continue
            if not line:
                continue
            parts = line.split()
            cmd = parts[0]
            if cmd in ("n", "next"):
                interp.step_mode = "over"
                interp._dbg_step_target = interp._dbg_depth()
                return
            if cmd in ("s", "step"):
                interp.step_mode = "into"
                return
            if cmd in ("o", "out"):
                interp.step_mode = "out"
                interp._dbg_step_target = interp._dbg_depth() - 1
                return
            if cmd in ("c", "continue"):
                interp.step_mode = None
                return
            if cmd in ("b", "break"):
                if len(parts) > 1:
                    try:
                        ln = int(parts[1]) - head_skip
                        cond = None
                        if len(parts) > 2 and parts[2] == "if":
                            cond = " ".join(parts[3:])
                            if not cond:
                                print("条件为空：b 行号 if 表达式")
                                continue
                            interp.breakpoint_conditions[ln] = cond
                        elif len(parts) > 2:
                            print("用法: b 行号 [if 表达式]")
                            continue
                        else:
                            interp.breakpoint_conditions.pop(ln, None)
                        interp.breakpoints.add(ln)
                        cond_txt = f" if {cond}" if cond else ""
                        print(f"断点已设在第 {parts[1]} 行{cond_txt}")
                        continue
                    except ValueError:
                        print("行号无效")
                        continue
                else:
                    if interp.breakpoints:
                        shown = []
                        for ln in sorted(interp.breakpoints):
                            c = interp.breakpoint_conditions.get(ln)
                            shown.append(f"  第{ln + head_skip}行" + (f" if {c}" if c else ""))
                        print("断点列表:")
                        print("\n".join(shown))
                    else:
                        print("（无断点）")
                    continue
            if cmd in ("del", "d"):
                if len(parts) > 1:
                    try:
                        dln = int(parts[1]) - head_skip
                        if dln in interp.breakpoints:
                            interp.breakpoints.discard(dln)
                            interp.breakpoint_conditions.pop(dln, None)
                            print(f"已删除断点: 第 {parts[1]} 行")
                        else:
                            print(f"该行没有断点: 第 {parts[1]} 行")
                    except ValueError:
                        print("行号无效")
                else:
                    interp.breakpoints.clear()
                    interp.breakpoint_conditions.clear()
                    print("已删除全部断点")
                continue
            if cmd in ("p", "print"):
                if len(parts) < 2:
                    print("用法: p 表达式（如 p a + b）")
                    continue
                expr = " ".join(parts[1:])
                try:
                    v = interp.eval_expr_text(expr)
                    print(f"{expr} = {v!r}")
                except ImporException as e:
                    print(f"{e.name}: {e.msg}")
                continue
            if cmd in ("bt", "stack"):
                print_stack()
                continue
            if cmd in ("l", "locals"):
                print_locals()
                continue
            if cmd in ("h", "help"):
                print(DBG_HELP)
                continue
            if cmd in ("q", "quit", "exit"):
                raise _DbgQuit()
            print(f"未知命令 {cmd}（输入 help 查看帮助）")

    interp.debug_pause_callback = on_pause
    interp.step_mode = "into"  # 启动即停第一行，便于先设断点
    print(f"[Impor 调试器] {fname}  （输入 help 查看命令）")
    try:
        interp.run(ast)
    except _DbgQuit:
        print("[已退出调试]")
        return EXIT_OK
    except ImporException as e:
        _err(render_diagnostic("运行错误", f"{e.name}: {e.msg}", (e.lineno or 0) + head_skip,
                               getattr(e, "col", 0) or 0, source_lines,
                               filename=fname, subject=e.name, context=1))
        if e.stack:
            print("调用栈（自顶向下）:")
            for fr in reversed(e.stack):
                print(f"  {fr.name}（第{fr.lineno + head_skip}行）")
        return EXIT_RUNTIME
    except Exception as e:
        _err(f"底层异常: {e}")
        if os.environ.get("IMPOR_DEBUG"):
            import traceback as _tb
            _tb.print_exc()
        return EXIT_RUNTIME
    return EXIT_OK


def cmd_repl(args):
    from .repl import repl
    repl()
    return EXIT_OK


def cmd_build(args):
    """impor build FILE.impor [-o OUT.exe] —— C 后端原生编译，产出独立 exe。

    v2.13：Impor → C 源码 → tcc 原生编译。产物是真正的独立可执行文件，
    运行不依赖 Python 宿主。第一版支持过程式子集（变量/算术/流程控制/
    函数/字符串/输入输出等）；类/闭包/异常/模块/数组等特性请用 Python 后端：
    `impor compile FILE --exe` 或 `impor run FILE`。
    """
    fname = None
    out = None
    use_cache = True
    fast_mode = "auto"  # v4.1：默认先尝试数值快路径，超出子集自动回退
    lib = False
    c_source = False
    target = None
    cc_choice = None       # v4.2：--cc tcc / gcc / <路径>；None 表示按环境变量与 auto 规则
    opt_level = 3          # 4.7.1：gcc 默认 -O3（此前 -O2）。tcc 只有一档，恒 -O2
    cc_static = False      # 4.7.1：--static 让 gcc/musl-gcc 产出全静态链接产物
    quiet = False          # v4.2：--quiet 只产出文件，不打印构建摘要（给原生启动器调用）
    dll_fast = False       # patch7：--dll 走数值快路径并产出可被宿主 LoadLibrary 的 DLL
    rest = list(args)
    while rest:
        a = rest.pop(0)
        if a == "-o":
            if not rest:
                _err("-o 需要输出文件名")
                return EXIT_USAGE
            out = rest.pop(0)
        elif a == "--cc":
            if not rest:
                _err("--cc 需要编译器：tcc / gcc / <gcc 路径>")
                return EXIT_USAGE
            cc_choice = rest.pop(0)
        elif a in ("-O0", "-O1", "-O2", "-O3"):
            opt_level = int(a[2])
        elif a == "--opt":
            if not rest:
                _err("--opt 需要 0/1/2/3")
                return EXIT_USAGE
            val = rest.pop(0)
            if val not in ("0", "1", "2", "3"):
                _err("--opt 只接受 0/1/2/3")
                return EXIT_USAGE
            opt_level = int(val)
        elif a == "--lib":
            lib = True
        elif a == "--dll":
            dll_fast = True
        elif a == "--c-source":
            c_source = True
        elif a == "--target":
            if not rest:
                _err("--target 需要目标名：win64 / linux64 / linux-arm64 / macos-arm64 / macos-x64")
                return EXIT_USAGE
            target = rest.pop(0)
        elif a == "--no-cache":
            use_cache = False
        elif a == "--quiet":
            quiet = True
        elif a == "--cache":
            use_cache = True
        elif a in ("--fast", "--fast-native"):
            fast_mode = True
        elif a == "--static":
            # 4.7.1：Linux 静态链（musl-gcc / gcc -static）。Windows 上给了也只影响
            # 传给编译器的参数，本机 MinGW 支持但产物会变大，故默认关闭。
            cc_static = True
        elif a == "--no-fast":
            fast_mode = False
        elif a in ("--big", "--bignum"):
            # 4.5.1：整数语义只有一套交付口径（int64）。build 收到 --big 直接拒绝，
            # 而不是静默降级成 int64 —— 静默降级会让「解释器能跑、exe 崩」的落差更隐蔽。
            _err("build 不接受 --big：任意精度是仅解释器可用的调试档，产物一律 int64。"
                 "先用 `impor check-int FILE` 确认脚本在 int64 内可编译。")
            return EXIT_USAGE
        elif a.startswith("-"):
            _err(f"未知参数 {a}")
            return EXIT_USAGE
        elif fname is None:
            fname = a
        else:
            _err(f"多余的参数 {a}（build 只接受一个源码文件）")
            return EXIT_USAGE
    if not fname:
        _err("build 需要脚本文件：impor build FILE.impor [-o OUT.exe]")
        return EXIT_USAGE
    if not fname.endswith(".impor"):
        _err("build 仅支持 .impor 源码文件")
        return EXIT_USAGE
    if out is None:
        stem = os.path.splitext(os.path.basename(fname))[0]
        if c_source:
            out = stem + "_csrc"
        else:
            out = stem + (".dll" if (lib or dll_fast) else ".exe")
    if dll_fast and lib:
        _err("--dll 与 --lib 是两种产物：--dll 走数值快路径、只导出 impor_entry（给启动器用）；"
             "--lib 走完整 C 后端、导出脚本里的 export func（给别的程序链接用）。二者只能选一个。")
        return EXIT_USAGE
    if c_source and not lib:
        _err("--c-source 必须与 --lib 一起使用：impor build --lib --c-source FILE.impor -o out/")
        return EXIT_USAGE
    if target and not lib:
        _err("--target 目前只用于 --lib：impor build --lib --c-source FILE.impor --target win64 -o out/")
        return EXIT_USAGE
    if target and target not in ("win64", "linux64", "linux-arm64", "macos-arm64", "macos-x64"):
        _err("未知目标 %s；可选：win64 / linux64 / linux-arm64 / macos-arm64 / macos-x64" % target)
        return EXIT_USAGE
    if target and not c_source:
        _err("--target 需要与 --lib --c-source 一起使用：先生成源码包，再调用目标平台编译器")
        return EXIT_USAGE
    if lib and fast_mode is True:
        _err("--lib 不支持 --fast：导出函数走完整 C 后端（装箱值模型）")
        return EXIT_USAGE
    if dll_fast and c_source:
        _err("--dll 不能与 --c-source 一起用：--dll 需要一个能加载的二进制，不是源码包。")
        return EXIT_USAGE
    if dll_fast:
        fast_mode = True   # --dll 固定走数值快路径（产物就是一个可直接调用的入口）
    if lib or c_source:
        fast_mode = False  # DLL / C 源码包固定走完整后端（要导出 Val 边界的函数）

    t0 = time.perf_counter()
    stats = {}
    tcc = _find_tcc()
    cc_path, cc_kind, cc_opt = _resolve_build_cc(cc_choice, tcc, opt_level)
    if dll_fast and cc_kind == "tcc":
        _err("--dll 需要 64 位编译器：内置 tcc 产出 32 位 DLL，64 位启动器加载不了。\n"
             "  请加 --cc gcc，或用 IMPOR_CC 指向 x86_64 gcc.exe。")
        return EXIT_USAGE
    if cc_choice is not None and not cc_path:
        _err("找不到编译器：%s。可用的内置选项是 tcc；gcc 请给出名字或完整路径。" % cc_choice)
        return EXIT_USAGE
    try:
        src = _read_source(fname)
        body = strip_header(src)
    except Exception as e:
        _err(_render_exc(e, ""))
        return EXIT_SYNTAX
    out_abs = os.path.abspath(out)
    if lib:
        use_cache = False  # DLL 每次重建，避免头文件/导出集与缓存不一致
    if c_source:
        use_cache = False  # C 源码包每次都重新生成
    cache_key = _build_cache_key(fname, src, out_abs, cc_path or "", fast=fast_mode)
    cache_key += "|cc=%s|opt=%d" % (os.path.basename(cc_path or ""), cc_opt)
    if dll_fast:
        cache_key += "|entry=impor_entry"
    cache = _read_build_cache(fname)
    hit = cache.get("entries", {}).get(cache_key) if use_cache else None
    if hit and _cache_hit_valid(hit, out_abs):
        total = time.perf_counter() - t0
        size = hit.get("size") or os.path.getsize(out_abs)
        if quiet:
            return EXIT_OK
        print("构建成功（缓存命中）：%s" % out_abs)
        print("  大小：%s（%d 字节）" % (_human_size(size), size))
        print("  耗时：%.2fs（源码、版本与 tcc 未变，跳过转译与编译）" % total)
        print("  如需强制重建：impor build %s -o %s --no-cache" % (fname, out))
        return EXIT_OK
    try:
        head_skip = len(src.splitlines()) - len(body.splitlines())
        ast = parse_source(body)
        if not _decl_gate(ast, body, fname, "build"):
            return EXIT_SYNTAX
        if not _type_gate(src, fname, "build"):
            return EXIT_SYNTAX
        from .ctrans import UnsupportedFeature, _reject_const_mutation
        # v4.1：const 只读是语义契约，必须在选后端之前挡住——
        # 否则自动选到数值快路径时，cfast 不认识 const，会放行一次非法写入。
        _reject_const_mutation(ast)
        if dll_fast:
            from .cfast import c_fast_transpile, FastUnsupported
            try:
                c_code = c_fast_transpile(ast, entry="impor_entry")
            except FastUnsupported as e:
                _err(
                    "build --dll（数值快路径 DLL）不支持：%s（第 %d 行第 %d 列）。\n"
                    "  该脚本不在可静态编译子集内，DLL 路径无法承载；请直接用 impor run（解释器）或 "
                    "impor build --lib 走完整 C 后端。"
                    % (e.msg, e.lineno or 0, e.col or 0)
                )
                return EXIT_USAGE
            stats["fast"] = True
        elif fast_mode is True:
            from .cfast import c_fast_transpile, FastUnsupported
            try:
                c_code = c_fast_transpile(ast)
            except FastUnsupported as e:
                _err(
                    "build --fast（原生数值快路径）不支持：%s（第 %d 行第 %d 列）。\n"
                    "  该脚本请去掉 --fast 使用完整 C 后端：impor build %s -o %s"
                    % (e.msg, e.lineno or 0, e.col or 0, fname, out)
                )
                return EXIT_USAGE
            stats["fast"] = True
        elif fast_mode is False:
            from .ctrans import c_transpile, UnsupportedFeature
            c_code = c_transpile(ast, source_name=fname, stats=stats,
                                  source_lines=src.splitlines(),
                                  emit_main=not c_source)
            stats["fast"] = False
        else:
            # v4.1 auto：先试数值快路径，超出子集就静默回退完整后端。
            # 两条路径都产出“能用的 exe”，差别只在整数精度与速度，
            # 所以这里不报错，只把实际选中的后端写进构建摘要。
            #
            # 4.7 备注：曾试过「有单态化实例就优先完整后端」，但实测在 1e8 长循环上
            # 带 E1012 检查的原生实例（1.55×）并不比数值快路径（1.46×）快，
            # 反而产物大一倍，所以仍按原判据：默认快路径，完整后端按需（--no-fast）。
            from .cfast import c_fast_transpile, FastUnsupported
            try:
                c_code = c_fast_transpile(ast)
                stats["fast"] = True
            except FastUnsupported as e:
                from .ctrans import c_transpile, UnsupportedFeature
                c_code = c_transpile(ast, source_name=fname, stats=stats,
                                     source_lines=src.splitlines(),
                                     emit_main=not c_source)
                stats["fast"] = False
                stats["fast_skip"] = (e.msg, e.lineno or 0, e.col or 0)
        if lib:
            from .cabi import CabiError, wrappers_text, header_text, def_text
            exports = stats.get("exports") or []
            try:
                c_code += wrappers_text(exports)
            except CabiError as e:
                _err("build --lib 的 C ABI 边界不满足：%s\n"
                     "  本版仅支持顶层 `export func`，参数/返回显式标注 int/float/bool/str/void。" % e)
                return EXIT_USAGE
            header_abs = os.path.splitext(out_abs)[0] + ".h"
            def_abs = os.path.splitext(out_abs)[0] + ".def"
            module = os.path.splitext(os.path.basename(out_abs))[0]
            try:
                htext = header_text(module, exports)
            except CabiError as e:
                _err("build --lib 的 C ABI 边界不满足：%s" % e)
                return EXIT_USAGE
            stats["_cabi"] = {"exports": exports, "header": header_abs, "def": def_abs,
                              "htext": htext, "deftext": def_text(module, exports)}
    except SyntaxError as e:
        _err(_render_exc(e, src.splitlines()))
        return EXIT_SYNTAX
    except UnsupportedFeature as e:
        _err(f"build（C 后端，语法子集）不支持：{e.msg}"
             f"（第 {e.lineno} 行第 {e.col} 列）。\n"
             f"  解释器是唯一语义定义：先用 impor run {fname} 验证逻辑；"
             f"C 后端只支持可静态翻译的子集，不支持即编译期拒绝。")
        return EXIT_USAGE
    except ImporException as e:
        _err(_render_exc(e, src.splitlines()))
        return EXIT_RUNTIME

    t_cc = time.perf_counter()
    t_gen = t_cc - t0
    if c_source:
        cabi_info = stats.get("_cabi") or {}
        if not cabi_info:
            _err("--c-source 需要 --lib 的导出信息")
            return EXIT_USAGE
        out_arg = out
        if out_arg.lower().endswith(".c"):
            out_dir = os.path.dirname(os.path.abspath(out_arg)) or os.getcwd()
        elif out_arg.endswith(("/", "\\")) or os.path.isdir(out_arg):
            out_dir = os.path.abspath(out_arg)
        else:
            out_dir = os.path.abspath(out_arg)
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as exc:
            _err("无法创建输出目录 %s：%s" % (out_dir, exc))
            return EXIT_RUNTIME
        stem = os.path.splitext(os.path.basename(fname))[0]
        c_path = os.path.join(out_dir, stem + ".c")
        h_path = os.path.join(out_dir, stem + ".h")
        def_path = os.path.join(out_dir, stem + ".def")
        msvc_path = os.path.join(out_dir, "build_msvc.bat")
        gcc_path = os.path.join(out_dir, "build_gcc.bat")
        try:
            with open(c_path, "w", encoding="utf-8") as f:
                f.write(c_code)
            with open(h_path, "w", encoding="utf-8") as f:
                f.write(cabi_info["htext"])
            with open(def_path, "w", encoding="utf-8") as f:
                f.write(cabi_info["deftext"])
            with open(msvc_path, "w", encoding="utf-8", newline="") as f:
                f.write("@echo off\r\n"
                        "setlocal\r\n"
                        "cd /d \"%%~dp0\"\r\n"
                        "cl /nologo /LD \"%s.c\" /Fe:\"%s.dll\" /link /DEF:\"%s.def\"\r\n"
                        "if errorlevel 1 exit /b 1\r\n"
                        "echo 已生成 %s.dll 与 %s.lib\r\n"
                        % (stem, stem, stem, stem, stem))
            # v4.3.2：生成脚本写死 Impor 已经解析到的 x86_64 gcc 绝对路径，并把它的
            # bin 目录前置到 PATH（TDM/MinGW 的 gcc 需要自己的 cc1/as/ld 与运行时
            # DLL 才找得到）。此前写裸 `gcc`：gcc 未进 PATH 的机器上直接
            # 'gcc' is not recognized，README 的 --c-source 复现步骤走不通。
            gcc64 = find_gcc64()
            gcc_bin_dir = os.path.dirname(gcc64) if gcc64 else ""
            gcc_exe = gcc64 if gcc64 else "gcc"
            gcc_path_line = ('set "PATH=%s;%%PATH%%"\r\n' % gcc_bin_dir) if gcc_bin_dir else ""
            gcc_body = ("cd /d \"%%~dp0\"\r\n"
                        "\"%s\" -shared -m64 -std=gnu99 -O2 \"%s.c\" -o \"%s.dll\" "
                        "-Wl,--out-implib,\"%s.lib\"\r\n"
                        "if errorlevel 1 exit /b 1\r\n"
                        "echo 已生成 %s.dll（gcc 的导入库为 %s.lib）\r\n"
                        % (gcc_exe, stem, stem, stem, stem, stem))
            with open(gcc_path, "w", encoding="utf-8", newline="") as f:
                f.write("@echo off\r\n"
                        "setlocal\r\n"
                        + gcc_path_line
                        + gcc_body)
        except OSError as exc:
            _err("写出 C 源码包失败：%s" % exc)
            return EXIT_RUNTIME

        # v3.10：--target 直接调用目标平台编译器，不再让用户手写 build_msvc.bat。
        # 缺工具链时必须明确拒绝，不静默退回 32 位 tcc。
        if target:
            import subprocess
            if target == "win64":
                cc = shutil.which("cl")
                if not cc:
                    gcc64 = find_gcc64()
                    if not gcc64:
                        _err("目标 win64 需要 64 位工具链，但既没有 MSVC 的 cl，也没找到 x86_64 gcc。\n"
                             "  三条任选：安装 Visual Studio Build Tools（含 C++ x64）；\n"
                             "  安装 MinGW-w64（Dev-Cpp 自带 C:\\Program Files (x86)\\Dev-Cpp\\MinGW64）；\n"
                             "  或用 IMPOR_CC 指向已有的 x86_64 gcc.exe。\n"
                             "  本命令不会静默退回 32 位 tcc。")
                        return EXIT_USAGE
                    lib_name = stem + ".dll"
                    out_lib = os.path.abspath(os.path.join(out_dir, lib_name))
                    cmd = [gcc64, "-shared", "-m64", "-std=gnu99", "-O2", stem + ".c", "-o", out_lib,
                           "-Wl,--out-implib," + stem + ".lib", "-lm"]
                    # TDM/MinGW 的 gcc 需要把自身 bin 放进 PATH，否则找不到 cc1/as/ld
                    env_cc = os.environ.copy()
                    env_cc["PATH"] = os.path.dirname(gcc64) + os.pathsep + env_cc.get("PATH", "")
                    proc = subprocess.run(cmd, cwd=out_dir, text=True, env=env_cc,
                                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                    if proc.returncode != 0:
                        _err("MinGW x64 编译失败（退出码 %d）：\n%s" % (proc.returncode, proc.stdout[-2000:]))
                        return EXIT_RUNTIME
                    try:
                        from .runtime import _pe_machine
                        machine = _pe_machine(out_lib)
                    except Exception:
                        machine = None
                    if machine != 0x8664:
                        _err("MinGW 产物不是 x64 PE（Machine=%s）；拒绝把 32 位库当作 win64 交付。" % machine)
                        return EXIT_RUNTIME
                    print("目标 win64 编译成功：%s" % out_lib)
                    print("  PE Machine=0x8664（x64；编译器 %s）" % os.path.basename(gcc64))
                    print("  导出：%s" % ", ".join("impor_" + e["name"] for e in (cabi_info.get("exports") or [])))
                    print("  耗时：%.2fs" % (time.perf_counter() - t0))
                    return EXIT_OK
                lib_name = stem + ".dll"
                out_lib = os.path.abspath(os.path.join(out_dir, lib_name))
                cmd = [cc, "/nologo", "/LD", stem + ".c", "/Fe:" + out_lib,
                       "/link", "/DEF:" + stem + ".def"]
                proc = subprocess.run(cmd, cwd=out_dir, text=True,
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                if proc.returncode != 0:
                    _err("MSVC x64 编译失败（退出码 %d）：\n%s" % (proc.returncode, proc.stdout[-2000:]))
                    return EXIT_RUNTIME
                try:
                    from .runtime import _pe_machine
                    machine = _pe_machine(out_lib)
                except Exception:
                    machine = None
                if machine != 0x8664:
                    _err("MSVC 产物不是 x64 PE（Machine=%s）；拒绝把 32 位库当作 win64 交付。" % machine)
                    return EXIT_RUNTIME
                print("目标 win64 编译成功：%s" % out_lib)
                print("  PE Machine=0x8664（x64）")
                print("  导出：%s" % ", ".join("impor_" + e["name"] for e in (cabi_info.get("exports") or [])))
                print("  耗时：%.2fs" % (time.perf_counter() - t0))
                return EXIT_OK
            if target == "linux64":
                cc = shutil.which("gcc")
                if not cc:
                    _err("目标 linux64 需要 gcc，但当前 PATH 中没有 gcc。\n"
                         "  请安装 gcc（x86-64），或使用 CI 的 ubuntu-latest。"
                         "  本命令不会静默退回 32 位 tcc。")
                    return EXIT_USAGE
                lib_name = "lib" + stem + ".so"
                out_lib = os.path.abspath(os.path.join(out_dir, lib_name))
                cmd = [cc, "-shared", "-fPIC", "-m64", "-std=gnu99", "-O2", stem + ".c", "-o", out_lib,
                       "-lm", "-ldl", "-pthread"]
                proc = subprocess.run(cmd, cwd=out_dir, text=True,
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                if proc.returncode != 0:
                    _err("GCC x64 编译失败（退出码 %d）：\n%s" % (proc.returncode, proc.stdout[-2000:]))
                    return EXIT_RUNTIME
                # 校验产物真的是 64 位 ELF（x86-64），避免把 32 位 .so 当 linux64 交付
                try:
                    with open(out_lib, "rb") as fh:
                        head = fh.read(20)
                    is_elf = head[:4] == b"\x7fELF"
                    is_64 = head[4] == 2
                    machine = int.from_bytes(head[18:20], "little")
                except Exception:
                    is_elf = is_64 = False
                    machine = None
                if not (is_elf and is_64 and machine == 62):
                    _err("GCC 产物不是 x86-64 ELF（magic/class/machine 校验未通过，machine=%s）；"
                         "拒绝把非 64 位库当作 linux64 交付。" % machine)
                    return EXIT_RUNTIME
                print("目标 linux64 编译成功：%s" % out_lib)
                print("  ELF64 Machine=62（x86-64；编译器 %s）" % os.path.basename(cc))
                print("  导出：%s" % ", ".join("impor_" + e["name"] for e in (cabi_info.get("exports") or [])))
                print("  耗时：%.2fs" % (time.perf_counter() - t0))
                return EXIT_OK
            _err("目标 %s 尚未实现（v3.11 计划）：当前只支持 win64 / linux64。" % target)
            return EXIT_USAGE

        total = time.perf_counter() - t0
        print("C 源码包已生成：%s" % out_dir)
        print("  源码：%s" % c_path)
        print("  头文件：%s" % h_path)
        print("  导出定义：%s" % def_path)
        print("  构建脚本：%s / %s" % (msvc_path, gcc_path))
        print("  产物位宽：由外部编译器决定（内置 tcc 为 32 位 i386；gcc -m64 / cl 生成 64 位）")
        print("  导出：%s" % ", ".join(
            "impor_" + e["name"] for e in (cabi_info.get("exports") or [])))
        print("  说明：本命令只生成源码，不调用内置 tcc；请运行 build_msvc.bat 或 build_gcc.bat。")
        print("  耗时：%.2fs（转译 %.2fs）" % (total, t_gen))
        return EXIT_OK

    if not cc_path:
        _err("未找到 C 编译器。内置 tcc 应位于 impor/tcc/win32/tcc.exe；"
             "也可以用 --cc 指定 gcc 的路径，或用 IMPOR_CC 指向它。"
             "C 后端是可选路径，不是语义定义；不提供 Python 转译兜底。")
        return EXIT_RUNTIME

    import tempfile
    fd, cpath = tempfile.mkstemp(suffix=".c", prefix="impor_build_")
    os.close(fd)
    try:
        with open(cpath, "w", encoding="utf-8") as f:
            f.write(c_code)
        t1 = time.perf_counter()
        import subprocess
        cc_cmd = _cc_build_cmd(cc_path, cc_kind, cpath, out_abs, cc_opt,
                               lib=(lib or dll_fast), strip=dll_fast,
                               static=cc_static)
        r = subprocess.run(cc_cmd, capture_output=True, text=True,
                           env=_cc_env(cc_path) if cc_kind != "tcc" else None)
        if r.returncode != 0 and cc_kind != "tcc" and not (lib or dll_fast):
            # LTO 在某些老 binutils 上会链接失败（plugin 缺失）。回退到不带 -flto，
            # 只降优化手段、不改语义，并在摘要里说明发生了什么。
            r2 = subprocess.run(_cc_build_cmd(cc_path, cc_kind, cpath, out_abs, cc_opt,
                                              lib=False, strip=dll_fast, lto=False,
                                              static=cc_static),
                                capture_output=True, text=True, env=_cc_env(cc_path))
            if r2.returncode == 0:
                r = r2
                if isinstance(stats, dict):
                    stats["lto"] = "fallback"
        t_compile = time.perf_counter() - t1
        if r.returncode != 0:
            detail = r.stderr or r.stdout
            if cc_kind != "tcc" and not (detail or "").strip():
                detail = ("%s 没有输出任何错误信息就退出了（退出码 %d）。"
                          "常见原因是它的 bin 目录不在 PATH 里，找不到 cc1/as/ld。"
                          % (os.path.basename(cc_path), r.returncode))
            _err("C 编译失败（%s）：\n" % os.path.basename(cc_path) + _render_c_error(
                detail, c_code, src.splitlines(), head_skip))
            return EXIT_RUNTIME
        # tcc 无 windres：编译成功后直接把 I++ 图标写进 PE 资源段
        if lib:
            cabi_info = stats.get("_cabi") or {}
            try:
                with open(cabi_info["header"], "w", encoding="utf-8") as f:
                    f.write(cabi_info["htext"])
                with open(cabi_info["def"], "w", encoding="utf-8") as f:
                    f.write(cabi_info["deftext"])
            except OSError as exc:
                _err("DLL 已生成，但头文件/.def 写入失败：%s" % exc)
                return EXIT_RUNTIME
        if sys.platform == "win32" and not (lib or dll_fast):
            try:
                from .peicon import apply_icon
                apply_icon(out_abs)
            except Exception as exc:
                print(f"警告：exe 图标写入失败（{exc}）；可执行文件本身仍可使用。",
                      file=sys.stderr)
        if use_cache:
            try:
                _write_build_cache(cache_key, out_abs, tcc, fname)
            except Exception:
                note_swallowed("build cache write")
    finally:
        try:
            os.unlink(cpath)
        except OSError:
            pass
    total = time.perf_counter() - t0
    try:
        size = os.path.getsize(out_abs)
    except OSError:
        size = 0
    mods = stats.get("modules") or []
    cc_label = "内置 tcc" if cc_kind == "tcc" else "%s -O%d" % (os.path.basename(cc_path), cc_opt)
    if quiet:
        return EXIT_OK
    print("构建成功：%s" % out_abs)
    print("  大小：%s（%d 字节）" % (_human_size(size), size))
    print("  耗时：%.2fs（转译 %.2fs / %s %.2fs）" % (total, t_gen, cc_label, t_compile))
    if cc_kind != "tcc":
        ver = _cc_version(cc_path)
        print("  编译器：%s（%s）" % (cc_label, ver or os.path.basename(cc_path)))
    if not lib and not c_source:
        # v4.3.2：口径修正。4.x 起交付层默认 int64（越界报 E1012），
        # 这里再说"任意精度整数"会与产物行为直接矛盾（用户算大数立刻报错）。
        print("  后端：%s" % ("数值快路径（64 位整数，溢出报 E1012 并中止）"
                            if stats.get("fast") else "完整 Val 值模型（整数 int64 语义，溢出报 E1012）"))
        if stats.get("mono"):
            print("  单态化：%d 个原生实例（标注/泛型函数整段 long long / double，不经 Val 分派）"
                  % stats["mono"])
        if stats.get("mono_tail"):
            print("  尾调用：%d 个自尾递归实例已转成 while 循环（栈不随递归增长）"
                  % stats["mono_tail"])
        if stats.get("inline"):
            print("  内联：%d 处单表达式小函数已在调用点展开（报错位置仍指向调用点）"
                  % stats["inline"])
        if cc_kind != "tcc" and cc_opt > 0:
            print("  优化：-O%d%s" % (cc_opt,
                                    " + -flto" if stats.get("lto") != "fallback" else "（LTO 链接失败已回退）"))
        if stats.get("fast_skip") and os.environ.get("IMPOR_BUILD_VERBOSE") == "1":
            skip = stats["fast_skip"]
            print("  未走快路径：%s（第 %d 行）" % (skip[0], skip[1]))
    if lib:
        cabi_info = stats.get("_cabi") or {}
        print("  模式：C ABI 共享库（DLL）")
        print("  产物位宽：%s" % ("32 位(i386)（内置 tcc；64 位请用 --c-source 以 cl / gcc -m64 编译）"
                                if cc_kind == "tcc" else
                                "64 位（%s）" % os.path.basename(cc_path)))
        print("  头文件：%s" % cabi_info.get("header", ""))
        print("  导出：%s" % ", ".join(
            "impor_" + e["name"] for e in (cabi_info.get("exports") or [])))
        print("  说明：字符串返回值由 DLL 分配，调用方必须用 impor_free_str() 释放。")
    if dll_fast:
        print("  模式：启动器专用 DLL（导出 impor_entry，宿主 LoadLibrary 后直接调用，省掉第二次进程创建）")
        print("  加载方式：LoadLibrary(\"%s\") → GetProcAddress(\"impor_entry\") → 调用 → 返回退出码"
              % os.path.basename(out_abs))
    if mods:
        print("  模块：%d 个（%s）" % (len(mods), ", ".join(mods)))
    else:
        print("  模块：0 个")
    print("  运行：不依赖 Python，可直接分发 %s。" % ("dll" if lib else "exe"))
    return EXIT_OK


def _build_cache_key(fname, src, out_abs, tcc, fast=False):
    """v7.3 增量构建：源码 + 版本 + tcc 指纹 + 输出路径 决定缓存键。"""
    import hashlib
    h = hashlib.sha256()
    h.update(src.encode("utf-8", "replace"))
    h.update(b"\0" + __version__.encode())
    h.update(b"\0" + (b"fast" if fast else b"full"))
    h.update(b"\0" + os.path.abspath(fname).encode("utf-8", "replace"))
    h.update(b"\0" + out_abs.encode("utf-8", "replace"))
    if tcc and os.path.isfile(tcc):
        try:
            st = os.stat(tcc)
            h.update(b"\0%d:%d" % (st.st_mtime_ns, st.st_size))
        except OSError:
            note_swallowed("tcc stat")
    # 编译器/运行时指纹：改 c_runtime.c 或转译器后必须重建，不能命中旧产物
    here = os.path.dirname(os.path.abspath(__file__))
    for rel in ("c_runtime.c", "ctrans.py", "cfast.py"):
        p = os.path.join(here, rel)
        if os.path.isfile(p):
            try:
                st = os.stat(p)
                h.update(b"\0" + rel.encode("ascii") + b"\0%d:%d" % (st.st_mtime_ns, st.st_size))
            except OSError:
                note_swallowed("toolchain stat")
    # 依赖指纹：v4.3.1 起递归（含 std/ 与包目录），叶子模块改动也会让缓存失效
    base = os.path.dirname(os.path.abspath(fname))
    _hash_imports(h, src, base, set(), [80])
    return h.hexdigest()


def _build_cache_path(fname=None):
    """v4.3.1：优先用项目内 .impor-cache，避免全局缓存跨工程串味。"""
    if fname:
        base = os.path.dirname(os.path.abspath(fname))
        proj = os.path.join(base, ".impor-cache")
        try:
            os.makedirs(proj, exist_ok=True)
            return os.path.join(proj, "build-cache.json")
        except OSError:
            note_swallowed("project cache dir")
    root = os.environ.get("IMPOR_HOME") or os.path.expanduser("~/.impor")
    return os.path.join(root, "build-cache.json")


def _module_candidates(name, base):
    """把 import 名展开成候选路径：平铺模块 / 包 / 安装目录里的 std。"""
    mod = name.strip("\"'").replace(".", os.sep).replace("/", os.sep).replace("\\", os.sep)
    cands = [
        os.path.join(base, mod + ".impor"),
        os.path.join(base, mod, "__pkg__.impor"),
    ]
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    cands.append(os.path.join(here, mod + ".impor"))
    cands.append(os.path.join(root, "impor", mod + ".impor"))
    return cands


def _hash_imports(h, src, base, seen, budget):
    """v4.3.1：递归把被 import 的模块源码并进缓存键（旧版只看直接依赖的一层）。"""
    import re as _re
    stack = []
    for line in src.splitlines():
        m = _re.match(r"\s*(?:from|import)\s+([^\s,;]+)", line)
        if m:
            stack.append(m.group(1))
    while stack and budget[0] > 0:
        name = stack.pop(0)
        key = name.strip("\"'")
        if key in seen:
            continue
        seen.add(key)
        budget[0] -= 1
        for cand in _module_candidates(name, base):
            if not os.path.isfile(cand):
                continue
            try:
                with open(cand, "rb") as f:
                    data = f.read()
            except OSError:
                note_swallowed("module hash")
                break
            # 4.7.2：跨盘时 relpath 会抛 ValueError（源文件在 C: 临时目录、std 在 D: 安装目录就会命中），
            # 这里只用它做 hash 输入的一部分，退回绝对路径即可，不影响缓存语义。
            try:
                rel = os.path.relpath(cand, base)
            except ValueError:
                rel = os.path.abspath(cand)
            h.update(b"\0" + rel.encode("utf-8", "replace"))
            h.update(b"\0" + data)
            try:
                text = data.decode("utf-8", "replace")
            except Exception:
                text = ""
            sub = os.path.dirname(cand)
            for subline in text.splitlines():
                m2 = _re.match(r"\s*(?:from|import)\s+([^\s,;]+)", subline)
                if m2:
                    stack.append(m2.group(1))
            break



def _read_build_cache(fname=None):
    import json
    p = _build_cache_path(fname)
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("entries"), dict):
            return data
    except Exception:
        note_swallowed("build cache read")
    return {"version": 1, "entries": {}}


def _cache_hit_valid(entry, out_abs):
    if not os.path.isfile(out_abs):
        return False
    try:
        if int(entry.get("size") or -1) != os.path.getsize(out_abs):
            return False
        import hashlib
        h = hashlib.sha256()
        with open(out_abs, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return entry.get("out_sha") == h.hexdigest()
    except OSError:
        return False


def _write_build_cache(key, out_abs, tcc, fname=None):
    import json, hashlib
    data = _read_build_cache(fname)
    entries = data.setdefault("entries", {})
    if len(entries) > 200:
        entries = dict(list(entries.items())[-120:])
        data["entries"] = entries
    h = hashlib.sha256()
    with open(out_abs, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    entries[key] = {
        "out": out_abs,
        "size": os.path.getsize(out_abs),
        "out_sha": h.hexdigest(),
        "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    data["version"] = 1
    p = _build_cache_path(fname)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def _human_size(n):
    if n >= 1024 * 1024:
        return "%.2f MB" % (n / 1024.0 / 1024.0)
    if n >= 1024:
        return "%.1f KB" % (n / 1024.0)
    return "%d B" % n


_TCC_POS_RE = re.compile(r":(\d+)(?::(\d+))?:\s*(?:error:\s*)?(.*)")
_C_MARK_RE = re.compile(r"/\*@L(\d+)\*/")


def _render_c_error(c_err, c_code, src_lines, head_skip=0):
    """v3.3：把 tcc 的 C 行号错误映射回 Impor 行，输出「第N行 列M」+ 波浪线。

    ctrans 在每条语句前发射 /*@L<行号>*/ 标记；tcc 报某 C 行错误时，
    向上回溯最近的标记即得到 Impor 源行。无法映射时保留原始消息。
    """
    c_lines = (c_code or "").splitlines()
    blocks = []
    for raw in (c_err or "").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        m = _TCC_POS_RE.search(raw)
        if not m:
            continue
        cl = int(m.group(1))
        col = int(m.group(2)) if m.group(2) else 0
        msg = (m.group(3) or "").strip()
        impor_ln = 0
        for i in range(min(cl, len(c_lines)) - 1, -1, -1):
            mm = _C_MARK_RE.search(c_lines[i])
            if mm:
                impor_ln = int(mm.group(1))
                break
        if impor_ln:
            blocks.append(render_diagnostic("C 编译错误", msg, impor_ln + head_skip, col or 1,
                                            src_lines, subject="C 编译错误", context=1))
        else:
            blocks.append("C 编译错误: " + msg)
    if not blocks:
        first = ((c_err or "").strip().splitlines() or ["tcc 未给出具体信息"])[0]
        blocks.append("C 编译错误: " + first.strip())
    return "\n".join(blocks)


def _find_tcc():
    """定位 tcc：优先随 Impor 分发的 impor/tcc/win32/tcc.exe，其次 PATH。"""
    here = os.path.dirname(os.path.abspath(__file__))
    cands = [
        os.path.join(here, "tcc", "win32", "tcc.exe"),
        os.path.join(here, "tcc", "tcc.exe"),
        os.path.join(here, "..", "tcc", "win32", "tcc.exe"),
    ]
    for c in cands:
        if os.path.isfile(c):
            return c
    import shutil
    return shutil.which("tcc")


def cmd_compile(args):
    fname = args[0] if args else None
    if not fname:
        _err("compile 需要脚本文件：impor compile FILE [-o OUT] [--exe]")
        return EXIT_USAGE
    out = None
    want_exe = False
    rest = args[1:]
    while rest:
        a = rest.pop(0)
        if a == "-o":
            if not rest:
                _err("-o 需要输出文件名")
                return EXIT_USAGE
            out = rest.pop(0)
        elif a == "--exe":
            want_exe = True
        else:
            _err(f"未知参数 {a}")
            return EXIT_USAGE

    from .transpiler import transpile
    try:
        src = _read_source(fname)
        body = strip_header(src)
        ast = parse_source(body)
        if not _decl_gate(ast, body, fname, "compile"):
            return EXIT_SYNTAX
        if not _type_gate(src, fname, "compile"):
            return EXIT_SYNTAX
        py_code = transpile(ast, source_name=fname, src_lines=src.splitlines())
    except SyntaxError as e:
        _err(_render_exc(e, src.splitlines()))
        return EXIT_SYNTAX
    except Exception as e:
        _err(_render_exc(e, src.splitlines()))
        return EXIT_SYNTAX

    out = out or (os.path.splitext(fname)[0] + ".py")
    try:
        with open(out, "w", encoding="utf-8") as f:
            f.write(py_code)
    except Exception as e:
        _err(f"写入失败: {e}")
        return EXIT_SYNTAX
    print(f"已编译: {out}")

    if want_exe:
        _try_pyinstaller(out)
    return EXIT_OK


def _try_pyinstaller(py_file):
    import subprocess
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        _err("未找到 PyInstaller；请先执行 `pip install pyinstaller`，再 `impor compile --exe`")
        return
    icon = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "assets", "icons", "impor.ico")
    cmd = [sys.executable, "-m", "PyInstaller", "--onefile", py_file]
    if os.path.exists(icon):
        cmd += ["--icon", icon]
    import subprocess
    code = subprocess.call(cmd)
    if code == 0:
        print("打包完成，产物在 dist/ 目录")


def cmd_ir(args):
    """4.4：impor ir FILE [--run] [--stats] [-o OUT] —— 打印/执行 AST 降下来的三地址 IR。

    这一层对齐 xlang 的编译阶段分层（Lex → Parse → Semantic → IR → Lowing）：
    `--run` 走 IR 参考求值器，与树遍历解释器是两条独立路径，用于语义对拍；
    `-o` 把 IR 文本落盘，便于 diff（对应 xlang 的 `Ir.txt` 调试产物）。
    """
    from .ir import IRError, evaluate
    from .ir_lower import lower_ast

    files = [a for a in args if not a.startswith("-")]
    if not files:
        _err("ir 需要脚本文件：impor ir FILE [--run] [--stats] [-o OUT]")
        return EXIT_USAGE
    fname = files[0]
    out_path = ""
    for i, a in enumerate(args):
        if a == "-o" and i + 1 < len(args):
            out_path = args[i + 1]
    run_it = "--run" in args
    want_stats = "--stats" in args
    want_c = "--c" in args
    build_out = ""
    for i, a in enumerate(args):
        if a == "--build" and i + 1 < len(args):
            build_out = args[i + 1]

    src = ""
    try:
        src = _read_source(fname)
        ast = parse_source(strip_header(src), fname)
        module = lower_ast(ast, fname)
        # 4.7：三项优化 pass（常量折叠 / 死代码 / 循环不变量外提）。
        # 在这里先跑一次，`render` / `--run` / `--c` / `--build` 看到的是同一份优化结果。
        from . import irop as _irop
        opt_stats = _irop.optimize_module(module)
    except FileNotFoundError:
        _err("文件不存在: %s" % fname)
        return EXIT_SYNTAX
    except IRError as e:
        _err(str(e))
        _err("IR 覆盖范围见 docs/编译管线-IR-4.6.md；不支持的语法一律明确拒绝，不静默降级。")
        return EXIT_SYNTAX
    except ImporSyntaxError as e:
        _err(_render_exc(e, src.splitlines() if src else None))
        return EXIT_SYNTAX
    except Exception as e:
        _err(_render_exc(e, src.splitlines() if src else None))
        return EXIT_SYNTAX

    # 4.5：IR → C 下降（`--c` 只打印 C 源码；`--build OUT` 用同一份 IR 编出 exe）
    if want_c or build_out:
        from .cir import CIRUnsupported, to_c
        try:
            csrc = to_c(module, fname)
        except CIRUnsupported as e:
            _err(str(e))
            return EXIT_SYNTAX
        if want_c:
            print(csrc, end="")
            return EXIT_OK
        import subprocess
        import tempfile
        cc_path, kind, cc_opt = _resolve_build_cc(None, _find_tcc(), 0)
        if not cc_path:
            _err("找不到 C 编译器（gcc / tcc），无法用 IR 后端产出二进制")
            return EXIT_BUILD
        fd, cpath = tempfile.mkstemp(suffix=".c")
        os.close(fd)
        try:
            with open(cpath, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(csrc)
            out_abs = os.path.abspath(build_out)
            p = subprocess.run(
                _cc_build_cmd(cc_path, kind, cpath, out_abs, cc_opt),
                capture_output=True, text=True, timeout=300, env=_cc_env(cc_path))
        finally:
            try:
                os.unlink(cpath)
            except OSError:
                note_swallowed("ir build temp c cleanup")
        if p.returncode != 0 or not os.path.isfile(out_abs):
            raw = ((p.stdout or "") + "\n" + (p.stderr or "")).strip().splitlines()
            errs = [l for l in raw if "error:" in l or "错误" in l]
            shown = errs[:3] if errs else raw[:3]
            _err("IR 后端编译失败（%s / -O%d）：" % (os.path.basename(cc_path), cc_opt))
            for line in shown:
                _err("  " + line)
            return EXIT_BUILD
        print("IR 后端产物：%s（%s / -O%d）" % (out_abs, os.path.basename(cc_path), cc_opt))
        return EXIT_OK

    text = module.render()
    if out_path:
        with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print("IR 已写入 %s" % out_path)
    else:
        print(text, end="")

    executed = 0
    if run_it:
        try:
            output, ev = evaluate(module)
            executed = ev.executed
        except IRError as e:
            _err(str(e))
            return EXIT_RUNTIME
        except Exception as e:
            _err("IR 求值失败：%s" % e)
            return EXIT_RUNTIME
        for line in output:
            print(line)

    if want_stats:
        st = module.stats()
        print("== IR 统计 ==")
        print("函数 %d / 指令 %d / 标签 %d / 实际执行指令 %d" % (
            st["functions"], st["instructions"], st["labels"], executed))
        print("== 优化 ==")
        print("折叠 %d / 删死代码 %d / 外提不变量 %d / 简化跳转 %d（指令 %d → %d）"
              % (opt_stats.folded, opt_stats.dead, opt_stats.hoisted,
                 opt_stats.jumps, opt_stats.before, opt_stats.after))
    return EXIT_OK


INT64_MAX = 9223372036854775807
INT64_MIN = -9223372036854775808


def _iter_ast(node):
    """深度优先遍历 AST（不依赖具体节点类型，按 __dict__ 递归）。

    ast_nodes 的节点都是普通 Python 对象（无 __slots__），所以变量表就是子节点表。
    """
    stack = [node]
    while stack:
        n = stack.pop()
        if isinstance(n, list):
            stack.extend(n)
            continue
        if isinstance(n, AST):
            yield n
            for v in vars(n).values():
                if isinstance(v, (AST, list)):
                    stack.append(v)


def cmd_check_types(args):
    """impor check-types FILE... [--json] [--strict-types]——语义层类型检查。

    与 `run/build/compile` 入口的门禁是同一套代码（`impor/semantic.py`），
    区别只是这里把 warn 也当失败来判定，适合放进 CI 或提交前钩子。

    三类诊断：
      - arith-type    算术两侧类型不可能成立（"a" - 1）
      - int-range     int64 字面量越界（2**63）
      - missing-return 标注了返回类型，函数体里却没有 return
    """
    as_json = False
    files = []
    for a in args:
        if a == "--json":
            as_json = True
        elif a == "--strict-types":
            os.environ["IMPOR_STRICT_TYPES"] = "1"
        elif a.startswith("-"):
            _err(f"check-types 未知参数 {a}（用法：impor check-types FILE... [--json] [--strict-types]）")
            return EXIT_USAGE
        else:
            files.append(a)
    if not files:
        _err("check-types 需要脚本文件：impor check-types FILE... [--json] [--strict-types]")
        return EXIT_USAGE

    # 目录参数按 .impor 递归展开（CI 里一条命令覆盖全仓）。
    expanded = []
    for fname in files:
        if os.path.isdir(fname):
            for dirpath, dirnames, filenames in os.walk(fname):
                dirnames[:] = [d for d in dirnames
                               if d not in (".git", "__pycache__", ".impor-cache", "node_modules")]
                for fn in filenames:
                    if fn.endswith(".impor"):
                        expanded.append(os.path.join(dirpath, fn))
        else:
            expanded.append(fname)
    files = expanded
    if not files:
        _err("check-types 在给定目录里没有找到 .impor 文件")
        return EXIT_USAGE

    strict = _strict_types_on()
    results = []
    failed = False
    for fname in files:
        try:
            src = _read_source(fname)
        except FileNotFoundError:
            _err(f"[FAIL] 文件不存在: {fname}")
            results.append({"file": fname, "ok": False, "error": "文件不存在"})
            failed = True
            continue
        except Exception as e:
            _err(f"[FAIL] 读取失败 {fname}: {e}")
            results.append({"file": fname, "ok": False, "error": str(e)})
            failed = True
            continue
        from .semantic import check as _sem_check
        diags = _sem_check(src, fname, strict=strict)
        errors = [d for d in diags if getattr(d, "severity", "error") == "error"]
        warns = [d for d in diags if getattr(d, "severity", "error") != "error"]
        ok = not errors and not (strict and warns)
        failed = failed or not ok
        results.append({
            "file": fname, "ok": ok, "strict": strict,
            "error": len(errors), "warn": len(warns),
            "diagnostics": [{
                "code": getattr(d, "code", ""), "severity": getattr(d, "severity", "error"),
                "line": d.line, "col": d.col, "msg": d.msg,
                "why": getattr(d, "why", None), "fix": getattr(d, "fix", None),
            } for d in diags],
        })
        if as_json:
            continue
        print(f"== check-types {fname} ==")
        for d in diags:
            tag = "FAIL" if getattr(d, "severity", "error") == "error" else "WARN"
            print(f"  [{tag}] {getattr(d, 'code', '')} {d.line}:{d.col} {d.msg}")
            if getattr(d, "why", None):
                print(f"         为什么：{d.why}")
            if getattr(d, "fix", None):
                print(f"         怎么改：{d.fix}")
        if ok:
            print(f"  [PASS] 语义层通过（错误 0 / 提示 {len(warns)}）"
                  + ("（strict 档）" if strict else ""))
        else:
            print(f"  [FAIL] 错误 {len(errors)} / 提示 {len(warns)}"
                  + ("（strict 档：提示也算失败）" if strict and warns else ""))

    if as_json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    return EXIT_RUNTIME if failed else EXIT_OK


def cmd_check_int(args):
    """impor check-int FILE[.impor] [--json]——静态判定整数语义的落点。

    交付层只有一套整数语义：int64（与 C 后端逐位一致）。本命令回答一个问题——
    「这份脚本能不能按 int64 交付」，而不是等到 gcc 编完再报 E1012。

    检查项：
      1) 整数字面量是否落在 [INT64_MIN, INT64_MAX]；
      2) 字面量前的一元负号按组合判定（-9223372036854775808 合法，
         -9223372036854775809 非法），避免把合法下界误报成上界溢出。

    退出码：0 = 全部可编译；1 = 存在超界常量（C 后端必然 E1012）；3 = 用法错误。
    """
    files = []
    as_json = False
    for a in args:
        if a == "--json":
            as_json = True
        elif a.startswith("-"):
            _err(f"check-int 未知参数 {a}（用法：impor check-int FILE [--json]）")
            return EXIT_USAGE
        else:
            files.append(a)
    if not files:
        _err("check-int 需要脚本文件：impor check-int FILE [--json]")
        return EXIT_USAGE

    results = []
    had_violation = False
    for fname in files:
        try:
            src = _read_source(fname)
            body = strip_header(src)
            tree = parse_source(body)
        except FileNotFoundError:
            _err(f"[FAIL] 文件不存在: {fname}")
            had_violation = True
            results.append({"file": fname, "ok": False, "error": "文件不存在"})
            continue
        except SyntaxError as e:
            _err(_render_exc(e, src.splitlines() if src else []))
            had_violation = True
            results.append({"file": fname, "ok": False, "error": "语法错误"})
            continue
        except Exception as e:
            _err(f"[FAIL] 解析失败 {fname}: {e}")
            had_violation = True
            results.append({"file": fname, "ok": False, "error": str(e)})
            continue

        # 先收集「被一元负号修饰」的字面量，再统一判定
        negated = set()
        for n in _iter_ast(tree):
            if isinstance(n, UnaryOp) and n.op == "-" and isinstance(n.child, Num):
                negated.add(id(n.child))

        lits = 0
        lo = None
        hi = None
        violations = []
        for n in _iter_ast(tree):
            if not isinstance(n, Num):
                continue
            if type(n.val) is not int:      # 浮点不受 int64 约束
                continue
            lits += 1
            v = -n.val if id(n) in negated else n.val
            lo = v if lo is None else min(lo, v)
            hi = v if hi is None else max(hi, v)
            if v > INT64_MAX:
                violations.append({
                    "line": n.lineno, "col": n.col, "value": v,
                    "reason": f"超出 int64 上限 {INT64_MAX}",
                })
            elif v < INT64_MIN:
                violations.append({
                    "line": n.lineno, "col": n.col, "value": v,
                    "reason": f"超出 int64 下限 {INT64_MIN}",
                })

        ok = not violations
        had_violation = had_violation or not ok
        results.append({
            "file": fname, "ok": ok, "literals": lits,
            "min": lo, "max": hi, "violations": violations,
        })

        if as_json:
            continue
        print(f"== check-int {fname} ==")
        if not violations:
            span = "无整数字面量" if lits == 0 else f"范围 [{lo}, {hi}]"
            print(f"  [PASS] int64 口径可编译：{lits} 个整数字面量，{span}")
            print("  [PASS] C 后端 / IR→C / 解释器默认档三处逐位一致")
            print("  注：只判定常量字面量；运行期算术溢出由 E1012 在解释器与产物中同一文案报出")
        else:
            for x in violations:
                print(f"  [FAIL] 第{x['line']}行第{x['col']}列 "
                      f"字面量 {x['value']} {x['reason']}")
            _err("  [FAIL] int64 口径不可编译：C 后端会在此处报 E1012；"
                 "若确需该量级，只能 `impor run --big` 调试（不可交付）")

    if as_json:
        import json as _json
        print(_json.dumps({"version": __version__, "files": results},
                          ensure_ascii=False, indent=2))
    return 1 if had_violation else 0


def cmd_check(args):
    """impor check FILE：聚合静态检查——语法 + 导入 + lint + 格式化检查。

    逐项输出 PASS/FAIL/WARN；只有 error 级问题返回非零退出码。
    WARN（lint 警告、格式未达标）不阻断 CI，退出码仍为 0。
    """
    files = [a for a in args if not a.startswith("-")]
    if not files:
        _err("check 需要脚本文件：impor check FILE [FILE...]")
        return EXIT_USAGE
    had_error = False
    for fname in files:
        print(f"== 检查 {fname} ==")
        # 1) 语法解析
        src = ""
        body = ""
        try:
            src = _read_source(fname)
            body = strip_header(src)
            ast = parse_source(body)
        except FileNotFoundError:
            _err(f"  [FAIL] 文件不存在: {fname}")
            had_error = True
            continue
        except SyntaxError as e:
            _err(_render_exc(e, src.splitlines()))
            had_error = True
            continue
        except Exception as e:
            _err(_render_exc(e, src.splitlines()) if src else f"  [FAIL] 读取失败: {e}")
            had_error = True
            continue
        print("  [PASS] 语法")
        # 2) 导入检查（缺失 / 循环 / export 引用）
        try:
            graph, ierrs, icycles = analyze_imports(fname)
            if ierrs or icycles:
                for x in ierrs:
                    _err(f"  [FAIL] 导入: {x}")
                for c in icycles:
                    _err(f"  [FAIL] 循环导入: {' -> '.join(c)}")
                had_error = True
            else:
                print(f"  [PASS] 导入（{len(graph)} 个模块，无缺失、无循环）")
        except Exception as e:
            _err(f"  [FAIL] 导入检查异常: {e}")
            had_error = True
        # 3) lint 静态检查
        try:
            from .analysis import analyze
            from .config import find_config
            disabled = set()
            cfg_info = find_config(os.path.dirname(os.path.abspath(fname)))
            if cfg_info:
                lint_cfg = (cfg_info[1].get("lint") or {}).get("disable") or []
                if isinstance(lint_cfg, list):
                    disabled = {str(x) for x in lint_cfg}
            an = analyze(ast, body.split("\n"), disabled_rules=disabled)
            if an.issues:
                for iss in an.issues:
                    sev = {"error": "错误", "warning": "警告", "info": "提示"}[iss.severity]
                    print(f"  [WARN] lint {fname}:{iss.line}:{iss.col} [{sev}] {iss.msg}")
                    if iss.severity == "error":
                        had_error = True
            else:
                print("  [PASS] lint")
        except Exception as e:
            _err(f"  [FAIL] lint 异常: {e}")
            had_error = True
        # 4) 可选类型 / 穷尽性检查（v7.3）
        try:
            from .typecheck import check_source
            tips = check_source(src, None, fname)
            if tips:
                for tip in tips:
                    _err(f"  [FAIL] 类型 {fname}:{tip.line}:{tip.col} {tip.msg}")
                had_error = True
            else:
                print("  [PASS] 类型")
        except Exception as e:
            _err(f"  [FAIL] 类型检查异常: {e}")
            had_error = True
        # 5) 格式化检查（不修改文件）
        try:
            from .fmt import main_fmt
            rc = main_fmt(["--check", fname])
            if rc == 0:
                print("  [PASS] 格式")
            else:
                print("  [WARN] 格式未达标（可用 impor fmt 自动修复）")
        except Exception as e:
            _err(f"  [FAIL] 格式检查异常: {e}")
            had_error = True
    return EXIT_RUNTIME if had_error else EXIT_OK


def cmd_test(args):
    """impor test [目录|文件] [--filter 关键字] [--no-isolate] [--keep] [--verbose]

    默认扫描当前目录下的 tests/（不存在则当前目录），文件名含 test 的 .impor
    文件各算一个测试文件：
      - 通过：文件正常结束
      - 失败：AssertionError / expect() 失败 / 其他 ImporException
    每个测试文件在独立临时目录中执行（--no-isolate 关闭），
    test 内可用相对路径安全读写；临时目录路径在 IMPOR_TEST_TMP 环境变量中。
    退出码：0 全部通过 / 1 存在失败。
    """
    import time
    import tempfile
    target = "tests"
    filter_kw = ""
    isolate = True
    keep = False
    verbose = False
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--no-isolate":
            isolate = False
        elif a == "--keep":
            keep = True
        elif a == "--verbose":
            verbose = True
        elif a == "--filter":
            if i + 1 >= len(args):
                _err("--filter 需要关键字")
                return EXIT_USAGE
            filter_kw = args[i + 1]
            i += 1
        elif a.startswith("--"):
            _err(f"未知参数 {a}")
            return EXIT_USAGE
        else:
            target = a
        i += 1

    files = []
    if os.path.isfile(target):
        files = [target]
    else:
        base = target if os.path.isdir(target) else "."
        if not os.path.isdir(base):
            _err(f"测试目录不存在: {base}")
            return EXIT_USAGE
        for name in sorted(os.listdir(base)):
            if name.endswith(".impor") and "test" in name.lower():
                files.append(os.path.join(base, name))
    # v2.12 修复：默认目标 tests/ 时额外扫描 cwd/examples/（Impor 测试样例惯例）
    if target == "tests":
        ex_dir = os.path.join(os.getcwd(), "examples")
        if os.path.isdir(ex_dir):
            for name in sorted(os.listdir(ex_dir)):
                if name.endswith(".impor") and "test" in name.lower():
                    pth = os.path.join(ex_dir, name)
                    if pth not in files:
                        files.append(pth)
    if filter_kw:
        files = [f for f in files if filter_kw in os.path.basename(f)]
    if not files:
        _err(f"未找到测试文件（目录 {target} 下无含 test 的 .impor 文件；"
             f"可运行 impor test examples/ 或指定测试文件）")
        return EXIT_USAGE

    passed, failed, asserts = 0, 0, 0
    total_start = time.time()
    for fname in files:
        f_start = time.time()
        src = _read_source(fname)
        old_cwd = os.getcwd()
        tmpdir = None
        old_tmp_env = os.environ.get("IMPOR_TEST_TMP")
        try:
            body = strip_header(src, warn=False)
            ast = parse_source(body)
            interp = Interpreter(log_cb=print)
            from .config import apply_config
            src_dir = os.path.dirname(os.path.abspath(fname))
            apply_config(interp, src_dir)
            if src_dir not in interp.module_search_paths:
                interp.module_search_paths.insert(0, src_dir)
            if isolate:
                tmpdir = tempfile.mkdtemp(prefix="impor_test_")
                os.environ["IMPOR_TEST_TMP"] = tmpdir
                os.chdir(tmpdir)
            interp.run(ast)
        except ImporException as e:
            failed += 1
            asserts += getattr(interp, "assert_count", 0) if 'interp' in locals() else 0
            _err(render_diagnostic("测试失败", f"{e.name}: {e.msg}", e.lineno,
                                   getattr(e, "col", 0) or 0, src.splitlines(),
                                   filename=fname, subject=e.name))
        except SyntaxError as e:
            failed += 1
            if isinstance(e, ImporSyntaxError):
                _ln, _col, _msg = e.lineno, e.col or 0, e.msg
            else:
                _ln, _col, _msg = parse_position(str(e))
            _err(render_diagnostic("测试失败·语法错误", _msg, _ln or 0, _col or 0,
                                   src.splitlines(), filename=fname,
                                   subject="语法错误"))
        except Exception as e:
            failed += 1
            _err(f"✗ 内部错误 {fname}: {type(e).__name__}: {e}")
        else:
            passed += 1
            n_assert = getattr(interp, "assert_count", 0)
            asserts += n_assert
            dt = time.time() - f_start
            extra = f"{n_assert} 断言，" if n_assert else ""
            print(f"✓ 通过 {fname}（{extra}{dt:.2f}s）")
        finally:
            if tmpdir:
                os.chdir(old_cwd)
                if old_tmp_env is None:
                    os.environ.pop("IMPOR_TEST_TMP", None)
                else:
                    os.environ["IMPOR_TEST_TMP"] = old_tmp_env
                if keep:
                    print(f"  临时目录保留: {tmpdir}")
                else:
                    import shutil
                    shutil.rmtree(tmpdir, ignore_errors=True)
    total = time.time() - total_start
    print(f"\n测试结果: {passed} 通过 / {failed} 失败"
          f"（共 {len(files)} 个文件，{asserts} 条断言，耗时 {total:.2f}s）")
    return EXIT_OK if failed == 0 else EXIT_RUNTIME


def cmd_new(args):
    if not args:
        _err("new 需要文件名：impor new NAME [--template script|lib|game]")
        return EXIT_USAGE
    fname = args[0]
    template = "script"
    rest = args[1:]
    while rest:
        a = rest.pop(0)
        if a == "--template":
            if not rest:
                _err("--template 需要 script|lib|game")
                return EXIT_USAGE
            template = rest.pop(0)
        else:
            _err(f"未知参数 {a}")
            return EXIT_USAGE
    if template not in ("script", "lib", "game", "pkg"):
        _err(f"未知模板 {template}（可选: script|lib|game|pkg）")
        return EXIT_USAGE
    if template == "pkg":
        return _create_pkg_project(fname)
    if not fname.endswith(".impor"):
        fname += ".impor"
    if os.path.exists(fname):
        _err(f"文件已存在: {fname}")
        return EXIT_SYNTAX
    body = {"script": NEW_TEMPLATE, "lib": NEW_LIB_TEMPLATE,
            "game": NEW_GAME_TEMPLATE}[template]
    with open(fname, "w", encoding="utf-8") as f:
        f.write(body)
    print(f"已创建: {fname}（模板: {template}）")
    return EXIT_OK


def _create_pkg_project(name):
    """impor new NAME --template pkg：创建目录包项目骨架。

    生成 NAME/ 目录：impor.toml + main.impor + lib/<name>_lib/ 库包 + README。
    """
    name = name.rstrip("/\\")
    if os.path.exists(name):
        _err(f"目录已存在: {name}")
        return EXIT_SYNTAX
    try:
        os.makedirs(name)
        for rel, tmpl in NEW_PKG_FILES.items():
            path = os.path.join(name, rel.format(name=name))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(tmpl.format(name=name))
    except OSError as e:
        _err(f"创建项目失败: {e}")
        return EXIT_SYNTAX
    print(f"已创建目录包项目: {name}/（模板: pkg）")
    print(f"  运行: cd {name} && impor run main.impor")
    return EXIT_OK


def _build_interp():
    interp = Interpreter(log_cb=lambda t: None)
    from .config import apply_config
    apply_config(interp)
    return interp


def cmd_list_modules(args):
    interp = _build_interp()
    for name in interp.list_modules():
        print(name)
    return EXIT_OK


def cmd_reload_mod(args):
    if not args:
        _err("reload 需要模块名：impor reload mymod")
        return EXIT_USAGE
    interp = _build_interp()
    modname = args[0]
    try:
        if modname in interp.module_cache:
            mod = interp.reload_module(modname)
        else:
            mod = interp._load_module(modname, 0, 0)
    except ImporException as e:
        _err(_render_exc(e))
        return EXIT_RUNTIME
    kind = "包" if mod.__is_package__ else "模块"
    print(f"{kind} {mod.__name__} 就绪")
    print(f"  文件: {mod.__file__}")
    names = [k for k in mod._store if not k.startswith("__")]
    print(f"  导出 {len(names)} 个符号: {', '.join(sorted(names))}")
    return EXIT_OK


def _collect_imports(stmts, deps):
    for n in stmts:
        if isinstance(n, ImportNode):
            deps.append((n.modname, n.is_python, 0))
        elif isinstance(n, FromImportNode):
            deps.append((n.modname, False, n.rel_level))
        elif hasattr(n, "stmts"):
            _collect_imports(n.stmts, deps)


def _find_spec(name):
    import importlib.util
    try:
        return importlib.util.find_spec(name)
    except (ImportError, ModuleNotFoundError, ValueError):
        return None


def analyze_imports(fname, search_paths=None):
    """静态分析脚本的导入依赖。

    返回 (graph, errors, cycles)：
      graph  {逻辑名: [依赖逻辑名]}
      errors [描述]
      cycles [环路径列表，如 ["a", "b", "a"]]
    """
    interp = Interpreter(log_cb=lambda t: None)
    from .config import find_config, search_paths_from_config
    interp.module_search_paths.insert(0, os.path.dirname(os.path.abspath(fname)))
    found_cfg = find_config(os.path.dirname(os.path.abspath(fname)))
    if found_cfg:
        cfg_dir, cfg = found_cfg
        for p in search_paths_from_config(cfg):
            full = os.path.join(cfg_dir, p) if not os.path.isabs(p) else p
            full = os.path.normpath(full)
            interp.module_search_paths.insert(0, full)
    for p in (search_paths or []):
        interp.module_search_paths.insert(0, p)

    graph = {}
    errors = []
    state = {}
    cycles = []
    path_stack = []

    def resolve(logical, rel_level, cur_pkg):
        if rel_level == 0:
            return logical
        if cur_pkg is None:
            errors.append(f"相对导入（from {'.' * rel_level}{logical}）仅允许在包内使用")
            return None
        pkg_parts = cur_pkg.split(".")
        if len(pkg_parts) < rel_level - 1:
            errors.append(f"相对导入（from {'.' * rel_level}{logical}）超出包层级 {cur_pkg}")
            return None
        parts = pkg_parts[:len(pkg_parts) - (rel_level - 1)]
        if logical:
            parts.extend(logical.split("."))
        return ".".join(parts)

    def cur_pkg_of(logical):
        if logical is None:
            return None
        found = interp._find_user_module_path(logical)
        if found and found[1]:
            return logical
        if "." in logical:
            return logical.rsplit(".", 1)[0]
        return None

    def check_python(name):
        return _find_spec(name) is not None

    def scan(name, is_python, cur_pkg):
        if is_python:
            if not check_python(name):
                errors.append(f"{name}: Python 模块不存在")
            state.setdefault(name, 2)
            return
        if name in state:
            if state[name] == 1:
                start = path_stack.index(name) if name in path_stack else 0
                cycles.append(list(path_stack[start:]) + [name])
            return
        state[name] = 1
        path_stack.append(name)
        found = interp._find_user_module_path(name)
        if found is None:
            if not check_python(name):
                errors.append(f"{name}: 模块未找到（无 .impor 模块/包，Python 也不存在）")
            state[name] = 2
            path_stack.pop()
            return
        graph.setdefault(name, [])
        try:
            with open(found[0], "r", encoding="utf-8") as f:
                src = f.read()
            ast = parse_source(strip_header(src))
        except ImporException as e:
            errors.append(f"{name}: 语法错误 {e.msg}")
            state[name] = 2
            path_stack.pop()
            return
        pkg = cur_pkg_of(name)
        deps = []
        _collect_imports(ast.stmts, deps)
        for dep, py, rel in deps:
            if py:
                check_python(dep)
                continue
            dep_name = resolve(dep, rel, pkg)
            if dep_name is None:
                continue
            if dep_name not in graph[name]:
                graph[name].append(dep_name)
            scan(dep_name, False, cur_pkg_of(dep_name))
        state[name] = 2
        path_stack.pop()

    # 主脚本：直接读取解析，不按模块名查找
    root_name = os.path.basename(fname)
    graph.setdefault(root_name, [])
    state[root_name] = 1
    path_stack.append(root_name)
    try:
        with open(fname, "r", encoding="utf-8") as f:
            src = f.read()
        ast = parse_source(strip_header(src))
    except ImporException as e:
        errors.append(f"{root_name}: 语法错误 {e.msg}")
        state[root_name] = 2
        path_stack.pop()
        return graph, errors, cycles
    deps = []
    _collect_imports(ast.stmts, deps)
    for dep, py, rel in deps:
        if py:
            check_python(dep)
            continue
        dep_name = resolve(dep, rel, None)
        if dep_name is None:
            continue
        if dep_name not in graph[root_name]:
            graph[root_name].append(dep_name)
        scan(dep_name, False, cur_pkg_of(dep_name))
    state[root_name] = 2
    path_stack.pop()
    return graph, errors, cycles


def cmd_check_imports(args):
    if not args:
        _err("check-imports 需要脚本文件")
        return EXIT_USAGE
    fname = args[0]
    if not os.path.exists(fname):
        _err(f"文件不存在: {fname}")
        return EXIT_SYNTAX
    graph, errors, cycles = analyze_imports(fname)
    if errors:
        for e in errors:
            _err(e)
    if cycles:
        for cyc in cycles:
            _err("循环导入: " + " -> ".join(cyc))
    if not errors and not cycles:
        print(f"导入检查通过（{len(graph)} 个模块，无缺失、无循环）")
        return EXIT_OK
    return EXIT_RUNTIME


PKG_INIT_TOML = """[project]
name = "{name}"
version = "0.1.0"
entry = "main.impor"
sandbox_default = false

# 模块搜索路径（相对项目根）
[module_search_paths]
paths = ["./lib"]

# 依赖声明（impor-pkg 包管理器使用）
[dependencies]

# 私有源（可选）；令牌只从环境变量读取，不写进仓库
# [registry]
# url = "https://registry.example.com/impor"
# token_env = "IMPOR_TOKEN"
"""

PKG_INIT_MAIN = ('important<<DAY<<english("English"):\n'
                 '// {name} 项目入口\n'
                 'import {name_lib}\n'
                 'print({name_lib}.greet())\n')

PKG_INIT_LIB = ('important<<DAY<<english("English"):\n'
                '// {name} 库模块\n'
                'def greet()\n'
                '    return "你好，{name}！"\n'
                'export greet\n')


def cmd_fmt(args):
    """impor fmt FILE [-o OUT] / impor fmt --check FILE"""
    from .fmt import main_fmt
    return main_fmt(args)


def cmd_lint(args):
    """impor lint FILE [FILE...]：静态检查未定义/未使用/死代码/未使用导入。

    v3.5：加 --style（或在 impor.toml [lint] style = true）时，额外报告
    「不推荐写法」（v3.4 兼容写法）并给出唯一推荐写法。全部为 info 级，不影响退出码。
    v3.8：同一开关下增加可读性提示（命名 / 魔法数字 / 函数过长）；--readability 为等价别名。
    v3.9：--fix 自动删除未使用导入、给未使用变量加 _ 前缀；[lint] 阈值可配置。
    """
    from .analysis import analyze, readability_lint, style_lint
    from .config import find_config
    files = [a for a in args if not a.startswith("-")]
    want_style = "--style" in args or "--readability" in args
    want_fix = "--fix" in args
    if not files:
        _err("lint 需要脚本文件：impor lint FILE [FILE...]")
        return EXIT_USAGE
    # impor.toml [lint]：禁用规则 / 风格开关 / 可读性阈值
    disabled = set()
    lint_cfg = {}
    cfg_info = find_config(os.path.dirname(os.path.abspath(files[0]))) if files else None
    if cfg_info:
        lint_cfg = (cfg_info[1].get("lint") or {})
        disabled_raw = lint_cfg.get("disable") or []
        if isinstance(disabled_raw, list):
            disabled = {str(x) for x in disabled_raw}
        if lint_cfg.get("style") in (True, "true", "True"):
            want_style = True

    def apply_fixes(src, body, issues):
        """按 lint 结果做最小自动修复，返回 (新源码, 修复数)。"""
        src_lines = src.split("\n")
        offset = max(0, len(src_lines) - len(body.split("\n")))
        changed = 0
        drop = set()
        edits = {}
        for iss in issues:
            idx = iss.line - 1 + offset
            if idx < 0 or idx >= len(src_lines):
                continue
            if iss.code == "unused-import":
                drop.add(idx)
            elif iss.code == "unused-variable":
                line = src_lines[idx]
                col = max(0, iss.col)
                if col < len(line) and not line[col:].startswith("_"):
                    word = ""
                    for ch in line[col:]:
                        if ch.isalnum() or ch == "_":
                            word += ch
                        else:
                            break
                    if word:
                        edits[idx] = (col, "_" + word)
        for idx in drop:
            src_lines[idx] = None
        for idx, (col, new) in edits.items():
            line = src_lines[idx]
            src_lines[idx] = line[:col] + new + line[col + len(new) - 1:]
        new_src = "\n".join(l for l in src_lines if l is not None)
        return new_src, len(drop) + len(edits)

    had_error = False
    for fname in files:
        try:
            src = _read_source(fname)
            body = strip_header(src)
            ast = parse_source(body)
        except FileNotFoundError as e:
            _err(str(e))
            had_error = True
            continue
        except ImporException as e:
            _err(f"{fname}: {e.name}: {e.msg}")
            had_error = True
            continue
        except SyntaxError as e:
            _err(render_diagnostic(
                "语法错误", getattr(e, "msg", str(e)),
                getattr(e, "lineno", 0) or 0, getattr(e, "col", 0) or 0,
                src.splitlines(), fname, hint=getattr(e, "hint", None)))
            had_error = True
            continue
        except Exception as e:
            _err(f"{fname}: 解析失败: {e}")
            had_error = True
            continue
        an = analyze(ast, body.split("\n"), disabled_rules=disabled)
        issues = list(an.issues)
        if want_style:
            issues.extend(i for i in style_lint(body, fname) if i.code not in disabled)
            issues.extend(i for i in readability_lint(ast, fname, lint_cfg)
                          if i.code not in disabled)
            issues.sort(key=lambda i: (i.line, i.col))
        if want_fix and an.issues:
            fixed, count = apply_fixes(src, body, an.issues)
            if count:
                with open(fname, "w", encoding="utf-8", newline="\n") as f:
                    f.write(fixed)
                print(f"{fname}: 已自动修复 {count} 处")
                continue
        if not issues:
            print(f"{fname}: 未发现问题")
            continue
        for iss in issues:
            sev = {"error": "错误", "warning": "警告", "info": "提示"}[iss.severity]
            print(f"{fname}:{iss.line}:{iss.col} [{sev}] {iss.msg}")
            if iss.severity in ("error", "warning"):
                had_error = True
                fix = issue_fix(iss.code, iss.msg)
                if fix:
                    print(f"          修复：{fix}")
    return EXIT_RUNTIME if had_error else EXIT_OK


def cmd_doc(args):
    """impor doc FILE [-o OUT.md]：从 `##` 文档注释生成 Markdown API 文档（v3.5）。"""
    from . import ast_nodes as _A
    from .analysis import analyze
    files = [a for a in args if not a.startswith("-")]
    out_path = None
    if "-o" in args:
        idx = args.index("-o")
        if idx + 1 < len(args):
            out_path = args[idx + 1]
            files = [f for f in files if f != out_path]
    if not files:
        _err("doc 需要脚本文件：impor doc FILE [-o OUT.md]")
        return EXIT_USAGE
    fname = files[0]
    try:
        src = _read_source(fname)
        body = strip_header(src)
        ast = parse_source(body)
    except FileNotFoundError as e:
        _err(str(e))
        return EXIT_RUNTIME
    except Exception as e:
        _err(f"{fname}: 解析失败: {e}")
        return EXIT_RUNTIME

    def _sig(node):
        params = ", ".join(str(p) for p in getattr(node, "params", []) or [])
        return f"{node.name}({params})"

    def _emit_def(node, level, out):
        if getattr(node, "doc", None):
            out.append(f"{'#' * level} `{_sig(node)}`")
            out.append("")
            out.append(node.doc)
            out.append("")
        # 嵌套函数（含类方法）一并输出
        for st in getattr(node, "body", []) or []:
            if isinstance(st, _A.Def):
                _emit_def(st, min(level + 1, 6), out)

    def _emit_class(node, level, out):
        base = f"({node.base_name})" if getattr(node, "base_name", None) else ""
        out.append(f"{'#' * level} class `{node.cls_name}{base}`")
        out.append("")
        if getattr(node, "doc", None):
            out.append(node.doc)
            out.append("")
        for st in getattr(node, "body", []) or []:
            if isinstance(st, _A.Def):
                _emit_def(st, min(level + 1, 6), out)

    lines = [f"# {os.path.splitext(os.path.basename(fname))[0]}", ""]
    for st in ast.stmts:
        if isinstance(st, _A.Def):
            _emit_def(st, 2, lines)
        elif isinstance(st, _A.ClassDef):
            _emit_class(st, 2, lines)
        elif isinstance(st, _A.ExceptionDef) and getattr(st, "doc", None):
            lines.extend([f"## exception `{st.exc_name}`", "", st.doc, ""])
    if not any(getattr(st, "doc", None) for st in ast.stmts):
        lines.append("> 本文件没有 `##` 文档注释；在声明上方写 `## 说明文字` 即可生成文档。")

    text = "\n".join(lines).rstrip() + "\n"
    if out_path:
        with open(out_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        print(f"已生成文档：{os.path.abspath(out_path)}（{len(lines)} 行）")
    else:
        print(text)
    return EXIT_OK


def cmd_lsp(args):
    """impor lsp：前台运行语言服务器（stdio JSON-RPC，供 VS Code 等 LSP 客户端连接）。"""
    from .lsp import main_lsp
    return main_lsp(args)


def cmd_dap(args):
    """impor dap：前台运行 DAP 调试适配器（stdio，供 VS Code 调试协议使用）。"""
    from .dap import main_dap
    return main_dap(args)


def cmd_pkg(args):
    if not args:
        _err("pkg 需要子命令：impor pkg init|install|remove|list|search|lock|check|upgrade|registry")
        return EXIT_USAGE
    if args[0] == "init":
        from . import pkgmgr
        return _cmd_pkg_init(args[1:])
    from .pkgmgr import main_pkg
    return main_pkg(args)


def _cmd_pkg_init(args):
    target = args[0] if args else "."
    base = os.path.join(target, "impor_toml")
    os.makedirs(base, exist_ok=True)
    name = os.path.basename(os.path.abspath(target)) or "myproject"
    import re
    safe = re.sub(r"[^0-9A-Za-z_]", "_", name) or "myproject"
    files = {
        "impor.toml": PKG_INIT_TOML.format(name=safe),
        "main.impor": PKG_INIT_MAIN.format(name=safe, name_lib=safe + "_lib"),
        os.path.join("lib", safe + "_lib.impor"): PKG_INIT_LIB.format(name=safe),
    }
    for rel, content in files.items():
        full = os.path.join(base, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
    print(f"已生成项目骨架: {base}")
    print(f"  入口: {os.path.join(base, 'main.impor')}")
    print(f"  配置: {os.path.join(base, 'impor.toml')}")
    print(f"  运行: impor run {os.path.join(base, 'main.impor')}")
    return EXIT_OK


def cmd_env(args):
    """impor env —— 环境诊断：版本 / 平台 / 后端 / 自举状态 / 缓存（v2.16）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)

    def _st(p):
        return "可用" if os.path.isfile(p) else "缺失"

    print(f"Impor (I++) {__version__} 环境诊断")
    print(f"  平台       {sys.platform} / Python {sys.version.split()[0]}")
    print(f"  源码根     {root}")
    print(f"  C 编译器   imporc.exe：{_st(os.path.join(here, 'imporc.exe'))}")
    tcc = _find_tcc()
    print(f"  tcc        {os.path.basename(tcc) + '（' + tcc + '）' if tcc else '缺失'}")
    print(f"  自举种子   bootstrap/imporc.impor：{_st(os.path.join(root, 'bootstrap', 'imporc.impor'))}")
    print(f"  自举产物 B imporc_self.exe：{_st(os.path.join(root, 'imporc_self.exe'))}")
    hist = os.path.join(os.path.expanduser("~"), ".impor_history")
    if os.path.isfile(hist):
        try:
            with open(hist, encoding="utf-8") as f:
                n = sum(1 for _ in f)
        except OSError:
            n = 0
        print(f"  REPL 历史   {hist}（{n} 条）")
    else:
        print(f"  REPL 历史   {hist}（无）")
    _color = "开启" if color_enabled() else "关闭（非终端 / NO_COLOR / IMPOR_COLOR=never）"
    print(f"  诊断彩色   {_color}（IMPOR_COLOR=always|never、NO_COLOR 可覆盖）")
    print(f"  包缓存     {os.path.expanduser('~/.impor/pkg')}")
    return EXIT_OK


# 示例目录（impor demo 用）：名字 -> (文件名, 说明, 是否需要交互/图形)
DEMO_CATALOG = (
    ("hello", "hello.impor", "你好，Impor（基础输出）", False),
    ("cn", "cn_demo.impor", "中文关键字示例", False),
    ("grade", "grade.impor", "成绩分级（if/elif/else）", False),
    ("fib", "fib.impor", "斐波那契数列（数组 + 循环）", False),
    ("guess", "guess.impor", "猜数字游戏（需要输入）", True),
    ("snake", "snake.impor", "贪吃蛇（图形窗口）", True),
)


def _load_demo_extras():
    """把 examples/demo_01…demo_40 动态加入 `impor demo` 列表。"""
    out = []
    demo_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "examples")
    try:
        names = sorted(n for n in os.listdir(demo_dir)
                       if n.startswith("demo_") and n.endswith(".impor"))
    except OSError:
        return out
    for name in names:
        stem = name[:-6]
        parts = stem.split("_")
        short = "demo_" + parts[1] if len(parts) > 1 else stem
        desc = ""
        try:
            with open(os.path.join(demo_dir, name), "r", encoding="utf-8") as f:
                for line in f:
                    s = line.strip()
                    if s.startswith("//"):
                        desc = s[2:].strip()
                        break
        except OSError:
            pass
        out.append((short, name, desc or stem, False))
    return out


DEMO_CATALOG = DEMO_CATALOG + tuple(_load_demo_extras())


def cmd_demo(args):
    """impor demo [名字] —— 列出并一键运行内置示例（v2.16）。"""
    if not args:
        print(f"Impor (I++) {__version__} 内置示例（impor demo 名字 运行）：")
        for name, _fn, desc, gui in DEMO_CATALOG:
            print(f"  {name.ljust(8)} {desc}{'（交互/图形）' if gui else ''}")
        print("\n提示：impor demo snake 运行贪吃蛇；impor new --template game 生成游戏模板。")
        return EXIT_OK
    name = args[0]
    for dn, fn, desc, _gui in DEMO_CATALOG:
        if dn == name:
            path = os.path.abspath(os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "..", "examples", fn))
            if not os.path.isfile(path):
                _err(f"示例文件缺失：{path}")
                return EXIT_RUNTIME
            return cmd_run([path] + args[1:])
    _err(f"未知示例 {name}（impor demo 查看列表）")
    return EXIT_USAGE


# 性能基准脚本（纯 C 后端支持的过程式子集；算术循环最能体现原生编译优势）
BENCH_SCRIPT = ('important<<DAY<<english("English"):\n'
                'var i = 0\n'
                'var s = 0\n'
                'while i < 1000000\n'
                '    s = s + i\n'
                '    i = i + 1\n'
                'print("sum =", s)\n')


def _proc_rss_kb(proc):
    """当前进程常驻内存（KB）；Windows 用 psapi，POSIX 用 resource。"""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        psapi = ctypes.WinDLL("psapi")
        counters = PROCESS_MEMORY_COUNTERS()
        handle = int(proc._handle)
        if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters),
                                       ctypes.sizeof(counters)):
            return counters.WorkingSetSize // 1024
        return 0
    try:
        import resource
        kb = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
        return kb if sys.platform.startswith("linux") else kb // 1024
    except Exception:
        return 0


def cmd_bench_spec(args):
    """impor bench-spec FILE.impor —— 体积 / 冷启动 / 常驻内存 / 构建耗时（v3.11）。"""
    import json
    import subprocess
    import tempfile
    import time

    if not args or args[0].startswith("-"):
        _err("用法：impor bench-spec FILE.impor [--runs N] [--json]")
        return EXIT_USAGE
    src = args[0]
    if not os.path.isfile(src):
        _err(f"文件不存在：{src}")
        return EXIT_USAGE
    runs = 30
    as_json = "--json" in args
    if "--runs" in args:
        try:
            runs = max(1, int(args[args.index("--runs") + 1]))
        except (IndexError, ValueError):
            _err("--runs 需要正整数")
            return EXIT_USAGE

    fd, exe = tempfile.mkstemp(suffix=".exe" if os.name == "nt" else "")
    os.close(fd)
    try:
        t0 = time.perf_counter()
        rc = cmd_build([src, "-o", exe, "--no-cache"])
        build_s = time.perf_counter() - t0
        if rc != EXIT_OK or not os.path.isfile(exe):
            _err("bench-spec：构建失败")
            return rc if rc else EXIT_RUNTIME

        size_b = os.path.getsize(exe)

        cold = []
        for _ in range(runs):
            t0 = time.perf_counter()
            r = subprocess.run([exe], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=120)
            cold.append((time.perf_counter() - t0) * 1000)
            if r.returncode != 0:
                _err(f"bench-spec：exe 运行失败 rc={r.returncode}")
                return EXIT_RUNTIME

        peak = 0
        p = subprocess.Popen([exe], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        while p.poll() is None:
            peak = max(peak, _proc_rss_kb(p))
            time.sleep(0.002)
        peak = max(peak, _proc_rss_kb(p))

        cold_ms = sum(cold) / len(cold)
        result = {
            "exe_size_kb": round(size_b / 1024.0, 1),
            "cold_start_ms": round(cold_ms, 1),
            "rss_kb": peak,
            "build_s": round(build_s, 2),
            "runs": runs,
        }
        if as_json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            print(f"Impor (I++) {__version__} 规格实测：{os.path.basename(src)}")
            print(f"  exe 体积        ：{result['exe_size_kb']:.1f} KB")
            print(f"  冷启动（{runs} 次均值）：{result['cold_start_ms']:.1f} ms")
            print(f"  常驻内存        ：{result['rss_kb']} KB")
            print(f"  构建耗时        ：{result['build_s']:.2f} s")
        return EXIT_OK
    finally:
        try:
            os.unlink(exe)
        except OSError:
            pass


def cmd_bench(args):
    """impor bench —— 性能基准：解释器 vs C 后端原生 exe（v2.16）。"""
    import tempfile
    import time
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    fd, fpath = tempfile.mkstemp(suffix=".impor", prefix="impor_bench_")
    os.close(fd)
    exe = os.path.splitext(fpath)[0] + ".exe"
    try:
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(BENCH_SCRIPT)

        src = _read_source(fpath)
        ast = parse_source(strip_header(src, warn=False))
        t0 = time.perf_counter()
        interp = Interpreter()
        interp.run(ast)
        t_walk = time.perf_counter() - t0

        t0 = time.perf_counter()
        fast = Interpreter(fast_mode=True)
        fast.run(ast)
        t_int = time.perf_counter() - t0

        rc = cmd_build([fpath, "-o", exe])
        if rc != EXIT_OK or not os.path.isfile(exe):
            _err("基准：C 后端编译失败，跳过 exe 对比")
            return rc
        t1 = time.perf_counter()
        import subprocess
        r = subprocess.run([exe], capture_output=True, text=True, timeout=120)
        t_c = time.perf_counter() - t1
        if r.returncode != 0:
            _err(f"基准：exe 运行失败 rc={r.returncode}\n{r.stderr[-300:]}")
            return EXIT_RUNTIME

        ratio = t_walk / t_int if t_int > 0 else float("inf")
        print(f"\nImpor (I++) {__version__} 性能基准")
        print("  负载：算术循环 100 万次累加（while + 加法）")
        print(f"  树遍历解释器         ：{t_walk:8.3f} 秒")
        print(f"  快速后端（转译+执行） ：{t_int:8.3f} 秒")
        print(f"  快速后端加速比       ：x{ratio:6.1f}")
        print(f"  C 后端（原生 exe）   ：{t_c:8.3f} 秒")
        print("  （原生 exe 由 impor build 生成，运行不依赖 Python）")
        if "--gate" in args:
            import json
            gate_path = os.path.join(root, "benchmarks", "gate.json")
            try:
                with open(gate_path, encoding="utf-8") as f:
                    gate = json.load(f)
            except Exception as e:
                _err(f"基准门槛读取失败：{e}")
                return EXIT_RUNTIME
            limit = float(gate.get("interpreter_loop_max_ms", 9000))
            ms = t_int * 1000
            if ms > limit:
                _err(f"性能门槛失败：解释器 {ms:.0f} ms > 上限 {limit:.0f} ms")
                return EXIT_RUNTIME
            print(f"  性能门槛             ：通过（{ms:.0f} ms ≤ {limit:.0f} ms）")
    finally:
        for p in (fpath, exe):
            try:
                os.unlink(p)
            except OSError:
                pass
    return EXIT_OK


def cmd_mem(args):
    """impor mem [FILE] —— 自动内存管理概览；带脚本时运行并显示内存变化（v3.2）。"""
    from .memory import MemoryManager, process_memory_report

    if not args:
        print(f"Impor (I++) {__version__} 自动内存管理")
        print("  " + process_memory_report())
        st = MemoryManager().stats()
        kinds = "、".join(f"{k} {v}" for k, v in sorted(st["live_by_type"].items())) or "无"
        print(f"  存活对象   {st['live']} 个（{kinds}）")
        print("  默认阈值   20000 次分配触发一次自动回收（脚本内 autogc(N) 可调，0 关闭）")
        print("  用法       impor mem FILE.impor   运行脚本并输出内存变化")
        print("  脚本内建   mem() / memstats() / gc() / autogc()")
        return EXIT_OK

    fname = args[0]
    try:
        src = _read_source(fname)
    except FileNotFoundError as e:
        _err(str(e))
        return EXIT_SYNTAX
    except Exception as e:
        _err(f"读取失败: {e}")
        return EXIT_SYNTAX
    try:
        body = strip_header(src)
        ast = parse_source(body, filename=fname)
    except ImporException as e:
        _err(_render_exc(e))
        return EXIT_SYNTAX
    except Exception as e:
        _err(f"解析失败: {e}")
        return EXIT_SYNTAX

    interp = Interpreter(log_cb=print, argv=args[1:], filename=fname)
    before = interp.memory.stats()
    rc = EXIT_OK
    try:
        interp.run(ast)
    except ImporException as e:
        _err(_render_exc(e))
        rc = EXIT_RUNTIME
    after = interp.memory.stats()
    print("\n[自动内存管理] 运行前存活 %d → 运行后存活 %d；本次分配 %d 个对象（峰值 %d）；回收 %d 次"
          % (before["live"], after["live"], after["alloc"], after["peak"], after["collections"]))
    print("[自动内存管理] " + interp.memory.report())
    return rc


def cmd_version(args):
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    print(f"Impor (I++) {__version__}")
    print(f"  平台: {sys.platform} | Python {sys.version.split()[0]}")
    tcc = _find_tcc()
    print(f"  C 后端: {'可用' if tcc else '缺失'}" + (f"（{os.path.basename(tcc)}）" if tcc else ""))
    if os.path.isfile(os.path.join(here, "imporc.exe")):
        print("  自举: 原生 C 编译器 imporc.exe 可用")
        if os.path.isfile(os.path.join(root, "imporc_self.exe")):
            print("        自举产物 B（imporc_self.exe）已构建，B/C/D 三代产物一致")
    return EXIT_OK


# v2.5：子命令帮助文本（impor help <子命令> / impor <子命令> --help）
SUBCOMMAND_HELP = {
    "run": "impor run [--native] [--cc gcc|tcc] [-O0..-O3] FILE [参数...]\n"
           "  运行 Impor 脚本（FILE 省略为 repl）。\n"
           "  --native：能静态编译的脚本先编译成原生产物再执行（产物按源码指纹缓存），\n"
           "            子集外脚本自动回落解释器；语义、输出、错误信息与解释器一致。",
    "run-dbg": "impor run-dbg FILE [参数...]\n  交互式调试：n 步过 / s 步入 / o 步出 / c 继续 /\n"
               "  b 行号 [if 表达式] 条件断点 / d 行号 删除 / p 表达式 求值 / bt 栈 / l 局部变量 / q 退出。",
    "repl": "impor repl\n  启动交互式 REPL（历史持久化到 ~/.impor_history）。",
    "compile": "impor compile FILE [-o OUT] [--exe]\n  开发兼容：Impor → Python（仅用于本地调试，不是发布路径）。发布用 impor run / 便携版，或用 impor build 的 C 子集。",
    "build": "impor build FILE.impor [-o OUT.exe] [--cc auto|tcc|gcc|<路径>] [-O0..-O3] [--quiet]\n  C 后端编译（语法子集，可选路径）：Impor → C → gcc -O2（找不到 x86_64 gcc 时用内置 tcc）。\n  解释器是唯一语义定义；C 后端不支持即编译期拒绝，不静默降级。\n  --quiet 只产出文件、不打印构建摘要（原生启动器调用时用）。\n  默认产出独立 exe；--lib 产出 C ABI 共享库：OUT.dll + OUT.h + OUT.def。\n  --dll 产出「启动器专用 DLL」：数值快路径，只导出 impor_entry，给 impor-fast.exe 在本进程内调用。\n  --lib --c-source 产出 C 源码包：lib.c/lib.h/lib.def/build_msvc.bat/build_gcc.bat。\n  加 --target win64 / linux64 可直接调用 cl / gcc -m64 生成 64 位库；缺工具链明确报错，不静默降级。\n  内置 tcc 产出 32 位（i386）产物；64 位工程请用 --target 或 --c-source 以 cl 或 gcc -m64 自行编译。\n  字符串返回值用 impor_free_str() 释放；类型支持 int/float/bool/str/void/size_t/handle/ptr。",
    "check": "impor check FILE\n  语法检查（解析并报告错误，不执行）。",
    "check-types": "impor check-types FILE... [--json] [--strict-types]\n"
                   "  语义层类型检查（4.7 新增）：算术混型、int64 字面量越界、\n"
                   "  标注了返回类型却没有 return。默认这些只是提示（不拦运行）；\n"
                   "  --strict-types 把它们当错误，可在 CI / 提交前钩子里当门禁用。\n"
                   "  run/build/compile 入口共用同一套检查，加 --strict-types 即同等收紧。",
    "ir": "impor ir FILE [--run] [--stats] [-o OUT]\n"
          "  打印中端表示（三地址 IR，4.4 新增）：Lex → Parse → Semantic → IR 之后的结果。\n"
          "  --run：用 IR 参考求值器直接执行 IR（与解释器独立的一条路径，用于语义对拍）。\n"
          "  --stats：输出函数/指令/标签数与实际执行指令数；-o 把 IR 文本写入文件便于 diff。\n"
          "  不支持的语法（类/异常/导入/闭包等）会带行号明确拒绝，不静默降级。",
    "test": "impor test [目录|文件] [--filter 关键字] [--no-isolate] [--keep]\n  运行单元测试：默认扫描 tests/ 下文件名含 test 的 .impor 文件，\n  每个文件在独立临时目录执行（IMPOR_TEST_TMP 可取路径）；\n  assert / expect 失败或运行异常计为失败；退出码 0 全部通过 / 1 有失败。",
    "new": "impor new NAME [--template script|lib|game]\n  生成项目模板（默认 script）。",
    "list-modules": "impor list-modules\n  列出当前可发现模块（含包）。",
    "reload": "impor reload MODNAME\n  重新加载模块（开发调试用）。",
    "check-imports": "impor check-imports FILE\n  静态检查全部导入：缺失模块、循环导入。",
    "fmt": "impor fmt FILE [-o OUT] [--indent N] [--max-line N] / impor fmt --check FILE\n"
           "  格式化 Impor 源码；读取 impor.toml [fmt] 配置（indent_width/max_line_length）。",
    "check-fmt": "impor check-fmt FILE\n  检查是否已格式化（CI 可用，未格式化返回 1）。",
    "lint": "impor lint FILE [--style|--readability] [--fix]\n  静态检查：未定义变量、未使用变量/导入、不可达代码、参数数量不匹配。\n  可读性开关：额外报告命名过短、重复魔法数字、函数过长，以及「不推荐写法」；\n  --fix：自动删除未使用导入、给未使用变量加 _ 前缀；\n  impor.toml [lint] 可配置 max_function_lines / max_function_statements /\n  magic_number_repeats / short_name_whitelist / enum_exhaustive；全部为提示级，不影响退出码。",
    "doc": "impor doc FILE [-o OUT.md]\n  从 `##` 文档注释生成 Markdown API 文档（v3.5）。",
    "lsp": "impor lsp [ROOT]\n  启动 LSP 语言服务器（stdio JSON-RPC，供 VS Code 等编辑器接入）。",
    "dap": "impor dap\n  启动 DAP 调试适配器（stdio，供 VS Code 调试协议接入断点/单步/调用栈）。",
    "pkg": "impor pkg init|install|remove|list|search|lock|check|upgrade|registry ...\n  包管理器：本地目录 / git / 私有源；install --frozen 按 impor.lock 还原并校验哈希；\n  search 只扫本地（缓存 + 项目 *.zip，离线可用）；lock --check 校验版本与内容哈希。",
    "demo": "impor demo [名字]\n  列出内置示例；impor demo 名字 一键运行（snake 为图形窗口）。",
    "env": "impor env\n  环境诊断：版本/平台/C 编译器/tcc/自举状态/缓存目录。",
    "bench": "impor bench\n  性能基准：解释器 vs C 后端原生 exe，输出耗时与加速比。",
    "wasm": "impor wasm FILE.impor [-o OUT.wasm] [--html OUT.html]\n  第二后端：直接编成 WebAssembly（手写二进制，无 LLVM/无 C 工具链）。\n  产出可在浏览器、Node、任何 WASM 运行时执行；子集与 C 后端同口径，超出即编译期拒绝。\n  --html 另外生成一个内嵌 wasm 的单文件演示页，双击即可在浏览器里跑。",
    "vm": "impor vm run|compile|exec|dump FILE [-o OUT.ibc] [--big]\n  自有字节码后端（NVM）：AST → 栈式字节码 → 自带虚拟机，不依赖 gcc/tcc。\n  run 立即执行；compile 生成 .ibc 落盘；exec 加载 .ibc；dump 反汇编。\n  默认 int64 与 impor run 同口径（溢出报 E1012）；--big 改任意精度。\n  支持过程式核心（变量/控制流/函数/闭包/list/dict），class/import/try 等会明确报错。",
    "opt": "impor opt run|dump FILE [-o OUT.asm]\n  教科书优化编译器后端（OPTC）：TAC → 基本块/CFG → 活跃变量数据流分析 → 线性扫描寄存器分配 → 寄存器式目标码 → 寄存器机执行，不依赖 gcc/tcc。\n  run 立即执行；dump 反汇编寄存器式目标码（含块/区间/溢出统计）；-o 把反汇编落盘。\n  默认 int64（溢出报 E1012）；支持标量过程式核心（变量/控制流/递归/print），list/class/try/import 等会明确报错。",
    "native": "impor native [run|build] FILE [-o OUT.exe]\n  纯自足原生 x64 AOT：TAC → 自写 x86-64 机器码字节 → 自写 PE32+ 可执行文件，全程不调 nasm/gcc/ld。\n  build（默认）产出同名 .exe；run 编译到临时 exe 并执行（stdout 与退出码透传）。\n  支持标量过程式核心（int/bool/str/none、变量/控制流/递归/print，含 6 参数与深 spill）；浮点/容器/内建等编译期干净 BackendError。",
    "bench-spec": "impor bench-spec FILE.impor [--runs N] [--json]\n  规格实测：exe 体积 / 冷启动均值 / 常驻内存 / 构建耗时。",
    "mem": "impor mem [FILE]\n  自动内存管理概览（无参数）或运行脚本并输出运行前后内存变化、分配/峰值/回收统计。",
    "version": "impor version\n  显示版本与 C 后端/自举状态。",
    "explain": "impor explain [E1001|ZeroDivisionError]\n  解释错误码：为什么发生、怎么修、最小示例；不带参数列出全部错误码。",
    "stability": "impor stability [--limits] [--strict] [--json] [--quiet]\n"
                 "  稳定性自检：死循环/卡死/刷屏/巨型分配/内存累积/无限递归/除零，\n"
                 "  逐个验证「结构化熔断 + 父进程存活 + 无 Traceback」。\n"
                 "  --limits 打印默认与严格额度；--strict 用严格档跑自检；--json 输出机器可读结果。",
    "syntax": "impor syntax [关键字|内建] [--search 词] [--json]\n  语法查询：意思 / 使用格式 / 程序例子 / 例子说明；覆盖全部关键字与常用内建函数。",
}


def cmd_explain(args):
    """v4.3：impor explain [错误码|异常名] —— 讲清错误为什么发生、怎么修。"""
    if not args:
        print("可用错误码（impor explain E1001 查看详情）：")
        for code in sorted(ERROR_TITLES):
            etype, title, _ = ERROR_TITLES[code]
            print("  %s  %-18s %s" % (code, etype, title))
        return EXIT_OK
    info = code_info(args[0])
    if not info:
        _err("未知错误码或异常名: %s（运行 impor explain 查看列表）" % args[0])
        return EXIT_USAGE
    print(format_code_info(info))
    return EXIT_OK


def cmd_help(args):
    if args:
        name = args[0]
        if name in SUBCOMMAND_HELP:
            print(SUBCOMMAND_HELP[name])
            return EXIT_OK
        _err(f"未知子命令: {name}")
        return EXIT_USAGE
    print(__doc__)
    return EXIT_OK


# 子命令派发表（唯一维护点：新增命令只需在此注册一行，main 与启动器自动生效）
def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    try:
        return _main_impl(argv)
    except ImporException as e:
        _err(_render_exc(e))
        return EXIT_RUNTIME
    except KeyboardInterrupt:
        _err("已中断（KeyboardInterrupt）")
        return 130
    except Exception as e:
        from .stability import write_crash_log as _crash
        path = _crash(e, where="cli.main", extra={"argv": argv})
        _err(_render_exc(e))
        if path:
            _err("内部错误现场已写入 %s" % path)
        return EXIT_RUNTIME


def cmd_migrate_cn(args):
    """v5.3：把旧中文关键字迁移为英文关键字。用法：impor migrate-cn [--dry-run] [路径...]"""
    import io
    from .lexer import CN_TO_EN
    dry = "--dry-run" in args
    targets = [a for a in args if a != "--dry-run"] or ["."]
    keys = sorted(CN_TO_EN, key=len, reverse=True)
    skip = {"build", "dist", "__pycache__", ".git", "node_modules", ".impor_recovery", "vscode-impor"}

    def rewrite(line):
        out, i, n, quote = [], 0, len(line), None
        while i < n:
            c = line[i]
            if quote:
                out.append(c)
                if c == "\\" and i + 1 < n:
                    out.append(line[i + 1]); i += 2; continue
                if c == quote:
                    quote = None
                i += 1
                continue
            if c in ('"', "'"):
                quote = c; out.append(c); i += 1; continue
            if c == "#":
                out.append(line[i:]); break
            hit = None
            for k in keys:
                if line.startswith(k, i):
                    hit = k; break
            if hit:
                out.append(CN_TO_EN[hit]); i += len(hit); continue
            out.append(c); i += 1
        return "".join(out)

    files = []
    for target in targets:
        if os.path.isfile(target):
            files.append(target); continue
        for dirpath, dirnames, filenames in os.walk(target):
            dirnames[:] = [d for d in dirnames if d not in skip]
            for fn in filenames:
                if fn.endswith(".impor"):
                    files.append(os.path.join(dirpath, fn))
    changed = 0
    for path in files:
        try:
            raw = io.open(path, encoding="utf-8-sig").read()
        except Exception as e:
            print(f"跳过 {path}: {e}")
            continue
        src = raw.splitlines()
        new = "\n".join(rewrite(x) for x in src)
        if raw.endswith("\n"):
            new += "\n"
        if new != raw:
            changed += 1
            print(("[DRY] " if dry else "[改写] ") + path)
            if not dry:
                import shutil
                shutil.copy2(path, path + ".cn.bak")
                io.open(path, "w", encoding="utf-8", newline="\n").write(new)
    print(f"migrate-cn: 扫描 {len(files)} 个文件，{'待改写' if dry else '已改写'} {changed} 个")
    return EXIT_OK


def cmd_conformance(args):
    """impor conformance [--ci] [--only 05] [--json OUT] —— 三后端逐字节一致性门禁（v7.3）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    runner = os.path.join(root, "conformance", "run_conformance.py")
    if not os.path.isfile(runner):
        _err("缺少 conformance/run_conformance.py")
        return EXIT_RUNTIME
    import subprocess
    cmd = [sys.executable, runner] + [str(a) for a in args]
    try:
        r = subprocess.run(cmd, cwd=root)
    except Exception as e:
        _err(f"conformance 启动失败：{e}")
        return EXIT_RUNTIME
    return r.returncode


def cmd_doctor(args):
    """impor doctor —— 体检：运行时模块、C 后端、自举产物、标准库扩展（v5.6）。"""
    import importlib
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    checks = []

    mods = ["lexer", "parser", "interpreter", "builtins", "cli", "editor",
            "lsp", "dap", "transpiler", "ctrans", "runtime", "stdlib", "errors", "diagnostics"]
    for m in mods:
        try:
            importlib.import_module("impor." + m)
            checks.append((True, "模块", "impor." + m))
        except Exception as e:
            checks.append((False, "模块", "impor.%s：%s" % (m, e)))
    for name, path in (("自举编译器", os.path.join(here, "imporc.exe")),
                       ("自举种子", os.path.join(root, "bootstrap", "imporc.impor"))):
        checks.append((os.path.isfile(path), "产物", name))
    # 自举产物 B 是本地构建缓存，不是干净 clone 的必需交付物；
    # 有就报告，没有不能让 doctor 失败（否则 CI 每次都会红）。
    self_b = os.path.join(root, "imporc_self.exe")
    checks.append((True, "产物",
                   "自举产物 B" if os.path.isfile(self_b)
                   else "自举产物 B（可选，未构建）"))
    tcc = _find_tcc()
    checks.append((bool(tcc), "C 后端", os.path.basename(tcc) if tcc else "未找到 tcc.exe"))

    print("Impor (I++) %s 体检" % __version__)
    for ok, kind, detail in checks:
        print("  [%s] %-10s %s" % ("OK" if ok else "FAIL", kind, detail))
    bad = sum(1 for ok, _, _ in checks if not ok)
    print("结果：%d 项通过，%d 项失败" % (len(checks) - bad, bad))
    return EXIT_OK if bad == 0 else EXIT_RUNTIME


def cmd_syscheck(args):
    """impor sys-check [--json OUT] —— 系统编程能力审计（v3.6.1）。"""
    from .syscheck import main as sys_main
    return sys_main(args)


def cmd_release(args):
    """impor release-check [--full] [--json OUT] —— 生产化发布门禁（v3.6.0）。"""
    from .release import run_release_check
    return run_release_check(args)


def cmd_teach(args):
    """impor teach —— 12 课课程表 / 讲义 / 练习 / 答案 / 自动判题（v7.3）。"""
    from . import teach
    if not args:
        return teach.list_lessons() or EXIT_OK
    if args[0] == "grade":
        if len(args) < 3:
            _err("用法：impor teach grade 3 FILE.impor")
            return EXIT_USAGE
        return teach.grade(args[1], args[2]) or EXIT_OK
    n = args[0]
    flags = set(args[1:])
    if "--exercise" in flags:
        return teach.write_lesson(n, "exercise") or EXIT_OK
    if "--answer" in flags:
        return teach.write_lesson(n, "answer") or EXIT_OK
    if "--write" in flags:
        return teach.write_lesson(n, "both") or EXIT_OK
    return teach.show_lesson(n) or EXIT_OK


def cmd_syntax(args):
    """v3.6.6：impor syntax [关键字/内建] —— 查意思、格式、例子、例子说明。"""
    from . import syntax_ref
    raw = list(args)
    as_json = "--json" in raw
    if as_json:
        raw = [a for a in raw if a != "--json"]
    if not raw:
        cats = syntax_ref.categories()
        if as_json:
            import json
            print(json.dumps([syntax_ref.ENTRIES[n] for n in syntax_ref.names()],
                             ensure_ascii=False, indent=2))
            return EXIT_OK
        print("Impor 语法查询：%d 个关键字/内建条目" % sum(len(v) for v in cats.values()))
        for kind, names in sorted(cats.items()):
            print("\n%s（%d）" % (kind, len(names)))
            print("  " + "  ".join(names))
        print("\n用法：impor syntax print  /  impor syntax --search 条件  /  impor syntax --json")
        return EXIT_OK
    if raw[0] in ("--search", "-s"):
        q = " ".join(raw[1:]).strip()
        if not q:
            _err("--search 后面需要关键词")
            return EXIT_USAGE
        hits = syntax_ref.search(q)
        if as_json:
            import json
            print(json.dumps([e for e in hits], ensure_ascii=False, indent=2))
            return EXIT_OK
        if not hits:
            print("没有找到和 %r 相关的语法条目。" % q)
            return EXIT_OK
        print("找到 %d 条：" % len(hits))
        for e in hits:
            print("  %-12s %s" % (e["name"], e["meaning"]))
        return EXIT_OK
    name = raw[0]
    text = syntax_ref.format_entry(name)
    if not text:
        _err("没有找到语法条目：%s（运行 impor syntax 查看全部）" % name)
        return EXIT_USAGE
    if as_json:
        import json
        print(json.dumps(syntax_ref.get(name), ensure_ascii=False, indent=2))
    else:
        print(text, end="")
    return EXIT_OK


def cmd_stability(args):
    """4.1：稳定性自检。impor stability [--limits] [--strict] [--json] [--quiet]"""
    import json as _json
    from .stability import Limits, STRICT_LIMITS, selftest

    if "--limits" in args or "-l" in args:
        print("稳定模式额度（默认 / 严格）：")
        d = Limits().as_dict()
        s = Limits(base=STRICT_LIMITS).as_dict()
        rows = [("步数", "steps", "条"), ("时间", "seconds", "秒"), ("内存", "memory_mb", "MB"),
                ("输出", "output_kb", "KB"), ("单次分配", "alloc", "元素")]
        print("  %-10s %14s %14s" % ("项目", "默认", "严格"))
        for label, key, unit in rows:
            print("  %-10s %14s %14s   %s" % (label, d[key], s[key], unit))
        print("\n环境变量：IMPOR_STABLE=1 / IMPOR_LIMIT_STEPS / _SECONDS / _MEMORY_MB / _OUTPUT_KB / _ALLOC")
        return EXIT_OK

    limits = Limits(base=STRICT_LIMITS) if "--strict" in args else None
    quiet = "--quiet" in args
    result = selftest(limits=limits, verbose=(not quiet and "--json" not in args))
    if "--json" in args:
        print(_json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("\n稳定性自检：%d/%d 通过（脚本目录 %s）"
              % (result["passed"], result["total"], result["workdir"]))
        for c in result["cases"]:
            if not c["ok"]:
                print("  FAIL %-16s %s %s" % (c["name"], c["reason"], c["stderr_tail"]))
    return EXIT_OK if result["passed"] == result["total"] else EXIT_RUNTIME


SERVE_USAGE = """serve 用法：
  impor serve [--port N]          前台常驻（默认端口 0 = 自动挑，写入 ~/.impor/serve.json）
  impor serve start [--port N]    后台常驻（脱离控制台）
  impor serve stop                停掉后台服务
  impor serve status              看是否在跑、端口、pid
  impor serve run FILE [参数...]  向常驻服务发一次请求（诊断用，不起客户端）
"""


def cmd_serve(args):
    """P2：常驻执行进程。把每次运行的固定成本（Python 启动 174 ms + 进程创建 18 ms）摊到 0。"""
    from . import serve as _serve
    sub = "start"
    port = 0
    rest = list(args)
    if rest and not rest[0].startswith("-"):
        sub = rest.pop(0)
    pos = []
    while rest:
        a = rest.pop(0)
        if a == "--port" and rest:
            try:
                port = int(rest.pop(0))
            except ValueError:
                _err("--port 需要数字")
                return EXIT_USAGE
        elif a in ("--bg", "--background"):
            sub = "start"
        elif a in ("-h", "--help"):
            print(SERVE_USAGE)
            return EXIT_OK
        elif a.startswith("-"):
            _err("未知参数 %s\n%s" % (a, SERVE_USAGE))
            return EXIT_USAGE
        else:
            # patch8b：`serve run FILE [参数...]` 的位置参数必须收下来。
            # 旧实现把它们当「未知参数」，于是 README 写的 `impor serve run` 根本跑不了。
            pos.append(a)

    if sub == "run":
        if not pos:
            _err(SERVE_USAGE)
            return EXIT_USAGE
        script, sargs = pos[0], pos[1:]
        resp = _serve.client_request(os.path.abspath(script), sargs)
        if resp is None:
            _err("没有在跑的服务（先 `impor serve start`），或连接失败")
            return EXIT_USAGE
        sys.stdout.write(resp.get("out") or "")
        if resp.get("err"):
            _err(resp["err"].rstrip())
        return EXIT_OK if resp.get("code") == 0 else EXIT_RUNTIME

    if sub == "status":
        st = _serve.read_state()
        if not st:
            print("未运行")
            return EXIT_OK
        print("运行中：端口 %s，pid %s" % (st.get("port"), st.get("pid")))
        return EXIT_OK

    if sub in ("stop", "kill"):
        ok = _serve._stop()
        print("已停止" if ok else "未运行")
        return EXIT_OK if ok else EXIT_USAGE

    if sub in ("start", "serve", "bg"):
        st = _serve.read_state()
        if st:
            # state 可能是上次崩溃的残留：先试一次连接再认它
            import socket as _sock
            try:
                c = _sock.create_connection((_serve.DEFAULT_HOST, int(st["port"]), ), timeout=1.0)
                c.close()
                print("已有服务在跑：端口 %s，pid %s" % (st.get("port"), st.get("pid")))
                return EXIT_OK
            except Exception:
                _serve.clear_state()
        if sub == "bg" or "--bg" in args:
            p = _serve._start_detached(port)
            if not p:
                _err("后台服务没起来（看 ~/.impor/serve.json 与日志）")
                return EXIT_RUNTIME
            print("后台服务已启动：端口 %d" % p)
            return EXIT_OK
        # 前台
        try:
            _serve.serve(port, ready_cb=lambda p: print("服务已启动：127.0.0.1:%d（Ctrl+C 结束）" % p, flush=True))
        except KeyboardInterrupt:
            return EXIT_OK
        return EXIT_OK

    _err(SERVE_USAGE)
    return EXIT_USAGE


_WASM_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>Impor × WebAssembly · %(name)s</title>
<style>
  :root { color-scheme: dark; }
  body { margin: 0; padding: 32px; background: #12151c; color: #e6e9ef;
         font-family: "Cascadia Code", Consolas, monospace; }
  h1 { font-size: 18px; font-weight: 600; margin: 0 0 4px; letter-spacing: .5px; }
  .sub { color: #7f8899; font-size: 13px; margin-bottom: 20px; }
  pre { background: #1a1f29; border: 1px solid #262c38; border-radius: 10px;
        padding: 18px; font-size: 14px; line-height: 1.6; margin: 0;
        white-space: pre-wrap; min-height: 40px; }
  .foot { margin-top: 14px; color: #6b7484; font-size: 12px; }
  .err { color: #ff7b72; }
</style>
</head>
<body>
<h1>%(name)s</h1>
<div class="sub">由 Impor %(ver)s 直接编译成 WebAssembly（手写二进制，无 LLVM）。</div>
<pre id="out">载入中…</pre>
<div class="foot" id="meta"></div>
<script>
const B64 = "%(b64)s";
const bytes = Uint8Array.from(atob(B64), c => c.charCodeAt(0));
const lines = [], errs = [], out = document.getElementById('out');
let inst = null;
const imports = { env: {
  print_begin() { lines.length = lines.length; },
  print_i64(v) { lines.push(String(v)); },
  print_f64(v) {
    lines.push((Number.isFinite(v) && Math.floor(v) === v && Math.abs(v) < 1e16)
      ? v.toFixed(1) : String(v));
  },
  print_str(p, l) {
    const m = new Uint8Array(inst.exports.memory.buffer, p, l);
    lines.push(new TextDecoder('utf-8').decode(m));
  },
  print_nl() { out.textContent += lines.join(' ') + '\\n'; lines.length = 0; },
  overflow() { errs.push('ImporE1012 整数溢出'); }
}};
WebAssembly.instantiate(bytes, imports).then(r => {
  inst = r.instance;
  out.textContent = '';
  inst.exports._start();
  if (errs.length) { out.textContent += errs.join('\\n') + '\\n'; }
  document.getElementById('meta').textContent =
    'wasm ' + bytes.length + ' 字节 · 函数 ' + %(funcs)d + ' 个 · 全局 ' + %(globals)d + ' 个';
}).catch(e => {
  out.innerHTML = '<span class="err">' + e.message + '</span>';
});
</script>
</body>
</html>
"""


def _wasm_html(data, src_name, meta):
    import base64
    return _WASM_HTML % {
        "name": src_name,
        "ver": __version__,
        "b64": base64.b64encode(data).decode("ascii"),
        "funcs": meta.get("funcs", 0),
        "globals": meta.get("globals", 0),
    }


def cmd_wasm(args):
    """impor wasm FILE.impor [-o OUT.wasm] [--html OUT.html]

    把脚本编成 WebAssembly。产物是可移植的第二后端：浏览器、Node、任何
    WASM 运行时都能直接跑，不依赖 Python 与 C 工具链。
    """
    out = None
    html = None
    fname = None
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-o", "--out") and i + 1 < len(args):
            out = args[i + 1]
            i += 2
            continue
        if a == "--html" and i + 1 < len(args):
            html = args[i + 1]
            i += 2
            continue
        if fname is None:
            fname = a
        i += 1
    if fname is None:
        _err("用法：impor wasm FILE.impor [-o OUT.wasm] [--html OUT.html]")
        return EXIT_USAGE
    if not os.path.isfile(fname):
        _err("impor: 文件不存在: %s" % fname)
        return EXIT_USAGE
    with open(fname, "r", encoding="utf-8") as f:
        src = f.read()
    from .interpreter import parse_source, strip_header
    from .wasm import WasmUnsupported, wasm_transpile
    try:
        ast = parse_source(strip_header(src), os.path.basename(fname))
        data, meta = wasm_transpile(ast)
    except WasmUnsupported as e:
        _err("WASM 后端不支持这段代码（第 %d 行）：%s" % (e.lineno, e.msg))
        _err("  这是语法子集边界，不是静默降级：需要完整语义请用 impor build / impor run。")
        return 3
    out = out or (os.path.splitext(fname)[0] + ".wasm")
    with open(out, "wb") as f:
        f.write(data)
    print("已生成 %s（%d 字节 · 函数 %d · 全局 %d）"
          % (out, len(data), meta["funcs"], meta["globals"]))
    if html:
        with open(html, "w", encoding="utf-8") as f:
            f.write(_wasm_html(data, os.path.basename(fname), meta))
        print("已生成演示页 %s（内嵌 wasm，双击即可在浏览器里运行）" % html)
    return 0


def cmd_vm(args):
    """impor vm run|compile|exec|dump FILE [-o OUT.ibc]

    自有字节码后端（NVM）：AST → 栈式字节码 → 自带虚拟机，全程不依赖
    gcc / tcc 等外部编译器。
      run      编译 .impor 并立即执行
      compile  把 .impor 编译成 .ibc 字节码落盘（之后零编译 exec）
      exec     直接加载 .ibc 执行
      dump     反汇编字节码（调试用）
    """
    if not args:
        _err("用法：impor vm run|compile|exec|dump FILE [-o OUT.ibc]")
        return EXIT_USAGE
    action = args[0]
    if action in ("-h", "--help"):
        print(SUBCOMMAND_HELP.get("vm", __doc__))
        return EXIT_OK
    out = None
    fname = None
    big_mode = False
    i = 1
    while i < len(args):
        a = args[i]
        if a in ("-o", "--out") and i + 1 < len(args):
            out = args[i + 1]
            i += 2
            continue
        if a in ("--big", "--bignum"):
            big_mode = True
            i += 1
            continue
        if fname is None:
            fname = a
        i += 1
    # 兼容 `impor vm FILE.impor`（省略动作，默认 run）
    if action not in ("run", "compile", "exec", "dump"):
        if fname is None and os.path.isfile(action):
            fname = action
            action = "run"
        else:
            _err("未知 vm 动作: %s（可选 run / compile / exec / dump）" % action)
            return EXIT_USAGE
    if fname is None:
        _err("用法：impor vm %s FILE [-o OUT.ibc]" % action)
        return EXIT_USAGE
    if not os.path.isfile(fname):
        _err("impor: 文件不存在: %s" % fname)
        return EXIT_USAGE
    from . import nvm
    if action == "exec":
        nvm.run_ibc(fname, int64_mode=not big_mode)
        return EXIT_OK
    if action == "compile":
        with open(fname, encoding="utf-8") as f:
            program = nvm.compile_source(f.read(), os.path.basename(fname))
        outp = out or (os.path.splitext(fname)[0] + ".ibc")
        nvm.dump_ibc(program, outp)
        print("已生成 %s（%d 字节）" % (outp, os.path.getsize(outp)))
        return EXIT_OK
    if action == "dump":
        with open(fname, encoding="utf-8") as f:
            if fname.endswith(".ibc"):
                program = nvm.load_ibc(fname)
            else:
                program = nvm.compile_source(f.read(), fname)
        print(nvm.disassemble(program))
        return EXIT_OK
    # run（默认 int64，与 impor run 同口径；--big 改任意精度）
    with open(fname, encoding="utf-8") as f:
        nvm.run_source(f.read(), os.path.basename(fname),
                       int64_mode=not big_mode)
    return EXIT_OK


def cmd_opt(args):
    """impor opt run|dump FILE [-o OUT.asm]

    教科书优化编译器后端（OPTC）：在三地址 IR 上做 CFG、活跃变量数据流分析、线性扫描
    寄存器分配，生成寄存器式目标码并由寄存器机执行，全程不依赖外部编译器。
      run   编译 .impor 并立即执行
      dump  反汇编寄存器式目标码（含基本块/活跃区间/溢出统计）
    """
    if not args:
        _err("用法：impor opt run|dump FILE [-o OUT.asm]")
        return EXIT_USAGE
    action = args[0]
    if action in ("-h", "--help"):
        print(SUBCOMMAND_HELP.get("opt", __doc__))
        return EXIT_OK

    out = None
    fname = None
    i = 1
    while i < len(args):
        a = args[i]
        if a in ("-o", "--out") and i + 1 < len(args):
            out = args[i + 1]
            i += 2
            continue
        if fname is None:
            fname = a
        i += 1

    # 兼容 `impor opt FILE.impor`（省略动作，默认 run）
    if action not in ("run", "dump"):
        if fname is None and os.path.isfile(action):
            fname = action
            action = "run"
        else:
            _err("未知 opt 动作: %s（可选 run / dump）" % action)
            return EXIT_USAGE
    if fname is None:
        _err("用法：impor opt %s FILE [-o OUT.asm]" % action)
        return EXIT_USAGE
    if not os.path.isfile(fname):
        _err("impor: 文件不存在: %s" % fname)
        return EXIT_USAGE

    from . import optc
    try:
        with open(fname, encoding="utf-8") as f:
            source = f.read()
        if action == "dump":
            _, program = optc.run_source(source, os.path.basename(fname))
            text = optc.disassemble(program)
            if out:
                with open(out, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(text)
                print("反汇编已写入 %s" % out)
            else:
                print(text)
            return EXIT_OK
        # run
        output, _ = optc.run_source(source, os.path.basename(fname))
    except ImporException as e:
        _err(_render_exc(e, source.splitlines() if source else None))
        return EXIT_RUNTIME
    except ImporSyntaxError as e:
        _err(_render_exc(e, source.splitlines() if source else None))
        return EXIT_SYNTAX
    for line in output:
        print(line)
    return EXIT_OK


def cmd_native(args):
    """impor native [run|build] FILE [-o OUT.exe]

    纯自足原生 x64 AOT：自己把三地址 IR 编码成 x86-64 机器码字节、自己生成 Windows
    PE32+ 可执行文件，全程不调用 nasm/gcc/ld 等任何外部工具链。
      build（默认） 编译 .impor 为同名 .exe
      run           编译到临时 exe 并立即执行（stdout 直接透传，退出码透传）
    """
    import subprocess
    import tempfile
    if not args:
        _err("用法：impor native [run|build] FILE [-o OUT.exe]")
        return EXIT_USAGE
    action = "build"
    if args[0] in ("run", "build"):
        action = args[0]
        args = args[1:]
    elif args[0] in ("-h", "--help"):
        print(__doc__)
        return EXIT_OK

    out = None
    fname = None
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-o", "--out") and i + 1 < len(args):
            out = args[i + 1]
            i += 2
            continue
        if a in ("-h", "--help"):
            print(__doc__)
            return EXIT_OK
        if fname is None:
            fname = a
        i += 1

    if fname is None:
        _err("用法：impor native [run|build] FILE [-o OUT.exe]")
        return EXIT_USAGE
    if not os.path.isfile(fname):
        _err("impor: 文件不存在: %s" % fname)
        return EXIT_USAGE

    from .native64 import build_exe_from_source
    source = None
    try:
        with open(fname, encoding="utf-8") as f:
            source = f.read()
        data = build_exe_from_source(source, os.path.basename(fname))
    except ImporException as e:
        _err(_render_exc(e, source.splitlines() if source else None))
        return EXIT_RUNTIME
    except ImporSyntaxError as e:
        _err(_render_exc(e, source.splitlines() if source else None))
        return EXIT_SYNTAX

    if action == "run":
        d = tempfile.mkdtemp(prefix="impornative_")
        exe = os.path.join(d, "t.exe")
        try:
            with open(exe, "wb") as f:
                f.write(data)
            r = subprocess.run([exe])
            return EXIT_OK if r.returncode == 0 else EXIT_RUNTIME
        finally:
            shutil.rmtree(d, ignore_errors=True)

    out = out or (os.path.splitext(fname)[0] + ".exe")
    with open(out, "wb") as f:
        f.write(data)
    print("已生成原生可执行文件: %s（%d 字节，零外部工具链）" % (out, len(data)))
    return EXIT_OK


_COMMANDS = {
    "run": cmd_run,
    "run-dbg": cmd_run_dbg,
    "serve": cmd_serve,
    "repl": cmd_repl,
    "compile": cmd_compile,
    "build": cmd_build,
    "wasm": cmd_wasm,
    "vm": cmd_vm,
    "opt": cmd_opt,
    "native": cmd_native,
    "check": cmd_check,
    "check-int": cmd_check_int,
    "check-types": cmd_check_types,
    "ir": cmd_ir,
    "new": cmd_new,
    "list-modules": cmd_list_modules,
    "reload": cmd_reload_mod,
    "check-imports": cmd_check_imports,
    "fmt": cmd_fmt,
    "check-fmt": lambda args: cmd_fmt(["--check"] + args),
    "lint": cmd_lint,
    "doc": cmd_doc,
    "lsp": cmd_lsp,
    "dap": cmd_dap,
    "test": cmd_test,
    "pkg": cmd_pkg,
    "demo": cmd_demo,
    "env": cmd_env,
    "bench": cmd_bench,
    "bench-spec": cmd_bench_spec,
    "mem": cmd_mem,
    "explain": cmd_explain,
    "syntax": cmd_syntax,
    "migrate-cn": cmd_migrate_cn,
    "doctor": cmd_doctor,
    "conformance": cmd_conformance,
    "stability": cmd_stability,
    "release-check": cmd_release,
    "sys-check": cmd_syscheck,
    "teach": cmd_teach,
}

# 启动器（Impor.py / python -m impor）可透传的命令白名单，
# 供外部入口导入，避免子命令集合在两处硬编码维护。
SUPPORTED_COMMANDS = frozenset(_COMMANDS)


def _main_impl(argv):
    if "--legacy-cn" in argv:
        os.environ["IMPOR_LEGACY_CN"] = "1"
        argv = [a for a in argv if a != "--legacy-cn"]
    # 4.7：--strict-types 是全局开关（run/build/compile/check-types 共用）。
    # 只剔除 `--` 之前的那一个，脚本自己的参数原样保留。
    if "--" in argv:
        head, tail = argv[:argv.index("--")], argv[argv.index("--"):]
    else:
        head, tail = argv, []
    if "--strict-types" in head:
        os.environ["IMPOR_STRICT_TYPES"] = "1"
        head = [a for a in head if a != "--strict-types"]
    if "--no-mono" in head:
        os.environ["IMPOR_NO_MONO"] = "1"
        head = [a for a in head if a != "--no-mono"]
    if "--no-int-ck" in head:
        os.environ["IMPOR_NO_INT_CK"] = "1"
        head = [a for a in head if a != "--no-int-ck"]
    argv = head + tail
    if not argv:
        # 无参数 → REPL
        return cmd_repl([])

    first = argv[0]
    if first in ("-v", "--version", "version"):
        return cmd_version(argv[1:])
    if first in ("-h", "--help", "help"):
        return cmd_help(argv[1:])

    handler = _COMMANDS.get(first)
    if handler is not None:
        if any(a in ("-h", "--help") for a in argv[1:]):
            print(SUBCOMMAND_HELP.get(first, __doc__))
            return EXIT_OK
        return handler(argv[1:])

    # 直接给 .impor 文件 → 相当于 run
    if first.endswith(".impor") or os.path.exists(first):
        return cmd_run(argv)
    # 4.1：允许 `impor --stable 脚本.impor` 这种「选项在前」的写法
    if first.startswith("-"):
        return cmd_run(argv)
    _err(f"未知命令: {first}")
    print(__doc__)
    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
