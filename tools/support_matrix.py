# -*- coding: utf-8 -*-
"""Impor 能力矩阵：一份可执行、可复现的“四端到底支持什么”清单。

背景：能力口径以前手写在 README 里，代码一改文档就落后，于是出现
“文档说 C 后端不支持异常、实际支持”这类漂移。这个脚本把口径变成测量：
同一份 .impor 分别走四条路径，逐字节比对 stdout，结果直接生成
docs/能力矩阵.md，文档不再手抄。

四条路径：
  1) 解释器    python Impor.py run    case.impor
  2) 转译器    python Impor.py compile case.impor -o out.py && python out.py
  3) C 后端    python Impor.py build  case.impor -o out.exe --no-fast
  4) 自举探针  impor/imporc.exe build（参数走 imporc_args.txt）

契约（--check 会守住前三条）：
  * 解释器是唯一语义定义；
  * 转译器与 C 后端只要“能构建”，输出就必须与解释器逐字节相同；
  * imporc 是自举探针，只承诺 bootstrap 子集；超出子集时允许 BUILD_FAIL，
    但必须计入矩阵（不允许静默不一致）。

用法：
  python tools/support_matrix.py            # 测量并写 docs/能力矩阵.md
  python tools/support_matrix.py --check    # 只判定契约，失败 exit 1
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
PY = sys.executable or "python"
IMPORC = os.path.join(ROOT, "impor", "imporc.exe")
ARGFILE = os.path.join(ROOT, "imporc_args.txt")
TIMEOUT = 180

# (标签, 源码, 说明) —— 源码短的直接内联，长的从 examples 读取
INLINE = {
    "类型化 catch（类型不匹配要外抛）": '''exception Foo
exception Bar

def f(kind)
    if kind == 1
        raise Foo("foo 消息")
    raise Bar("bar 消息")

try
    try
        f(2)
    catch Foo e
        print("误捕获 Foo:", e.msg)
    print("内层 try 之后（不应出现）")
catch Bar e
    print("外层捕获 Bar:", e.msg)
print("结束")

var arr = [1, 2]
try
    print(arr[5])
catch IndexError e
    print("越界:", e.msg)
print("再结束")
''',
    "64 位整数溢出（应报错而不是回绕）": '''var a = 4611686018427387904
var b = a * 3
print(b)
''',
    "标准库 import（import math）": '''import math
print(math.pi)
print(math.sqrt(2))
''',
}

# (标签, examples 下的文件名, 特性说明)
EXAMPLE_CASES = [
    ("基础算术 / 打印", "hello.impor", "变量、算术、print"),
    ("字符串运算", "demo_03_strings.impor", "字符串方法、拼接"),
    ("数组与排序", "demo_12_bubble_sort.impor", "数组、下标、循环"),
    ("字典统计", "demo_22_dict_stats.impor", "字典、键值遍历"),
    ("枚举", "demo_27_enum.impor", "enum 定义与取值"),
    ("函数与递归", "fib.impor", "函数、递归、循环"),
    ("闭包（一等函数）", "closures.impor", "嵌套函数、上值捕获"),
    ("闭包示例（30）", "demo_30_closures.impor", "闭包 + 高阶函数"),
    ("类与方法", "oop.impor", "class、self、方法"),
    ("继承 / super", "demo_28_inheritance.impor", "继承、方法覆盖"),
    ("异常（自定义 + catch）", "demo_29_exceptions.impor", "exception、raise、try/catch/finally"),
    ("异常边界用例", "errors.impor", "类型化 catch、越界、除零"),
    ("JSON（标准库）", "demo_39_json.impor", "import json、序列化"),
    ("泛型（v4.2）", "generics.impor", "def f<T> 声明位类型参数、调用点类型绑定"),
    ("线程 / 原子（v4.2）", "threads.impor", "thread_spawn/join、mutex、atomic_*"),
    ("通道 / 线程池（v4.3）", "channels.impor", "std.channel 生产消费、std.pool 常驻 worker"),
    ("条件变量 / 阻塞通道（v4.3.1）", "condvar.impor",
     "std.sync 挂起唤醒、有界通道阻塞、recv_timeout、跨行字面量与 nil"),
]


def _norm(s):
    return (s or "").replace("\r\n", "\n").replace("\r", "\n")


def _run(cmd, cwd=ROOT, timeout=TIMEOUT):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, cwd=cwd)


def _run_exe(exe):
    for _ in range(3):
        try:
            return _run([exe])
        except PermissionError:
            continue
    return None


def interp(case):
    r = _run([PY, os.path.join(ROOT, "Impor.py"), "run", case])
    return ("OK" if r.returncode == 0 else "FAIL", _norm(r.stdout), r)


def transpile(case, work):
    out_py = os.path.join(work, "t.py")
    r = _run([PY, os.path.join(ROOT, "Impor.py"), "compile", case, "-o", out_py])
    if not os.path.isfile(out_py):
        return ("BUILD_FAIL", "", r)
    r2 = _run([PY, out_py])
    return ("OK" if r2.returncode == 0 else "FAIL", _norm(r2.stdout), r2)


def ctrans(case, work, no_fast):
    exe = os.path.join(work, "c.exe")
    args = [PY, os.path.join(ROOT, "Impor.py"), "build", case, "-o", exe]
    if no_fast:
        args.append("--no-fast")
    r = _run(args)
    if not os.path.isfile(exe):
        return ("BUILD_FAIL", "", r)
    r2 = _run_exe(exe)
    if r2 is None:
        return ("RUN_FAIL", "", r)
    return ("OK" if r2.returncode == 0 else "FAIL", _norm(r2.stdout), r2)


def imporc(case, work):
    exe = os.path.join(work, "i.exe")
    local = os.path.join(work, "case.impor")
    try:
        shutil.copyfile(case, local)
    except OSError as exc:
        return ("BUILD_FAIL", "", None)
    # imporc 从“当前目录”读 imporc_args.txt，共享仓库 cwd 会和别的任务撞车；
    # 每个用例用自己的临时目录当 cwd，参数文件就天然隔离。
    argfile = os.path.join(work, "imporc_args.txt")
    try:
        with io.open(argfile, "w", encoding="utf-8", newline="\n") as f:
            f.write('build "%s" -o "%s"' % (local, exe))
        r = _run([IMPORC], cwd=work)
    finally:
        try:
            os.remove(argfile)
        except OSError:
            pass
    if not os.path.isfile(exe):
        return ("BUILD_FAIL", "", r)
    r2 = _run_exe(exe)
    if r2 is None:
        return ("RUN_FAIL", "", r)
    return ("OK" if r2.returncode == 0 else "FAIL", _norm(r2.stdout), r2)


def nvm_run(case):
    """自有字节码后端：python -m impor vm run（不做 native 构建）。
    返回非 0 即后端明确拒绝，归类为 BUILD_FAIL。"""
    r = _run([PY, "-m", "impor", "vm", "run", case])
    return ("OK" if r.returncode == 0 else "BUILD_FAIL", _norm(r.stdout), r)


def _why(result):
    """从失败输出的最后一条错误里摘一句话，作为矩阵里的“为什么”。"""
    _, _, r = result
    if r is None:
        return "无输出"
    text = _norm(r.stdout + r.stderr).strip().splitlines()
    for line in reversed(text):
        if "不支持" in line or "error" in line.lower() or "错误" in line:
            return line.strip().lstrip("- ")[:90]
    return text[-1][:90] if text else "无输出"


def _materialize(src, work, label):
    """内联用例要落成真实文件再跑——所有后端都只吃路径，不吃源码字符串。"""
    if os.path.isfile(src):
        return src
    path = os.path.join(work, "inline.impor")
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(src)
    return path


def measure(case_name, source, work):
    case = _materialize(source, work, case_name)
    row = {}
    row["interp"] = interp(case)
    row["trans"] = transpile(case, work)
    row["c_full"] = ctrans(case, work, True)
    row["c_auto"] = ctrans(case, work, False)
    row["imporc"] = imporc(case, work)
    row["nvm"] = nvm_run(case)
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验契约，不写文档")
    ap.add_argument("--update-baseline", action="store_true",
                    help="把当前的已知缺口登记为基线（只用于确认“这就是已知状态”）")
    ap.add_argument("--out", default=os.path.join(ROOT, "docs", "能力矩阵.md"))
    args = ap.parse_args()
    baseline_path = os.path.join(ROOT, "docs", "能力矩阵基线.json")

    cases = []
    for label, text in INLINE.items():
        cases.append((label, text, "内联用例"))
    for label, fname, note in EXAMPLE_CASES:
        path = os.path.join(ROOT, "examples", fname)
        if os.path.isfile(path):
            cases.append((label, path, note))

    rows, violations = [], []
    for label, src, note in cases:
        work = tempfile.mkdtemp(prefix="mx_")
        try:
            r = measure(label, src, work)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        base = r["interp"][1]
        # 契约：只要构建成功且正常退出，stdout 必须与解释器逐字节相同。
        # 出错用例（FAIL）不比 stdout —— 各后端的诊断文本本来就允许不同措辞。
        for key, name in (("trans", "转译器"), ("c_full", "C 后端"),
                          ("c_auto", "C 后端(auto)"), ("nvm", "NVM 字节码")):
            status, out, _ = r[key]
            if status == "OK" and out != base:
                violations.append("%s × %s：输出与解释器不一致" % (label, name))
        rows.append((label, note, r))

    def cell(res, base):
        status, out, _ = res
        if status == "OK":
            return "✅ 一致" if out == base else "⚠️ 输出不同"
        if status == "FAIL":
            return "⚠️ 运行出错" if out != base else "⚠️ 出错但输出一致"
        if status == "BUILD_FAIL":
            return "❌ 编译期拒绝"
        return "❌ 无法运行"

    known = set()
    if os.path.exists(baseline_path):
        try:
            with io.open(baseline_path, encoding="utf-8") as f:
                known = set(json.load(f).get("known", []))
        except (OSError, ValueError):
            known = set()
    if args.update_baseline:
        with io.open(baseline_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"known": sorted(violations)}, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print("已登记基线 %d 条：%s" % (len(violations), baseline_path))
        return 0

    lines = []
    lines.append("# Impor 能力矩阵（自动生成，请勿手改）")
    lines.append("")
    lines.append("由 `python tools/support_matrix.py` 实测生成。口径写在代码里、结果写在表里，"
                 "README 不再手抄能力清单——这样代码一改，矩阵就能重新生成。")
    lines.append("")
    lines.append("## 契约")
    lines.append("")
    lines.append("- **解释器**是唯一语义定义。")
    lines.append("- **转译器 / C 后端**只要构建成功，stdout 必须与解释器逐字节相同；不一致即视为缺陷。")
    lines.append("- **imporc** 是自举探针，只承诺 bootstrap 子集。超出子集允许编译期拒绝，"
                 "但必须在表中可见，不允许“能构建但行为不同”。")
    lines.append("")
    lines.append("## 矩阵")
    lines.append("")
    lines.append("| 能力 | 用例 | 解释器 | 转译器 | C 后端(完整) | C 后端(默认auto) | imporc | NVM 自有字节码 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for label, note, r in rows:
        base = r["interp"][1]
        cells = [cell(r["trans"], base), cell(r["c_full"], base),
                 cell(r["c_auto"], base), cell(r["imporc"], base), cell(r["nvm"], base)]
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
            label, note, cell(r["interp"], base),
            cells[0], cells[1], cells[2], cells[3], cells[4]))
    lines.append("")
    lines.append("### 被拒绝时的原因")
    lines.append("")
    for label, _, r in rows:
        for key, name in (("trans", "转译器"), ("c_full", "C 后端(完整)"),
                          ("c_auto", "C 后端(auto)"), ("imporc", "imporc"),
                          ("nvm", "NVM 字节码")):
            res = r[key]
            if res[0] in ("BUILD_FAIL", "RUN_FAIL", "FAIL"):
                lines.append("- **%s** / %s：%s —— %s" % (label, name, res[0], _why(res)))
    lines.append("")
    lines.append("## 覆盖率")
    lines.append("")
    ok_c = sum(1 for _, _, r in rows if r["c_full"][0] == "OK")
    ok_a = sum(1 for _, _, r in rows if r["c_auto"][0] == "OK")
    ok_i = sum(1 for _, _, r in rows if r["imporc"][0] == "OK")
    ok_n = sum(1 for _, _, r in rows if r["nvm"][0] == "OK")
    lines.append("- 用例 %d 个；C 后端(完整) %d/%d 通过，C 后端(auto) %d/%d，imporc %d/%d，NVM %d/%d。"
                 % (len(rows), ok_c, len(rows), ok_a, len(rows), ok_i, len(rows), ok_n, len(rows)))
    lines.append("- 契约违例：%d 条（已知基线 %d 条，本次新增 %d 条）。"
                 % (len(violations), len(known & set(violations)),
                    len([v for v in violations if v not in known])))
    for v in violations:
        lines.append("  - %s%s" % (v, "" if v in known else "（**新增，需修**）"))
    lines.append("")
    lines.append("> 已知基线写在 `docs/能力矩阵基线.json`，只在“已知缺口之外又多出一条”时才判红。"
                 " 校准基线用 `--update-baseline`，但校准前要先确认那条缺口确实不打算在本轮修。")
    lines.append("")

    if not args.check:
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        with io.open(args.out, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines))
        print("已写入 %s（用例 %d 个，契约违例 %d 条）" % (args.out, len(rows), len(violations)))
    else:
        new = [v for v in violations if v not in known]
        print("契约违例 %d 条（已知 %d，新增 %d）" % (
            len(violations), len(violations) - len(new), len(new)))
        for v in new:
            print("  新增：%s" % v)
    return 1 if [v for v in violations if v not in known] else 0


if __name__ == "__main__":
    sys.exit(main())
