# -*- coding: utf-8 -*-
"""v3.8.1 回归：产物跨目录运行、类型注解门禁、imporc argv 与 -o。"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMPOR_PY = os.path.join(ROOT, "Impor.py")
IMPORC = os.path.join(ROOT, "impor", "imporc.exe")


def _env_no_pythonpath():
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    return env


def _run(cmd, cwd, timeout=90, env=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          timeout=timeout, env=env)


class TestTranspileArtifact(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="impor_v381_move_")
        self.src_dir = os.path.join(self.tmp, "src")
        self.run_dir = os.path.join(self.tmp, "run")
        os.makedirs(self.src_dir)
        os.makedirs(self.run_dir)
        self.src = os.path.join(self.src_dir, "probe.impor")
        with open(self.src, "w", encoding="utf-8") as f:
            f.write("print(2 + 3)\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_compiled_artifact_runs_from_other_directory(self):
        out = os.path.join(self.src_dir, "probe.py")
        proc = _run([sys.executable, IMPOR_PY, "compile", self.src, "-o", out], ROOT)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        moved = os.path.join(self.run_dir, "probe.py")
        shutil.copy2(out, moved)
        proc = _run([sys.executable, moved], self.run_dir,
                    timeout=30, env=_env_no_pythonpath())
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(proc.stdout.strip(), "5")

    def test_compiled_artifact_has_self_location_header(self):
        out = os.path.join(self.src_dir, "probe.py")
        proc = _run([sys.executable, IMPOR_PY, "compile", self.src, "-o", out], ROOT)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        with open(out, "r", encoding="utf-8") as f:
            text = f.read()
        self.assertIn("IMPOR_HOME", text)
        self.assertIn("sys.path.insert", text)
        self.assertIn("找不到运行时", text)


class TestTypeGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="impor_v381_type_")
        self.src = os.path.join(self.tmp, "bad_type.impor")
        with open(self.src, "w", encoding="utf-8") as f:
            f.write('var y: int = "oops"\nprint(y)\n')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_compile_blocks_on_type_mismatch(self):
        out = os.path.join(self.tmp, "bad.py")
        proc = _run([sys.executable, IMPOR_PY, "compile", self.src, "-o", out], ROOT)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("[FAIL] 类型", proc.stdout + proc.stderr)
        self.assertFalse(os.path.exists(out))

    def test_run_blocks_on_type_mismatch(self):
        proc = _run([sys.executable, IMPOR_PY, "run", self.src], ROOT)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("[FAIL] 类型", proc.stdout + proc.stderr)


@unittest.skipUnless(os.name == "nt", "imporc.exe 仅在 Windows 构建")
class TestImporcCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="impor_v381_imporc_")
        self.script = os.path.join(self.tmp, "hi.impor")
        with open(self.script, "w", encoding="utf-8") as f:
            f.write('print("hi")\n')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _imporc(self, *args):
        return _run([IMPORC] + list(args), self.tmp, timeout=90)

    def test_version(self):
        proc = self._imporc("version")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        from impor import __version__ as ver
        self.assertIn("v" + ver, proc.stdout)

    def test_help(self):
        proc = self._imporc("--help")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("用法：imporc", proc.stdout)

    def test_output_flag_is_honored_from_other_directory(self):
        out = os.path.join(self.tmp, "custom.exe")
        proc = self._imporc("hi.impor", "-o", "custom.exe")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(os.path.isfile(out), proc.stdout + proc.stderr)
        self.assertGreater(os.path.getsize(out), 0)
        run = _run([out], self.tmp, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stdout.strip(), "hi")

    def test_missing_output_value_returns_2(self):
        proc = self._imporc("hi.impor", "-o")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("-o 缺少输出文件名", proc.stdout + proc.stderr)

    def test_unknown_argument_returns_2(self):
        proc = self._imporc("hi.impor", "--bogus")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("未知参数 --bogus", proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
