# -*- coding: utf-8 -*-
"""全项目搜索 / 批量替换的纯逻辑层（编辑器 3.1）。

这里刻意不依赖 Tk：搜索、替换、预览、事务备份都可以独立测试。
UI 在 editor.py 中调用这些函数，保证窗口关闭时也能安全撤销。
"""

from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple
from .observability import note_swallowed
from .pathsafe import rel_quiet

DEFAULT_SUFFIXES = (".impor", ".md", ".txt", ".json", ".toml", ".yaml", ".yml")
SKIP_DIRS = {".git", ".hg", ".svn", "__pycache__", ".impor_recovery", "node_modules",
             ".venv", "venv", "dist", "build", "_backup"}


@dataclass
class SearchOptions:
    query: str
    regex: bool = False
    case_sensitive: bool = False
    whole_word: bool = False
    suffixes: Tuple[str, ...] = DEFAULT_SUFFIXES
    max_file_bytes: int = 8 * 1024 * 1024

    def pattern(self) -> re.Pattern:
        if not self.query:
            raise ValueError("搜索内容不能为空")
        if self.regex:
            body = self.query
        else:
            body = re.escape(self.query)
        if self.whole_word:
            # \b 对中文不可靠，显式用「标识符字符」的边界判断。
            body = r"(?<![A-Za-z0-9_\u4e00-\u9fff])" + body + r"(?![A-Za-z0-9_\u4e00-\u9fff])"
        flags = 0 if self.case_sensitive else re.IGNORECASE
        try:
            return re.compile(body, flags)
        except re.error as exc:
            raise ValueError("正则表达式无效：%s" % exc) from exc


@dataclass
class Match:
    path: str
    line: int
    column: int
    start: int
    end: int
    text: str
    context: str = ""


@dataclass
class FileResult:
    path: str
    before: str
    after: str
    matches: List[Match] = field(default_factory=list)
    selected: bool = True
    error: str = ""
    dirty: bool = False

    @property
    def count(self) -> int:
        return len(self.matches)

    @property
    def changed(self) -> bool:
        return bool(self.before != self.after and not self.error)

    def diff(self, context: int = 3, max_lines: int = 400) -> str:
        rel = self.path
        if not self.changed:
            return ""
        lines = list(difflib.unified_diff(
            self.before.splitlines(), self.after.splitlines(),
            fromfile=rel, tofile=rel, lineterm="", n=context))
        if len(lines) > max_lines:
            lines = lines[:max_lines] + ["... 预览已截断 ..."]
        return "\n".join(lines)


def line_of(text: str, pos: int) -> Tuple[int, int]:
    line = text.count("\n", 0, pos) + 1
    last = text.rfind("\n", 0, pos)
    return line, pos - (last + 1)


def context_line(text: str, pos: int, width: int = 90) -> str:
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    if end < 0:
        end = len(text)
    line = text[start:end]
    rel = pos - start
    if len(line) <= width * 2:
        return line
    a = max(0, rel - width)
    b = min(len(line), rel + width)
    return ("…" if a else "") + line[a:b] + ("…" if b < len(line) else "")


def find_matches(text: str, options: SearchOptions, path: str = "") -> List[Match]:
    rx = options.pattern()
    out: List[Match] = []
    for m in rx.finditer(text):
        if m.end() == m.start():
            continue
        line, col = line_of(text, m.start())
        out.append(Match(path=path, line=line, column=col, start=m.start(), end=m.end(),
                         text=m.group(0), context=context_line(text, m.start())))
    return out


def replace_text(text: str, options: SearchOptions, replacement: str) -> str:
    rx = options.pattern()
    if options.regex:
        try:
            return rx.sub(replacement, text)
        except re.error as exc:
            raise ValueError("替换模板无效：%s" % exc) from exc
    if options.whole_word or not options.case_sensitive:
        # 字面替换也必须复用同一条正则：否则「全词/忽略大小写」只影响搜索、不影响替换。
        return rx.sub(lambda m: replacement, text)
    return text.replace(options.query, replacement)


def iter_project_files(root: str, suffixes: Sequence[str] = DEFAULT_SUFFIXES,
                       max_file_bytes: int = 8 * 1024 * 1024) -> Iterable[str]:
    root = os.path.abspath(root)
    suffixes = tuple(s.lower() for s in suffixes)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            if suffixes and not name.lower().endswith(suffixes):
                continue
            path = os.path.join(dirpath, name)
            try:
                if os.path.getsize(path) > max_file_bytes:
                    continue
            except Exception:
                continue
            yield path


def _read_text(path: str) -> str:
    with open(path, "rb") as f:
        raw = f.read()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    # 只处理 UTF-8；二进制/其它编码直接报错并让 UI 标红，避免误改。
    return raw.decode("utf-8")


def search_project(root, options: SearchOptions, paths: Optional[Iterable[str]] = None,
                   dirty_paths: Optional[Iterable[str]] = None,
                   should_cancel=None, buffer_texts: Optional[dict] = None) -> List[FileResult]:
    if isinstance(root, (list, tuple, set)):
        roots = [os.path.abspath(str(r)) for r in root if str(r)]
    else:
        roots = [os.path.abspath(str(root))]
    roots = [r for r in roots if os.path.isdir(r)]
    dirty = {os.path.abspath(p) for p in (dirty_paths or [])}
    buffers = {os.path.abspath(k): v for k, v in (buffer_texts or {}).items()}
    results: List[FileResult] = []
    if paths is not None:
        files = list(paths)
    else:
        files = []
        seen = set()
        for base in roots:
            for path in iter_project_files(base, options.suffixes, options.max_file_bytes):
                key = os.path.normcase(os.path.abspath(path))
                if key in seen:
                    continue
                seen.add(key)
                files.append(path)
    for path in files:
        # 3.2.2：支持取消——已扫描到的结果保留，未扫描文件不再阻塞界面。
        if should_cancel is not None and should_cancel():
            break
        path = os.path.abspath(path)
        try:
            if not any(os.path.normcase(os.path.commonpath([base, path])) ==
                       os.path.normcase(base) for base in roots):
                continue
        except ValueError:
            continue
        try:
            # v3.7.7：显式传入未保存缓冲区时，以缓冲区为准，避免用磁盘旧内容覆盖编辑。
            if path in buffers:
                before = buffers[path]
            else:
                before = _read_text(path)
        except Exception as exc:
            results.append(FileResult(path=path, before="", after="", error=str(exc)[:120]))
            continue
        try:
            matches = find_matches(before, options, path)
            if not matches:
                continue
            results.append(FileResult(path=path, before=before, after=before,
                                      matches=matches, dirty=path in dirty))
        except Exception as exc:
            results.append(FileResult(path=path, before=before, after="",
                                      error=str(exc)[:120]))
    results.sort(key=lambda r: (r.error == "", r.path.lower()))
    return results


def prepare_replacements(results: Iterable[FileResult], replacement: str,
                         options: SearchOptions) -> None:
    for r in results:
        if r.error:
            continue
        try:
            r.after = replace_text(r.before, options, replacement)
            r.selected = bool(r.changed)
        except Exception as exc:
            r.error = str(exc)[:120]
            r.after = r.before
            r.selected = False


def count_selected(results: Iterable[FileResult]) -> Tuple[int, int]:
    files = sum(1 for r in results if r.selected and r.changed)
    hits = sum(r.count for r in results if r.selected and r.changed)
    return files, hits


def _atomic_write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".impor_tmp_%d" % os.getpid()
    with open(tmp, "wb") as f:
        f.write(text.encode("utf-8"))
        f.flush()
        try:
            os.fsync(f.fileno())
        except Exception:
            note_swallowed("search scan")
    os.replace(tmp, path)


def ensure_inside(root: str, path: str) -> str:
    root = os.path.abspath(root)
    path = os.path.abspath(path)
    if os.path.commonpath([root, path]) != root:
        raise PermissionError("目标不在工作区内：%s" % path)
    return path


def apply_results(root: str, results: Iterable[FileResult], backup_root: str,
                  label: str = "search_replace") -> str:
    """把选中的 FileResult 写入磁盘，返回事务目录；失败时尽量全量回滚。"""
    root = os.path.abspath(root)
    backup_root = os.path.abspath(backup_root)
    stamp = time.strftime("%Y%m%d_%H%M%S") + "_%d" % os.getpid()
    txn = os.path.join(backup_root, "%s_%s" % (label, stamp))
    os.makedirs(txn, exist_ok=True)
    manifest = {"schema": 1, "created_at": time.time(), "label": label, "root": root,
                "files": []}
    written = []
    try:
        for i, r in enumerate(results):
            if not (r.selected and r.changed) or r.error:
                continue
            path = ensure_inside(root, r.path)
            rel = rel_quiet(path, root)
            backup_name = "%04d_%s" % (i, os.path.basename(path))
            backup_path = os.path.join(txn, backup_name)
            existed = os.path.exists(path)
            if existed:
                shutil.copy2(path, backup_path)
            manifest["files"].append({
                "rel": rel, "backup": backup_name, "existed": existed,
                "before_len": len(r.before), "after_len": len(r.after),
            })
            _atomic_write(path, r.after)
            written.append(path)
        with open(os.path.join(txn, "manifest.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
            f.write("\n")
        return txn
    except Exception:
        for item in reversed(manifest["files"]):
            try:
                target = os.path.join(root, item["rel"])
                if item["existed"]:
                    shutil.copy2(os.path.join(txn, item["backup"]), target)
                elif os.path.exists(target):
                    os.remove(target)
            except Exception:
                note_swallowed("search scan")
        raise


def find_latest_transaction(backup_root: str) -> Optional[str]:
    try:
        names = sorted((n for n in os.listdir(backup_root)
                        if n.startswith("search_replace_") and
                        os.path.isfile(os.path.join(backup_root, n, "manifest.json"))),
                       reverse=True)
    except Exception:
        return None
    return os.path.join(backup_root, names[0]) if names else None


def undo_transaction(txn_dir: str, root: Optional[str] = None) -> int:
    txn_dir = os.path.abspath(txn_dir)
    with open(os.path.join(txn_dir, "manifest.json"), "r", encoding="utf-8") as f:
        manifest = json.load(f)
    root = os.path.abspath(root or manifest.get("root") or ".")
    restored = 0
    for item in manifest.get("files") or []:
        try:
            target = ensure_inside(root, os.path.join(root, item["rel"]))
            if item.get("existed", True):
                shutil.copy2(os.path.join(txn_dir, item["backup"]), target)
            elif os.path.exists(target):
                os.remove(target)
            restored += 1
        except Exception:
            continue
    return restored


def describe(results: Iterable[FileResult], dirty_included: bool = False) -> str:
    files, hits = count_selected(results)
    errors = sum(1 for r in results if r.error)
    dirty = sum(1 for r in results if r.dirty)
    parts = ["%d 个文件 / %d 处" % (files, hits)]
    if dirty:
        parts.append("%d 个文件含未保存缓冲区（%s）" % (
            dirty, "已纳入替换" if dirty_included else "已跳过替换"))
    if errors:
        parts.append("%d 个文件读取失败" % errors)
    return "，".join(parts)
