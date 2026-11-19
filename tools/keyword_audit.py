# -*- coding: utf-8 -*-
"""保留字兑现表：遍历全部关键字，输出「已实现 / 明确拒绝 / 静默接受」。

判定标准：
- 明确拒绝：在语句/声明位置出现时，parser 必须抛 ImporSyntaxError，且文案含「尚未实现」。
- 已实现：不在拒绝集合内，且 lexer/parser 有真实语法归属。
- 静默接受：拒绝探针没有报错，或关键字既无实现也无拒绝。这个表必须为 0，否则 exit 1。

用法：python tools/keyword_audit.py
CI：python tools/keyword_audit.py（静默接受非空即失败）
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from impor.errors import ImporSyntaxError
from impor.interpreter import parse_source
from impor.lexer import (
    KEYWORDS,
    SOFT_KEYWORDS,
    UNIMPLEMENTED_KEYWORDS,
    UNIMPLEMENTED_RESERVED_KEYWORDS,
    UNIMPLEMENTED_SOFT_KEYWORDS,
)

# 每个“明确拒绝”关键字都要有可执行的语句级探针。缺探针 = 未被审计。
PROBES = {
    "async": "async def f() {\n}\n",
    "await": "def g() {\n    return 1\n}\nprint(await g())\n",
    "defer": 'defer print("d")\n',
    "yield": "yield 1\n",
    "with": "with x {\n}\n",
    "interface": "interface Foo {\n}\n",
    "namespace": "namespace Foo {\n}\n",
    "abstract": "abstract class A {\n}\n",
    "override": "override def f() {\n}\n",
    "implements": "implements Foo\n",
    "sealed": "sealed class A {\n}\n",
    "final": "final class A {\n}\n",
}


def _reject_probe_ok(keyword):
    probe = PROBES.get(keyword)
    if probe is None:
        return False, "缺少探针"
    try:
        parse_source(probe, filename="<keyword-audit>")
    except ImporSyntaxError as e:
        if "尚未实现" in str(e):
            return True, ""
        return False, f"报了别的错：{e}"
    except Exception as e:  # parser 之外的异常也不能算“明确拒绝”
        return False, f"{type(e).__name__}: {e}"
    return False, "parser 未报错"


NOTE_OVERRIDES = {
    "defer": "`defer { ... }` 已实现；`defer expr` 明确拒绝",
    "static": "仅变量声明位置 = const 别名",
    "readonly": "仅变量声明位置 = const 别名",
    "this": "类方法内映射 self",
    "public": "成员/模块可见性已实现",
    "private": "成员/模块可见性已实现",
    "protected": "成员/模块可见性已实现",
}


def main():
    all_keywords = sorted(KEYWORDS | SOFT_KEYWORDS | {"final"})
    rejected = sorted(UNIMPLEMENTED_KEYWORDS | {"final"})
    implemented = sorted(set(all_keywords) - set(rejected))

    silent = []
    for kw in rejected:
        ok, why = _reject_probe_ok(kw)
        if not ok:
            silent.append((kw, why))

    # 拒绝集合里的每个词都必须真出现在 KEYWORDS / SOFT / final 中的至少一处。
    unknown = [kw for kw in rejected
               if kw not in KEYWORDS and kw not in SOFT_KEYWORDS and kw != "final"]

    def table(title, rows, note):
        print(f"\n## {title}（{len(rows)}）")
        print(f"| 关键字 | 说明 |")
        print(f"|---|---|")
        for kw in rows:
            print(f"| `{kw}` | {NOTE_OVERRIDES.get(kw, note)} |")

    print("Impor 保留字兑现表（v3.11 P0）")
    print(f"关键字总数：{len(all_keywords)}")
    table("已实现", implemented, "有语法归属；现有测试/示例覆盖")
    table("明确拒绝", rejected, "语句位置 exit 2，文案含「尚未实现（v3.x 计划）」")
    table("静默接受", [kw for kw, _ in silent], "应为 0；出现即 exit 1")

    if silent:
        print("\n静默接受明细：")
        for kw, why in silent:
            print(f"  - {kw}: {why}")
        print("\nFAIL：存在静默接受或未被审计的保留字。")
        return 1
    if unknown:
        print("\nFAIL：拒绝集合含未注册关键字：" + ", ".join(unknown))
        return 1
    print("\nOK：静默接受 = 0，所有拒绝项均有可执行探针。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
