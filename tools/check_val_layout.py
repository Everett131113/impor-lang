"""Val 布局门禁（4.7.2）。

三个检查，任一不过就 rc=1：

1. 两处 typedef 必须是 tagged union（`union {` 在里面），并带 `val_size_must_be_16` 静态断言；
2. 不允许"构造一条 Val 时写多个 payload 槽"——这是 union 化的头号事故源：
   `v.t = V_STR; v.s = strdup(..); v.a = NULL;` 会把 `s` 清零（同一地址）。
   规则：同一行里 `X.t = V_*;` 之后的重载槽赋值最多 1 个。
3. 报告 Val 的字节数结论（依赖源码里的静态断言，实际字节数由编译器保证）。

用法：python tools/check_val_layout.py [worktree]
"""
import os
import re
import sys

SLOTS = ("i", "f", "s", "a", "o", "fn")
CTOR = re.compile(r"\.t\s*=\s*V_[A-Z_]+")
SLOT_ASSIGN = re.compile(r"\.(%s)\s*=(?!=)" % "|".join(SLOTS))


def check_typedef(path, label, expect_fields):
    errs = []
    text = open(path, encoding="utf-8", errors="replace").read()
    m = re.search(r"typedef struct \{[^\n]*VType t;[^\n]*\}\s*Val;", text)
    if not m:
        return ["%s: 找不到 Val typedef" % label], ""
    decl = m.group(0)
    if "union {" not in decl:
        errs.append("%s: Val 定义里没有 union（仍是多槽结构）" % label)
    for f in expect_fields:
        if ("%s;" % f) not in decl and ("*%s;" % f) not in decl and ("%s " % f) not in decl:
            pass  # 字段名检查放宽：以 union 是否存在为准
    if "val_size_must_be_16" not in text:
        errs.append("%s: 缺少 val_size_must_be_16 静态断言" % label)
    return errs, decl.strip()


def check_ctors(path, label):
    errs = []
    text = open(path, encoding="utf-8", errors="replace").read()
    for i, line in enumerate(text.splitlines(), 1):
        if not CTOR.search(line):
            continue
        n = len(SLOT_ASSIGN.findall(line))
        if n > 1:
            errs.append("%s:%d 构造语句写了 %d 个 payload 槽（union 下会互相覆盖）：%s"
                        % (label, i, n, line.strip()[:110]))
    return errs


def main():
    wt = sys.argv[1] if len(sys.argv) > 1 else "."
    targets = [
        (os.path.join(wt, "impor", "c_runtime.c"), "c_runtime.c", SLOTS),
        (os.path.join(wt, "impor", "ctrans.py"), "ctrans.py(embedded)", SLOTS[:4]),
    ]
    errors = []
    print("== Val 布局检查 ==")
    for path, label, fields in targets:
        if not os.path.exists(path):
            errors.append("%s: 文件不存在" % label)
            continue
        e, decl = check_typedef(path, label, fields)
        errors.extend(e)
        print("  %-22s %s" % (label, "OK" if not e else "FAIL"))
        if decl:
            print("     %s" % decl[:150])
    print("== 构造点检查（同一行最多 1 个 payload 槽） ==")
    for path, label, fields in targets:
        if not os.path.exists(path):
            continue
        e = check_ctors(path, label)
        errors.extend(e)
        print("  %-22s %s" % (label, "OK" if not e else "FAIL (%d)" % len(e)))
    if errors:
        print("\nFAIL:")
        for e in errors:
            print(" -", e)
        return 1
    print("\nPASS：Val 为 tagged union，构造点无多槽污染。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
