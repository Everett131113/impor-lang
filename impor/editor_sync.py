# -*- coding: utf-8 -*-
"""设置同步（编辑器 3.1）。

不依赖网络：用户把同步目录指向 OneDrive/Dropbox/坚果云等任意云盘目录即可
跨设备交换设置。快照只包含可移植的偏好，不包含工作区绝对路径、最近文件、
命令历史等机器相关状态。
"""

from __future__ import annotations

import copy
import json
import os
import time
from typing import Dict, Optional, Tuple
from .observability import note_swallowed

SCHEMA = 1
FILENAME = "impor-studio-settings.json"
CONFIG_KEYS = (
    "theme", "font_family", "font_size", "indent_width", "word_wrap", "minimap",
    "inlay_hints", "semantic_highlight", "large_file_kb", "autosave", "auto_format",
    "auto_update", "plugin_isolation", "screen_reader",
)
PROJECT_KEYS = ("theme", "font_size", "editor", "plugins", "plugin_write_allow", "tasks")


def default_sync_dir():
    env = os.environ.get("IMPOR_SETTINGS_SYNC", "").strip()
    if env:
        return os.path.abspath(os.path.expanduser(env))
    return os.path.join(os.path.expanduser("~"), ".impor_studio", "sync")


def snapshot_path(folder):
    return os.path.join(os.path.abspath(folder), FILENAME)


def _portable_project(project):
    if not isinstance(project, dict):
        return {}
    out = {}
    for key in PROJECT_KEYS:
        if key in project:
            out[key] = copy.deepcopy(project[key])
    return out


def build_snapshot(cfg, project=None, version="", updated_at=None):
    cfg = cfg if isinstance(cfg, dict) else {}
    portable = {k: copy.deepcopy(cfg[k]) for k in CONFIG_KEYS if k in cfg}
    return {
        "schema": SCHEMA,
        "app": "Impor Studio",
        "version": version,
        "updated_at": float(updated_at if updated_at is not None else time.time()),
        "config": portable,
        "project": _portable_project(project),
    }


def validate_snapshot(data):
    if not isinstance(data, dict):
        raise ValueError("设置快照不是 JSON 对象")
    if data.get("schema") != SCHEMA:
        raise ValueError("不支持的设置快照版本：%r" % (data.get("schema"),))
    if not isinstance(data.get("config", {}), dict):
        raise ValueError("config 字段不是对象")
    return data


def read_snapshot(path):
    with open(path, "r", encoding="utf-8") as f:
        return validate_snapshot(json.load(f))


def write_snapshot(folder, data):
    folder = os.path.abspath(folder)
    os.makedirs(folder, exist_ok=True)
    path = snapshot_path(folder)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)
    return path


def _deep_merge(local, remote):
    out = copy.deepcopy(local)
    for k, v in (remote or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def merge_snapshots(local, remote, strategy="newer"):
    """返回合并后的快照；strategy: newer / local / remote。"""
    local = validate_snapshot(local)
    remote = validate_snapshot(remote)
    use_remote = strategy == "remote"
    if strategy == "newer":
        use_remote = float(remote.get("updated_at", 0)) > float(local.get("updated_at", 0))
    winner, loser = (remote, local) if use_remote else (local, remote)
    merged = copy.deepcopy(winner)
    merged["config"] = _deep_merge(loser.get("config", {}), winner.get("config", {}))
    merged["project"] = _deep_merge(loser.get("project", {}), winner.get("project", {}))
    merged["updated_at"] = max(float(local.get("updated_at", 0)), float(remote.get("updated_at", 0)),
                               time.time())
    merged["version"] = winner.get("version") or local.get("version") or ""
    return merged


def push_snapshot(folder, cfg, project=None, version="", strategy="newer"):
    """把本地设置写入同步目录；若已有远端快照，按策略合并后写回。"""
    folder = os.path.abspath(folder)
    local = build_snapshot(cfg, project, version)
    path = snapshot_path(folder)
    if os.path.isfile(path):
        try:
            remote = read_snapshot(path)
            local = merge_snapshots(local, remote, strategy)
        except Exception:
            note_swallowed("remote snapshot merge")
    return write_snapshot(folder, local)


def pull_snapshot(folder, cfg, project=None, version="", strategy="newer"):
    """读取同步目录并合并到本地；返回 (merged_cfg, merged_project, snapshot)。"""
    snapshot = read_snapshot(snapshot_path(folder))
    local = build_snapshot(cfg, project, version)
    merged = merge_snapshots(local, snapshot, strategy)
    cfg_out = copy.deepcopy(cfg if isinstance(cfg, dict) else {})
    cfg_out.update(merged.get("config", {}))
    project_out = copy.deepcopy(project if isinstance(project, dict) else {})
    project_out.update(merged.get("project", {}))
    return cfg_out, project_out, merged


def sync_info(folder):
    path = snapshot_path(folder)
    if not os.path.isfile(path):
        return {"exists": False, "path": path, "updated_at": 0, "version": ""}
    try:
        data = read_snapshot(path)
        return {"exists": True, "path": path, "updated_at": data.get("updated_at", 0),
                "version": data.get("version", "")}
    except Exception as exc:
        return {"exists": True, "path": path, "updated_at": 0, "version": "",
                "error": str(exc)[:120]}
