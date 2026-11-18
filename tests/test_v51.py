# -*- coding: utf-8 -*-
"""v3.10 包管理器：锁文件复现、私有源、令牌与完整性校验。"""
import hashlib
import http.server
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.parse
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from impor import pkgmgr


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _make_pkg(dirpath, name, version, requires=None):
    _write(os.path.join(dirpath, "impor.toml"),
           '[project]\nname = "%s"\nversion = "%s"\n' % (name, version))
    _write(os.path.join(dirpath, "lib.impor"), 'func hello() {\n    return "%s"\n}\n' % name)
    if requires:
        lines = ['\n[dependencies]']
        for k, v in requires.items():
            lines.append('%s = "%s"' % (k, v))
        with io.open(os.path.join(dirpath, "impor.toml"), "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    return dirpath


def _zip_pkg(src_dir, zip_path, name):
    os.makedirs(os.path.dirname(zip_path), exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _dirs, files in os.walk(src_dir):
            for fn in files:
                fp = os.path.join(root, fn)
                rel = os.path.relpath(fp, src_dir).replace(os.sep, "/")
                z.write(fp, rel)
    with open(zip_path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _project(root, registry=None):
    lines = ['[project]', 'name = "proj"', 'version = "0.1.0"', '']
    if registry:
        lines += ['[registry]', 'url = "%s"' % registry, 'token_env = "IMPOR_TEST_TOKEN"', '']
    _write(os.path.join(root, "impor.toml"), "\n".join(lines))
    return root


class TestLockFrozen(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp(prefix="impor_v51_", dir=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.old_cache = pkgmgr.CACHE_DIR
        self.cache = os.path.join(self.td, "cache")
        pkgmgr.CACHE_DIR = self.cache
        self.proj = _project(os.path.join(self.td, "proj"))
        self.src = _make_pkg(os.path.join(self.td, "mylib_src"), "mylib", "0.2.0")

    def tearDown(self):
        pkgmgr.CACHE_DIR = self.old_cache
        shutil.rmtree(self.td, ignore_errors=True)

    def test_lock_records_source_and_hash(self):
        name, ver = pkgmgr.install(self.src, project_dir=self.proj, cache_dir=self.cache)
        self.assertEqual((name, ver), ("mylib", "0.2.0"))
        entries = pkgmgr.lock(project_dir=self.proj, cache_dir=self.cache)
        e = entries["mylib"]
        self.assertTrue(os.path.isabs(e["source"]))
        self.assertEqual(len(e["hash"]), 12)
        self.assertEqual(len(e["dir_sha256"]), 64)
        self.assertEqual(e["version"], "0.2.0")
        self.assertEqual(pkgmgr.check_lock(project_dir=self.proj, cache_dir=self.cache), [])

    def test_check_detects_tampered_cache_and_frozen_restores(self):
        pkgmgr.install(self.src, project_dir=self.proj, cache_dir=self.cache)
        victim = os.path.join(self.cache, "mylib", "lib.impor")
        with io.open(victim, "a", encoding="utf-8") as f:
            f.write('// tampered\n')
        issues = pkgmgr.check_lock(project_dir=self.proj, cache_dir=self.cache)
        self.assertTrue(any("哈希" in x for x in issues), issues)
        restored = pkgmgr.restore_frozen(project_dir=self.proj, cache_dir=self.cache)
        self.assertEqual(restored, ["mylib"])
        self.assertEqual(pkgmgr.check_lock(project_dir=self.proj, cache_dir=self.cache), [])

    def test_transitive_requires_in_lock(self):
        child_src = _make_pkg(os.path.join(self.td, "child_src"), "child", "0.1.0")
        os.makedirs(self.cache, exist_ok=True)
        pkgmgr._install_from_source(child_src, self.cache)
        parent_src = _make_pkg(os.path.join(self.td, "parent_src"), "parent", "1.0.0",
                               {"child": ">=0.1"})
        pkgmgr.install(parent_src, project_dir=self.proj, cache_dir=self.cache)
        entries = pkgmgr.lock(project_dir=self.proj, cache_dir=self.cache)
        self.assertIn("parent", entries)
        self.assertIn("child", entries)
        self.assertIn("child>=0.1", entries["parent"]["requires"])

    def test_cli_frozen_and_check(self):
        pkgmgr.install(self.src, project_dir=self.proj, cache_dir=self.cache)
        shutil.rmtree(os.path.join(self.cache, "mylib"))
        old_cwd = os.getcwd()
        os.chdir(self.proj)
        try:
            self.assertEqual(pkgmgr.main_pkg(["lock", "--check"]), 1)
            self.assertEqual(pkgmgr.main_pkg(["install", "--frozen"]), 0)
            self.assertEqual(pkgmgr.main_pkg(["check"]), 0)
        finally:
            os.chdir(old_cwd)


class TestPrivateRegistry(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp(prefix="impor_v51reg_", dir=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.old_cache = pkgmgr.CACHE_DIR
        self.cache = os.path.join(self.td, "cache")
        pkgmgr.CACHE_DIR = self.cache
        self.reg = os.path.join(self.td, "registry")
        os.makedirs(self.reg, exist_ok=True)
        self.pkgs = {}
        for ver in ("0.1.0", "0.2.0", "0.3.0"):
            src = _make_pkg(os.path.join(self.td, "src", "mylib-" + ver), "mylib", ver)
            zpath = os.path.join(self.reg, "mylib-%s.zip" % ver)
            self.pkgs[ver] = {"url": os.path.basename(zpath),
                              "sha256": _zip_pkg(src, zpath, "mylib")}
        index = {"packages": {"mylib": self.pkgs}}
        with io.open(os.path.join(self.reg, "index.json"), "w", encoding="utf-8") as f:
            json.dump(index, f)
        self.reg_url = "file://" + self.reg.replace("\\", "/")
        self.proj = _project(os.path.join(self.td, "proj"), self.reg_url)

    def tearDown(self):
        pkgmgr.CACHE_DIR = self.old_cache
        shutil.rmtree(self.td, ignore_errors=True)

    def test_registry_install_records_registry_source(self):
        name, ver = pkgmgr.install("mylib@0.2.0", project_dir=self.proj, cache_dir=self.cache)
        self.assertEqual((name, ver), ("mylib", "0.2.0"))
        entry = pkgmgr.lock(project_dir=self.proj, cache_dir=self.cache)["mylib"]
        self.assertTrue(entry["source"].startswith("registry:"))
        self.assertEqual(len(entry["sha256"]), 64)

    def test_registry_picks_highest_matching(self):
        name, ver = pkgmgr.install("mylib= >=0.1", project_dir=self.proj, cache_dir=self.cache)
        self.assertEqual((name, ver), ("mylib", "0.3.0"))

    def test_frozen_restore_from_registry(self):
        pkgmgr.install("mylib@0.1.0", project_dir=self.proj, cache_dir=self.cache)
        shutil.rmtree(os.path.join(self.cache, "mylib"))
        restored = pkgmgr.restore_frozen(project_dir=self.proj, cache_dir=self.cache)
        self.assertEqual(restored, ["mylib"])
        self.assertEqual(pkgmgr.check_lock(project_dir=self.proj, cache_dir=self.cache), [])

    def test_registry_rejects_bad_sha(self):
        idx_path = os.path.join(self.reg, "index.json")
        with io.open(idx_path, encoding="utf-8") as f:
            data = json.load(f)
        data["packages"]["mylib"]["0.1.0"]["sha256"] = "0" * 64
        _write(idx_path, json.dumps(data))
        with self.assertRaises(ValueError):
            pkgmgr.install("mylib@0.1.0", project_dir=self.proj, cache_dir=self.cache)

    def test_registry_installs_transitive_dependencies(self):
        child = _make_pkg(os.path.join(self.td, "src", "childlib"), "childlib", "1.0.0")
        child_zip = os.path.join(self.reg, "childlib-1.0.0.zip")
        child_sha = _zip_pkg(child, child_zip, "childlib")
        parent = _make_pkg(os.path.join(self.td, "src", "parentlib"), "parentlib", "1.0.0",
                           {"childlib": ">=1.0"})
        parent_zip = os.path.join(self.reg, "parentlib-1.0.0.zip")
        parent_sha = _zip_pkg(parent, parent_zip, "parentlib")
        idx_path = os.path.join(self.reg, "index.json")
        with io.open(idx_path, encoding="utf-8") as f:
            data = json.load(f)
        data["packages"]["childlib"] = {"1.0.0": {"url": "childlib-1.0.0.zip", "sha256": child_sha}}
        data["packages"]["parentlib"] = {"1.0.0": {"url": "parentlib-1.0.0.zip", "sha256": parent_sha}}
        _write(idx_path, json.dumps(data))

        name, ver = pkgmgr.install("parentlib", project_dir=self.proj, cache_dir=self.cache)
        self.assertEqual((name, ver), ("parentlib", "1.0.0"))
        self.assertTrue(os.path.isdir(os.path.join(self.cache, "childlib")))
        entry = pkgmgr.lock(project_dir=self.proj, cache_dir=self.cache)["childlib"]
        self.assertTrue(entry["source"].startswith("registry:"))
        self.assertTrue(entry["dir_sha256"])


class _AuthHandler(http.server.BaseHTTPRequestHandler):
    root = ""
    token = ""

    def do_GET(self):
        if self.path == "/index.json" and self.headers.get("Authorization") != "Bearer " + self.token:
            self.send_response(401)
            self.end_headers()
            return
        path = urllib.parse.urlparse(self.path).path
        fp = os.path.join(self.root, path.lstrip("/"))
        if not os.path.isfile(fp):
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        ctype = "application/json" if fp.endswith(".json") else "application/zip"
        self.send_header("Content-Type", ctype)
        self.end_headers()
        with open(fp, "rb") as f:
            self.wfile.write(f.read())

    def log_message(self, *args):
        pass


class TestRegistryAuth(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp(prefix="impor_v51auth_", dir=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.old_cache = pkgmgr.CACHE_DIR
        self.cache = os.path.join(self.td, "cache")
        pkgmgr.CACHE_DIR = self.cache
        self.reg = os.path.join(self.td, "registry")
        src = _make_pkg(os.path.join(self.td, "src"), "secretlib", "1.0.0")
        zpath = os.path.join(self.reg, "secretlib-1.0.0.zip")
        sha = _zip_pkg(src, zpath, "secretlib")
        _write(os.path.join(self.reg, "index.json"), json.dumps(
            {"packages": {"secretlib": {"1.0.0": {"url": "secretlib-1.0.0.zip", "sha256": sha}}}}))
        self.handler = _AuthHandler
        self.handler.root = self.reg
        self.handler.token = "s3cr3t"
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), self.handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.proj = _project(os.path.join(self.td, "proj"), "http://127.0.0.1:%d" % self.port)

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        pkgmgr.CACHE_DIR = self.old_cache
        shutil.rmtree(self.td, ignore_errors=True)

    def test_token_header_required(self):
        os.environ["IMPOR_TEST_TOKEN"] = "s3cr3t"
        try:
            name, ver = pkgmgr.install("secretlib", project_dir=self.proj, cache_dir=self.cache)
            self.assertEqual((name, ver), ("secretlib", "1.0.0"))
        finally:
            os.environ.pop("IMPOR_TEST_TOKEN", None)

    def test_missing_token_fails(self):
        os.environ.pop("IMPOR_TEST_TOKEN", None)
        with self.assertRaises(Exception):
            pkgmgr.install("secretlib", project_dir=self.proj, cache_dir=self.cache)


if __name__ == "__main__":
    unittest.main(verbosity=2)
