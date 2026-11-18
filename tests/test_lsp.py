# -*- coding: utf-8 -*-
"""LSP 语言服务器 v2.5 能力测试：补全 / 重命名 / QuickFix。"""
import io
import json
import unittest

from impor.lsp import LanguageServer

URI = "file:///demo.impor"
SRC = (
    'important<<DAY<<english("English"):\n'
    "import math\n"                 # 未使用导入 → unused-import
    "var counter = 0\n"             # 模块级全局
    "func helper(x)\n"              # 定义 helper（第 4 行）
    "    return x + counter\n"
    "func main()\n"
    "    var temp = 1\n"            # 未使用变量 → unused-variable（第 8 行）
    "    var y = helper(3)\n"       # 引用 helper（第 9 行）
    "    print(y)\n"
    "main()\n"
)


def parse_frames(data):
    """解析 Content-Length 帧序列，返回 body 列表。"""
    frames, i = [], 0
    while True:
        idx = data.find(b"Content-Length: ", i)
        if idx < 0:
            break
        end = data.find(b"\r\n\r\n", idx)
        header = data[idx:end].decode("utf-8", "replace")
        length = int(header.split(":")[1].strip())
        body = data[end + 4: end + 4 + length]
        frames.append(json.loads(body.decode("utf-8")))
        i = end + 4 + length
    return frames


def make_server():
    return LanguageServer(stdin=io.BytesIO(b""), stdout=io.BytesIO(),
                          workspace_root=".")


class TestLspV25(unittest.TestCase):
    def setUp(self):
        self.srv = make_server()
        self.srv._update_doc(URI, SRC)

    def last_reply(self, msg_id):
        frames = parse_frames(self.srv.stdout.getvalue())
        for f in reversed(frames):
            if f.get("id") == msg_id:
                return f
        raise AssertionError(f"未找到 id={msg_id} 的响应: {frames}")

    def test_completion_contains_symbols_and_builtins(self):
        self.srv._on_completion(1, {"textDocument": {"uri": URI}})
        items = self.last_reply(1)["result"]["items"]
        labels = {it["label"] for it in items}
        self.assertIn("helper", labels)      # 本文件函数
        self.assertIn("counter", labels)     # 模块级变量
        self.assertIn("math", labels)        # 导入名
        self.assertIn("print", labels)       # 内建函数
        self.assertIn("func", labels)        # 关键字
        for it in items:
            self.assertIn("kind", it)

    def test_rename_updates_definition_and_references(self):
        # 光标停在 helper 定义处（第 4 行，字符 6），改名为 helper2
        self.srv._on_rename(2, {"textDocument": {"uri": URI},
                                "position": {"line": 3, "character": 6},
                                "newName": "helper2"})
        result = self.last_reply(2)["result"]
        self.assertIsNotNone(result)
        changes = result["changes"][URI]
        locs = {(e["range"]["start"]["line"], e["range"]["start"]["character"])
                for e in changes}
        self.assertIn((3, 5), locs)   # 定义 func helper（源码第 4 行，0 起 3）
        self.assertIn((7, 12), locs)  # 调用 helper(3)（源码第 8 行，0 起 7）

    def test_rename_unknown_symbol_rejected(self):
        self.srv._on_rename(3, {"textDocument": {"uri": URI},
                                "position": {"line": 0, "character": 1},
                                "newName": "x"})
        self.assertIsNone(self.last_reply(3)["result"])

    def test_code_action_unused_import(self):
        self.srv._on_code_action(4, {"textDocument": {"uri": URI},
                                     "range": {"start": {"line": 1, "character": 0},
                                               "end": {"line": 1, "character": 4}}})
        actions = self.last_reply(4)["result"]
        titles = [a["title"] for a in actions]
        self.assertTrue(any("删除未使用的导入" in t for t in titles))

    def test_code_action_unused_variable(self):
        # var temp 在源码第 7 行（0 起 6）
        self.srv._on_code_action(5, {"textDocument": {"uri": URI},
                                     "range": {"start": {"line": 6, "character": 0},
                                               "end": {"line": 6, "character": 4}}})
        actions = self.last_reply(5)["result"]
        titles = [a["title"] for a in actions]
        self.assertTrue(any("未使用变量" in t for t in titles))
        edit = next(a["edit"] for a in actions if "未使用变量" in a["title"])
        new_text = edit["changes"][URI][0]["newText"]
        self.assertEqual(new_text, "_temp")

    def test_diagnostics_include_unused(self):
        frames = parse_frames(self.srv.stdout.getvalue())
        diag = [f for f in frames if f.get("method") == "textDocument/publishDiagnostics"]
        self.assertTrue(diag)
        messages = [d["message"] for d in diag[-1]["params"]["diagnostics"]]
        self.assertTrue(any("未使用导入" in m for m in messages))
        self.assertTrue(any("未使用变量" in m for m in messages))


if __name__ == "__main__":
    unittest.main(verbosity=2)
