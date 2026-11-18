import io, sys, os
sys.path.insert(0, r"C:\Users\41482\Doubao\chats\2026-11-01\new-chat\ImporLang")
from impor.interpreter import strip_header, parse_source
from impor.ctrans import c_transpile
p = r"C:\Users\41482\Doubao\chats\2026-11-01\new-chat\ImporLang\tests\_err_demo.impor"
src = io.open(p, encoding="utf-8").read()
body = strip_header(src, warn=False)
ast = parse_source(body, filename=p)
c = c_transpile(ast, source_name=p, source_lines=src.splitlines())
io.open(r"C:\Users\41482\Doubao\chats\2026-11-01\new-chat\ImporLang\tests\_err_demo.c", "w", encoding="utf-8").write(c)
print("len", len(c))
