# -*- coding: utf-8 -*-
"""`python -m impor` 入口（impor.bat / VS Code 扩展 LSP 启动均依赖）。"""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
