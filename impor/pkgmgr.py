# -*- coding: utf-8 -*-
"""
Impor (I++) 包管理器（impor pkg）：本地源码包 + git 仓库来源。

命令：
  impor pkg install <源>        安装（本地目录 / git URL / 私有源包名@版本；name=约束 需已声明依赖）
  impor pkg install --frozen    按 impor.lock 还原并逐项校验哈希（CI/复现构建）
  impor pkg remove <name>       卸载
  impor pkg list                列出项目依赖与已安装包
  impor pkg lock                生成/刷新 impor.lock 锁文件
  impor pkg lock --check        校验缓存内容与锁文件是否一致
  impor pkg upgrade [name]      升级全部或指定包（git/私有源重新拉取）
  impor pkg registry            显示当前私有源配置（不打印令牌）

包格式：目录内含 impor.toml（[project] name/version）+ .impor 源码。
缓存：~/.impor/pkg/<name>；运行时会自动加入模块搜索路径。
锁文件：项目根 impor.lock（TOML 子集），记录版本/来源/目录哈希/归档 sha256/依赖。
私有源：impor.toml [registry] url + token_env（默认读取 IMPOR_TOKEN），
        索引为 <url>/index.json，包归档为 zip，必须提供 sha256。
版本约束：>=1.2.0 / <= / > / < / == / != 或裸版本号（精确匹配）。
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile

try:
    import tomllib
except ImportError:  # Python < 3.11
    tomllib = None

from .config import CONFIG_NAME, _SimpleToml, pkg_cache_dir
from .observability import note_swallowed
from . import versionsolve

REGISTRY_ENV = "IMPOR_REGISTRY"
TOKEN_ENV_DEFAULT = "IMPOR_TOKEN"
LOCK_VERSION = "1.1"

def _posix_cache_dir(xdg_home):
    """POSIX（Linux/macOS）缓存目录：XDG Base Directory 规范。"""
    base = (xdg_home if xdg_home and os.path.isabs(xdg_home)
            else os.path.join(os.path.expanduser("~"), ".cache"))
    return os.path.join(base, "impor", "pkg")


def _default_cache_dir():
    """跨平台包缓存目录（统一走 config.pkg_cache_dir）。"""
    return pkg_cache_dir()


CACHE_DIR = _default_cache_dir()
LOCK_NAME = "impor.lock"


# ---------- 版本工具 ----------
def _norm_ver(v):
    v = str(v).strip().lstrip("vV")
    parts = []
    for seg in v.split("."):
        digits = ""
        for ch in seg:
            if ch.isdigit():
                digits += ch
            else:
                break
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def _ver_str(v):
    return ".".join(str(x) for x in v)


def parse_constraint(spec):
    """'>=1.2' → ('>=', (1,2,0))；'1.2' → ('==', (1,2,0))。"""
    spec = (spec or "").strip()
    for op in (">=", "<=", "==", "!=", ">", "<"):
        if spec.startswith(op):
            return op, _norm_ver(spec[len(op):].strip())
    if spec:
        return "==", _norm_ver(spec)
    return None, None


def ver_satisfies(ver, constraint):
    """ver 是否满足 constraint。

    4.7.1：交给 `versionsolve.satisfies`，因此支持 `^1.2` / `~1.2` / `>=1.0,<2.0`
    这类多段合取（原先只认单个比较符，`^`/`~` 会被当成裸版本号按 `==` 匹配）。
    """
    try:
        return versionsolve.satisfies(ver, constraint)
    except ValueError:
        return False


# ---------- TOML 读写 ----------
def _read_toml(path):
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    if tomllib is not None:
        try:
            return tomllib.loads(text)
        except Exception:
            return _SimpleToml.loads(text)
    return _SimpleToml.loads(text)


def _write_toml(path, data):
    """写回极简 TOML（保持现有注释/顺序丢失可接受，v1 简单实现）。"""
    lines = []
    for section, kv in data.items():
        if not isinstance(kv, dict):
            continue
        lines.append(f"[{section}]")
        for k, v in kv.items():
            lines.append(f"{k} = {_toml_value(v)}")
        lines.append("")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines).rstrip() + "\n")


def _toml_value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


# ---------- 项目定位 ----------
def _project_dir(start=None):
    """向上找 impor.toml 的项目根；找不到返回 cwd。"""
    d = os.path.abspath(start or os.getcwd())
    while True:
        if os.path.isfile(os.path.join(d, CONFIG_NAME)):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return os.path.abspath(start or os.getcwd())
        d = parent


def _has_project(proj_dir):
    """proj_dir 下是否真的有 impor.toml（区分"项目内"与"退回 cwd"）。"""
    return os.path.isfile(os.path.join(proj_dir, CONFIG_NAME))


def _ensure_path_entry(proj_dir, entry):
    """确保 impor.toml 的 [module_search_paths] paths 里含 entry。

    按行编辑：保留注释、空行与原有顺序（不用 _write_toml 整篇重写）。
    返回 True 表示做了修改。
    """
    path = os.path.join(proj_dir, CONFIG_NAME)
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return False

    sec_start = None
    for i, ln in enumerate(lines):
        if ln.strip() == "[module_search_paths]":
            sec_start = i
            break

    if sec_start is not None:
        sec_end = len(lines)
        for j in range(sec_start + 1, len(lines)):
            st = lines[j].strip()
            if st.startswith("[") and st.endswith("]"):
                sec_end = j
                break
        for j in range(sec_start + 1, sec_end):
            st = lines[j].strip()
            if st.startswith("paths") and "=" in st and "[" in st:
                inner = st.split("[", 1)[1].rsplit("]", 1)[0]
                items = [x.strip().strip("\"'") for x in inner.split(",") if x.strip()]
                if entry.lstrip("./") in {x.lstrip("./") for x in items}:
                    return False
                items.append(entry)
                lines[j] = "paths = [" + ", ".join('"%s"' % x for x in items) + "]"
                break
        else:
            lines.insert(sec_end, 'paths = ["%s"]' % entry)
    else:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("[module_search_paths]")
        lines.append('paths = ["%s"]' % entry)

    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    return True


def _vendor_pkg(name, proj_dir, cache_dir=None):
    """把缓存里的包复制到 <project>/lib/<name>/，并保证 ./lib 在搜索路径里。

    返回 (目标目录, 建议 import 名或 None, 是否额外加了 ./lib/<name>)。
    """
    cache_dir = cache_dir or CACHE_DIR
    src = os.path.join(cache_dir, name)
    if not os.path.isdir(src):
        return None, None, False
    lib_dir = os.path.join(proj_dir, "lib")
    os.makedirs(lib_dir, exist_ok=True)
    dest = os.path.join(lib_dir, name)
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    _ensure_path_entry(proj_dir, "./lib")

    # 判断装完能不能直接 import：
    #   1) 有 __pkg__.impor → 以 ./lib 为根，import <name>
    #   2) 有 <name>.impor   → 以 ./lib/<name> 为根，import <name>
    #   3) 只有一个顶层模块 → 以 ./lib/<name> 为根，import <那个模块名>
    extra_root = False
    if os.path.isfile(os.path.join(dest, "__pkg__.impor")):
        return dest, name, extra_root
    if os.path.isfile(os.path.join(dest, name + ".impor")):
        _ensure_path_entry(proj_dir, "./lib/" + name)
        extra_root = True
        return dest, name, extra_root
    mods = sorted(fn[:-6] for fn in os.listdir(dest) if fn.endswith(".impor"))
    if len(mods) == 1:
        _ensure_path_entry(proj_dir, "./lib/" + name)
        extra_root = True
        return dest, mods[0], extra_root
    return dest, None, extra_root



def _pkg_meta(pkg_dir):
    """读取包内 impor.toml 元信息，返回 (name, version) 或抛异常。"""
    toml_path = os.path.join(pkg_dir, CONFIG_NAME)
    if not os.path.isfile(toml_path):
        raise ValueError(f"{pkg_dir} 不是 Impor 包（缺少 {CONFIG_NAME}）")
    data = _read_toml(toml_path)
    proj = data.get("project") or {}
    name = proj.get("name")
    if not name:
        raise ValueError(f"{pkg_dir} 的 {CONFIG_NAME} 缺少 [project] name")
    return str(name), str(proj.get("version", "0.0.0"))


# ---------- 安装源 ----------
def _is_git_source(spec):
    s = spec.strip().lower()
    return s.endswith(".git") or s.startswith("git+") or s.startswith("git://")


def _git_clone(url, dest):
    url = url[4:] if url.lower().startswith("git+") else url
    try:
        r = subprocess.run(["git", "clone", "--depth", "1", url, dest],
                           capture_output=True, text=True, timeout=180)
    except FileNotFoundError:
        raise ValueError("需要 git 命令：请安装 git 后重试（https://git-scm.com）")
    if r.returncode != 0:
        raise ValueError(f"git clone 失败: {r.stderr.strip() or r.stdout.strip()}")


def _install_from_source(spec, cache_dir):
    """把源安装进缓存，返回 (name, version, source_desc)。"""
    if _is_git_source(spec):
        tmp = cache_dir + ".tmp_" + str(int(time.time()))
        try:
            _git_clone(spec, tmp)
            name, ver = _pkg_meta(tmp)
            dest = os.path.join(cache_dir, name)
            if os.path.isdir(dest):
                shutil.rmtree(dest)
            shutil.move(tmp, dest)
            return name, ver, spec
        finally:
            if os.path.isdir(tmp):
                shutil.rmtree(tmp, ignore_errors=True)
    # 本地目录 / 相对路径
    local = os.path.abspath(spec)
    if not os.path.isdir(local):
        raise ValueError(f"找不到包源: {spec}（支持本地目录或 git URL）")
    name, ver = _pkg_meta(local)
    dest = os.path.join(cache_dir, name)
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    shutil.copytree(local, dest, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    return name, ver, local


def _dir_sha256(path):
    h = hashlib.sha256()
    for root, dirs, files in os.walk(path):
        dirs[:] = sorted(d for d in dirs if d not in (".git", "__pycache__"))
        for fn in sorted(files):
            fp = os.path.join(root, fn)
            rel = os.path.relpath(fp, path).replace(os.sep, "/")
            h.update(b"F" + rel.encode("utf-8", "replace"))
            try:
                with open(fp, "rb") as f:
                    h.update(f.read(1 << 20))
            except OSError:
                note_swallowed("lock hash")
    return h.hexdigest()


def _dir_hash(path):
    return _dir_sha256(path)[:12]


def _registry_config(proj_dir):
    """返回 (registry_url, token)。环境变量优先，令牌不打印。"""
    cfg = _read_toml(os.path.join(proj_dir, CONFIG_NAME))
    reg = cfg.get("registry") or {}
    url = os.environ.get(REGISTRY_ENV) or str(reg.get("url") or "").strip()
    token_env = str(reg.get("token_env") or TOKEN_ENV_DEFAULT)
    token = os.environ.get(token_env) or str(reg.get("token") or "")
    return url.rstrip("/"), token


def _is_remote_url(s):
    return str(s).lower().startswith(("http://", "https://", "file://"))


def _read_bytes(url, token=""):
    if _is_remote_url(url) and str(url).lower().startswith(("http://", "https://")):
        req = urllib.request.Request(url, headers={"Accept": "application/json, application/zip"})
        if token:
            req.add_header("Authorization", "Bearer " + token)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            hint = "（检查 IMPOR_TOKEN / token_env 是否有访问权限）" if e.code in (401, 403) else ""
            raise ValueError(f"私有源请求失败 HTTP {e.code}: {url}{hint}")
    path = url[7:] if str(url).lower().startswith("file://") else url
    with open(path, "rb") as f:
        return f.read()


def _join_url(base, rel):
    if _is_remote_url(rel):
        return rel
    if str(base).lower().startswith("file://"):
        path = base[7:]
        return os.path.join(path, rel.replace("/", os.sep))
    if _is_remote_url(base):
        return urllib.parse.urljoin(base + "/", rel)
    path = base
    return os.path.join(path, rel.replace("/", os.sep))


def _registry_index(base, token=""):
    if not base:
        raise ValueError("未配置私有源：请在 impor.toml 添加 [registry] url，或设置 IMPOR_REGISTRY")
    data = json.loads(_read_bytes(_join_url(base, "index.json"), token).decode("utf-8"))
    pkgs = data.get("packages") if isinstance(data, dict) else None
    if not isinstance(pkgs, dict):
        raise ValueError("私有源 index.json 缺少 packages 对象")
    return pkgs


def _read_bytes_optional(url, token=""):
    """读索引；HTTP 404 视为空源，其他错误照常抛出。"""
    try:
        return _read_bytes(url, token)
    except ValueError as e:
        if "HTTP 404" in str(e):
            return None
        raise


def _write_bytes(url, data, token="", content_type="application/octet-stream"):
    """写远端（HTTP PUT）或本地文件；私有源必须支持 PUT 归档与 index.json。"""
    if str(url).lower().startswith(("http://", "https://")):
        req = urllib.request.Request(url, data=data, method="PUT")
        req.add_header("Content-Type", content_type)
        if token:
            req.add_header("Authorization", "Bearer " + token)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            hint = "（检查源是否允许 PUT 与 IMPOR_TOKEN 写权限）" if e.code in (401, 403, 405) else ""
            raise ValueError(f"私有源写入失败 HTTP {e.code}: {url}{hint}")
    path = url[7:] if str(url).lower().startswith("file://") else url
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return b""


def _zip_pkg_dir(pkg_dir, zip_path):
    """确定性打包：固定时间戳、排序、排除缓存/版本库；同内容必然同哈希。"""
    if not os.path.isfile(os.path.join(pkg_dir, CONFIG_NAME)):
        raise ValueError(f"{pkg_dir} 不是包目录（缺少 {CONFIG_NAME}）")
    os.makedirs(os.path.dirname(os.path.abspath(zip_path)), exist_ok=True)
    skip_dirs = {".git", "__pycache__", ".impor", ".pytest_cache", "dist", "node_modules"}
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(pkg_dir):
            dirs[:] = sorted(d for d in dirs if d not in skip_dirs and not d.startswith("."))
            for fn in sorted(files):
                if fn.endswith((".pyc", ".pyo", ".zip")):
                    continue
                fp = os.path.join(root, fn)
                rel = os.path.relpath(fp, pkg_dir).replace(os.sep, "/")
                info = zipfile.ZipInfo(rel, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                with open(fp, "rb") as f:
                    z.writestr(info, f.read())
    return _file_sha256(zip_path)


def _file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_index_or_empty(base, token):
    try:
        return _registry_index(base, token)
    except (ValueError, FileNotFoundError) as e:
        if isinstance(e, FileNotFoundError) or "HTTP 404" in str(e):
            return {}
        raise


def _write_index(base, packages, token):
    data = json.dumps({"packages": packages}, ensure_ascii=False,
                      indent=2, sort_keys=True).encode("utf-8")
    _write_bytes(_join_url(base, "index.json"), data, token, "application/json")


def pack(package_dir=None, out=None, project_dir=None):
    """把包目录打成可上传的 zip，返回 (zip 路径, sha256, name, version)。"""
    proj_dir = _project_dir(project_dir)
    pkg_dir = os.path.abspath(package_dir or proj_dir)
    name, ver = _pkg_meta(pkg_dir)
    if out is None:
        out = os.path.join(proj_dir, "dist", f"{name}-{ver}.zip")
    sha = _zip_pkg_dir(pkg_dir, out)
    return os.path.abspath(out), sha, name, ver


def publish(package=None, registry_url=None, token=None, project_dir=None,
            force=False, out=None):
    """发布包到私有源：本地目录直接复制，HTTP 源用 PUT 上传归档并更新 index.json。"""
    proj_dir = _project_dir(project_dir)
    base_cfg, token_cfg = _registry_config(proj_dir)
    base = (registry_url or base_cfg).rstrip("/")
    if not base:
        raise ValueError("未配置私有源：请在 impor.toml 添加 [registry] url，或设置 IMPOR_REGISTRY")
    token = token_cfg if token is None else token

    src = os.path.abspath(package or proj_dir)
    if os.path.isdir(src):
        zip_path, sha, name, ver = pack(src, out=out, project_dir=proj_dir)
    elif os.path.isfile(src) and src.lower().endswith(".zip"):
        zip_path, sha = src, _file_sha256(src)
        import tempfile
        with tempfile.TemporaryDirectory(prefix="impor_pub_") as td:
            _safe_extract_zip(zip_path, td)
            root = _package_root(td)
            name, ver = _pkg_meta(root)
    else:
        raise ValueError("publish 需要包目录或 .zip 归档")

    archive_name = f"{name}-{ver}.zip"
    packages = _load_index_or_empty(base, token)
    versions = _normalize_versions(packages.get(name))
    if ver in versions and not force:
        raise ValueError(f"{name} {ver} 已存在于私有源；如需覆盖请加 --force")
    archive_url = _join_url(base, archive_name)
    with open(zip_path, "rb") as f:
        _write_bytes(archive_url, f.read(), token, "application/zip")
    versions[ver] = {"url": archive_name, "sha256": sha}
    packages[name] = versions
    _write_index(base, packages, token)
    return name, ver, archive_url, sha


def outdated(project_dir=None, cache_dir=None, base=None, token=None):
    """列出锁文件里可升级的私有源包，返回 [(name, current, latest, constraint)]。"""
    proj_dir = _project_dir(project_dir)
    entries = _read_lock(proj_dir)
    deps = _deps_from_toml(proj_dir)
    base_cfg, token_cfg = _registry_config(proj_dir)
    base = (base or base_cfg).rstrip("/")
    token = token_cfg if token is None else token
    rows = []
    for name, meta in entries.items():
        cur = str(meta.get("version") or "")
        if not _source_parts(str(meta.get("source") or "")):
            continue
        constraint = deps.get(name) or str(meta.get("requested") or "")
        try:
            info = resolve_registry_package(name, constraint, base=base, token=token,
                                            project_dir=proj_dir)
        except ValueError:
            continue
        latest = str(info.get("version") or "")
        try:
            if latest and cur and _norm_ver(latest) > _norm_ver(cur):
                rows.append((name, cur, latest, constraint))
        except ValueError:
            continue
    return rows


def _prune_unreachable(proj_dir, cache_dir):
    """删除缓存中不被锁文件引用的包，返回删除列表。"""
    entries = _read_lock(proj_dir)
    reachable = set(entries.keys())
    removed = []
    if not os.path.isdir(cache_dir):
        return removed
    for name in sorted(os.listdir(cache_dir)):
        p = os.path.join(cache_dir, name)
        if not os.path.isdir(p) or name in reachable:
            continue
        if not os.path.isfile(os.path.join(p, CONFIG_NAME)):
            continue
        shutil.rmtree(p)
        removed.append(name)
    return removed


def _normalize_versions(entry):
    """index 条目支持 {version: {...}} 或 [{version, url, sha256}]。"""
    if isinstance(entry, list):
        out = {}
        for item in entry:
            if isinstance(item, dict) and item.get("version"):
                out[str(item["version"])] = item
        return out
    if isinstance(entry, dict):
        out = {}
        for ver, info in entry.items():
            if isinstance(info, dict):
                item = dict(info)
                item.setdefault("version", str(ver))
                out[str(ver)] = item
        return out
    return {}


def resolve_registry_package(name, constraint="", base=None, token=None, project_dir=None):
    """从私有源挑选满足约束的最高版本，返回 {version,url,sha256}。"""
    proj_dir = project_dir or _project_dir()
    base_cfg, token_cfg = _registry_config(proj_dir)
    base = (base or base_cfg).rstrip("/")
    token = token_cfg if token is None else token
    pkgs = _registry_index(base, token)
    versions = _normalize_versions(pkgs.get(name))
    if not versions:
        raise ValueError(f"私有源中找不到包: {name}")
    ordered = sorted(versions.keys(), key=_norm_ver, reverse=True)
    for ver in ordered:
        if ver_satisfies(ver, constraint):
            info = versions[ver]
            sha = str(info.get("sha256") or "").lower()
            if not sha:
                raise ValueError(f"{name} {ver} 未提供 sha256，私有源拒绝安装（无法校验完整性）")
            url = _join_url(base, str(info.get("url") or f"{name}-{ver}.zip"))
            return {"version": ver, "url": url, "sha256": sha}
    raise ValueError(f"{name} 没有满足约束 {constraint or '(任意)'} 的版本")


def _safe_extract_zip(zip_path, dest):
    with zipfile.ZipFile(zip_path) as z:
        for member in z.infolist():
            target = os.path.abspath(os.path.join(dest, member.filename))
            if not (target == os.path.abspath(dest) or target.startswith(os.path.abspath(dest) + os.sep)):
                raise ValueError(f"私有源归档包含越界路径: {member.filename}")
        z.extractall(dest)


def _package_root(extract_dir):
    if os.path.isfile(os.path.join(extract_dir, CONFIG_NAME)):
        return extract_dir
    entries = [e for e in os.listdir(extract_dir) if not e.startswith(".")]
    if len(entries) == 1:
        sub = os.path.join(extract_dir, entries[0])
        if os.path.isdir(sub) and os.path.isfile(os.path.join(sub, CONFIG_NAME)):
            return sub
    raise ValueError(f"归档内找不到 {CONFIG_NAME}（应位于 zip 根目录或唯一顶层目录）")


def _install_from_registry(name, constraint, proj_dir, cache_dir, base=None, token=None,
                           expect_version=None, expect_sha=None):
    """下载私有源归档 → 校验 sha256 → 解压安装。返回 (name, ver, source_desc, sha256)。"""
    base_cfg, token_cfg = _registry_config(proj_dir)
    base = (base or base_cfg).rstrip("/")
    token = token_cfg if token is None else token
    if expect_version:
        info = resolve_registry_package(name, "==" + expect_version, base=base, token=token,
                                       project_dir=proj_dir)
    else:
        info = resolve_registry_package(name, constraint, base=base, token=token,
                                        project_dir=proj_dir)
    if expect_sha and info["sha256"] != expect_sha.lower():
        raise ValueError(f"{name} {info['version']} 归档 sha256 与锁文件不一致")
    tmp = tempfile.mkdtemp(prefix="impor_pkgdl_", dir=cache_dir)
    zip_path = os.path.join(tmp, "pkg.zip")
    try:
        payload = _read_bytes(info["url"], token)
        with open(zip_path, "wb") as f:
            f.write(payload)
        got = hashlib.sha256(payload).hexdigest()
        if got != info["sha256"]:
            raise ValueError(f"{name} 归档 sha256 校验失败（期望 {info['sha256'][:12]}…，实际 {got[:12]}…）")
        extract = os.path.join(tmp, "x")
        os.makedirs(extract, exist_ok=True)
        _safe_extract_zip(zip_path, extract)
        root = _package_root(extract)
        pname, ver = _pkg_meta(root)
        if pname != name:
            raise ValueError(f"归档内包名 {pname} 与请求 {name} 不一致")
        if str(ver) != str(info["version"]):
            raise ValueError(f"归档内版本 {ver} 与索引版本 {info['version']} 不一致")
        dest = os.path.join(cache_dir, name)
        if os.path.isdir(dest):
            shutil.rmtree(dest)
        shutil.copytree(root, dest, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        return name, ver, f"registry:{base}#{name}@{ver}", info["sha256"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- 锁文件 ----------
def _lock_path(proj_dir):
    return os.path.join(proj_dir, LOCK_NAME)


def _read_lock_meta(proj_dir):
    data = _read_toml(_lock_path(proj_dir))
    meta = data.get("lock") if isinstance(data.get("lock"), dict) else {}
    return meta


def _read_lock(proj_dir):
    """返回包条目 {name: meta}；忽略 [lock] 元数据段。兼容 v1 扁平格式。"""
    data = _read_toml(_lock_path(proj_dir))
    return {k: v for k, v in data.items() if k != "lock" and isinstance(v, dict)}


def _write_lock(proj_dir, entries, extra=None):
    """写 impor.lock：`[lock]` 元数据 + 每个包一个 `[name]` 段。"""
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    lines = ["[lock]",
             f'version = "{LOCK_VERSION}"',
             f'generated_by = "impor pkg"',
             f'generated_at = "{now}"']
    if extra:
        for k in ("registry", "frozen"):
            if extra.get(k):
                lines.append(f'{k} = {_toml_value(extra[k])}')
    lines.append("")
    for name in sorted(entries):
        meta = entries[name]
        lines.append(f"[{name}]")
        for key in ("version", "source", "hash", "dir_sha256", "sha256", "requested", "installed_at"):
            val = meta.get(key)
            if val is None or val == "":
                continue
            lines.append(f"{key} = {_toml_value(val)}")
        req = meta.get("requires") or []
        lines.append("requires = [" + ", ".join(_toml_value(x) for x in req) + "]")
        if not meta.get("installed", True):
            lines.append("installed = false")
        lines.append("")
    with open(_lock_path(proj_dir), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines).rstrip() + "\n")


def _deps_from_toml(proj_dir):
    cfg = _read_toml(os.path.join(proj_dir, CONFIG_NAME))
    return dict(cfg.get("dependencies") or {})


def _pkg_requires(pkg_dir):
    data = _read_toml(os.path.join(pkg_dir, CONFIG_NAME))
    return dict(data.get("dependencies") or {})


def _check_dep_conflicts(pkg_dir, proj_dir):
    """检测新安装包声明的依赖与项目已有依赖的版本冲突。

    4.7.1：判定从「字符串不同就报」改成**真区间交集**（`versionsolve.ranges_intersect`）。
    `>=1.0` 与 `>=1.0,<2.0` 以前会被报成冲突，其实前者包含后者，装上去完全没问题；
    反过来 `>=1.0,<1.5` 和 `>=2.0` 才是真冲突，现在能识别出来。
    """
    toml_path = os.path.join(pkg_dir, CONFIG_NAME)
    if not os.path.isfile(toml_path):
        return []
    data = _read_toml(toml_path)
    deps = data.get("dependencies") or {}
    existing = _deps_from_toml(proj_dir)
    out = []
    for dep_name, dep_constraint in deps.items():
        old = existing.get(dep_name)
        if old is None:
            continue
        try:
            compatible = versionsolve.ranges_intersect(old, dep_constraint)
        except ValueError:
            compatible = False
        if not compatible:
            out.append((dep_name, old, dep_constraint))
    return out


def _set_dep(proj_dir, name, constraint):
    """按行更新 impor.toml 的 [dependencies]，保留注释与顺序（不整篇重写）。"""
    path = os.path.join(proj_dir, CONFIG_NAME)
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        lines = []
    key = re.escape(name)

    sec_start = None
    for i, ln in enumerate(lines):
        if ln.strip() == "[dependencies]":
            sec_start = i
            break

    if sec_start is None:
        if not constraint:
            return
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("[dependencies]")
        lines.append('%s = "%s"' % (name, constraint))
    else:
        sec_end = len(lines)
        for j in range(sec_start + 1, len(lines)):
            st = lines[j].strip()
            if st.startswith("[") and st.endswith("]"):
                sec_end = j
                break
        hit = None
        for j in range(sec_start + 1, sec_end):
            if re.match(r"\s*%s\s*=" % key, lines[j]):
                hit = j
                break
        if constraint:
            if hit is None:
                lines.insert(sec_end, '%s = "%s"' % (name, constraint))
            else:
                lines[hit] = '%s = "%s"' % (name, constraint)
        elif hit is not None:
            del lines[hit]

    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


def _refresh_lock(proj_dir, cache_dir, new_sources=None, extra=None):
    """递归收集直接+传递依赖，写入版本/来源/目录哈希/归档 sha256。"""
    new_sources = new_sources or {}
    old = _read_lock(proj_dir)
    entries = {}
    seen = set()

    def visit(name, requested, via):
        if name in seen:
            return
        seen.add(name)
        pkg = os.path.join(cache_dir, name)
        meta = new_sources.get(name) or old.get(name) or {}
        source = meta.get("source") or "local"
        archive_sha = meta.get("sha256") or ""
        entry = {"source": source,
                 "requested": requested or "",
                 "sha256": archive_sha,
                 "installed_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        if os.path.isdir(pkg):
            try:
                _, ver = _pkg_meta(pkg)
            except ValueError:
                ver = "0.0.0"
            reqs = _pkg_requires(pkg)
            entry.update({"version": ver, "hash": _dir_hash(pkg),
                          "dir_sha256": _dir_sha256(pkg),
                          "requires": [f"{k}{v}" for k, v in reqs.items()],
                          "installed": True})
            for dep_name, dep_constraint in reqs.items():
                visit(dep_name, dep_constraint, name)
        else:
            entry.update({"version": meta.get("version", ""), "hash": "",
                          "requires": [], "installed": False})
        entries[name] = entry

    for name, constraint in _deps_from_toml(proj_dir).items():
        visit(name, constraint, None)

    registry, _token = _registry_config(proj_dir)
    extra = dict(extra or {})
    if registry:
        extra.setdefault("registry", registry)
    _write_lock(proj_dir, entries, extra)
    return entries


def check_lock(project_dir=None, cache_dir=None):
    """校验锁文件与缓存：返回问题列表；空列表表示一致。"""
    cache_dir = cache_dir or CACHE_DIR
    proj_dir = _project_dir(project_dir)
    if not os.path.isfile(_lock_path(proj_dir)):
        return ["缺少 impor.lock（先运行 impor pkg lock）"]
    entries = _read_lock(proj_dir)
    issues = []
    for name, meta in entries.items():
        pkg = os.path.join(cache_dir, name)
        if not os.path.isdir(pkg):
            issues.append(f"{name}: 未安装（缓存缺少 {pkg}）")
            continue
        if not meta.get("installed", True):
            issues.append(f"{name}: 锁文件标记为未安装")
            continue
        try:
            _, ver = _pkg_meta(pkg)
        except ValueError as e:
            issues.append(f"{name}: 元信息损坏（{e}）")
            continue
        want_ver = str(meta.get("version") or "")
        if want_ver and ver != want_ver:
            issues.append(f"{name}: 版本不一致（锁 {want_ver}，实际 {ver}）")
        want_hash = str(meta.get("hash") or "")
        if want_hash and _dir_hash(pkg) != want_hash:
            issues.append(f"{name}: 内容哈希不一致（锁 {want_hash}，实际 {_dir_hash(pkg)}）")
        want_full = str(meta.get("dir_sha256") or "")
        if want_full and _dir_sha256(pkg) != want_full:
            issues.append(f"{name}: 完整内容 sha256 不一致")
    return issues


def _source_parts(source):
    """registry:<base>#<name>@<ver> → (base,name,ver)；其他返回 None。"""
    if not source or not source.startswith("registry:") or "#" not in source:
        return None
    base, frag = source[len("registry:"):].split("#", 1)
    if "@" not in frag:
        return None
    name, ver = frag.rsplit("@", 1)
    return base, name, ver


def restore_frozen(project_dir=None, cache_dir=None):
    """严格按 impor.lock 还原缺失/被改动的包，并校验哈希。返回还原包名列表。"""
    cache_dir = cache_dir or CACHE_DIR
    os.makedirs(cache_dir, exist_ok=True)
    proj_dir = _project_dir(project_dir)
    if not os.path.isfile(_lock_path(proj_dir)):
        raise ValueError("--frozen 需要 impor.lock：先运行 impor pkg lock")
    entries = _read_lock(proj_dir)
    restored = []
    for name, meta in entries.items():
        pkg = os.path.join(cache_dir, name)
        ok = False
        if os.path.isdir(pkg) and meta.get("hash") and _dir_hash(pkg) == meta["hash"]:
            try:
                ok = _pkg_meta(pkg)[1] == meta.get("version")
            except ValueError:
                ok = False
        if ok and meta.get("dir_sha256") and _dir_sha256(pkg) != meta["dir_sha256"]:
            ok = False
        if ok:
            continue
        source = str(meta.get("source") or "")
        parts = _source_parts(source)
        if parts:
            base, sname, sver = parts
            n, v, desc, _sha = _install_from_registry(
                sname, "", proj_dir, cache_dir, base=base,
                expect_version=sver, expect_sha=str(meta.get("sha256") or ""))
            restored.append(n)
        elif source and source != "local":
            n, v, desc = _install_from_source(source, cache_dir)
            restored.append(n)
        else:
            raise ValueError(f"{name}: 锁文件没有可还原来源，请重新 install")
        got = _read_lock(proj_dir).get(name) or {}
        # 重新安装后必须与锁一致
        pkg = os.path.join(cache_dir, name)
        if meta.get("hash") and _dir_hash(pkg) != meta["hash"]:
            raise ValueError(f"{name}: 还原后内容哈希与锁文件不一致（来源可能已被替换）")
        if meta.get("dir_sha256") and _dir_sha256(pkg) != meta["dir_sha256"]:
            raise ValueError(f"{name}: 还原后完整 sha256 与锁文件不一致")
        if meta.get("version") and _pkg_meta(pkg)[1] != meta["version"]:
            raise ValueError(f"{name}: 还原后版本与锁文件不一致")
        _ = got
    # 还原后刷新（补齐新出现的传递依赖），并做整体复核
    _refresh_lock(proj_dir, cache_dir)
    issues = check_lock(proj_dir, cache_dir)
    if issues:
        raise ValueError("按锁还原后仍有 %d 处不一致：%s" % (len(issues), "; ".join(issues)))
    return restored


def _ensure_registry_requires(pkg_dir, proj_dir, cache_dir, visited=None):
    """确保包的依赖都在缓存里；缺失/不满足时从同一私有源安装。返回来源映射。"""
    visited = visited or set()
    sources = {}
    for dep_name, constraint in _pkg_requires(pkg_dir).items():
        if dep_name in visited:
            continue
        visited.add(dep_name)
        dep_dir = os.path.join(cache_dir, dep_name)
        have = False
        if os.path.isdir(dep_dir):
            try:
                have = ver_satisfies(_pkg_meta(dep_dir)[1], constraint)
            except ValueError:
                have = False
        if not have:
            _n, _v, desc, sha = _install_from_registry(dep_name, constraint, proj_dir, cache_dir)
            sources[dep_name] = {"source": desc, "sha256": sha}
        sources.update(_ensure_registry_requires(
            os.path.join(cache_dir, dep_name), proj_dir, cache_dir, visited))
    return sources

def _looks_like_source(spec):
    s = str(spec).strip()
    return bool(s) and (os.path.isdir(os.path.abspath(s)) or _is_git_source(s) or _is_remote_url(s))


def _registry_spec(spec, deps):
    """'name@1.2' / 'name=约束' / 'name' → (name, constraint)。"""
    s = str(spec).strip()
    if "@" in s and not s.lower().startswith(("http://", "https://")):
        name, _, ver = s.partition("@")
        return name.strip(), "==" + ver.strip().lstrip("vV")
    if "=" in s:
        name, _, constraint = s.partition("=")
        name = name.strip()
        return name, (constraint.strip() or deps.get(name, ""))
    return s, deps.get(s, "")


def install(spec, project_dir=None, cache_dir=None, vendor=True, report=None):
    """安装包：本地目录 / git URL / 私有源包名[@版本]。返回 (name, version)。

    vendor=True（默认）：装完复制到 <project>/lib/<name>/，并把 ./lib 写进
    impor.toml 的 [module_search_paths]，使 `import <name>` 立刻可用。
    vendor=False：只进全局缓存（1.0 行为）。
    不在项目内（找不到 impor.toml）：只进缓存，**不生成任何配置文件**。
    """
    cache_dir = cache_dir or CACHE_DIR
    os.makedirs(cache_dir, exist_ok=True)
    proj_dir = _project_dir(project_dir)
    has_proj = _has_project(proj_dir)
    deps = _deps_from_toml(proj_dir) if has_proj else {}
    sources = {}

    if _looks_like_source(spec):
        name, ver, source = _install_from_source(spec, cache_dir)
        archive_sha = ""
        constraint = deps.get(name)
    else:
        name, constraint = _registry_spec(spec, deps)
        if not name:
            raise ValueError("包名不能为空")
        name, ver, source, archive_sha = _install_from_registry(
            name, constraint, proj_dir, cache_dir)
        sources = _ensure_registry_requires(
            os.path.join(cache_dir, name), proj_dir, cache_dir, {name})

    # 校验依赖约束
    if constraint and not ver_satisfies(ver, constraint):
        raise ValueError(f"{name} 版本 {ver} 不满足约束 {constraint}")

    # 新包自身声明的依赖与项目已有依赖冲突检测
    installed_dir = os.path.join(cache_dir, name)
    conflicts = _check_dep_conflicts(installed_dir, proj_dir)
    if conflicts:
        detail = "；".join(f"{dep}（项目要求 {proj_c}，而 {name} 要求 {pkg_c}）"
                          for dep, proj_c, pkg_c in conflicts)
        raise ValueError(f"依赖冲突：{name} 的依赖与项目现有声明不一致：{detail}")

    if has_proj:
        _set_dep(proj_dir, name, constraint or ">=%s" % ver)
        sources[name] = {"source": source, "sha256": archive_sha}
        _refresh_lock(proj_dir, cache_dir, sources)
        if vendor:
            dest, import_name, extra = _vendor_pkg(name, proj_dir, cache_dir)
            if report is not None:
                report["vendored"] = dest
                report["import_name"] = import_name
                report["vendor_root"] = ("./lib/" + name) if extra else "./lib"
    elif report is not None:
        # 不在项目内：只进缓存，绝不凭空生成 impor.toml
        report["no_project"] = True
    return name, ver

def remove(name, project_dir=None, cache_dir=None):
    """卸载包：从缓存删除、删掉 lib/ 下的 vendor 副本，并从 impor.toml / lock 移除。"""
    cache_dir = cache_dir or CACHE_DIR
    proj_dir = _project_dir(project_dir)
    pkg = os.path.join(cache_dir, name)
    removed = False
    if os.path.isdir(pkg):
        shutil.rmtree(pkg)
        removed = True
    if _has_project(proj_dir):
        vendored = os.path.join(proj_dir, "lib", name)
        if os.path.isdir(vendored):
            shutil.rmtree(vendored)
            removed = True
        _set_dep(proj_dir, name, None)
        _refresh_lock(proj_dir, cache_dir)
    return removed


def list_pkgs(project_dir=None, cache_dir=None):
    """返回 (deps, installed)。deps: 声明依赖 dict；installed: 缓存内包 dict。"""
    cache_dir = cache_dir or CACHE_DIR
    proj_dir = _project_dir(project_dir)
    deps = _deps_from_toml(proj_dir)
    installed = {}
    if os.path.isdir(cache_dir):
        for name in os.listdir(cache_dir):
            p = os.path.join(cache_dir, name)
            if os.path.isdir(p) and os.path.isfile(os.path.join(p, CONFIG_NAME)):
                try:
                    _, ver = _pkg_meta(p)
                except ValueError:
                    ver = "?"
                installed[name] = ver
    return deps, installed


def lock(project_dir=None, cache_dir=None):
    """生成/刷新 impor.lock，返回条目 dict。"""
    cache_dir = cache_dir or CACHE_DIR
    proj_dir = _project_dir(project_dir)
    return _refresh_lock(proj_dir, cache_dir)


def prune(project_dir=None, cache_dir=None):
    """删除当前项目锁文件之外的缓存包；共享缓存下请先确认其他项目不依赖。"""
    proj_dir = _project_dir(project_dir)
    return _prune_unreachable(proj_dir, cache_dir or CACHE_DIR)


def _installed_source(proj_dir, name, cache_dir=None):
    """从 impor.lock 找回某个包的安装来源；没有可用来源时返回 ""。

    lock 的 source 可能是本地绝对路径、git URL、或 registry:<base>#<name>@<ver>；
    老 lock 里可能只剩占位值 local / cache / project，这些都无法重新拉取。
    """
    entries = _read_lock(proj_dir)
    meta = entries.get(name) or {}
    src = str(meta.get("source") or "").strip()
    if src in ("", "local", "cache", "project"):
        return ""
    return src


def upgrade(name=None, project_dir=None, cache_dir=None, report=None):
    """升级：git/本地源重新拉取，私有源按约束取最高版本；返回更新的包名列表。

    4.0.1：升级后若项目里存在 <project>/lib/<name>/（说明这个包是 vendor 过的），
    同步刷新这份副本；否则不动项目目录（只改全局缓存）。
    """
    cache_dir = cache_dir or CACHE_DIR
    os.makedirs(cache_dir, exist_ok=True)
    proj_dir = _project_dir(project_dir)
    deps = _deps_from_toml(proj_dir)
    updated = []
    revendored = []
    for dep_name, constraint in deps.items():
        if name and dep_name != name:
            continue
        source = _installed_source(proj_dir, dep_name, cache_dir)
        if not source:
            print(f"impor pkg: {dep_name} 无记录来源，跳过（用 install 重新安装）")
            continue
        try:
            parts = _source_parts(source)
            if parts:
                _base, _n, _v = parts
                n, v, desc, sha = _install_from_registry(
                    dep_name, constraint, proj_dir, cache_dir)
                if not ver_satisfies(v, constraint):
                    raise ValueError(f"新版本 {v} 不满足项目约束 {constraint}")
                extra = _ensure_registry_requires(
                    os.path.join(cache_dir, n), proj_dir, cache_dir, {n})
                extra[n] = {"source": desc, "sha256": sha}
                _refresh_lock(proj_dir, cache_dir, extra)
            else:
                n, v, desc = _install_from_source(source, cache_dir)
                if not ver_satisfies(v, constraint):
                    raise ValueError(f"新版本 {v} 不满足项目约束 {constraint}")
                _refresh_lock(proj_dir, cache_dir, {n: {"source": desc}})
            updated.append(n)
            print(f"impor pkg: 已升级 {n} → {v}")
            if os.path.isdir(os.path.join(proj_dir, "lib", dep_name)):
                dest, _import_name, _extra = _vendor_pkg(dep_name, proj_dir, cache_dir)
                if dest:
                    revendored.append(dest)
                    print(f"impor pkg: 已刷新 vendor 副本 {dest}")
        except ValueError as e:
            print(f"impor pkg: {dep_name} 升级失败: {e}")
    _refresh_lock(proj_dir, cache_dir)
    if report is not None:
        report["updated"] = updated
        report["revendored"] = revendored
    return updated


# ---------- CLI ----------
def _toml_meta_from_text(text):
    if tomllib is not None:
        try:
            return tomllib.loads(text)
        except Exception:
            pass
    return _SimpleToml.loads(text)


def _zip_meta(zip_path):
    """从 .zip 包里读 (name, version)；读不到返回 None。"""
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
    return (name, ver) if name else None


def search(keyword="", project_dir=None, cache_dir=None, include_zip=True):
    """本地包索引：扫全局缓存目录与项目里的 *.zip，按名字关键字过滤。

    不联网、不需要远端索引，离线可用。返回
    [{"name","version","kind","path"}]，kind 为 "cache" 或 "zip"。
    """
    cache_dir = cache_dir or CACHE_DIR
    proj_dir = _project_dir(project_dir)
    kw = (keyword or "").strip().lower()
    hits = []
    if os.path.isdir(cache_dir):
        for n in sorted(os.listdir(cache_dir)):
            d = os.path.join(cache_dir, n)
            if not os.path.isdir(d):
                continue
            try:
                name, ver = _pkg_meta(d)
            except ValueError:
                continue
            if kw and kw not in name.lower():
                continue
            hits.append({"name": name, "version": ver, "kind": "cache", "path": d})
    if include_zip:
        for base in (proj_dir, os.path.join(proj_dir, "dist")):
            if not os.path.isdir(base):
                continue
            for fn in sorted(os.listdir(base)):
                if not fn.endswith(".zip"):
                    continue
                zp = os.path.join(base, fn)
                meta = _zip_meta(zp)
                if not meta:
                    continue
                name, ver = meta
                if kw and kw not in name.lower():
                    continue
                hits.append({"name": name, "version": ver, "kind": "zip", "path": zp})
    return hits


def _cli_positionals(args, value_flags):
    """取出位置参数；跳过 --flag 及其值，避免把 --out 的路径当包目录。"""
    out = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in value_flags:
            i += 2
            continue
        if a.startswith("--"):
            i += 1
            continue
        out.append(a)
        i += 1
    return out


def dependency_graph(project_dir=None):
    """v7.3：从 impor.lock 生成依赖图（返回 (roots, entries)）。"""
    proj = os.path.abspath(project_dir or _project_dir())
    entries = _read_lock(proj)
    declared = _deps_from_toml(proj)
    required = set()
    for meta in entries.values():
        for r in (meta.get("requires") or []):
            required.add(str(r))
    roots = [n for n in declared if n in entries]
    if not roots:
        roots = [n for n in entries if n not in required]
    if not roots and entries:
        roots = [sorted(entries)[0]]  # 全部互相依赖时仍从一个入口展示
    return roots, entries


def print_dependency_graph(project_dir=None):
    roots, entries = dependency_graph(project_dir)
    if not entries:
        print("impor.lock 为空：先运行 impor pkg install 或 impor pkg lock")
        return 0
    lines = []

    def walk(name, prefix="", last=True, stack=()):
        meta = entries.get(name)
        connector = "└── " if last else "├── "
        if meta is None:
            lines.append(prefix + connector + name + "（缺失于 lock）")
            return
        ver = meta.get("version") or "?"
        mark = "" if meta.get("installed", True) else "  [未安装]"
        lines.append(prefix + connector + f"{name} {ver}{mark}")
        if name in stack:
            lines.append(prefix + ("    " if last else "│   ") + "↺ 循环依赖")
            return
        req = [str(x) for x in (meta.get("requires") or [])]
        child_prefix = prefix + ("    " if last else "│   ")
        for i, child in enumerate(req):
            walk(child, child_prefix, i == len(req) - 1, stack + (name,))

    print(f"依赖图（{len(entries)} 个包，根 {len(roots)} 个）")
    for i, root in enumerate(roots):
        walk(root, "", i == len(roots) - 1)
    if lines:
        print("\n".join(lines))
    return 0


def audit(project_dir=None):
    """v7.3：离线依赖审计，返回 [(级别, 文本)]。"""
    proj = os.path.abspath(project_dir or _project_dir())
    entries = _read_lock(proj)
    issues = []
    for it in check_lock(proj):
        issues.append(("错误", it))
    for name, meta in entries.items():
        src = str(meta.get("source") or "")
        if src.startswith("http://"):
            issues.append(("警告", f"{name} 使用明文 HTTP 源：{src}"))
        if not meta.get("installed", True):
            issues.append(("提示", f"{name} 尚未安装，运行 impor pkg install --frozen"))
        if not meta.get("sha256") and not meta.get("dir_sha256"):
            issues.append(("警告", f"{name} 缺少内容哈希，无法校验完整性"))
        for req in (meta.get("requires") or []):
            if str(req) not in entries:
                issues.append(("错误", f"{name} 依赖 {req} 不在 impor.lock 中"))
    return issues


def main_pkg(args):
    if not args:
        print("用法: impor pkg install|remove|list|lock|check|graph|audit|upgrade|registry|pack|publish|outdated|prune ...",
              file=sys.stderr)
        return 3
    sub = args[0]
    try:
        if sub == "install":
            frozen = "--frozen" in args[1:]
            rest = [a for a in args[1:] if not a.startswith("--")]
            if frozen:
                if rest:
                    print("--frozen 只按 impor.lock 还原，不接受包名/来源参数", file=sys.stderr)
                    return 3
                restored = restore_frozen()
                if restored:
                    print(f"已按 impor.lock 还原 {len(restored)} 个包: " + ", ".join(restored))
                else:
                    print("impor.lock 与缓存一致，无需还原")
                issues = check_lock()
                if issues:
                    for it in issues:
                        print("  ✗ " + it, file=sys.stderr)
                    return 1
                return 0
            if not rest:
                print("用法: impor pkg install <本地目录|git URL|包名[@版本]|名字=约束> [--no-vendor]", file=sys.stderr)
                return 3
            do_vendor = "--no-vendor" not in args[1:]
            report = {}
            name, ver = install(" ".join(rest), vendor=do_vendor, report=report)
            print(f"已安装 {name} {ver} → {os.path.join(CACHE_DIR, name)}")
            if report.get("no_project"):
                print("未检测到 impor.toml：只装入缓存，未生成任何配置文件。")
                print("要装完即可 import：先在项目目录运行 impor pkg init，再执行本命令；")
                print("或临时用 import_path(\"%s\") 手动加入搜索路径。" % os.path.join(CACHE_DIR, name))
            else:
                print("已在项目 impor.toml 声明依赖，并刷新 impor.lock")
                if report.get("vendored"):
                    print(f"已 vendor 到 {report['vendored']}（搜索路径 {report.get('vendor_root')}）")
                    if report.get("import_name"):
                        print(f"现在即可直接使用：import {report['import_name']}")
                    else:
                        print("包内没有唯一可导入模块，请检查包结构（<name>.impor 或 __pkg__.impor）")
                else:
                    print("已跳过 vendor（--no-vendor）：仅缓存，运行时需 import_path 才可用")
            return 0
        if sub == "remove":
            if len(args) < 2:
                print("用法: impor pkg remove <name>", file=sys.stderr)
                return 3
            removed = remove(args[1])
            print(f"已卸载: {args[1]}" if removed else f"{args[1]} 不在缓存中，已清理依赖声明")
            return 0
        if sub == "list":
            deps, installed = list_pkgs()
            print("项目依赖 (impor.toml [dependencies]):")
            if deps:
                for k, v in deps.items():
                    mark = "✓" if k in installed else "✗ 未安装"
                    print(f"  {k} = {v}   {mark}")
            else:
                print("  （无）")
            print(f"已安装包 ({CACHE_DIR}):")
            if installed:
                for k, v in installed.items():
                    print(f"  {k} {v}")
            else:
                print("  （无）")
            return 0
        if sub == "lock":
            if "--check" in args[1:]:
                issues = check_lock()
                if not issues:
                    print("impor.lock 校验通过：版本与内容哈希全部一致")
                    return 0
                for it in issues:
                    print("  ✗ " + it, file=sys.stderr)
                print(f"impor.lock 校验失败：{len(issues)} 处不一致", file=sys.stderr)
                return 1
            entries = lock()
            n = len(entries)
            missing = [k for k, v in entries.items() if not v.get("installed", True)]
            print(f"已生成/刷新 impor.lock（{n} 个依赖）")
            if missing:
                print("  未安装: " + ", ".join(missing), file=sys.stderr)
                return 1
            return 0
        if sub == "check":
            issues = check_lock()
            if not issues:
                print("impor.lock 校验通过：版本与内容哈希全部一致")
                return 0
            for it in issues:
                print("  ✗ " + it, file=sys.stderr)
            return 1
        if sub == "registry":
            proj = _project_dir()
            url, token = _registry_config(proj)
            if not url:
                print("未配置私有源：impor.toml 添加 [registry] url，或设置 IMPOR_REGISTRY")
                return 1
            print(f"私有源: {url}")
            print("令牌: " + ("已配置（不显示）" if token else "未配置（公开源或允许匿名）"))
            return 0
        if sub == "search":
            kw = None
            for a in args[1:]:
                if not a.startswith("--"):
                    kw = a
                    break
            hits = search(kw or "")
            if not hits:
                print("本地没有匹配的包（已扫过缓存目录与项目的 *.zip）")
                return 0
            for h in hits:
                print("  %-20s %-10s  [%s]  %s" % (h["name"], h["version"], h["kind"], h["path"]))
            print("共 %d 个" % len(hits))
            return 0
        if sub == "upgrade":
            report = {}
            updated = upgrade(args[1] if len(args) > 1 and not args[1].startswith("--") else None,
                              report=report)
            print(f"共升级 {len(updated)} 个包")
            rev = report.get("revendored") or []
            if rev:
                print(f"已同步刷新 {len(rev)} 份项目内 vendor 副本")
            elif updated:
                print("（项目内没有 vendor 副本，仅更新了全局缓存）")
            return 0
        if sub == "pack":
            rest = _cli_positionals(args[1:], {"--out"})
            out = None
            if "--out" in args:
                i = args.index("--out")
                if i + 1 >= len(args):
                    print("--out 需要文件路径", file=sys.stderr)
                    return 3
                out = args[i + 1]
            zip_path, sha, name, ver = pack(rest[0] if rest else None, out=out)
            print(f"已打包 {name} {ver}: {zip_path}")
            print(f"sha256: {sha}")
            return 0
        if sub == "publish":
            rest = _cli_positionals(args[1:], {"--registry", "--out"})
            registry = None
            out = None
            force = "--force" in args
            for flag in ("--registry", "--out"):
                if flag in args:
                    i = args.index(flag)
                    if i + 1 >= len(args):
                        print(f"{flag} 需要参数", file=sys.stderr)
                        return 3
                    if flag == "--registry":
                        registry = args[i + 1]
                    else:
                        out = args[i + 1]
            name, ver, url, sha = publish(
                rest[0] if rest and not rest[0].startswith("--") else None,
                registry_url=registry, force=force, out=out)
            print(f"已发布 {name} {ver}")
            print(f"归档: {url}")
            print(f"sha256: {sha}")
            return 0
        if sub == "graph":
            return print_dependency_graph()
        if sub == "audit":
            issues = audit()
            if not issues:
                print("依赖审计通过：锁文件完整、哈希齐全，未发现高风险源")
                return 0
            for level, text in issues:
                print(f"  [{level}] {text}", file=sys.stderr)
            errors = sum(1 for level, _ in issues if level == "错误")
            print(f"依赖审计完成：{errors} 个错误 / {len(issues) - errors} 个提示", file=sys.stderr)
            return 1 if errors else 0
        if sub == "outdated":
            rows = outdated()
            if not rows:
                print("所有私有源依赖均为最新版本")
                return 0
            for name, cur, latest, constraint in rows:
                print(f"  {name}: {cur} → {latest}  （约束 {constraint or '任意'}）")
            return 0
        if sub == "prune":
            removed = prune()
            if removed:
                print("已清理未引用缓存包: " + ", ".join(removed))
            else:
                print("没有可清理的缓存包")
            return 0
        print(f"未知 pkg 子命令: {sub}（install / remove / list / lock / check / graph / audit / "
              f"upgrade / registry / pack / publish / outdated / prune）",
              file=sys.stderr)
        return 3
    except ValueError as e:
        print(f"impor pkg: {e}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"impor pkg: {e}", file=sys.stderr)
        return 1
