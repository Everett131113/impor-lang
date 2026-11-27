# -*- coding: utf-8 -*-
"""IR 覆盖门禁：`examples` 全量逐个「解释器 vs IR→C 产物」逐字节对拍。

这个脚本原来是 %TEMP% 里的一次性验收（4.5.0/4.6.0 都是靠它给出数字），
现在正式进仓库，理由只有一个：**验收要可复现**。

判定：
  - 不一致（rc 或 stdout 不同）一律失败，必须为 0；
  - 拒绝（lowering 明确报"IR 覆盖范围"）与构建失败的数量只能**减少**，不能新增；
    与 tools/ir_coverage_baseline.json 比对，新增即失败。
  - 缺 gcc 时 SKIP（退出 0，明确打印原因，不静默算绿）。

用法：
    python tools/verify_ir_coverage.py
    python tools/verify_ir_coverage.py --json ir_cov.json
    python tools/verify_ir_coverage.py --update-baseline
"""
import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMPOR = os.path.join(ROOT, "Impor.py")
BASELINE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "ir_coverage_baseline.json")
# 需要交互输入（stdin）或读键盘，不进对拍
INTERACTIVE = {"guess.impor", "snake.impor", "game.impor", "game_2048.impor",
               "test_fail_demo.impor", "_selfhost_demo.impor"}
REJECT_MARKS = ("IR 覆盖范围", "IR4001", "IR→C", "IR 暂不支持")
# 产物本身非确定的：随机种子来自时间 / 图标渲染依赖终端，对拍无意义。
# 明写在表里，并在报告里单列一类，不混进「不一致」也不静默丢弃。
NONDET = {
    "demo_38_dice.impor": "random 种子来自时间，两次运行本就不同",
    "icon_demo.impor": "图标宽度依赖终端渲染，rc 随管道环境变化",
}


def find_gcc():
    env = os.environ.get("IMPOR_CC")
    if env and os.path.isfile(env):
        return env
    return shutil.which("gcc")


def run(argv, timeout=120):
    try:
        p = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout,
                           stdin=subprocess.DEVNULL)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return -9, "", "<timeout>"


def classify(files, gcc, tmp):
    rows = []
    for f in files:
        if f in NONDET:
            rows.append({"file": f, "kind": "nondet", "note": NONDET[f]})
            continue
        path = os.path.join(ROOT, "examples", f)
        rc_i, out_i, err_i = run([sys.executable, IMPOR, "run", "--no-native", path])
        exe = os.path.join(tmp, f.replace(".impor", ".exe"))
        rc_b, out_b, err_b = run([sys.executable, IMPOR, "ir", path, "--build", exe,
                                  "--cc", gcc])
        if rc_b != 0 or not os.path.isfile(exe):
            why = (err_b or out_b).strip().splitlines()
            low = " ".join(why)
            kind = "reject" if any(m in low for m in REJECT_MARKS) else "buildfail"
            rows.append({"file": f, "kind": kind,
                         "note": why[-1][:120] if why else ""})
            continue
        rc_e, out_e, _ = run([exe])
        if rc_e == rc_i and out_e == out_i:
            rows.append({"file": f, "kind": "match", "note": ""})
        else:
            rows.append({"file": f, "kind": "mismatch",
                         "note": "rc %d/%d" % (rc_i, rc_e) if rc_e != rc_i else "输出不同"})
    return rows


def load_baseline():
    if not os.path.isfile(BASELINE):
        return None
    try:
        with io.open(BASELINE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="IR 覆盖门禁（解释器 vs IR→C 产物）")
    ap.add_argument("--json", default=None)
    ap.add_argument("--update-baseline", action="store_true",
                    help="把当前拒绝 / 构建失败清单写回基线（只在确认改善后使用）")
    args = ap.parse_args(argv)

    gcc = find_gcc()
    if not gcc:
        print("SKIP: 找不到 gcc（IR→C 产物需要完整 C 编译器；设 IMPOR_CC 或把 gcc 放进 PATH）")
        if args.json:
            io.open(args.json, "w", encoding="utf-8", newline="\n").write(json.dumps(
                {"skipped": True, "reason": "no gcc"}, ensure_ascii=False, indent=2))
        return 0

    exdir = os.path.join(ROOT, "examples")
    files = sorted(f for f in os.listdir(exdir) if f.endswith(".impor"))
    todo = [f for f in files if f not in INTERACTIVE]
    tmp = tempfile.mkdtemp(prefix="ircov_")
    print("gcc：%s" % gcc)
    print("示例：%d 个（跳过需交互的 %d 个）" % (len(todo), len(files) - len(todo)))
    rows = classify(todo, gcc, tmp)
    shutil.rmtree(tmp, ignore_errors=True)

    counts = {k: sum(1 for r in rows if r["kind"] == k)
              for k in ("match", "reject", "buildfail", "nondet", "mismatch")}
    print("\n一致 %d / 拒绝 %d / 构建失败 %d / 非确定跳过 %d / 不一致 %d"
          % (counts["match"], counts["reject"], counts["buildfail"],
             counts["nondet"], counts["mismatch"]))

    problems = []
    for r in rows:
        if r["kind"] == "mismatch":
            problems.append("不一致：%s（%s）" % (r["file"], r["note"]))
    cur = {"reject": sorted(r["file"] for r in rows if r["kind"] == "reject"),
           "buildfail": sorted(r["file"] for r in rows if r["kind"] == "buildfail")}

    if args.update_baseline:
        io.open(BASELINE, "w", encoding="utf-8", newline="\n").write(json.dumps(
            {"match": counts["match"], **cur, "nondet": sorted(NONDET),
             "updated": "5.6.0"},
            ensure_ascii=False, indent=2))
        print("基线已更新：%s" % BASELINE)
    else:
        base = load_baseline()
        if base is None:
            print("（无基线，跳过「拒绝不得新增」的比对；用 --update-baseline 建立基线）")
        else:
            for kind in ("reject", "buildfail"):
                added = set(cur[kind]) - set(base.get(kind) or [])
                gone = set(base.get(kind) or []) - set(cur[kind])
                if added:
                    problems.append("%s 新增：%s" % (kind, " ".join(sorted(added))))
                if gone:
                    print("（%s 减少：%s — 记得 --update-baseline）"
                          % (kind, " ".join(sorted(gone))))

    if args.json:
        io.open(args.json, "w", encoding="utf-8", newline="\n").write(json.dumps(
            {"counts": counts, "rows": rows, "problems": problems},
            ensure_ascii=False, indent=2))

    if problems:
        print("\n失败：")
        for p in problems:
            print("  - " + p)
        return 1
    print("\nOK：无「编出来但结果不同」，拒绝 / 构建失败未新增。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
