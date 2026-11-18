import io
import os
import sys

from impor.lexer import tokenize
from impor.parser import Parser
from impor.clink import link_modules
import impor.ctrans as ct

src_path = sys.argv[1]
out_path = sys.argv[2]
src = io.open(src_path, encoding='utf-8').read()
lines = src.splitlines()
if lines and lines[0].startswith('important<<'):
    lines = lines[1:]
body = '\n'.join(lines)
ast = Parser(tokenize(body), src_path).program()
flat = link_modules(ast, source_name=src_path)
c = ct.c_transpile(flat, source_name=src_path)
io.open(out_path, 'w', encoding='utf-8').write(c)
for i, line in enumerate(c.splitlines(), 1):
    if 'module_new' in line or 'rt_getattr' in line:
        print(i, line)
print('ok', out_path)
