# -*- coding: utf-8 -*-
"""S1 第一小步：类型注解门禁的 20 条正反用例。

口径：
- 只验证现有 `impor/typecheck.py` 的静态诊断，不改解释器/C 后端行为。
- 正向用例断言无诊断；负向用例断言错误码。
- 这是“先只报错、不改行为”的回归锁，不是完整类型系统。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from impor.typecheck import check_source


# (名称, 源码, 期望错误码元组)
CASES = [
    ("ok_param_int",
     'def f(a: int) -> int:\n    return a\nprint(f(3))\n', ()),
    ("ok_param_float_from_int",
     'def f(a: float) -> float:\n    return a\nprint(f(3))\n', ()),
    ("ok_param_unannotated",
     'def f(a):\n    return a\nprint(f("s"))\n', ()),
    ("ok_return_int",
     'def f() -> int:\n    return 1\nprint(f())\n', ()),
    ("ok_var_annotated",
     'var x: int = 1\nprint(x)\n', ()),
    ("ok_var_float_from_int",
     'var x: float = 1\nprint(x)\n', ()),
    ("ok_nullable",
     'var x: int? = null\nprint(x)\n', ()),
    ("ok_any",
     'def f(a: any) -> any:\n    return a\nprint(f("s"))\n', ()),
    ("ok_generic_same",
     'def max2<T>(a: T, b: T) -> T:\n    return a\nprint(max2(1, 2))\n', ()),
    ("ok_nested_call",
     'def g() -> int:\n    return 1\ndef f() -> int:\n    return g()\nprint(f())\n', ()),
    ("bad_param_int_str",
     'def f(a: int) -> int:\n    return a\nprint(f("s"))\n', ("type-mismatch",)),
    ("bad_param_str_int",
     'def f(a: str) -> str:\n    return a\nprint(f(1))\n', ("type-mismatch",)),
    ("bad_return_int_str",
     'def f() -> int:\n    return "s"\nprint(f())\n', ("return-type",)),
    ("bad_return_bare",
     'def f() -> int:\n    return\nprint(f())\n', ("return-type",)),
    ("bad_arg_few",
     'def f(a: int, b: int) -> int:\n    return a\nprint(f(1))\n', ("arg-count",)),
    ("bad_arg_many",
     'def f(a: int) -> int:\n    return a\nprint(f(1, 2))\n', ("arg-count",)),
    ("bad_var_annotated",
     'var x: int = "s"\nprint(x)\n', ("type-mismatch",)),
    ("bad_reassign",
     'var x = 1\nset x = "s"\nprint(x)\n', ("type-mismatch",)),
    ("bad_generic_mixed",
     'def max2<T>(a: T, b: T) -> T:\n    return a\nprint(max2(1, "s"))\n',
     ("generic-mismatch",)),
    ("bad_bool_from_int",
     'var b: bool = 1\nprint(b)\n', ("type-mismatch",)),
]


class TestAnnotationGate(unittest.TestCase):
    """每个方法对应一条用例，便于失败时直接定位。"""


def _make_case(source, expected_codes, name):
    def test(self):
        codes = tuple(issue.code for issue in check_source(source))
        self.assertEqual(codes, expected_codes, name)
    test.__doc__ = name
    return test


for _idx, (_name, _src, _expected) in enumerate(CASES, 1):
    _test = _make_case(_src, _expected, _name)
    _test.__name__ = "test_%02d_%s" % (_idx, _name)
    setattr(TestAnnotationGate, _test.__name__, _test)


if __name__ == "__main__":
    unittest.main()
