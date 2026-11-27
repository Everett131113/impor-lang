"""统计 Val 字段访问面（改造 accessor 前必须先知道规模）。

口径：形如 `<expr>.<字段>` 的访问，字段名取自 Val 的 7 个槽 t/i/f/s/a/o/fn，
只统计 c_runtime.c / ctrans.py（内嵌运行时）/ cabi.py 三个文件。
按「读」与「写」分开：出现在赋值左侧（`.x =` / `.x +=` 等）算写。

用法: python tools/count_val_access.py [repo_root]
"""
import os
import re
import sys

FIELDS = ("t", "i", "f", "s", "a", "o", "fn")
FILES = ("impor/c_runtime.c", "impor/ctrans.py", "impor/cabi.py")

ACCESS = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\.(?:" + "|".join(FIELDS) + r")\b")
WRITE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\.(?:" + "|".join(FIELDS) + r")\s*(?:=[^=]|[-+*/|&^]=)")


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    total = reads = writes = 0
    by_file = {}
    by_field = {f: 0 for f in FIELDS}
    for rel in FILES:
        path = os.path.join(root, rel)
        if not os.path.exists(path):
            print("SKIP (missing):", rel)
            continue
        text = open(path, encoding="utf-8", errors="replace").read()
        # 去掉注释，避免把说明文字里的 .s 计入
        text = re.sub(r"//[^\n]*", "", text)
        text = re.sub(r"#.*", "", text)
        hits = ACCESS.findall(text)
        acc = len(hits)
        w = len(WRITE.findall(text))
        by_file[rel] = {"access": acc, "write": w, "read": acc - w}
        total += acc
        reads += acc - w
        writes += w
        for f in FIELDS:
            by_field[f] += len(re.findall(r"[A-Za-z_][A-Za-z0-9_]*\." + f + r"\b", text))
    print("Val field accesses (c_runtime.c + ctrans.py + cabi.py)")
    print("  total=%d  read=%d  write=%d" % (total, reads, writes))
    for k, v in by_file.items():
        print("  %-22s access=%-4d read=%-4d write=%-3d" % (k, v["access"], v["read"], v["write"]))
    print("  by field: " + "  ".join("%s=%d" % (f, by_field[f]) for f in FIELDS))


if __name__ == "__main__":
    main()
