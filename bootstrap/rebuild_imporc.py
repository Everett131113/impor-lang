# -*- coding: utf-8 -*-
"""重建 imporc.exe：种子（v2.14 引导器）编译 bootstrap/imporc.impor → 补 8MB 栈 → 拷入 impor/imporc.exe。

用法：python bootstrap/rebuild_imporc.py
改过 bootstrap/imporc.impor 后必须执行本脚本；随后用 bootstrap/selfhost_check.py 复验自举闭环。

注意：种子编译器对较新语法会失败。本脚本会先删除旧产物并在编译后校验产物，
失败时立即退出，绝不把旧 exe 误拷成新版本。种子失败时改用
`python bootstrap/build_with_python.py --install`（用 Python 解释器自举，不依赖旧种子）。
"""
import io
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
log = []

RAW = "imporc_self_raw.exe"
PATCHED = "imporc_self.exe"
for stale in (RAW, PATCHED):
    if os.path.exists(stale):
        os.remove(stale)

with io.open("imporc_args.txt", "w", encoding="utf-8") as f:
    f.write("build bootstrap\\imporc.impor -o imporc_self_raw.exe")
r = subprocess.run([os.path.join("bootstrap", "imporc_seed_v214.exe")],
                   capture_output=True, text=True, encoding="utf-8", errors="replace",
                   timeout=600)
log.append("seed build rc=%s\nOUT: %s\nERR: %s" % (r.returncode, r.stdout[-800:], r.stderr[-800:]))

if r.returncode != 0 or not os.path.exists(RAW) or os.path.getsize(RAW) <= 0:
    log.append("FAILED: 种子编译器未产出 imporc_self_raw.exe；"
               "请改用 python bootstrap/build_with_python.py --install")
    log_path = os.path.join(os.environ["TEMP"], "rebuild_imporc.txt")
    with io.open(log_path, "w", encoding="utf-8") as f:
        f.write("\n".join(log))
    print("\n".join(log))
    sys.exit(1)

r2 = subprocess.run([sys.executable, "_setstack.py", RAW, PATCHED,
                     str(8 * 1024 * 1024)], capture_output=True, text=True,
                    encoding="utf-8", errors="replace")
log.append("patch rc=%s out=%s err=%s" % (r2.returncode, r2.stdout.strip(), r2.stderr.strip()))

if r2.returncode != 0 or not os.path.exists(PATCHED):
    log.append("FAILED: 补栈失败，未改动 impor/imporc.exe")
    log_path = os.path.join(os.environ["TEMP"], "rebuild_imporc.txt")
    with io.open(log_path, "w", encoding="utf-8") as f:
        f.write("\n".join(log))
    print("\n".join(log))
    sys.exit(1)

shutil.copyfile(PATCHED, os.path.join("impor", "imporc.exe"))
log.append("copied impor/imporc.exe size=%d" % os.path.getsize(os.path.join("impor", "imporc.exe")))
try:
    sys.path.insert(0, ROOT)
    from impor.peicon import apply_icon
    apply_icon(os.path.join("impor", "imporc.exe"))
    log.append("icon embedded into impor/imporc.exe")
except Exception as exc:
    log.append("icon embed skipped: %s" % exc)

log_path = os.path.join(os.environ["TEMP"], "rebuild_imporc.txt")
with io.open(log_path, "w", encoding="utf-8") as f:
    f.write("\n".join(log))
print("\n".join(log))
