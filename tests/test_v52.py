# -*- coding: utf-8 -*-
"""v4.1 私有源写入闭环：pack / publish / outdated / prune / HTTP PUT。"""
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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from impor import pkgmgr


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _make_pkg(dirpath, name, version, requires=None):
    _write(os.path.join(dirpath, "impor.toml"),
           '[project]\nname = "%s"\nversion = "%s"\n' % (name, version))
    _write(os.path.join(dirpath, "lib.impor"), 'def hello() {\n    return "%s"\n}\n' % name)
    if requires:
        with io.open(os.path.join(dirpath, "impor.toml"), "a", encoding="utf-8") as f:
            f.write("\n[dependencies]\n")
            for k, v in requires.items():
                f.write('%s = "%s"\n' % (k, v))
    return dirpath


def _project(root, registry=None):
    lines = ['[project]', 'name = "proj"', 'version = "0.1.0"', '']
    if registry:
        lines += ['[registry]', 'url = "%s"' % registry, 'token_env = "IMPOR_TEST_TOKEN"', '']
    _write(os.path.join(root, "impor.toml"), "\n".join(lines))
    return root


class _StoreHandler(http.server.BaseHTTPRequestHandler):
    store = {}
    puts = []

    def do_GET(self):
        body = self.store.get(urllib.parse.urlparse(self.path).path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_PUT(self):
        path = urllib.parse.urlparse(self.path).path
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.store[path] = body
        self.puts.append(path)
        self.send_response(201)
        self.end_headers()

    def log_message(self, *args):
        pass


class TestPublish(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp(prefix="impor_v52_",
                                   dir=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.old_cache = pkgmgr.CACHE_DIR
        self.cache = os.path.join(self.td, "cache")
        pkgmgr.CACHE_DIR = self.cache
        self.reg = os.path.join(self.td, "registry")
        self.proj = _project(os.path.join(self.td, "proj"), "file://" + self.reg.replace(os.sep, "/"))
        self.src = _make_pkg(os.path.join(self.td, "mylib_src"), "mylib", "1.0.0")

    def tearDown(self):
        pkgmgr.CACHE_DIR = self.old_cache
        shutil.rmtree(self.td, ignore_errors=True)

    def test_pack_is_deterministic_and_has_root_toml(self):
        z1, sha1, name, ver = pkgmgr.pack(self.src, out=os.path.join(self.td, "a.zip"))
        z2, sha2, _n, _v = pkgmgr.pack(self.src, out=os.path.join(self.td, "b.zip"))
        self.assertEqual((name, ver), ("mylib", "1.0.0"))
        self.assertEqual(sha1, sha2)
        import zipfile
        with zipfile.ZipFile(z1) as z:
            self.assertIn("impor.toml", z.namelist())
            self.assertIn("lib.impor", z.namelist())

    def test_publish_local_registry_then_install(self):
        name, ver, url, sha = pkgmgr.publish(self.src, project_dir=self.proj)
        self.assertEqual((name, ver), ("mylib", "1.0.0"))
        idx_path = os.path.join(self.reg, "index.json")
        self.assertTrue(os.path.isfile(idx_path))
        with io.open(idx_path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["packages"]["mylib"]["1.0.0"]["sha256"], sha)
        self.assertTrue(os.path.isfile(os.path.join(self.reg, "mylib-1.0.0.zip")))
        n, v = pkgmgr.install("mylib", project_dir=self.proj, cache_dir=self.cache)
        self.assertEqual((n, v), ("mylib", "1.0.0"))

    def test_publish_duplicate_requires_force(self):
        pkgmgr.publish(self.src, project_dir=self.proj)
        with self.assertRaises(ValueError):
            pkgmgr.publish(self.src, project_dir=self.proj)
        pkgmgr.publish(self.src, project_dir=self.proj, force=True)

    def test_publish_http_put_and_outdated(self):
        _StoreHandler.store = {}
        _StoreHandler.puts = []
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _StoreHandler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = "http://127.0.0.1:%d" % srv.server_address[1]
        try:
            pkgmgr.publish(self.src, registry_url=base, token="t")
            self.assertIn("/index.json", _StoreHandler.puts)
            self.assertIn("/mylib-1.0.0.zip", _StoreHandler.puts)
            _project(self.proj, base)
            pkgmgr.CACHE_DIR = self.cache
            pkgmgr.install("mylib", project_dir=self.proj, cache_dir=self.cache)
            newer = _make_pkg(os.path.join(self.td, "mylib_new"), "mylib", "1.1.0")
            pkgmgr.publish(newer, registry_url=base, token="t")
            rows = pkgmgr.outdated(project_dir=self.proj, cache_dir=self.cache, base=base, token="t")
            self.assertEqual(rows[0][0], "mylib")
            self.assertEqual(rows[0][1], "1.0.0")
            self.assertEqual(rows[0][2], "1.1.0")
        finally:
            srv.shutdown()

    def test_prune_removes_unreferenced_cache(self):
        pkgmgr.install(self.src, project_dir=self.proj, cache_dir=self.cache)
        orphan = os.path.join(self.cache, "orphan")
        _make_pkg(orphan, "orphan", "9.9.9")
        removed = pkgmgr.prune(project_dir=self.proj, cache_dir=self.cache)
        self.assertIn("orphan", removed)
        self.assertFalse(os.path.isdir(orphan))


if __name__ == "__main__":
    unittest.main()
