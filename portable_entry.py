# -*- coding: utf-8 -*-
"""ImporPortable 入口：PyInstaller 打包后不依赖本机 Python。"""
import os
import sys


def _utf8_console():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main():
    _utf8_console()
    if len(sys.argv) == 1:
        print("Impor (I++) 便携版")
        print("用法：ImporPortable.exe run 文件.impor")
        print("示例：ImporPortable.exe run examples\\quickstart\\hello.impor")
        print("编译：ImporPortable.exe build 文件.impor -o out.exe")
        return 0
    from impor.cli import main as cli_main
    return cli_main(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
