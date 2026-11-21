# -*- coding: utf-8 -*-
"""
Impor (I++) 项目配置文件（impor.toml）支持。

查找规则：从当前目录向上逐级查找 impor.toml（最近的为准）。
配置项：
  [project]
    name = "..."            # 项目名
    version = "0.1.0"       # 项目版本
    entry = "main.impor"    # 入口脚本（可选）
    sandbox_default = false # 沙盒默认开关（可选）
  [module_search_paths]     # 模块搜索路径（相对项目根）
    paths = ["./lib", "./src"]
  [dependencies]            # 依赖声明（阶段三包管理器使用）
    name = ">=0.2"
"""

import os


def pkg_cache_dir():
    """包管理器缓存目录（与 pkgmgr.CACHE_DIR 同源；避免启动时为它导入 urllib/zipfile）。"""
    if os.name == "nt":
        return os.path.join(os.path.expanduser("~"), ".impor", "pkg")
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = xdg if xdg and os.path.isabs(xdg) else os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "impor", "pkg")

CONFIG_NAME = "impor.toml"


class _SimpleToml:
    """极简 TOML 子集解析器（tomllib 不可用时的回退）。支持 [section]、key = 值、
    字符串 / 数字 / 布尔 / 字符串数组。"""

    @staticmethod
    def loads(text):
        data = {}
        section = None
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1].strip()
                data.setdefault(section, {})
                continue
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip()
            target = data.setdefault(section, {}) if section else data
            target[key] = _SimpleToml._parse_value(val)
        return data

    @staticmethod
    def _parse_value(val):
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            if not inner:
                return []
            return [_SimpleToml._parse_value(v.strip()) for v in inner.split(",")]
        if val.startswith('"') and val.endswith('"'):
            return val[1:-1]
        if val.startswith("'") and val.endswith("'"):
            return val[1:-1]
        if val in ("true", "false"):
            return val == "true"
        try:
            return int(val)
        except ValueError:
            try:
                return float(val)
            except ValueError:
                return val


def _load_raw(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def find_config(start_dir=None):
    """从 start_dir（默认 cwd）向上查找 impor.toml，返回 (配置目录, 配置dict) 或 None。"""
    if start_dir is None:
        start_dir = os.getcwd()
    d = os.path.abspath(start_dir)
    while True:
        cand = os.path.join(d, CONFIG_NAME)
        if os.path.isfile(cand):
            text = _load_raw(cand)
            try:
                import tomllib  # 仅在实际读到配置时才付导入成本
            except ImportError:
                tomllib = None
            if tomllib is not None:
                data = tomllib.loads(text)
            else:
                data = _SimpleToml.loads(text)
            return d, data
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def search_paths_from_config(cfg):
    """从配置中提取模块搜索路径（已转为绝对路径）。"""
    paths = []
    section = cfg.get("module_search_paths") or {}
    for p in section.get("paths", []):
        paths.append(str(p))
    return paths


def sandbox_default_from_config(cfg):
    proj = cfg.get("project") or {}
    return bool(proj.get("sandbox_default", False))


def apply_config(interp, start_dir=None):
    """找到配置则应用到解释器（搜索路径、沙盒、包缓存），返回配置 dict 或 None。"""
    found = find_config(start_dir)
    cfg = None
    if found is not None:
        cfg_dir, cfg = found
        for p in search_paths_from_config(cfg):
            full = os.path.join(cfg_dir, p) if not os.path.isabs(p) else p
            full = os.path.normpath(full)
            if full not in interp.module_search_paths:
                interp.module_search_paths.insert(0, full)
        sb = sandbox_default_from_config(cfg)
        if sb:
            interp.sandbox_mode = True
    # 包管理器缓存目录也加入搜索路径（impor pkg install 安装的包可直接 import）
    cache_dir = pkg_cache_dir()
    if os.path.isdir(cache_dir) and cache_dir not in interp.module_search_paths:
        interp.module_search_paths.append(cache_dir)
    # v4.0.1：每个已安装包目录各自也加入搜索路径。
    # 原因：缓存根只能解析 <pkg>/__pkg__.impor；旧式包（<pkg>/<name>.impor）必须以包目录为根才能 import。
    if os.path.isdir(cache_dir):
        try:
            for _n in sorted(os.listdir(cache_dir)):
                _p = os.path.join(cache_dir, _n)
                if os.path.isdir(_p) and _p not in interp.module_search_paths:
                    interp.module_search_paths.append(_p)
        except OSError:
            pass
    return cfg


def project_name(cfg):
    proj = cfg.get("project") or {}
    return proj.get("name", "unnamed")


def project_version(cfg):
    proj = cfg.get("project") or {}
    return str(proj.get("version", "0.0.0"))


def entry_script(cfg):
    proj = cfg.get("project") or {}
    return proj.get("entry")


def dependencies(cfg):
    return dict(cfg.get("dependencies") or {})
