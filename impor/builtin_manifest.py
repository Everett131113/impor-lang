# -*- coding: utf-8 -*-
"""内建函数的唯一注册表。

历史问题：`impor.builtins.make_builtins()` 注册解释器运行时函数，
`impor.runtime` 另有一份转译产物可见的函数清单。两边靠人肉同步，
3.6.15 打包时就漏了 `fileexists` / `json_encode` 等六个函数。

从 3.6.16 起，所有内建名字只在这里登记：

- `RUNTIME_BUILTINS`：转译产物通过 `from impor.runtime import *` 必须能拿到；
  `impor.runtime` 导入时会自检，缺一个就立刻 ImportError，打包阶段就会暴露。
- `HOST_BUILTINS`：由 Python 宿主直接提供，不额外导出（如 print/len/int）。
- `INTERPRETER_ONLY_BUILTINS`：依赖解释器状态、沙盒、FFI 或宿主动态环境，
  编译产物不承诺提供。

`impor.builtins.make_builtins()` 启动时也会与 `ALL_BUILTINS` 对账：
新增内建却忘了登记，解释器直接报错，而不是等用户双击 exe 才发现。
"""

RUNTIME_BUILTINS = frozenset({
    "angle", "args", "arrappend", "arrdup", "arrfill", "arrfind",
    "arrpop", "arrrev", "breakpoint", "clamp", "clock", "distance",
    "exedir", "exit", "expect", "filecat", "fileexists", "filemkdir", "filemv", "fileread",
    "filewrite", "filelist", "fps", "frame", "hash", "import_path", "inputstr",
    "json_decode", "json_encode", "lerp", "lower", "max_arr", "min_arr",
    "now", "open", "pause", "randi", "randstr", "range", "readln",
    "regex_match", "remap", "rgb", "seed", "shuffle", "sign", "sleep", "snapshot",
    "strcut", "strjoin", "strpad", "sum_arr", "to_num", "to_str",
    "trim", "type", "type_of", "upper", "vec2", "wait", "watch",
    "writeln", "ffi_open", "ffi_call",
})

HOST_BUILTINS = frozenset({
    "abs", "input", "int", "len", "list", "max", "min", "print", "str",
})

INTERPRETER_ONLY_BUILTINS = frozenset({
    "autogc", "div", "ffi_alloc", "ffi_alloc_str", "ffi_buffer_size",
    "ffi_buffers", "ffi_read_f64", "ffi_read_i64",
    "ffi_read_str", "ffi_read_str_at", "ffi_write_f64", "ffi_write_i64", "ffi_write_str",
    "filesize", "filewritebytes", "gc", "mem", "memstats",
    "regex_find", "regex_replace", "reload", "run_tasks",
    "system", "task_status",
})

ALL_BUILTINS = RUNTIME_BUILTINS | HOST_BUILTINS | INTERPRETER_ONLY_BUILTINS


def check_interpreter_names(names):
    """解释器注册表与唯一清单对账；返回 (缺失, 多出)。"""
    names = set(names)
    return sorted(ALL_BUILTINS - names), sorted(names - ALL_BUILTINS)


def check_runtime_namespace(namespace):
    """转译运行时命名空间自检；返回缺失的运行时内建。"""
    return sorted(n for n in RUNTIME_BUILTINS if not callable(namespace.get(n)))
