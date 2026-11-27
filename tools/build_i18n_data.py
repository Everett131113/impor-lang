# -*- coding: utf-8 -*-
"""构建 Impor 编辑器 UI 语言表。

只抽取会直接显示在编辑器界面上的字符串（控件 text/label、菜单项、标题、
toast / 对话框消息），避免把解释器诊断和数据文案误翻译。
生成的 locales/*.json 是纯静态资源，运行时不联网。
"""
import ast
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = [
    "impor/editor.py", "impor/editor_dialogs.py", "impor/editor_panels.py",
    "impor/editor_pane.py", "impor/editor_widgets.py", "impor/editor_search_ui.py",
    "impor/editor_graph_ui.py", "impor/editor_runtime.py", "impor/studio_ext.py",
]
CJK = re.compile(r"[\u4e00-\u9fff]")
PLACEHOLDER = re.compile(r"(%[-+ #0]*[0-9.*]*[diouxXeEfgGrsa%]|\{[^{}]*\})")
MSG_FUNCS = {
    "toast", "_set_out", "set_status", "showerror", "showinfo", "showwarning",
    "askyesno", "askyesnocancel", "messagebox", "title",
}


def collect():
    found = {}
    for fn in FILES:
        path = os.path.join(ROOT, fn)
        if not os.path.isfile(path):
            continue
        tree = ast.parse(open(path, encoding="utf-8").read())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else (
                func.id if isinstance(func, ast.Name) else "")
            kws = {k.arg: k.value for k in node.keywords if k.arg}
            for key in ("text", "label"):
                if key in kws:
                    mark(kws[key], found)
            if name in MSG_FUNCS and node.args:
                mark(node.args[0], found)
            # add_command / add_checkbutton / add_radiobutton 的 label
            if name.startswith("add_") and "label" in kws:
                mark(kws["label"], found)
    # 纯文本常量也收进来：命令面板/工具栏等以列表传入的标签
    for fn in FILES:
        path = os.path.join(ROOT, fn)
        tree = ast.parse(open(path, encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                value = node.value
                # 过滤长句/文档；只收短标签和直接提示
                if CJK.search(value) and len(value) <= 60 and "\n" not in value:
                    found[value] = found.get(value, 0) + 1
    return sorted(found, key=lambda s: (len(s), s))


def mark(node, found):
    for item in ast.walk(node):
        if isinstance(item, ast.Constant) and isinstance(item.value, str):
            value = item.value
            if CJK.search(value):
                found[value] = found.get(value, 0) + 1


def protect(text):
    tokens = []

    def repl(m):
        tokens.append(m.group(0))
        return f"⟦P{len(tokens) - 1}⟧"

    return PLACEHOLDER.sub(repl, text), tokens


def restore(text, tokens):
    for i, token in enumerate(tokens):
        text = text.replace(f"⟦P{i}⟧", token)
        text = text.replace(f"[P{i}]", token)
        text = text.replace(f"P{i}", token) if False else text
    return text


def translate_batch(items, target):
    """items: [(index, source)] -> {index: translation}"""
    query = "\n".join(f"[[{i}]] {s}" for i, s in items)
    url = ("https://api.mymemory.translated.net/get?q=" + urllib.parse.quote(query)
           + "&langpair=" + urllib.parse.quote("zh-CN|" + target))
    last = None
    for attempt in range(7):
        try:
            raw = urllib.request.urlopen(url, timeout=30).read().decode("utf-8")
            data = json.loads(raw)
            if data.get("responseStatus") != 200:
                last = data.get("responseDetails") or str(data)[:120]
                time.sleep(20 + attempt * 20)
                continue
            body = data["responseData"]["translatedText"]
            if "QUERY LENGTH LIMIT" in body.upper() or "MYMEMORY WARNING" in body.upper():
                raise ValueError(body[:200])
            matches = list(re.finditer(r"\[\[\s*(\d+)\s*\]\]", body))
            out = {}
            for pos, m in enumerate(matches):
                idx = int(m.group(1))
                end = matches[pos + 1].start() if pos + 1 < len(matches) else len(body)
                seg = body[m.end():end].strip("\n").strip()
                out[idx] = seg
            if len(out) != len(items):
                last = f"marker mismatch {len(out)}/{len(items)}"
                time.sleep(10)
                continue
            time.sleep(1.0)  # 免费接口限流：匀速请求，避免 429
            return out
        except Exception as exc:  # 网络/配额重试
            last = f"{type(exc).__name__}: {exc}"
            time.sleep(20 + attempt * 25)
    raise RuntimeError(last)


def main():
    strings = collect()
    print(f"strings={len(strings)} chars={sum(len(s) for s in strings)}", flush=True)
    outdir = os.path.join(ROOT, "impor", "locales")
    os.makedirs(outdir, exist_ok=True)
    for lang, target in (("en_us", "en"), ("zh_tw", "zh-TW")):
        cache_path = os.path.join(outdir, lang + ".json")
        cache = {}
        if os.path.isfile(cache_path):
            try:
                cache = json.load(open(cache_path, encoding="utf-8"))
            except Exception:
                cache = {}
        todo = [s for s in strings if s not in cache]
        print(f"{lang}: todo={len(todo)} cached={len(cache)}", flush=True)
        batch = []
        size = 0

        def flush():
            nonlocal batch, size
            if not batch:
                return
            payload = []
            protected = {}
            for i, s in enumerate(batch):
                p, tokens = protect(s)
                protected[i] = tokens
                payload.append((i, p))
            try:
                got = translate_batch(payload, target)
            except Exception as exc:
                print(f"  skip {len(batch)}: {exc}", flush=True)
                batch, size = [], 0
                return
            for i, s in enumerate(batch):
                text = restore(got.get(i, ""), protected[i])
                lead = s[:len(s) - len(s.lstrip())]
                trail = s[len(s.rstrip()):]
                text = text.strip()
                if text:
                    cache[s] = lead + text + trail
            batch, size = [], 0
            json.dump(cache, open(cache_path, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
            print(f"  progress {len(cache)}/{len(strings)}", flush=True)

        for s in todo:
            # 免费接口按请求数限流：在不触发长度上限的前提下合并短句，
            # 单批控制在 620 字以内，减少请求次数。
            if batch and (size + len(s) + 12 > 620 or len(batch) >= 40):
                flush()
            batch.append(s)
            size += len(s) + 12
        flush()
        print(f"{lang}: translated={len(cache)}/{len(strings)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
