# -*- coding: utf-8 -*-
"""v3.6 P2：人类友好的诊断系统（四段式 + 彩色 + 上下文片段）。

覆盖：
  - explain() 知识库：语法 / 运行两类典型错误都有「为什么 + 修复」
  - render_diagnostic()：位置行、上下文片段、波浪线、四段结构、彩色开关
  - CLI：语法错误 / 运行错误 / 测试失败的实跑输出（管道下自动纯文本）
  - REPL：错误输出含「运行错误」且带修复建议
  - LSP：publishDiagnostics 的 message 带「为什么 / 修复」
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

from impor.diagnostics import explain, fix_for, render_diagnostic, color_enabled
from impor.lsp import LanguageServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URI = "file:///demo.impor"


def _write(text, suffix=".impor"):
    fd, path = tempfile.mkstemp(suffix=suffix, text=True)
    os.close(fd)
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def run_cli(path, extra_env=None, cmd="run"):
    env = dict(os.environ)
    env.pop("IMPOR_COLOR", None)
    env.pop("NO_COLOR", None)
    env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
    if extra_env:
        env.update(extra_env)
    return subprocess.run([sys.executable, os.path.join(ROOT, "Impor.py"), cmd, path],
                          capture_output=True, text=True, timeout=120, env=env, cwd=ROOT)


class TestExplainKnowledge(unittest.TestCase):
    def test_syntax_expect_has_why_and_fix(self):
        why, fix = explain("语法错误", "期望 ID，得到 '='")
        self.assertIn("语法", why)
        self.assertTrue(fix)

    def test_undefined_name(self):
        why, fix = explain("NameError", "变量 foo 未定义")
        self.assertIn("隐式全局", why)
        self.assertIn("var", fix)

    def test_zero_division(self):
        why, fix = explain("ZeroDivisionError", "division by zero")
        self.assertIn("除数是 0", why)
        self.assertIn("判断除数", fix)

    def test_const_assign(self):
        why, fix = explain("ConstError", "常量 PI 不可修改")
        self.assertIn("只读", why)
        self.assertIn("var", fix)

    def test_unknown_gets_generic_fallback(self):
        why, fix = explain("WeirdError", "某种没人见过的错")
        self.assertTrue(why)
        self.assertTrue(fix)
        self.assertNotEqual(why, "")

    def test_fix_for_matches_explain(self):
        self.assertEqual(fix_for("IndexError", "下标越界"), explain("IndexError", "下标越界")[1])


class TestRenderDiagnostic(unittest.TestCase):
    SRC = ["var a = 1", "var x = ", "print(x)"]

    def test_four_sections(self):
        out = render_diagnostic("语法错误", "表达式缺失", 2, 9, self.SRC)
        self.assertIn("语法错误: 第2行 列9", out)      # 哪里 + 是什么
        self.assertIn("为什么：", out)                 # 为什么
        self.assertIn("修复：", out)                   # 修复
        self.assertIn("^", out)                        # 波浪线

    def test_context_snippet_and_gutter(self):
        out = render_diagnostic("语法错误", "表达式缺失", 2, 9, self.SRC)
        self.assertIn("1 │ var a = 1", out)            # 上文
        self.assertIn("2 │ var x = ", out)             # 出错行
        self.assertIn("3 │ print(x)", out)             # 下文
        self.assertIn("│ ", out)

    def test_caret_aligned_under_column(self):
        out = render_diagnostic("语法错误", "表达式缺失", 1, 6, ["var x = 1"])
        caret_line = [ln for ln in out.split("\n") if "^" in ln][0]
        idx = caret_line.index("^")
        self.assertEqual(idx, caret_line.index("│") + 1 + 6)   # 「│ 」+ (列-1) 空格

    def test_plain_when_color_false(self):
        out = render_diagnostic("语法错误", "表达式缺失", 2, 9, self.SRC, color=False)
        self.assertNotIn("\x1b[", out)

    def test_colored_when_color_true(self):
        out = render_diagnostic("语法错误", "表达式缺失", 2, 9, self.SRC, color=True)
        self.assertIn("\x1b[", out)
        self.assertIn("为什么：", out)

    def test_hint_overrides_generic_fix(self):
        out = render_diagnostic("语法错误", "表达式缺失", 2, 9, self.SRC, hint="补一个值试试")
        self.assertIn("补一个值试试", out)

    def test_no_line_still_renders(self):
        out = render_diagnostic("运行错误", "NameError: x 未定义", 0, 0, None, color=False)
        self.assertIn("运行错误: NameError: x 未定义", out)
        self.assertIn("为什么：", out)

    def test_color_enabled_env_switch(self):
        old_c, old_n = os.environ.pop("IMPOR_COLOR", None), os.environ.pop("NO_COLOR", None)
        try:
            os.environ["IMPOR_COLOR"] = "always"
            self.assertTrue(color_enabled())
            os.environ["IMPOR_COLOR"] = "never"
            self.assertFalse(color_enabled())
            os.environ.pop("IMPOR_COLOR")
            os.environ["NO_COLOR"] = "1"
            self.assertFalse(color_enabled())
        finally:
            os.environ.pop("IMPOR_COLOR", None)
            os.environ.pop("NO_COLOR", None)
            if old_c is not None:
                os.environ["IMPOR_COLOR"] = old_c
            if old_n is not None:
                os.environ["NO_COLOR"] = old_n


class TestCliDiagnostics(unittest.TestCase):
    def test_syntax_error_four_sections(self):
        p = _write("var x =\nprint(x)\n")
        try:
            r = run_cli(p)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("语法错误: 第1行", r.stderr)
            self.assertIn("为什么：", r.stderr)
            self.assertIn("修复：", r.stderr)
            self.assertIn("^", r.stderr)
            self.assertNotIn("\x1b[", r.stderr)        # 管道捕获 → 自动纯文本
        finally:
            os.unlink(p)

    def test_runtime_error_name_fix(self):
        p = _write("print(missing_name)\n")
        try:
            r = run_cli(p)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("运行错误", r.stderr)
            self.assertIn("NameError", r.stderr)
            self.assertIn("为什么：", r.stderr)
            self.assertIn("修复：", r.stderr)
            self.assertIn("print(missing_name)", r.stderr)
        finally:
            os.unlink(p)

    def test_color_forced_by_env(self):
        p = _write("print(missing_name)\n")
        try:
            r = run_cli(p, {"IMPOR_COLOR": "always"})
            self.assertIn("\x1b[", r.stderr)
        finally:
            os.unlink(p)

    def test_check_reports_why_and_fix(self):
        p = _write("func f( {\n    return 1\n}\n")
        try:
            r = run_cli(p, cmd="check")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("为什么：", r.stderr)
            self.assertIn("修复：", r.stderr)
        finally:
            os.unlink(p)

    def test_test_cmd_failure_shows_name(self):
        p = _write("assert 1 == 2\n")
        try:
            r = run_cli(p, cmd="test")
            self.assertIn("AssertionError", r.stderr)
            self.assertIn("修复：", r.stderr)
        finally:
            os.unlink(p)


class TestReplDiagnostics(unittest.TestCase):
    def test_repl_runtime_error_has_fix(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
        env.pop("IMPOR_COLOR", None)
        env["NO_COLOR"] = "1"
        r = subprocess.run([sys.executable, os.path.join(ROOT, "Impor.py"), "repl"],
                           input="var a = 1\na / 0\nexit\n", capture_output=True,
                           text=True, timeout=60, env=env, cwd=ROOT)
        out = r.stdout + r.stderr
        self.assertIn("运行错误", out)
        self.assertIn("修复：", out)


def parse_frames(data):
    frames, i = [], 0
    while True:
        idx = data.find(b"Content-Length: ", i)
        if idx < 0:
            break
        end = data.find(b"\r\n\r\n", idx)
        header = data[idx:end].decode("utf-8", "replace")
        length = int(header.split(":")[1].strip())
        body = data[end + 4: end + 4 + length]
        frames.append(json.loads(body.decode("utf-8")))
        i = end + 4 + length
    return frames


class TestLspDiagnostics(unittest.TestCase):
    def test_syntax_diagnostic_has_why_and_fix(self):
        srv = LanguageServer(stdin=io.BytesIO(b""), stdout=io.BytesIO(),
                             workspace_root=".")
        srv._update_doc(URI, "var x = \n")
        frames = parse_frames(srv.stdout.getvalue())
        diags = [f for f in frames if f.get("method") == "textDocument/publishDiagnostics"]
        self.assertTrue(diags)
        msg = diags[-1]["params"]["diagnostics"][0]["message"]
        self.assertIn("为什么：", msg)
        self.assertIn("修复：", msg)

    def test_unused_issue_has_targeted_fix(self):
        srv = LanguageServer(stdin=io.BytesIO(b""), stdout=io.BytesIO(),
                             workspace_root=".")
        srv._update_doc(URI, "func main()\n    var unused_thing = 1\n    print('ok')\nmain()\n")
        frames = parse_frames(srv.stdout.getvalue())
        diags = [f for f in frames if f.get("method") == "textDocument/publishDiagnostics"]
        msgs = [d["message"] for d in diags[-1]["params"]["diagnostics"]]
        self.assertTrue(any("修复：" in m for m in msgs), msgs)
        self.assertTrue(any("删除" in m or "用" in m for m in msgs))


if __name__ == "__main__":
    unittest.main()
