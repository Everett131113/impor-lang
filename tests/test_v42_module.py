# -*- coding: utf-8 -*-
"""v4.2 回归：模块对象作为值（解释器 / C 后端）。"""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMPOR = os.path.join(ROOT, "Impor.py")

MOD_SRC = (
    'export add, base\n'
    'def add(a, b)\n'
    '    return a + b\n'
    'var base = 10\n'
)
MAIN_SRC = (
    'import modx as m\n'
    'def apply_mod(x)\n'
    '    return x.add(1, 2) + x.base\n'
    'var mm = m\n'
    'print(apply_mod(mm))\n'
    'print(mm.add(10, 20))\n'
    'print(type_of(mm))\n'
    'print(mm)\n'
    'print(mm == m)\n'
)
EXPECTED = ["13", "30", "module", "<Impor 模块 modx>", "True"]


def _write(path, text):
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _run(args, cwd, timeout=120):
    for attempt in range(8):
        try:
            return subprocess.run([sys.executable, "-X", "utf8", IMPOR] + args,
                                  capture_output=True, text=True, cwd=cwd,
                                  timeout=timeout)
        except (PermissionError, OSError):
            if attempt == 7:
                raise
            time.sleep(1.5)


class TestModuleValue(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="impor_module_", dir=os.path.join(ROOT, "tests"))
        _write(os.path.join(self.dir, "modx.impor"), MOD_SRC)
        _write(os.path.join(self.dir, "main.impor"), MAIN_SRC)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _check(self, out, rc, label):
        self.assertEqual(rc, 0, "%s rc=%s\n%s" % (label, rc, out))
        lines = [x.strip() for x in out.splitlines() if x.strip()]
        tail = lines[-len(EXPECTED):]
        self.assertEqual(tail, EXPECTED, "%s:\n%s" % (label, out))

    def test_interpreter_module_value(self):
        r = _run(["run", "main.impor"], self.dir)
        self._check(r.stdout, r.returncode, "解释器")

    def test_c_backend_module_value(self):
        b = _run(["build", "main.impor", "-o", "main.exe"], self.dir, timeout=300)
        self.assertEqual(b.returncode, 0, b.stdout + b.stderr)
        exe = os.path.join(self.dir, "main.exe")
        self.assertTrue(os.path.exists(exe), b.stdout + b.stderr)
        time.sleep(2)
        for attempt in range(8):
            try:
                p = subprocess.run([exe], capture_output=True, text=True,
                                   cwd=self.dir, timeout=120)
                break
            except (PermissionError, OSError):
                if attempt == 7:
                    raise
                time.sleep(1.5)
        self._check(p.stdout, p.returncode, "C 后端")

    def test_missing_attribute_reports(self):
        _write(os.path.join(self.dir, "bad.impor"),
               'import modx as m\nprint(m.nope)\n')
        r = _run(["run", "bad.impor"], self.dir)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("nope", r.stderr + r.stdout)

    def test_imporc_module_value(self):
        args_file = os.path.join(ROOT, "imporc_args.txt")
        backup = None
        if os.path.exists(args_file):
            with io.open(args_file, encoding="utf-8") as f:
                backup = f.read()
        main = os.path.join(self.dir, "main.impor")
        out = os.path.join(self.dir, "main_imporc.exe")
        try:
            cmdline = 'cmd /c ""{}" build "{}" -o "{}""'.format(
                os.path.join(ROOT, "imporc.cmd"), main, out)
            r = subprocess.run(cmdline, capture_output=True, text=True,
                               cwd=ROOT, timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertTrue(os.path.exists(out), r.stdout + r.stderr)
            time.sleep(2)
            p = subprocess.run([out], capture_output=True, text=True,
                               cwd=self.dir, timeout=120)
            self._check(p.stdout, p.returncode, "imporc")
        finally:
            if backup is not None:
                with io.open(args_file, "w", encoding="utf-8", newline="\n") as f:
                    f.write(backup)


if __name__ == "__main__":
    unittest.main()
