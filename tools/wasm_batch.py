# -*- coding: utf-8 -*-
"""4.9.0 WASM 回归批跑：解释器产期望输出 → wasm 构建 → Node 运行 → 比对。"""
import os
import subprocess
import sys

ROOT = r"D:\ImporWork\impor471"
NODE = (r"C:\Users\41482\AppData\Local\Doubao\User Data\sandbox_runtime\bases"
        r"\fa694f517e5995233c80a9a8db5212c2\node\node.exe")
PY = sys.executable

CASES = [
    os.path.join(ROOT, "bench", "suite", "fib25.impor"),
    os.path.join(ROOT, "bench", "suite", "sum2m.impor"),
    os.path.join(ROOT, "bench", "suite", "mod1m.impor"),
    os.path.join(ROOT, "bench", "suite", "nested1m.impor"),
    os.path.join(ROOT, "bench", "suite", "wasm_str.impor"),
    os.path.join(ROOT, "bench", "suite", "flow1.impor"),
    os.path.join(ROOT, "bench", "suite", "flow2.impor"),
    os.path.join(ROOT, "bench", "suite", "flow3.impor"),
    os.path.join(ROOT, "bench", "suite", "wasm_f64.impor"),
    os.path.join(ROOT, "bench", "suite", "wasm_str2.impor"),
    os.path.join(ROOT, "bench", "caps", "02_float.impor"),
    os.path.join(ROOT, "bench", "caps", "03_str.impor"),
    os.path.join(ROOT, "bench", "caps", "04_list.impor"),
    os.path.join(ROOT, "bench", "caps", "05_dict.impor"),
    os.path.join(ROOT, "bench", "caps", "06_class.impor"),
    os.path.join(ROOT, "bench", "caps", "11_have.impor"),
]

env = dict(os.environ)
env["PYTHONIOENCODING"] = "utf-8"


def run(args, **kw):
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env, **kw)


ok = fail = 0
lines = []
for src in CASES:
    name = os.path.basename(src)
    r = run([PY, "Impor.py", "run", src])
    if r.returncode != 0:
        lines.append("INTERP_FAIL %s rc=%d %s" % (name, r.returncode,
                                                 (r.stderr or r.stdout).strip()[:200]))
        fail += 1
        continue
    want = r.stdout.strip()
    out = os.path.splitext(src)[0] + ".wasm"
    b = run([PY, os.path.join("tools", "wasm_build.py"), src, out])
    if b.returncode != 0:
        lines.append("BUILD_FAIL  %s rc=%d %s" % (name, b.returncode,
                                                 (b.stdout + b.stderr).strip()[:200]))
        fail += 1
        continue
    size = os.path.getsize(out)
    w = run([NODE, os.path.join("tools", "wasm_run.js"), out, want])
    if w.returncode == 0:
        ok += 1
        lines.append("MATCH       %s bytes=%d" % (name, size))
    else:
        fail += 1
        lines.append("RUN_FAIL    %s bytes=%d rc=%d %s" % (
            name, size, w.returncode, (w.stdout + w.stderr).strip()[:300]))

lines.append("TOTAL ok=%d fail=%d" % (ok, fail))
print("\n".join(lines))
sys.exit(0 if fail == 0 else 1)
