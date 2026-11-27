# -*- coding: utf-8 -*-
import os
import tempfile
import time
import unittest
from unittest import mock

from impor.symbol_index import ProjectIndex


def _source(n):
    out = []
    for i in range(n):
        out.append("def f%d(x, y):" % i)
        out.append("    var total = x + y")
        out.append("    if total > 0:")
        out.append("        return total")
        out.append("    return 0")
    return "\n".join(out) + "\n"


class TestSemanticPositionsFast(unittest.TestCase):
    def test_does_not_rescan_scope_for_every_reference(self):
        """千行文件不能对每个引用重扫全文；旧实现会卡住编辑器十几秒。"""
        src = _source(1000)
        fd, path = tempfile.mkstemp(suffix=".impor")
        os.close(fd)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(src)
            index = ProjectIndex(os.path.dirname(path), roots=[os.path.dirname(path)])
            with mock.patch.object(ProjectIndex, "scope_at_line",
                                   side_effect=AssertionError("per-reference rescan")):
                positions = index.semantic_positions(path, src)
            self.assertTrue(positions)
            self.assertIn("var", set(positions.values()))
        finally:
            os.unlink(path)

    def test_thousand_lines_under_budget(self):
        src = _source(1000)
        fd, path = tempfile.mkstemp(suffix=".impor")
        os.close(fd)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(src)
            index = ProjectIndex(os.path.dirname(path), roots=[os.path.dirname(path)])
            start = time.perf_counter()
            index.semantic_positions(path, src)
            elapsed = time.perf_counter() - start
            self.assertLess(elapsed, 3.0, "semantic_positions took %.2fs" % elapsed)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
