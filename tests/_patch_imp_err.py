import io
p = r"C:\Users\41482\Doubao\chats\2026-11-01\new-chat\ImporLang\bootstrap\imporc.impor"
s = io.open(p, encoding="utf-8").read()
old = 'print("imporc.impor: '
new = 'print("imporc.impor: 第", mi_line_at(gpos), "行: '
cnt = s.count(old)
s = s.replace(old, new)
io.open(p, "w", encoding="utf-8", newline="\n").write(s)
print("replaced", cnt)
