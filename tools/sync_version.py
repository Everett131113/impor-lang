# -*- coding: utf-8 -*-
"""把 VERSION 同步到构建期元数据（VS Code 清单 / lock / 版本兜底 / 自举编译器）。"""
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ver = open(os.path.join(ROOT, "VERSION"), encoding="utf-8").read().strip()
if not re.match(r"^\d+\.\d+\.\d+$", ver):
    raise SystemExit("VERSION 不是 x.y.z：%r" % ver)

for rel in ("vscode-impor/package.json", "vscode-impor/package-lock.json"):
    p = os.path.join(ROOT, rel)
    if not os.path.exists(p):
        continue
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    data["version"] = ver
    if rel.endswith("package-lock.json"):
        data.setdefault("packages", {}).setdefault("", {})["version"] = ver
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")

# 3.12.0：VERSION 之外的旧版兜底与自举编译器版本串也必须同步，
# 否则便携版丢 VERSION 或 imporc.exe 未重建时会出现 3.6.17 / 3.11.0。
literal_targets = (
    ("Impor.py", r'(?m)^\s*return "\d+\.\d+\.\d+"$', '    return "%s"' % ver),
    ("impor/version.py", r'(?m)^_BASE = "\d+\.\d+\.\d+"$', '_BASE = "%s"' % ver),
    ("bootstrap/imporc.impor", r'(自举编译器\) v)\d+\.\d+\.\d+', r'\g<1>%s' % ver),
)
for rel, pat, repl in literal_targets:
    p = os.path.join(ROOT, rel)
    if not os.path.exists(p):
        continue
    with open(p, encoding="utf-8") as f:
        text = f.read()
    new, n = re.subn(pat, repl, text, count=1)
    if n != 1:
        raise SystemExit("同步失败：%s 中未找到唯一版本字面量" % rel)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(new)

print("synced", ver)
