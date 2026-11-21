# -*- coding: utf-8 -*-
"""
Impor (I++) LSP 语言服务器（impor lsp，stdio JSON-RPC）。

能力：
  - textDocument/publishDiagnostics：语法错误 + 未定义变量（Error）+ 未使用变量/导入（Info）
  - textDocument/definition：跳转定义（同一文件内全部声明位置）
  - textDocument/hover：符号说明（var / 函数签名 / 类 / 导入）
  - textDocument/documentSymbol：文档符号大纲
  - textDocument/completion：自动补全（v2.5：本文件符号 + 内建函数 + 关键字）
  - textDocument/rename：符号重命名（v2.5：按引用表同文件内全部位置同步改名）
  - textDocument/codeAction：QuickFix（v2.5：删除未使用导入 / 未使用变量加 _ 前缀）
  - textDocument/formatting / rangeFormatting：v3.9 保存时自动格式化（复用 impor fmt）
  - 读取 impor.toml 的 module_search_paths（模块搜索路径提示能力预留）

协议：Content-Length 帧 + JSON-RPC 2.0，标准输入/标准输出。
"""

import json
import os
import re
import sys

from .lexer import BUILTIN_NAMES, KEYWORDS, CANONICAL_KEYWORDS, tokenize
from .parser import Parser
from .interpreter import ImporException, strip_header
from .diagnostics import parse_position, explain, issue_fix
from .analysis import analyze
from .typecheck import check_source
from .config import find_config, search_paths_from_config
from .fmt import fmt_options_from_dir, fmt_source
from .symbol_index import ProjectIndex
from . import __version__
from .observability import note_swallowed

MAX_PARSE_LINES = 4000  # 超大文件跳过诊断，避免卡死编辑器

# v3.7.4 semanticTokens 图例（顺序即 tokenType 索引）
_SEM_TYPES = ["namespace", "type", "class", "enum", "interface", "struct",
              "typeParameter", "parameter", "variable", "property", "enumMember",
              "event", "function", "method", "macro", "keyword", "modifier",
              "comment", "string", "number", "regexp", "operator", "decorator"]
_SEM_MODS = ["declaration", "definition", "readonly"]
_SEM_TYPE_INDEX = {name: i for i, name in enumerate(_SEM_TYPES)}
_SEM_KIND = {"def": "function", "class": "class", "exception": "class",
             "param": "parameter", "var": "variable", "for": "variable",
             "catch": "variable", "match": "variable", "field": "property",
             "import": "namespace", "module": "namespace"}


def _parse_body(body):
    """解析源码体，返回 (ast, error)。error 为 (line, col, msg) 或 None。"""
    try:
        ast = Parser(tokenize(body)).program()
        return ast, None
    except SyntaxError as e:
        msg = str(e)
        line = getattr(e, "lineno", 0) or 0
        _ln, _col, _msg = parse_position(msg)
        if _ln is not None:
            line, col, msg = _ln, _col or 0, _msg
        else:
            col = 0
        return None, (line, col, msg)
    except ImporException as e:
        return None, (getattr(e, "lineno", 0) or 0, 0, f"{e.name}: {e.msg}")
    except Exception as e:
        return None, (0, 0, f"解析失败: {e}")


def _issue_fix(iss):
    return issue_fix(iss.code, iss.msg)


def _word_at(text, character):
    """返回 (word, start_col, end_col) 或在字符处无词时返回 None。"""
    if character < 0 or character > len(text):
        return None
    i = character
    # 向左扩展到词首
    start = i
    while start > 0 and (text[start - 1].isalnum() or text[start - 1] == "_"):
        start -= 1
    end = i
    while end < len(text) and (text[end].isalnum() or text[end] == "_"):
        end += 1
    if start == end:
        return None
    return text[start:end], start, end


class LanguageServer:
    def __init__(self, stdin=None, stdout=None, workspace_root=None):
        self.stdin = stdin or sys.stdin.buffer
        self.stdout = stdout or sys.stdout.buffer
        self.root = workspace_root
        self.docs = {}          # uri -> text
        self.analyses = {}      # uri -> Analyzer
        self.sources = {}       # uri -> source_lines（原始行，含头部标记）
        self.head_skips = {}    # uri -> 头部标记被跳过的行数（analysis 行号→源码行号偏移）
        self.type_issues = {}   # uri -> 类型检查问题
        self.shutdown = False
        self.root_uri = None
        self.index = ProjectIndex(workspace_root or os.getcwd())

    # ---------- 帧读写 ----------
    def _read_message(self):
        headers = {}
        while True:
            line = self.stdin.readline()
            if not line:
                return None
            line = line.decode("utf-8", "replace").rstrip("\r\n")
            if not line:
                break
            if ":" in line:
                k, _, v = line.partition(":")
                headers[k.strip().lower()] = v.strip()
        length = int(headers.get("content-length", "0"))
        if length <= 0:
            return None
        body = self.stdin.read(length)
        return json.loads(body.decode("utf-8", "replace"))

    def _write_message(self, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        header = f"Content-Length: {len(data)}\r\n\r\n".encode("ascii")
        self.stdout.write(header + data)
        self.stdout.flush()

    def _send_notification(self, method, params):
        self._write_message({"jsonrpc": "2.0", "method": method, "params": params})

    def _reply(self, msg_id, result):
        self._write_message({"jsonrpc": "2.0", "id": msg_id, "result": result})

    def _error(self, msg_id, code, message):
        self._write_message({"jsonrpc": "2.0", "id": msg_id,
                             "error": {"code": code, "message": message}})

    # ---------- 文本管理 ----------
    def _uri_to_path(self, uri):
        if uri.startswith("file://"):
            p = uri[7:]
            # 处理 Windows 盘符 file:///C:/...
            if p.startswith("/") and len(p) > 2 and p[2] == ":":
                p = p[1:]
            return p.replace("/", os.sep)
        return uri
    def _update_doc(self, uri, text):
        self.docs[uri] = text
        try:
            self.index.update_text(self._uri_to_path(uri), text)
        except Exception:
            note_swallowed("lsp index update")
        lines = text.split("\n")
        body_text = text
        head_skip = 0
        if lines and lines[0].strip().startswith("important<<"):
            body_text = "\n".join(lines[1:])
            head_skip = 1
        self.sources[uri] = lines
        self.head_skips[uri] = head_skip
        self.type_issues[uri] = []
        ast, err = _parse_body(body_text)
        if ast is None:
            self.analyses[uri] = None
            diags = []
            if err and err[0]:
                why, fix = explain("语法错误", err[2])
                msg = "%s\n为什么：%s\n修复：%s" % (err[2], why, fix)
                diags = [{"range": _range_at(err[0] + head_skip, err[1]), "severity": 1,
                          "source": "impor", "message": msg}]
            self._publish(uri, diags)
            return
        try:
            an = analyze(ast, body_text.split("\n"))
            self.analyses[uri] = an
        except Exception as e:  # 分析失败不阻塞编辑
            self.analyses[uri] = None
            self._publish(uri, [])
            return
        diags = []
        if err is None:
            for iss in an.issues:
                sev = {"undefined-name": 2, "unused-variable": 3,
                       "unused-import": 3}.get(iss.code)
                if sev is not None:
                    diags.append({"range": _range_at(iss.line + head_skip, iss.col),
                                  "severity": sev, "source": "impor",
                                  "message": "%s\n修复：%s" % (iss.msg, _issue_fix(iss))})
        try:
            tips = check_source(body_text, self.index, self._uri_to_path(uri))
        except Exception:
            tips = []
        self.type_issues[uri] = tips
        for tip in tips:
            diags.append({"range": _range_at(tip.line + head_skip, tip.col),
                          "severity": 1, "source": "impor-type",
                          "message": tip.msg})
        self._publish(uri, diags)

    def _publish(self, uri, diags):
        self._send_notification("textDocument/publishDiagnostics",
                                {"uri": uri, "diagnostics": diags})

    # ---------- 请求处理 ----------
    def _on_initialize(self, msg_id, params):
        self.root_uri = (params or {}).get("rootUri") or (params or {}).get("rootPath")
        if self.root_uri:
            self.root = self._uri_to_path(self.root_uri)
            self.index = ProjectIndex(self.root)
        try:
            self.index.refresh(force=True)
        except Exception:
            note_swallowed("lsp project index")
        self._reply(msg_id, {
            "capabilities": {
                "textDocumentSync": {"openClose": True, "change": 1},
                "definitionProvider": True,
                "referencesProvider": True,
                "hoverProvider": True,
                "documentSymbolProvider": True,
                "workspaceSymbolProvider": True,
                "completionProvider": {"triggerCharacters": ["."]},
                "signatureHelpProvider": {"triggerCharacters": ["(", ","]},
                "renameProvider": {"prepareProvider": True},
                "codeActionProvider": True,
                "documentFormattingProvider": True,
                "documentRangeFormattingProvider": True,
                "documentHighlightProvider": True,
                "foldingRangeProvider": True,
                "codeLensProvider": {"resolveProvider": False},
                "inlayHintProvider": True,
                "semanticTokensProvider": {
                    "legend": {"tokenTypes": _SEM_TYPES, "tokenModifiers": _SEM_MODS},
                    "full": True,
                    "range": False,
                },
            },
            "serverInfo": {"name": "impor-lsp", "version": __version__},
        })

    def _on_definition(self, msg_id, params):
        uri = params["textDocument"]["uri"]
        pos = params["position"]
        line, char = pos["line"], pos["character"]
        text = self.docs.get(uri, "")
        lines = text.split("\n")
        if line >= len(lines):
            self._reply(msg_id, None)
            return
        word = _word_at(lines[line], char)
        if not word:
            self._reply(msg_id, None)
            return
        name = word[0]
        an = self.analyses.get(uri)
        results = []
        if an is not None:
            h = self.head_skips.get(uri, 0)
            for sym in an.symbols.get(name, []):
                results.append(_location(uri, sym.line, sym.col, h))
        if not results:
            for sym in self.index.definition(name, self._uri_to_path(uri)):
                results.append({
                    "uri": _path_to_uri(sym.path),
                    "range": {"start": {"line": sym.line - 1, "character": sym.col},
                              "end": {"line": sym.line - 1, "character": sym.col + len(name)}},
                })
        if results:
            self._reply(msg_id, results if len(results) > 1 else results[0])
        else:
            self._reply(msg_id, None)

    def _on_signature_help(self, msg_id, params):
        uri = params["textDocument"]["uri"]
        pos = params["position"]
        line, char = pos["line"], pos["character"]
        lines = self.sources.get(uri, [])
        if line >= len(lines):
            self._reply(msg_id, None)
            return
        before = lines[line][:char]
        depth, start = 0, -1
        for i in range(len(before) - 1, -1, -1):
            if before[i] == ")":
                depth += 1
            elif before[i] == "(":
                if depth:
                    depth -= 1
                else:
                    start = i
                    break
        if start < 0:
            self._reply(msg_id, None)
            return
        m = re.search(r"([A-Za-z_][A-Za-z0-9_]*|[一-龥]+)\s*$", before[:start])
        if not m:
            self._reply(msg_id, None)
            return
        name = m.group(1)
        sig = ""
        try:
            for sym in self.index.definition(name, self._uri_to_path(uri)):
                text = (getattr(sym, "text", "") or "").strip()
                sm = re.search(r"(?:func|def)\s+" + re.escape(name) +
                               r"\s*\(([^)]*)\)", text)
                if sm:
                    sig = name + "(" + sm.group(1) + ")"
                    break
        except Exception:
            pass
        if not sig:
            self._reply(msg_id, None)
            return
        params_list = [p.strip() for p in sig[sig.find("(") + 1:sig.rfind(")")].split(",") if p.strip()]
        active = before[start + 1:].count(",")
        self._reply(msg_id, {
            "signatures": [{
                "label": sig,
                "parameters": [{"label": p} for p in params_list],
            }],
            "activeSignature": 0,
            "activeParameter": min(active, max(0, len(params_list) - 1)),
        })

    def _on_hover(self, msg_id, params):
        uri = params["textDocument"]["uri"]
        pos = params["position"]
        line, char = pos["line"], pos["character"]
        lines = self.sources.get(uri, [])
        if line >= len(lines):
            self._reply(msg_id, None)
            return
        word = _word_at(lines[line], char)
        if not word:
            self._reply(msg_id, None)
            return
        name = word[0]
        an = self.analyses.get(uri)
        if an is not None:
            h = self.head_skips.get(uri, 0)
            syms = an.symbols.get(name)
            if syms:
                sym = syms[-1]  # 最近一次声明
                self._reply(msg_id, {"contents": {"kind": "markdown",
                                                  "value": _hover_md(sym, lines, h)}})
                return
        if name in BUILTIN_NAMES:
            self._reply(msg_id, {"contents": {"kind": "markdown",
                                              "value": f"内建函数 `{name}`"}})
            return
        syms = self.index.definition(name, self._uri_to_path(uri))
        if syms:
            sym = syms[0]
            val = f"**{sym.kind}** `{sym.name}`"
            if sym.typ and sym.typ != "?":
                val += f"  `: {sym.typ}`"
            if sym.text:
                val += f"\n\n```impor\n{sym.text}\n```"
            val += f"\n\n定义于 `{os.path.relpath(sym.path, self.index.root)}:{sym.line}`"
            self._reply(msg_id, {"contents": {"kind": "markdown", "value": val}})
            return
        if name in KEYWORDS:
            self._reply(msg_id, {"contents": {"kind": "markdown",
                                              "value": f"关键字 `{name}`"}})
            return
        self._reply(msg_id, None)

    def _on_document_symbol(self, msg_id, params):
        uri = params["textDocument"]["uri"]
        an = self.analyses.get(uri)
        out = []
        if an is not None:
            h = self.head_skips.get(uri, 0)
            for name, syms in an.symbols.items():
                for sym in syms:
                    if sym.scope == "<module>" and sym.kind in ("def", "class", "var",
                                                                "exception", "import"):
                        ln = sym.line - 1 + h
                        out.append({"name": name, "kind": _symbol_kind(sym.kind),
                                    "range": {"start": {"line": ln, "character": sym.col},
                                              "end": {"line": ln, "character": sym.col + len(name)}},
                                    "selectionRange": {"start": {"line": ln, "character": sym.col},
                                                       "end": {"line": ln, "character": sym.col + len(name)}}})
        self._reply(msg_id, out)

    # ---------- v2.5：补全 / 重命名 / QuickFix ----------
    _KIND_MAP = {"def": 3, "class": 7, "var": 6, "param": 6, "for": 6,
                 "catch": 6, "field": 6, "exception": 10, "import": 9,
                 "case": 6}

    def _on_completion(self, msg_id, params):
        uri = params["textDocument"]["uri"]
        an = self.analyses.get(uri)
        items, seen = [], set()
        if an is not None:
            for name in sorted(an.symbols):
                syms = an.symbols.get(name)
                if not syms:
                    continue
                sym = syms[-1]
                items.append({"label": name, "kind": self._KIND_MAP.get(sym.kind, 6),
                              "detail": f"{sym.scope} 内 {sym.kind}"})
                seen.add(name)
        for name in sorted(BUILTIN_NAMES):
            if name not in seen:
                items.append({"label": name, "kind": 3, "detail": "内建函数"})
                seen.add(name)
        for name, kind, typ, path, line in self.index.completion():
            if name in seen or not name:
                continue
            detail = f"项目符号  {typ if typ != '?' else ''}"
            if path:
                detail += f"  {os.path.relpath(path, self.index.root)}:{line}"
            items.append({"label": name, "kind": self._KIND_MAP.get(kind, 6),
                          "detail": detail.strip()})
            seen.add(name)
        # v3.7.2：补全只推主写法，deprecated 别名不再出现。
        for name in sorted(CANONICAL_KEYWORDS):
            if name not in seen:
                items.append({"label": name, "kind": 14, "detail": "关键字"})
        self._reply(msg_id, {"isIncomplete": False, "items": items})

    def _on_rename(self, msg_id, params):
        uri = params["textDocument"]["uri"]
        new_name = (params.get("newName") or "").strip()
        pos = params["position"]
        lines = self.sources.get(uri, [])
        if not new_name or pos["line"] >= len(lines):
            self._reply(msg_id, None)
            return
        w = _word_at(lines[pos["line"]], pos["character"])
        if not w:
            self._reply(msg_id, None)
            return
        name = w[0]
        if name not in self.index.by_name:
            self._reply(msg_id, None)
            return
        changes = {}
        own_path = os.path.normcase(os.path.normpath(os.path.abspath(self._uri_to_path(uri))))
        for path, edits in self.index.rename_edits_scoped(
                name, new_name, self._uri_to_path(uri), pos["line"] + 1).items():
            same_doc = os.path.normcase(os.path.normpath(os.path.abspath(path))) == own_path
            key = uri if same_doc else _path_to_uri(path)
            for e in edits:
                changes.setdefault(key, []).append({
                    "range": {"start": {"line": e["line"] - 1, "character": e["col"]},
                              "end": {"line": e["line"] - 1, "character": e["col"] + len(name)}},
                    "newText": new_name,
                })
        if not changes:
            self._reply(msg_id, None)
            return
        self._reply(msg_id, {"changes": changes})

    def _on_references(self, msg_id, params):
        uri = params["textDocument"]["uri"]
        pos = params["position"]
        lines = self.sources.get(uri, [])
        if pos["line"] >= len(lines):
            self._reply(msg_id, [])
            return
        w = _word_at(lines[pos["line"]], pos["character"])
        if not w:
            self._reply(msg_id, [])
            return
        name = w[0]
        out = []
        for path, line, col in self.index.references(name):
            out.append({"uri": _path_to_uri(path),
                        "range": {"start": {"line": line - 1, "character": col},
                                  "end": {"line": line - 1, "character": col + len(name)}}})
        self._reply(msg_id, out)

    def _on_workspace_symbol(self, msg_id, params):
        query = (params.get("query") or "").lower()
        out = []
        for name in sorted(self.index.by_name):
            if query and query not in name.lower():
                continue
            sym = self.index.by_name[name][0]
            out.append({"name": name, "kind": self._KIND_MAP.get(sym.kind, 6),
                        "location": {"uri": _path_to_uri(sym.path),
                                     "range": {"start": {"line": sym.line - 1, "character": sym.col},
                                               "end": {"line": sym.line - 1,
                                                       "character": sym.col + len(name)}}}})
            if len(out) >= 200:
                break
        self._reply(msg_id, out)

    def _on_code_action(self, msg_id, params):
        uri = params["textDocument"]["uri"]
        an = self.analyses.get(uri)
        lines = self.sources.get(uri, [])
        if an is None:
            self._reply(msg_id, [])
            return
        sel = params.get("range", {}).get("start", {}).get("line", -1)
        h = self.head_skips.get(uri, 0)
        actions = []
        for iss in an.issues:
            body_ln = iss.line - 1
            src_ln = body_ln + h
            if sel >= 0 and src_ln != sel:
                continue
            ln = src_ln
            if ln < 0 or ln >= len(lines):
                continue
            if iss.code == "unused-import":
                end_line = ln + 1 if ln + 1 < len(lines) else ln
                end_char = 0 if end_line != ln else len(lines[ln])
                actions.append({
                    "title": f"删除未使用的导入：{iss.msg}",
                    "kind": "quickfix",
                    "edit": {"changes": {uri: [{
                        "range": {"start": {"line": ln, "character": 0},
                                  "end": {"line": end_line, "character": end_char}},
                        "newText": ""}]}},
                })
            elif iss.code == "unused-variable":
                w = _word_at(lines[ln], iss.col)
                if not w:
                    continue
                var = w[0]
                if var.startswith("_"):
                    continue
                actions.append({
                    "title": f"给未使用变量加下划线前缀：{iss.msg}",
                    "kind": "quickfix",
                    "edit": {"changes": {uri: [{
                        "range": {"start": {"line": ln, "character": iss.col},
                                  "end": {"line": ln, "character": iss.col + len(var)}},
                        "newText": "_" + var}]}},
                })
            elif iss.code == "undefined-name":
                w = _word_at(lines[ln], max(0, iss.col))
                if not w:
                    continue
                var = w[0]
                defs = self.index.definition(var)
                if defs:
                    target = defs[0]
                    rel = os.path.relpath(target.path, self.index.root)
                    mod = rel[:-6] if rel.endswith(".impor") else rel
                    mod = os.path.basename(mod)
                    at = 1 if lines and lines[0].strip().startswith("important<<") else 0
                    actions.append({
                        "title": f"从 {rel} 导入 {var}",
                        "kind": "quickfix",
                        "edit": {"changes": {uri: [{
                            "range": {"start": {"line": at, "character": 0},
                                      "end": {"line": at, "character": 0}},
                            "newText": f"from {mod} import {var}\n"}]}},
                    })
                elif re.match(r"^\s*" + re.escape(var) + r"\s*(=|:=", lines[ln]):
                    actions.append({
                        "title": f"声明为局部变量：var {var}",
                        "kind": "quickfix",
                        "edit": {"changes": {uri: [{
                            "range": {"start": {"line": ln, "character": iss.col},
                                      "end": {"line": ln, "character": iss.col}},
                            "newText": "var "}]}},
                    })
                else:
                    actions.append({
                        "title": f"定义变量：{var}",
                        "kind": "quickfix",
                        "edit": {"changes": {uri: [{
                            "range": {"start": {"line": ln, "character": 0},
                                      "end": {"line": ln, "character": 0}},
                            "newText": f"var {var} = 0\n"}]}},
                    })
        for tip in self.type_issues.get(uri, []):
            src_ln = tip.line - 1 + h
            if sel >= 0 and src_ln != sel:
                continue
            if not tip.fix:
                continue
            fix_ln, new_line = tip.fix
            fix_ln += h
            if fix_ln < 0 or fix_ln >= len(lines):
                continue
            actions.append({
                "title": f"按类型修正：{tip.msg}",
                "kind": "quickfix",
                "edit": {"changes": {uri: [{
                    "range": {"start": {"line": fix_ln, "character": 0},
                              "end": {"line": fix_ln, "character": len(lines[fix_ln])}},
                    "newText": new_line}]}},
            })
        self._reply(msg_id, actions)

    def _on_formatting(self, msg_id, params):
        """v3.9：整篇格式化（rangeFormatting 复用同一实现）。"""
        uri = params["textDocument"]["uri"]
        text = self.docs.get(uri)
        if text is None:
            self._reply(msg_id, None)
            return
        try:
            from urllib.parse import unquote, urlparse
            path = unquote(urlparse(uri).path or "")
            start_dir = os.path.dirname(path) if path else os.getcwd()
            opts = fmt_options_from_dir(start_dir)
            fmt_opts = (params.get("options") or {})
            indent = fmt_opts.get("tabSize") or opts.get("indent_width", 4)
            max_len = opts.get("max_line_length", 0)
            formatted = fmt_source(text, int(indent), int(max_len))
        except Exception:
            self._reply(msg_id, None)
            return
        if formatted == text:
            self._reply(msg_id, [])
            return
        end_line = text.count("\n")
        end_char = len(text.rsplit("\n", 1)[-1])
        self._reply(msg_id, [{
            "range": {"start": {"line": 0, "character": 0},
                      "end": {"line": end_line, "character": end_char}},
            "newText": formatted,
        }])

    # ---------- v3.7.4：语义高亮 / 折叠 / inlay / lens / 高亮 / prepareRename ----------
    def _on_semantic_tokens(self, msg_id, params):
        uri = params["textDocument"]["uri"]
        lines = self.sources.get(uri, [])
        h = self.head_skips.get(uri, 0)
        an = self.analyses.get(uri)
        name_kind, decl = {}, {}
        if an is not None:
            rank = {"function": 5, "class": 4, "parameter": 3,
                    "variable": 2, "property": 1, "namespace": 0}
            for name, syms in an.symbols.items():
                for sym in syms:
                    kind = _SEM_KIND.get(sym.kind)
                    if not kind:
                        continue
                    if name not in name_kind or rank.get(kind, 0) >= rank.get(name_kind[name], 0):
                        name_kind[name] = kind
                    decl[(sym.line - 1 + h, sym.col)] = kind
        data = []
        prev_line = prev_col = 0
        for ln, col, word in _code_words(lines):
            if word in KEYWORDS:
                ttype = "keyword"
            elif word in BUILTIN_NAMES:
                ttype = "function"
            elif word in name_kind:
                ttype = name_kind[word]
            else:
                continue
            idx = _SEM_TYPE_INDEX[ttype]
            mods = 1 if (ln, col) in decl else 0
            if data and ln == prev_line:
                delta_line, delta_start = 0, col - prev_col
            else:
                delta_line, delta_start = ln - prev_line, col
            data.extend([delta_line, delta_start, len(word), idx, mods])
            prev_line, prev_col = ln, col
        self._reply(msg_id, {"data": data})

    def _on_folding_range(self, msg_id, params):
        uri = params["textDocument"]["uri"]
        lines = self.sources.get(uri, [])
        out = []
        stack = []
        import_start = None
        in_block = False
        block_start = 0
        prev_indent, prev_i = -1, 0
        for i, line in enumerate(lines):
            stripped = line.strip()
            if in_block:
                if "*/" in line:
                    out.append({"startLine": block_start, "endLine": i, "kind": "comment"})
                    in_block = False
                continue
            if "/*" in line:
                in_block, block_start = True, i
                if "*/" in line.split("/*", 1)[1]:
                    in_block = False
            is_import = bool(re.match(r"^(import|from)\b", stripped))
            if is_import:
                if import_start is None:
                    import_start = i
            elif import_start is not None:
                if i - import_start > 1:
                    out.append({"startLine": import_start, "endLine": i - 1, "kind": "imports"})
                import_start = None
            if not stripped or stripped.startswith("#") or stripped.startswith("//"):
                continue
            indent = len(line) - len(line.lstrip(" \t"))
            if prev_indent >= 0 and indent > prev_indent:
                stack.append((prev_i, prev_indent))
            while stack and indent <= stack[-1][1]:
                start, _ = stack.pop()
                if i - 1 > start:
                    out.append({"startLine": start, "endLine": i - 1, "kind": "region"})
            prev_indent, prev_i = indent, i
        if import_start is not None and len(lines) - 1 - import_start >= 1:
            out.append({"startLine": import_start, "endLine": len(lines) - 1, "kind": "imports"})
        while stack:
            start, _ = stack.pop()
            if len(lines) - 1 > start:
                out.append({"startLine": start, "endLine": len(lines) - 1, "kind": "region"})
        self._reply(msg_id, out)

    def _on_inlay_hint(self, msg_id, params):
        uri = params["textDocument"]["uri"]
        lines = self.sources.get(uri, [])
        out = []
        for i, line in enumerate(lines):
            m = re.match(r"^(\s*)(?:var|const|let)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+)$", line)
            if not m:
                continue
            rhs = m.group(3).strip()
            if rhs.startswith(('"', "'", '"""', "'''")):
                typ = "string"
            elif rhs.startswith("[") or rhs.startswith("list("):
                typ = "list"
            elif rhs.startswith("{") or rhs.startswith("dict("):
                typ = "dict"
            elif re.match(r"^(true|false)\b", rhs):
                typ = "bool"
            elif re.match(r"^-?\d+$", rhs):
                typ = "int"
            elif re.match(r"^-?\d+\.\d+$", rhs):
                typ = "float"
            else:
                continue
            col = m.start(2) + len(m.group(2))
            out.append({"position": {"line": i, "character": col},
                        "label": ": " + typ, "kind": 1, "paddingLeft": False})
        self._reply(msg_id, out)

    def _on_code_lens(self, msg_id, params):
        uri = params["textDocument"]["uri"]
        lines = self.sources.get(uri, [])
        out = []
        for i, line in enumerate(lines):
            m = re.match(r"^(\s*)(?:func|def|class|struct|exception)\s+([A-Za-z_][A-Za-z0-9_]*)", line)
            if not m:
                continue
            name = m.group(2)
            try:
                count = len(self.index.references(name))
            except Exception:
                count = 0
            out.append({"range": {"start": {"line": i, "character": 0},
                                  "end": {"line": i, "character": 0}},
                        "command": {"title": f"{count} 处引用",
                                    "command": "impor.showReferences",
                                    "arguments": [uri, i, m.start(2), name]}})
        self._reply(msg_id, out)

    def _on_document_highlight(self, msg_id, params):
        uri = params["textDocument"]["uri"]
        pos = params["position"]
        lines = self.sources.get(uri, [])
        if pos["line"] >= len(lines):
            self._reply(msg_id, [])
            return
        w = _word_at(lines[pos["line"]], pos["character"])
        if not w:
            self._reply(msg_id, [])
            return
        name = w[0]
        pat = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])")
        out = []
        for i, line in enumerate(lines):
            for m in pat.finditer(line):
                out.append({"range": {"start": {"line": i, "character": m.start()},
                                      "end": {"line": i, "character": m.end()}},
                            "kind": 1})
        self._reply(msg_id, out)

    def _on_prepare_rename(self, msg_id, params):
        uri = params["textDocument"]["uri"]
        pos = params["position"]
        lines = self.sources.get(uri, [])
        if pos["line"] >= len(lines):
            self._reply(msg_id, None)
            return
        w = _word_at(lines[pos["line"]], pos["character"])
        if not w:
            self._reply(msg_id, None)
            return
        name = w[0]
        an = self.analyses.get(uri)
        known = name in getattr(an, "symbols", {}) if an is not None else False
        if not known:
            try:
                known = bool(self.index.definition(name, self._uri_to_path(uri)))
            except Exception:
                known = False
        if not known:
            self._reply(msg_id, None)
            return
        self._reply(msg_id, {
            "range": {"start": {"line": pos["line"], "character": w[1]},
                      "end": {"line": pos["line"], "character": w[2]}},
            "placeholder": name,
        })

    # ---------- 主循环 ----------
    def serve(self):
        while not self.shutdown:
            try:
                msg = self._read_message()
            except Exception:
                return 0
            if msg is None:
                return 0
            method = msg.get("method")
            mid = msg.get("id")
            if method == "initialize":
                self._on_initialize(mid, msg.get("params") or {})
            elif method == "initialized":
                pass
            elif method == "shutdown":
                self.shutdown = True
                if mid is not None:
                    self._reply(mid, None)
            elif method == "exit":
                return 0
            elif method == "textDocument/didOpen":
                p = msg["params"]
                self._update_doc(p["textDocument"]["uri"], p["textDocument"]["text"])
            elif method == "textDocument/didChange":
                p = msg["params"]
                uri = p["textDocument"]["uri"]
                changes = p.get("contentChanges", [])
                if changes:
                    self._update_doc(uri, changes[-1].get("text", ""))
            elif method == "textDocument/didClose":
                uri = msg["params"]["textDocument"]["uri"]
                self.docs.pop(uri, None)
                self.analyses.pop(uri, None)
                self.sources.pop(uri, None)
                self.head_skips.pop(uri, None)
            elif method == "textDocument/definition":
                self._on_definition(mid, msg["params"])
            elif method == "textDocument/hover":
                self._on_hover(mid, msg["params"])
            elif method == "textDocument/signatureHelp":
                self._on_signature_help(mid, msg["params"])
            elif method == "textDocument/documentSymbol":
                self._on_document_symbol(mid, msg["params"])
            elif method == "textDocument/completion":
                self._on_completion(mid, msg["params"])
            elif method == "textDocument/rename":
                self._on_rename(mid, msg["params"])
            elif method == "textDocument/references":
                self._on_references(mid, msg["params"])
            elif method == "workspace/symbol":
                self._on_workspace_symbol(mid, msg["params"])
            elif method == "textDocument/codeAction":
                self._on_code_action(mid, msg["params"])
            elif method == "textDocument/semanticTokens/full":
                self._on_semantic_tokens(mid, msg["params"])
            elif method == "textDocument/foldingRange":
                self._on_folding_range(mid, msg["params"])
            elif method == "textDocument/inlayHint":
                self._on_inlay_hint(mid, msg["params"])
            elif method == "textDocument/codeLens":
                self._on_code_lens(mid, msg["params"])
            elif method == "textDocument/documentHighlight":
                self._on_document_highlight(mid, msg["params"])
            elif method == "textDocument/prepareRename":
                self._on_prepare_rename(mid, msg["params"])
            elif method in ("textDocument/formatting", "textDocument/rangeFormatting"):
                self._on_formatting(mid, msg["params"])
            elif mid is not None:
                self._reply(mid, None)
        return 0


def _code_words(lines):
    """返回源码里不在注释/字符串中的标识符 [(行, 列, word)]（0 起）。"""
    out = []
    in_block = False
    in_triple = None
    for ln, line in enumerate(lines):
        i, n = 0, len(line)
        while i < n:
            if in_block:
                j = line.find("*/", i)
                if j < 0:
                    break
                in_block, i = False, j + 2
                continue
            if in_triple:
                j = line.find(in_triple, i)
                if j < 0:
                    break
                in_triple, i = None, j + 3
                continue
            ch = line[i]
            if ch == "#" or line.startswith("//", i):
                break
            if line.startswith("/*", i):
                in_block, i = True, i + 2
                continue
            if line.startswith('"""', i) or line.startswith("'''", i):
                in_triple, i = line[i:i + 3], i + 3
                continue
            if ch in ('"', "'"):
                q, i = ch, i + 1
                while i < n:
                    if line[i] == "\\":
                        i += 2
                        continue
                    if line[i] == q:
                        i += 1
                        break
                    i += 1
                continue
            if ch.isalpha() or ch == "_":
                start = i
                while i < n and (line[i].isalnum() or line[i] == "_"):
                    i += 1
                out.append((ln, start, line[start:i]))
                continue
            i += 1
    return out

    p = os.path.abspath(path).replace(os.sep, "/")
    if not p.startswith("/"):
        p = "/" + p
    return "file://" + p.replace(" ", "%20")


def _range_at(line, col):
    return {"start": {"line": max(0, line - 1), "character": col},
            "end": {"line": max(0, line - 1), "character": col + 1}}


def _location(uri, line, col, head_skip=0):
    ln = line + head_skip
    return {"uri": uri,
            "range": {"start": {"line": ln - 1, "character": col},
                      "end": {"line": ln - 1, "character": col}}}


def _symbol_kind(kind):
    return {"var": 13, "def": 12, "class": 5, "exception": 22, "import": 2}.get(kind, 13)


def _hover_md(sym, lines, head_skip=0):
    kind_desc = {"var": "变量", "param": "参数", "def": "函数", "class": "类",
                 "exception": "异常类型", "import": "导入", "for": "循环变量",
                 "catch": "捕获变量", "match": "模式绑定", "field": "类字段"}
    desc = kind_desc.get(sym.kind, sym.kind)
    src_line = ""
    ln = sym.line + head_skip
    if 1 <= ln <= len(lines):
        src_line = lines[ln - 1].strip()
    # v3.5：`##` 文档注释进 hover（文档与代码绑定）
    doc_md = ""
    doc = getattr(sym, "doc", None)
    if doc:
        doc_md = "\n\n" + "\n".join(
            ("> " + d.strip()) if d.strip() else ">" for d in doc.split("\n"))
    if sym.kind == "def" and src_line:
        # 截取签名部分
        return f"```impor\n{src_line}\n```\n\n{desc} `{sym.name}`{doc_md}"
    return (f"**{desc}** `{sym.name}`\n\n```impor\n{src_line}\n```{doc_md}" if src_line
            else f"**{desc}** `{sym.name}`{doc_md}")


def main_lsp(args):
    """impor lsp：stdio 前台运行语言服务器。"""
    root = None
    if args and not args[0].startswith("-"):
        root = args[0]
    # 输出中不得混入任何非 LSP 内容 → 所有日志走 stderr
    server = LanguageServer(workspace_root=root)
    return server.serve()
