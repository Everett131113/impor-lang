# -*- coding: utf-8 -*-
"""把 VERSION 同步到构建期元数据（VS Code 清单 / lock）。"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ver = open(os.path.join(ROOT, "VERSION"), encoding="utf-8").read().strip()

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
print("synced", ver)
