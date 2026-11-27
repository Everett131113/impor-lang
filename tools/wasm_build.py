# -*- coding: utf-8 -*-
"""把 .impor 编成 .wasm（调试/验证用入口：python tools/wasm_build.py FILE.impor OUT.wasm）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from impor import wasm                       # noqa: E402
from impor.interpreter import parse_source, strip_header   # noqa: E402


def main():
    if len(sys.argv) < 3:
        print("用法：python tools/wasm_build.py FILE.impor OUT.wasm")
        return 2
    src_path, out_path = sys.argv[1], sys.argv[2]
    with open(src_path, "r", encoding="utf-8") as fh:
        src = fh.read()
    body = strip_header(src)
    ast = parse_source(body, os.path.basename(src_path))
    try:
        data, meta = wasm.wasm_transpile(ast)
    except wasm.WasmUnsupported as e:
        print("WASM_UNSUPPORTED: %s (line %s)" % (e.msg, e.lineno))
        return 3
    with open(out_path, "wb") as fh:
        fh.write(data)
    print("OK %s bytes=%d %s" % (out_path, len(data), meta))
    return 0


if __name__ == "__main__":
    sys.exit(main())
