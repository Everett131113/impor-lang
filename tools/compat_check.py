# -*- coding: utf-8 -*-
"""3.11 兼容性检查：20 个已发布示例必须在最新版全部通过。

fixture 的 SHA256 冻结在 manifest.json；改动 fixture 本身会让检查失败，
避免“把测试改成能过”而不是修兼容性。

用法：
  python tools/compat_check.py --init   # 首次写 manifest（仅 3.11 基线）
  python tools/compat_check.py          # CI：哈希 + 逐个 run，全过才 0
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join(ROOT, "conformance", "compat_3_11")
MANIFEST = os.path.join(DIR, "manifest.json")
IMPOR_PY = os.path.join(ROOT, "Impor.py")


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def files():
    return sorted(n for n in os.listdir(DIR) if n.endswith(".impor"))


def build_manifest():
    with open(os.path.join(ROOT, "VERSION"), encoding="utf-8") as f:
        version = f.read().strip()
    return {
        "schema": 1,
        "frozen_at_version": version,
        "note": "3.11 发布线冻结示例；不得为了让检查通过而修改这些文件。",
        "files": {n: sha(os.path.join(DIR, n)) for n in files()},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--timeout", type=int, default=30)
    args = ap.parse_args()
    if args.init:
        if os.path.exists(MANIFEST):
            print("manifest 已存在；兼容性 fixture 不应被重写。")
            return 1
        with open(MANIFEST, "w", encoding="utf-8", newline="\n") as f:
            json.dump(build_manifest(), f, ensure_ascii=False, indent=2)
            f.write("\n")
        print("已冻结 %d 个 3.11 示例" % len(files()))
        return 0
    if not os.path.exists(MANIFEST):
        print("缺少 manifest.json；先运行 --init")
        return 1
    with open(MANIFEST, encoding="utf-8") as f:
        manifest = json.load(f)
    expected = manifest.get("files", {})
    actual = {n: sha(os.path.join(DIR, n)) for n in files()}
    if actual != expected:
        print("兼容性 fixture 被修改；这会让“兼容性”失去意义：")
        for n in sorted(set(expected) | set(actual)):
            if expected.get(n) != actual.get(n):
                print("  - %s" % n)
        return 1
    failures = []
    for n in files():
        path = os.path.join(DIR, n)
        proc = subprocess.run(
            [sys.executable, IMPOR_PY, "run", path],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", timeout=args.timeout,
        )
        status = "OK" if proc.returncode == 0 else "FAIL(rc=%d)" % proc.returncode
        print("%-32s %s" % (n, status))
        if proc.returncode != 0:
            failures.append((n, proc.stdout[-800:]))
    print("\n通过 %d/%d" % (len(files()) - len(failures), len(files())))
    for n, out in failures:
        print("---- %s ----\n%s" % (n, out))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
