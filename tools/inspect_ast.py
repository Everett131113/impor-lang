# -*- coding: utf-8 -*-
"""打印关键 AST 节点字段。"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from impor.interpreter import parse_source

src = '''import m as mm
from m import add, sub as minus
export add
func add(a, b) {
    return a + b
}
var G = 1
'''
ast = parse_source(src)
for n in ast.stmts:
    d = dict(n.__dict__)
    for k, v in list(d.items()):
        if isinstance(v, list):
            d[k] = [type(x).__name__ for x in v]
    print(type(n).__name__, d)
