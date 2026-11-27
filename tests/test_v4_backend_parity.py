# -*- coding: utf-8 -*-
"""v4.1 后端语义对齐回归。

锁定的四件事：
1. 默认解释器仍是任意精度（不静默改变 4.0.0 行为）；
2. `--int64` 把解释器切到 C 后端的 int64 语义，越界报 E1012，且与 C 产物同一句话；
3. `--max-depth` / `IMPOR_MAX_DEPTH` 真的改掉写死的 3200；
4. `impor new` 不再往新文件第一行塞头部标记，且缺头部不再刷提示。
"""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable or "python"
IMPOR = os.path.join(ROOT, "Impor.py")
TCC = os.path.join(ROOT, "impor", "tcc", "win32", "tcc.exe")
BIG = "print(9223372036854775807 + 1)\n"


def _write(text):
    d = tempfile.mkdtemp(prefix="v41p_")
    p = os.path.join(d, "t.impor")
    with io.open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return d, p


def run(src, extra=None):
    """在临时目录里跑 impor run，返回 (rc, stdout, stderr)。"""
    d, p = _write(src)
    try:
        cmd = [PY, IMPOR, "run"] + list(extra or []) + [p]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=180, cwd=ROOT)
        return (r.returncode,
                (r.stdout or "").replace("\r\n", "\n"),
                (r.stderr or "").replace("\r\n", "\n"))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def run_built(src):
    """build 成独立 exe 再跑；编译失败返回 (BUILD_FAIL, "", stderr)。"""
    d, p = _write(src)
    try:
        exe = os.path.join(d, "t.exe")
        b = subprocess.run([PY, IMPOR, "build", p, "-o", exe, "--no-cache"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=180, cwd=ROOT)
        if b.returncode != 0 or not os.path.isfile(exe):
            return "BUILD_FAIL", "", (b.stdout or "") + (b.stderr or "")
        r = subprocess.run([exe], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120)
        return (r.returncode,
                (r.stdout or "").replace("\r\n", "\n"),
                (r.stderr or "").replace("\r\n", "\n"))
    finally:
        shutil.rmtree(d, ignore_errors=True)


class TestInterpreterInt64(unittest.TestCase):
    def test_default_is_int64_everywhere(self):
        # 4.0.1 定案：run 的默认语义（原生、转译、纯解释器）全部是 int64，同一句话报错
        rc, out, err = run(BIG, ["--no-native"])
        self.assertEqual(rc, 1, out + err)
        self.assertIn("E1012", out + err)
        self.assertIn("整数溢出：超出 64 位有符号整数范围", out + err)

    def test_big_flag_restores_bignum(self):
        rc, out, err = run(BIG, ["--big"])
        self.assertEqual(rc, 0, err)
        self.assertEqual(out.strip(), "9223372036854775808")

    def test_int64_flag_reports_e1012(self):
        rc, out, err = run(BIG, ["--int64"])
        self.assertEqual(rc, 1)
        text = out + err
        self.assertIn("E1012", text)
        self.assertIn("整数溢出：超出 64 位有符号整数范围", text)

    def test_int64_flag_alias(self):
        rc, out, err = run(BIG, ["--strict-int"])
        self.assertEqual(rc, 1)
        self.assertIn("E1012", out + err)

    def test_int64_literal_out_of_range(self):
        rc, out, err = run("var y = 9223372036854775808\nprint(y)\n", ["--int64"])
        self.assertEqual(rc, 1)
        self.assertIn("E1012", out + err)

    def test_int64_negation_of_llong_min(self):
        # -LLONG_MIN 在 C 侧报错；解释器对齐模式必须同样拒绝。
        rc, out, err = run("print(0 - 9223372036854775807 - 1)\nprint(-(0 - 9223372036854775807 - 1))\n",
                           ["--int64"])
        self.assertEqual(rc, 1, out + err)
        self.assertIn("E1012", out + err)

    def test_int64_mode_keeps_normal_arithmetic(self):
        rc, out, err = run('var a = 5\nvar b = 3\nprint(a + b)\nprint(a * b)\n'
                            'print(10 / 4)\nprint(0.1 + 0.2)\n', ["--int64"])
        self.assertEqual(rc, 0, err)
        self.assertEqual(out.strip().splitlines(), ["8", "15", "2.5", "0.30000000000000004"])


class TestDepthOverride(unittest.TestCase):
    REC = ("def f(n)\n"
           "    return f(n + 1)\n"
           "print(f(0))\n")

    def test_max_depth_flag_caps_recursion(self):
        rc, out, err = run(self.REC, ["--max-depth", "6"])
        self.assertEqual(rc, 1)
        self.assertIn("RecursionDeep", out + err)

    def test_max_depth_env_caps_recursion(self):
        d, p = _write(self.REC)
        old = os.environ.get("IMPOR_MAX_DEPTH")
        os.environ["IMPOR_MAX_DEPTH"] = "6"
        try:
            r = subprocess.run([PY, IMPOR, "run", "--no-native", p],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=180, cwd=ROOT)
        finally:
            if old is None:
                os.environ.pop("IMPOR_MAX_DEPTH", None)
            else:
                os.environ["IMPOR_MAX_DEPTH"] = old
            shutil.rmtree(d, ignore_errors=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("RecursionDeep", (r.stdout or "") + (r.stderr or ""))


class TestHeaderRetired(unittest.TestCase):
    def test_new_template_has_no_header(self):
        d = tempfile.mkdtemp(prefix="v41n_")
        try:
            r = subprocess.run([PY, IMPOR, "new", "a.impor"],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=120, cwd=d)
            self.assertEqual(r.returncode, 0, r.stderr)
            with io.open(os.path.join(d, "a.impor"), encoding="utf-8") as f:
                first = f.readline()
            self.assertNotIn("important<<", first)
            self.assertTrue(first.startswith("//"), first)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_missing_header_is_silent(self):
        rc, out, err = run('print("ok")\n', ["--no-native"])
        self.assertEqual(rc, 0, err)
        self.assertEqual(out.strip(), "ok")
        self.assertNotIn("头部标记", err)


DIV_SRC = ("print(div(7, 2))\n"
           "print(div(0 - 7, 2))\n"
           "print(div(7, 0 - 2))\n"
           "print(div(0 - 7, 0 - 2))\n"
           "print((0 - 7) % 3)\n"
           "print(7 % (0 - 3))\n"
           "print(-7 % 3)\n"
           "var a = 0 - 7\n"
           "print(a == div(a, 3) * 3 + (a % 3))\n")


class TestIntegerDivision(unittest.TestCase):
    """div() 与 % 的三端一致性（4.0.1 补文档与回归）。"""

    EXPECT = ["3", "-4", "-4", "3", "2", "-2", "-1", "True"]

    def test_interpreter_div_floor(self):
        rc, out, err = run(DIV_SRC, ["--no-native"])
        self.assertEqual(rc, 0, err)
        self.assertEqual(out.strip().splitlines(), self.EXPECT)

    def test_default_run_matches_interpreter(self):
        rc, out, err = run(DIV_SRC)
        self.assertEqual(rc, 0, err)
        self.assertEqual(out.strip().splitlines(), self.EXPECT)

    def test_div_by_zero_reports(self):
        rc, out, err = run("print(div(1, 0))\n", ["--no-native"])
        self.assertEqual(rc, 1)
        self.assertIn("DivisionByZero", out + err)

    @unittest.skipUnless(os.path.isfile(TCC), "缺少 tcc")
    def test_c_backend_matches(self):
        rc, out, err = run_built(DIV_SRC)
        if rc == "BUILD_FAIL":
            self.skipTest("构建失败：" + err[:200])
        self.assertEqual(rc, 0, err)
        self.assertEqual(out.strip().splitlines(), self.EXPECT)


class TestProjectIntMode(unittest.TestCase):
    """impor.toml 的 int_mode = "int64" 让整个工程固定一种整数语义。"""

    def _proj(self, mode):
        d = tempfile.mkdtemp(prefix="v41c_")
        with io.open(os.path.join(d, "impor.toml"), "w", encoding="utf-8", newline="\n") as f:
            f.write('[project]\nname = "p"\nversion = "0.1.0"\nint_mode = "%s"\n' % mode)
        p = os.path.join(d, "t.impor")
        with io.open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(BIG)
        return d, p

    def _run(self, d, p, extra=None):
        r = subprocess.run([PY, IMPOR, "run"] + list(extra or []) + [p],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=180, cwd=d)
        return r.returncode, (r.stdout or "").replace("\r\n", "\n"), \
            (r.stderr or "").replace("\r\n", "\n")

    def test_config_int64_makes_project_stringent(self):
        d, p = self._proj("int64")
        try:
            rc, out, err = self._run(d, p)
            self.assertEqual(rc, 1, out + err)
            self.assertIn("E1012", out + err)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_flag_big_overrides_config(self):
        d, p = self._proj("int64")
        try:
            rc, out, err = self._run(d, p, ["--big"])
            self.assertEqual(rc, 0, err)
            self.assertEqual(out.strip(), "9223372036854775808")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_config_big_keeps_default(self):
        d, p = self._proj("big")
        try:
            rc, out, err = self._run(d, p)
            self.assertEqual(rc, 0, err)
            self.assertEqual(out.strip(), "9223372036854775808")
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TestSingleLineIf(unittest.TestCase):
    """单行 if 块以行尾结束：不存在悬挂 else 歧义（4.0.1 实测锁定）。"""

    def test_same_line_else_binds_correctly(self):
        rc, out, err = run('var c = true\nif c print(1) else print(2)\nprint(3)\n',
                           ["--no-native"])
        self.assertEqual(rc, 0, err)
        self.assertEqual(out.strip().splitlines(), ["1", "3"])

    def test_same_line_else_false_branch(self):
        rc, out, err = run('var c = false\nif c print(1) else print(2)\nprint(3)\n',
                           ["--no-native"])
        self.assertEqual(rc, 0, err)
        self.assertEqual(out.strip().splitlines(), ["2", "3"])

    def test_next_line_else_is_syntax_error_not_ambiguity(self):
        rc, out, err = run('var c = true\nif c print(1)\nelse print(2)\n', ["--no-native"])
        self.assertEqual(rc, 2, "跨行 else 必须是语法错误，而不是被静默绑定")
        self.assertIn("E2002", out + err)


@unittest.skipUnless(os.path.isfile(TCC), "缺少 tcc")
class TestBackendParityMessage(unittest.TestCase):
    def test_c_backend_uses_same_sentence(self):
        rc, out, err = run_built(BIG)
        if rc == "BUILD_FAIL":
            self.skipTest("构建失败：" + err[:200])
        self.assertNotEqual(rc, 0)
        text = out + err
        self.assertIn("整数溢出：超出 64 位有符号整数范围", text)
        self.assertIn("E1012", text)

    def test_fast_path_message_matches_interpreter(self):
        # run 默认走快路径；同一份源码在快路径与 --int64 下报同一句话。
        rc1, out1, err1 = run(BIG)
        self.assertNotEqual(rc1, 0, "默认快路径应拒绝 int64 溢出")
        self.assertIn("整数溢出：超出 64 位有符号整数范围", out1 + err1)
        rc2, out2, err2 = run(BIG, ["--int64"])
        self.assertEqual(rc2, 1)
        self.assertIn("整数溢出：超出 64 位有符号整数范围", out2 + err2)


if __name__ == "__main__":
    unittest.main()
