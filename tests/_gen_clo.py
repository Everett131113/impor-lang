import io
from impor.lexer import tokenize
from impor.parser import Parser
import impor.ctrans as ct

src = io.open('examples/closures.impor', encoding='utf-8').read()
lines = src.splitlines()
if lines and lines[0].startswith('important<<'):
    lines = lines[1:]
src = '\n'.join(lines)
ast = Parser(tokenize(src), 'examples/closures.impor').program()
io.open('tests/_clo.c', 'w', encoding='utf-8').write(ct.c_transpile(ast, 'examples/closures.impor'))
print('ok')
