# -*- coding: utf-8 -*-
"""临时诊断：复现 HTTP publish + outdated。"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests import test_v52 as t
from impor import pkgmgr
import http.server, threading

td = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "impor_dbg_td")
import shutil
shutil.rmtree(td, ignore_errors=True)
os.makedirs(td)
pkgmgr.CACHE_DIR = os.path.join(td, "cache")
src = t._make_pkg(os.path.join(td, "mylib_src"), "mylib", "1.0.0")
proj = t._project(os.path.join(td, "proj"), "file://" + os.path.join(td, "reg"))
t._StoreHandler.store = {}
t._StoreHandler.puts = []
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), t._StoreHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = "http://127.0.0.1:%d" % srv.server_address[1]
try:
    pkgmgr.publish(src, registry_url=base, token="t")
    newer = t._make_pkg(os.path.join(td, "mylib_new"), "mylib", "1.1.0")
    pkgmgr.publish(newer, registry_url=base, token="t")
    t._project(proj, base)
    pkgmgr.CACHE_DIR = os.path.join(td, "cache")
    pkgmgr.install("mylib", project_dir=proj, cache_dir=os.path.join(td, "cache"))
    print("lock:", pkgmgr._read_lock(proj))
    print("deps:", pkgmgr._deps_from_toml(proj))
    print("cfg:", pkgmgr._registry_config(proj))
    print("idx:", pkgmgr._registry_index(base, "t"))
    print("outdated:", pkgmgr.outdated(project_dir=proj, cache_dir=os.path.join(td, "cache"), base=base, token="t"))
finally:
    srv.shutdown()
    shutil.rmtree(td, ignore_errors=True)
