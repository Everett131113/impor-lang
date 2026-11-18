# -*- coding: utf-8 -*-
"""生成 conformance 扩展用例：24-100，共 77 个三后端公共子集用例。

只在明确支持的特性上做参数化，避免把已知差异（range(a,b)、continue、
try/catch、+=、切片、dict、模块）混进来。生成后由 run_conformance.py 逐字节验证。
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASES = os.path.join(ROOT, "conformance", "cases")
HEAD = 'important<<DAY<<english("English"):\n'

ARITH = [(10, 4), (21, 6), (100, 8), (7, 2), (9, 4), (15, 6), (30, 8)]
STR = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf"]
LOOP = [3, 4, 5, 6, 7, 8, 9]
FUNC = [3, 4, 5, 6, 7, 8, 9]
ARR = [
    [1, 2, 3], [4, 5, 6], [7, 8, 9], [10, 11, 12],
    [2, 4, 6], [3, 6, 9], [5, 10, 15],
]
STRUCT = [(1, 2), (3, 4), (5, 6), (7, 8), (9, 10), (11, 12), (13, 14)]
ENUM = [0, 1, 2, 3, 4, 5, 6]
SWITCH = [1, 2, 3, 4, 5, 6, 7]
BOOL = [(3, 2), (5, 5), (8, 9), (10, 7), (0, 1), (6, 6), (4, 9)]
TYPE = [7, 11, 23, 42, 99, 123, 256]
MIX = [4, 5, 6, 7, 8, 9, 10]

cases = {}

for i, (a, b) in enumerate(ARITH):
    cases[24 + i] = f'''var a = {a}
var b = {b}
print(a + b, a - b, a * b, a / b)
print((a + b) * (a - b))
'''

for i, s in enumerate(STR):
    cases[31 + i] = f'''var s = "{s}"
print(len(s), s[0], s[len(s) - 1])
print(strcut(s, 1, len(s) - 1))
print(s + s, s * 2)
print(s == "{s}")
'''

for i, n in enumerate(LOOP):
    cases[38 + i] = f'''var total = 0
for i in range({n})
    total = total + i * i
print(total)
var j = {n}
while j > 0
    j = j - 1
print(j)
'''

for i, n in enumerate(FUNC):
    cases[45 + i] = f'''func sum_to(n)
    if n <= 1
        return 1
    return n + sum_to(n - 1)
print(sum_to({n}))
'''

for i, arr in enumerate(ARR):
    total = sum(arr)
    newv = arr[1] * 10
    cases[52 + i] = f'''var arr = {arr}
var total = 0
for i in range(len(arr))
    total = total + arr[i]
print(len(arr), arr[0], arr[len(arr) - 1], total)
set arr[1] = {newv}
print(arr[1], total)
'''

for i, (x, y) in enumerate(STRUCT):
    cases[59 + i] = f'''struct Point
    var x = 0
    var y = 0
var p = new Point({x}, {y})
print(p.x, p.y, p.x + p.y)
set p.x = {x + 10}
print(p.x, p.y)
'''

for i, n in enumerate(ENUM):
    cases[66 + i] = f'''enum Color {{ RED, GREEN, BLUE }}
print(Color.RED, Color.GREEN, Color.BLUE)
print(Color.BLUE > Color.RED)
print({n} + Color.GREEN)
'''

for i, n in enumerate(SWITCH):
    cases[73 + i] = f'''func label(x)
    switch x
        case {n}
            return "hit"
        default
            return "miss"
print(label({n}))
print(label({n + 1}))
'''

for i, (a, b) in enumerate(BOOL):
    cases[80 + i] = f'''print({a} > {b}, {a} == {b}, {a} != {b}, {a} <= {b}, {a} >= {b})
print(true and false, true or false, not false)
'''

for i, n in enumerate(TYPE):
    cases[87 + i] = f'''print(type({n}), type(1.5), type("s"), type(true), type([{n}, {n + 1}]))
print(to_str({n}), to_str({n + 1}))
'''

for i, n in enumerate(MIX):
    cases[94 + i] = f'''var a = 0
var b = 1
for i in range({n})
    var c = a + b
    a = b
    b = c
print(a, b)
var out = ""
for i in range({n})
    out = out + "x"
print(len(out), out)
'''

for num, body in cases.items():
    with open(os.path.join(CASES, f"{num:02d}_ext.impor"), "w", encoding="utf-8", newline="\n") as f:
        f.write(HEAD + body)

print("generated", len(cases), "cases")
