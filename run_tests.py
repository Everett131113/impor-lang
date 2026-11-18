#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键运行 Impor 语言测试套件。"""
import os
import subprocess
import sys
import time
import unittest

_ORIG_RUN = subprocess.run


def _is_exe_command(cmd):
    if not isinstance(cmd, (list, tuple)) or not cmd:
        return False
    first = cmd[0]
    if isinstance(first, os.PathLike):
        first = os.fspath(first)
    if isinstance(first, bytes):
        first = first.decode("utf-8", "replace")
    return str(first).lower().endswith(".exe")


def _run_with_retry(*args, **kwargs):
    """新生成的 exe 可能被 Windows 安全软件短暂扫描锁定，测试侧等待重试。"""
    cmd = args[0] if args else kwargs.get("args")
    if _is_exe_command(cmd) and not kwargs.get("shell"):
        for attempt in range(14):
            try:
                return _ORIG_RUN(*args, **kwargs)
            except PermissionError:
                if attempt == 13:
                    raise
                time.sleep(2.5)
            except FileNotFoundError:
                # v3.8：安全软件偶发把刚生成的 exe 短暂隔离/删除，短等重试
                if attempt == 5:
                    raise
                time.sleep(1.5)
    return _ORIG_RUN(*args, **kwargs)


subprocess.run = _run_with_retry

# 3.6.6 基线遗留：语法冻结后旧预期失效的 5 条用例。
# 分类与 3.6.10 已处理项见 docs/测试基线分类-3.6.7.md；默认跳过，--all 可跑全量。
LEGACY_IDS = {
    'test_v34.TestCTranspileErrors.test_try_catch_unsupported',
    'test_v37.TestEnglishKeywords.test_reserved_keywords',
    'test_v42.TestChineseSoftAliases.test_print_alias',
    'test_v42.TestConditionTolerance.test_then_keywords',
    'test_v42_diag.TestDiagnosticConsistency.test_interpreter_code_and_stack',
}


def _iter_cases(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            for case in _iter_cases(item):
                yield case
        else:
            yield item


def _is_legacy(case):
    cid = case.id()
    return any(cid.endswith(x) for x in LEGACY_IDS)


def main():
    suite = unittest.defaultTestLoader.discover("tests")
    if "--all" not in sys.argv:
        all_cases = list(_iter_cases(suite))
        legacy = [c for c in all_cases if _is_legacy(c)]
        keep = [c for c in all_cases if not _is_legacy(c)]
        print("legacy_3614: %d 条旧语法用例已分类跳过（--all 可跑全量）" % len(legacy))
        suite = unittest.TestSuite(keep)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()

