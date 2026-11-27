#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Impor 离线包索引 + 版本约束求解（4.7.1）。

`impor pkg` 原本只认单个远端 index.json，并且安装期冲突判定是「约束字符串不同就报
冲突」。本工具补上不依赖网络的那一层：

  index   扫描目录里的 *.zip，产出带 sha256 / size / requires 的索引文件
  verify  重新计算每个包的哈希，找出「索引说 A、文件已是 B」的篡改与缺失
  resolve 用 impor.versionsolve 求解一组根依赖，输出钉死的版本与冲突解释
  selftest 造一套真实 zip 包，跑 index→resolve→verify→篡改检测 的端到端断言

质量线（写进 docs/包索引与版本约束.md）：
  - 索引条目必须带 sha256 与 size，`verify` 才算通过；
  - 索引不含签名，**不是**防篡改信任链：它只保证「同一次发布里哈希稳定」，
    真正的信任要靠发布包整体签名或 HTTPS 源，这一点在文档里明确写清。
"""

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from impor import versionsolve                     # noqa: E402
from impor.pkgmgr import _toml_meta_from_text      # noqa: E402
from impor.config import CONFIG_NAME               # noqa: E402

FORMAT = "impor-pkg-index/1"


def _file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_zip_meta(zip_path):
    """读 zip 内的 impor.toml → (name, version, requires)；读不出返回 None。"""
    try:
        with zipfile.ZipFile(zip_path) as z:
            cands = [n for n in z.namelist() if n.endswith(CONFIG_NAME)]
            if not cands:
                return None
            cands.sort(key=lambda n: n.count("/"))
            data = _toml_meta_from_text(z.read(cands[0]).decode("utf-8", "replace"))
    except Exception:
        return None
    proj = data.get("project") or {}
    name = str(proj.get("name") or "").strip()
    ver = str(proj.get("version") or "").strip()
    if not name or not ver:
        return None
    return name, ver, dict(data.get("dependencies") or {})


def build_index(pkg_dir, base_rel=None):
    packages = {}
    skipped = []
    for root, _dirs, files in os.walk(pkg_dir):
        for fn in sorted(files):
            if not fn.lower().endswith(".zip"):
                continue
            path = os.path.join(root, fn)
            meta = _read_zip_meta(path)
            if not meta:
                skipped.append({"file": os.path.relpath(path, pkg_dir),
                                "reason": "读不出 impor.toml 里的 project.name/version"})
                continue
            name, ver, requires = meta
            rel = os.path.relpath(path, pkg_dir).replace("\\", "/")
            packages.setdefault(name, {"versions": {}})
            vkey = versionsolve.norm_ver(ver)
            vstr = ".".join(str(x) for x in vkey)
            if vstr in packages[name]["versions"]:
                skipped.append({"file": rel,
                                "reason": "同一版本 %s 已在索引里（后出现的忽略）" % vstr})
                continue
            packages[name]["versions"][vstr] = {
                "file": rel,
                "sha256": _file_sha256(path),
                "size": os.path.getsize(path),
                "requires": requires,
            }
    return {
        "format": FORMAT,
        "generated_by": "tools/pkg_index.py",
        "root": base_rel or os.path.abspath(pkg_dir),
        "note": "sha256 只保证本索引与文件一致，不含签名；信任来自发布渠道本身。",
        "packages": packages,
        "skipped": skipped,
    }


def verify_index(pkg_dir, index, check_orphans=True):
    issues = []
    listed = set()
    for name, entry in (index.get("packages") or {}).items():
        for ver, meta in (entry.get("versions") or {}).items():
            rel = meta.get("file") or ""
            path = os.path.join(pkg_dir, rel.replace("/", os.sep))
            listed.add(os.path.normcase(os.path.abspath(path)))
            if not os.path.isfile(path):
                issues.append({"file": rel, "kind": "缺失", "detail": "索引里有、磁盘上没有"})
                continue
            got_sha = _file_sha256(path)
            if got_sha != meta.get("sha256"):
                issues.append({"file": rel, "kind": "哈希不符",
                               "detail": "索引 %s… / 实际 %s…"
                                         % (str(meta.get('sha256'))[:12], got_sha[:12])})
            if os.path.getsize(path) != meta.get("size"):
                issues.append({"file": rel, "kind": "大小不符",
                               "detail": "索引 %s / 实际 %s"
                                         % (meta.get("size"), os.path.getsize(path))})
            meta2 = _read_zip_meta(path)
            if not meta2:
                issues.append({"file": rel, "kind": "包损坏", "detail": "读不出 impor.toml"})
                continue
            name2, ver2, _req = meta2
            if name2 != name or versionsolve.norm_ver(ver2) != versionsolve.norm_ver(ver):
                issues.append({"file": rel, "kind": "身份不符",
                               "detail": "索引 %s@%s / 实际 %s@%s" % (name, ver, name2, ver2)})
    if check_orphans:
        for root, _dirs, files in os.walk(pkg_dir):
            for fn in files:
                if not fn.lower().endswith(".zip"):
                    continue
                p = os.path.abspath(os.path.join(root, fn))
                if os.path.normcase(p) not in listed:
                    issues.append({"file": os.path.relpath(p, pkg_dir).replace("\\", "/"),
                                   "kind": "未索引", "detail": "磁盘上有、索引里没有"})
    return issues


def pool_from_index(index):
    """索引 → versionsolve.solve 要的 pool：{包: {版本: {"requires": {依赖: 约束}}}}。"""
    out = {}
    for name, entry in (index.get("packages") or {}).items():
        out[name] = {}
        for ver, meta in (entry.get("versions") or {}).items():
            out[name][ver] = {"requires": dict(meta.get("requires") or {})}
    return out


def _write_json(path, data):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


# ---------------- selftest ----------------
def _make_pkg(dest_dir, name, ver, requires=None):
    d = tempfile.mkdtemp(prefix="pidx_")
    with open(os.path.join(d, CONFIG_NAME), "w", encoding="utf-8") as f:
        f.write('[project]\nname = "%s"\nversion = "%s"\n' % (name, ver))
        if requires:
            f.write("\n[dependencies]\n")
            for k, v in requires.items():
                f.write('%s = "%s"\n' % (k, v))
    lib = os.path.join(d, "lib")
    os.makedirs(lib, exist_ok=True)
    with open(os.path.join(lib, "x.impor"), "w", encoding="utf-8") as f:
        f.write('important<<DAY<<english("English"):\n\ndef ping() {\n    return 1;\n}\n')
    out = os.path.join(dest_dir, "%s-%s.zip" % (name, ver))
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _dirs, files in os.walk(d):
            for fn in files:
                p = os.path.join(root, fn)
                z.write(p, os.path.relpath(p, d).replace("\\", "/"))
    shutil.rmtree(d, ignore_errors=True)
    return out


def selftest():
    fails = []
    base = os.path.join(tempfile.gettempdir(), "irverify", "pidx_test")
    shutil.rmtree(base, ignore_errors=True)
    os.makedirs(base, exist_ok=True)
    spec = {
        "a": {"1.0.0": {"c": ">=1.0,<2.0"}, "1.5.0": {"c": ">=1.2,<2.0"}},
        "b": {"1.0.0": {"c": "<1.5"}, "1.2.0": {"c": ">=1.0"}},
        "c": {"1.0.0": {}, "1.2.0": {}, "1.4.0": {}},
    }
    for name, vers in spec.items():
        for ver, req in vers.items():
            _make_pkg(base, name, ver, req)

    idx = build_index(base)
    if len(idx["packages"]) != 3:
        fails.append("索引包数应为 3，实际 %d" % len(idx["packages"]))
    total = sum(len(v["versions"]) for v in idx["packages"].values())
    if total != 7:
        fails.append("索引版本数应为 7，实际 %d" % total)
    if idx["skipped"]:
        fails.append("首次建索引不应有跳过项，实际 %s" % idx["skipped"])
    for name, v in idx["packages"].items():
        for ver, meta in v["versions"].items():
            if not meta["sha256"] or len(meta["sha256"]) != 64:
                fails.append("%s@%s 缺少合法 sha256" % (name, ver))

    idx_path = os.path.join(base, "pkg-index.json")
    _write_json(idx_path, idx)

    # resolve：两包对 c 的约束取交集
    res = versionsolve.solve({"a": "^1.0", "b": "^1.0"}, pool_from_index(idx))
    if not res.get("ok"):
        fails.append("求解应成功，实际失败：%s" % res.get("reason"))
    else:
        for k, want in (("a", "1.5.0"), ("b", "1.2.0"), ("c", "1.4.0")):
            if res["pins"].get(k) != want:
                fails.append("pin %s 期望 %s，实际 %s" % (k, want, res["pins"].get(k)))

    # 冲突：要求 c<1.5 又要求 c>=1.4 仍可（1.4.0），但 a 1.5.0 要 c>=1.2 且 b 1.0.0 要 c<1.5 → 1.2/1.4 都行
    issues = verify_index(base, idx)
    if issues:
        fails.append("未篡改时 verify 应为 0 问题，实际 %s" % issues)

    # 篡改：改一个 zip 的一个字节（重写进一个新条目，模拟被替换）
    victim = os.path.join(base, "c-1.4.0.zip")
    with open(victim, "ab") as f:
        f.write(b"X")
    issues = verify_index(base, idx)
    kinds = {i["kind"] for i in issues}
    if "哈希不符" not in kinds:
        fails.append("篡改后应报哈希不符，实际 %s" % kinds)
    if len(issues) < 2:
        fails.append("篡改后应同时报大小不符，实际只有 %s" % issues)

    # 未索引文件
    _make_pkg(base, "d", "9.9.9")
    issues2 = verify_index(base, idx, check_orphans=True)
    if not any(i["kind"] == "未索引" for i in issues2):
        fails.append("新增未索引 zip 应被报出来")

    print("== 索引 ==")
    print("包 %d / 版本 %d / 跳过 %d" % (len(idx["packages"]),
                                       total, len(idx["skipped"])))
    for name in sorted(idx["packages"]):
        vers = sorted(idx["packages"][name]["versions"], key=versionsolve.norm_ver)
        print("  %s: %s" % (name, ", ".join(vers)))
    print("== 求解 ==")
    print(versionsolve.explain(res))
    print("== 篡改检测 ==")
    for i in issues:
        print("  [%s] %s：%s" % (i["kind"], i["file"], i["detail"]))
    for i in issues2:
        if i["kind"] == "未索引":
            print("  [未索引] %s" % i["file"])
    if fails:
        print("SELFTEST FAIL")
        for f in fails:
            print("  - " + f)
        return 1
    print("SELFTEST PASS")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Impor 离线包索引与版本约束求解")
    sub = ap.add_subparsers(dest="cmd")
    p_idx = sub.add_parser("index", help="扫描 *.zip 生成索引")
    p_idx.add_argument("--dir", required=True)
    p_idx.add_argument("--out", default="pkg-index.json")
    p_ver = sub.add_parser("verify", help="校验索引与文件哈希")
    p_ver.add_argument("--dir", required=True)
    p_ver.add_argument("--index", required=True)
    p_res = sub.add_parser("resolve", help="求解根依赖")
    p_res.add_argument("--index", required=True)
    p_res.add_argument("--require", action="append", default=[],
                       help="形如 name^1.2 或 name>=1.0,<2.0，可重复")
    sub.add_parser("selftest", help="端到端自测")
    args = ap.parse_args(argv)

    if args.cmd == "index":
        idx = build_index(args.dir)
        _write_json(args.out, idx)
        print("索引已写入 %s：%d 个包 / %d 个版本 / 跳过 %d"
              % (args.out, len(idx["packages"]),
                 sum(len(v["versions"]) for v in idx["packages"].values()),
                 len(idx["skipped"])))
        for s in idx["skipped"]:
            print("  跳过 %s：%s" % (s["file"], s["reason"]))
        return 0

    if args.cmd == "verify":
        with open(args.index, encoding="utf-8") as f:
            idx = json.load(f)
        issues = verify_index(args.dir, idx)
        if not issues:
            print("校验通过：%d 个包全部一致"
                  % sum(len(v["versions"]) for v in (idx.get("packages") or {}).values()))
            return 0
        print("发现 %d 处问题：" % len(issues))
        for i in issues:
            print("  [%s] %s：%s" % (i["kind"], i["file"], i["detail"]))
        return 1

    if args.cmd == "resolve":
        with open(args.index, encoding="utf-8") as f:
            idx = json.load(f)
        roots = {}
        for spec in args.require:
            for op in (">=", "<=", "==", "!=", "^", "~", ">", "<"):
                if op in spec:
                    name, cons = spec.split(op, 1)
                    roots[name.strip()] = op + cons.strip()
                    break
            else:
                roots[spec.strip()] = "*"
        res = versionsolve.solve(roots, pool_from_index(idx))
        print(versionsolve.explain(res))
        return 0 if res.get("ok") else 1

    if args.cmd == "selftest":
        return selftest()

    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
