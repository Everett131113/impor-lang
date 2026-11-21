# -*- coding: utf-8 -*-
"""v3.7.2：保留字收敛与软关键字回归。"""
import os
import re
import unittest

from impor.lexer import KEYWORDS, DEPRECATED_ALIASES, CANONICAL_KEYWORDS, SOFT_KEYWORDS
from impor.interpreter import parse_source, Interpreter
from impor.fmt import fmt_source
from impor.parser import Parser
from impor.lexer import tokenize


class TestKeywordAudit(unittest.TestCase):
    def test_every_keyword_has_parser_reference(self):
        """KEYWORDS 里每个词都必须在 parser 源码中有实际出现；空保留字会被抓出来。"""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "impor", "parser.py"), encoding="utf-8") as fh:
            src = fh.read()
        dead = [k for k in sorted(KEYWORDS)
                if not re.search(r"(?<![A-Za-z_])" + re.escape(k) + r"(?![A-Za-z_])", src)]
        self.assertEqual(dead, [], "这些关键字没有语法产生式，应降级为软关键字: %s" % dead)

    def test_soft_keywords_usable_as_identifiers(self):
        words = ["static", "readonly", "async", "await", "this", "defer"]
        for w in words:
            src = "var %s = 1\nprint(%s)\n" % (w, w)
            out = []
            i = Interpreter()
            i.log_cb = out.append
            i.run(parse_source(src))
            self.assertEqual(out, ["1"], w)

    def test_deprecated_aliases_not_in_completion(self):
        self.assertEqual(set(DEPRECATED_ALIASES) & set(CANONICAL_KEYWORDS), set())
        for alias, canon in DEPRECATED_ALIASES.items():
            self.assertIn(canon, CANONICAL_KEYWORDS)

    def test_fmt_emits_canonical_spellings(self):
        src = (
            "def add(a, b) { return a + b }\n"
            "if 1 > 0 { print(1) } elseif 2 > 1 { print(2) }\n"
            "while true { break }\n"
            "var x = 3\n"
        )
        out = fmt_source(src)
        self.assertIn("def add", out)
        self.assertIn("elif", out)
        self.assertIn("while true", out)
        self.assertIn("var x", out)
        self.assertNotIn("loop", out)


if __name__ == "__main__":
    unittest.main()
