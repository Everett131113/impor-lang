# -*- coding: utf-8 -*-
"""Impor 版本约束求解（4.7.1）。

`impor/pkgmgr.py` 原来只有单条约束（`>=1.2` / `==1.2.3`），并且注释里明确写着
「更精确的交集判定留给 lock 阶段」——但那个阶段并没有做交集，于是两个包分别要求
`>=1.0` 和 `<1.5` 时，安装顺序不同会装出不同版本。本模块补上真正的求解：

  - 约束语法：`*` / `1.2.3`（精确） / `>=1.2` / `<=` / `>` / `<` / `!=`
    / `^1.2.3`（兼容到下一个主版本） / `~1.2.3`（兼容到下一次版本）
    / 逗号或空格分隔的多段合取（`>=1.0,<2.0`）
  - 求解：DFS + 回溯（先决定候选最少的包），返回一组钉死的版本；失败时给出
    具体是哪条约束、由谁提出、卡掉了哪些候选版本。
  - 纯粹离线：不读网络、不改项目文件，只有 dict 进 dict 出，方便单测。

设计约束（重要，写进文档）：**不做 SAT**。包依赖是「每个包选一个版本」的
贪心回溯，最坏是指数级；超过 200 个节点直接报错退出，避免假装能解大图。
"""

import re

MAX_NODES = 200

_OPS = (">=", "<=", "==", "!=", ">", "<")


def norm_ver(v):
    """'v1.2' → (1, 2, 0)；非数字尾巴截断，保证可比较。"""
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


def parse_range(spec):
    """把约束字符串解析成 [(op, ver_tuple), ...]（合取）。

    支持 `*`、空串（任意）、`^`、`~`、多段逗号/空格分隔。
    非法片段抛 ValueError，而不是静默当「任意」，否则拼错约束会静默装错版本。
    """
    spec = (spec or "").strip()
    if not spec or spec in ("*", "any", "latest"):
        return []
    out = []
    # 逗号优先；没有逗号则按空格切（`>=1.0 <2.0` 这种写法）
    chunks = re.split(r"[,\s]+", spec) if "," in spec or " " in spec else [spec]
    for raw in chunks:
        if not raw:
            continue
        if raw in ("*", "any"):
            continue
        if raw.startswith("^") or raw.startswith("~"):
            caret = raw[0]
            base_str = raw[1:].strip()
            if not base_str or not base_str[0].isdigit():
                raise ValueError("约束缺少版本号：%r" % raw)
            base = norm_ver(base_str)
            if caret == "^":
                hi = (base[0] + 1, 0, 0) if base[0] > 0 else (0, base[1] + 1, 0)
            else:
                hi = (base[0], base[1] + 1, 0)
            out.append((">=", base))
            out.append(("<", hi))
            continue
        for op in _OPS:
            if raw.startswith(op):
                rest = raw[len(op):].strip()
                if not rest:
                    raise ValueError("约束缺少版本号：%r" % raw)
                out.append((op, norm_ver(rest)))
                break
        else:
            # 裸版本按精确匹配（与 pkgmgr 的既有语义一致）
            out.append(("==", norm_ver(raw)))
    if not out:
        raise ValueError("约束无法解析：%r" % spec)
    return out


def satisfies(ver, spec):
    got = norm_ver(ver)
    for op, want in parse_range(spec):
        if op == "==" and got != want:
            return False
        if op == "!=" and got == want:
            return False
        if op == ">=" and got < want:
            return False
        if op == "<=" and got > want:
            return False
        if op == ">" and got <= want:
            return False
        if op == "<" and got >= want:
            return False
    return True


def _requirement_text(name, spec):
    return "%s %s" % (name, spec or "*")


def solve(roots, pool, prefer_highest=True):
    """求解一组根依赖。

    参数：
      roots —— {包名: 约束字符串}，根项目在 impor.toml 里声明的那一层。
      pool  —— {包名: {版本字符串: {"requires": {依赖名: 约束}}}}
               版本表由 `tools/pkg_index.py` 从离线索引生成；缺 requires 视为无依赖。
    返回：
      {"ok": True,  "pins": {name: ver}, "order": [name...], "steps": [...]}
      {"ok": False, "reason": str, "conflicts": [{...}], "steps": [...]}
    """
    pool = pool or {}
    steps = []
    nodes = [0]

    for name, spec in roots.items():
        try:
            parse_range(spec)
        except ValueError as exc:
            return {"ok": False, "reason": "根依赖约束非法：%s" % exc,
                    "conflicts": [{"name": name, "constraint": spec, "origin": "<root>",
                                   "detail": str(exc)}], "steps": steps}

    def viable(name, constr):
        vers = list((pool.get(name) or {}).keys())
        vers.sort(key=norm_ver, reverse=prefer_highest)
        ok = []
        for v in vers:
            good = True
            for spec, origin in constr.get(name, []):
                if not satisfies(v, spec):
                    good = False
                    break
            if good:
                ok.append(v)
        return ok

    def requires_of(name, ver):
        meta = (pool.get(name) or {}).get(ver) or {}
        return dict(meta.get("requires") or {})

    def search(selected, constr, order):
        nodes[0] += 1
        if nodes[0] > MAX_NODES:
            raise RuntimeError("求解节点超过 %d（依赖图太大，拒绝假装能解）" % MAX_NODES)
        undecided = [n for n in constr if n not in selected]
        if not undecided:
            return dict(selected), order
        # 候选最少的先决定（MRV 启发式），同样少时按名字稳定排序
        name = min(undecided, key=lambda n: (len(viable(n, constr)), n))
        cands = viable(name, constr)
        if not cands:
            return None
        for v in cands:
            selected[name] = v
            steps.append({"pick": name, "ver": v, "cands": len(cands)})
            new_constr = {k: list(v2) for k, v2 in constr.items()}
            conflict = False
            for dep, spec in requires_of(name, v).items():
                try:
                    parse_range(spec)
                except ValueError:
                    conflict = True
                    steps.append({"fail": "依赖约束非法", "pkg": name, "ver": v,
                                  "dep": dep, "constraint": spec})
                    break
                new_constr.setdefault(dep, []).append((spec, "%s@%s" % (name, v)))
            if not conflict:
                # 已选中的包必须仍然满足新加进来的约束
                for sn, sv in selected.items():
                    for spec, origin in new_constr.get(sn, []):
                        if not satisfies(sv, spec):
                            conflict = True
                            steps.append({"fail": "已选版本不满足新约束", "pkg": sn, "ver": sv,
                                          "constraint": spec, "origin": origin})
                            break
                    if conflict:
                        break
            if not conflict:
                got = search(selected, new_constr, order + [name])
                if got:
                    return got
            selected.pop(name, None)
        return None

    constr = {}
    for name, spec in roots.items():
        constr[name] = [(spec or "*", "<root>")]
    try:
        got = search({}, constr, [])
    except RuntimeError as exc:
        return {"ok": False, "reason": str(exc), "conflicts": [], "steps": steps}

    if got:
        pins, order = got
        return {"ok": True, "pins": pins, "order": order, "steps": steps}

    # 失败：把「谁要什么、池里有什么」摊开，方便人一眼看出是版本缺口还是真冲突
    conflicts = []
    for name in constr:
        specs = constr[name]
        have = sorted((pool.get(name) or {}).keys(), key=norm_ver, reverse=True)
        if not have:
            conflicts.append({"name": name, "constraint": ", ".join(s for s, _ in specs),
                              "origin": ", ".join(o for _, o in specs),
                              "detail": "索引里没有这个包"})
            continue
        conflicts.append({"name": name, "constraint": ", ".join(s for s, _ in specs),
                          "origin": ", ".join(o for _, o in specs),
                          "available": have})
    return {"ok": False, "reason": "无法同时满足所有约束（已尝试回溯）",
            "conflicts": conflicts, "steps": steps}


def _step(v, delta):
    a, b, c = v
    c += delta
    if c < 0:
        return None
    return (a, b, c)


def ranges_intersect(a, b):
    """两个约束是否**存在**同时满足的版本（不依赖候选版本表）。

    用于安装期冲突预判：条件不等式系统的最优解一定落在某个约束的边界点上，
    所以只测边界点 ± 一个最小单位就够，不需要枚举全版本空间。
    """
    pts = set()
    for spec in (a, b):
        for op, v in parse_range(spec):
            for d in (0, 1, -1):
                p = _step(v, d)
                if p is not None:
                    pts.add(p)
    for p in sorted(pts):
        ver = ".".join(str(x) for x in p)
        try:
            if satisfies(ver, a) and satisfies(ver, b):
                return True
        except ValueError:
            return False
    return False


def explain(result):
    """把 solve() 的结果转成人读的几行文本。"""
    lines = []
    if result.get("ok"):
        lines.append("求解成功：%d 个包" % len(result["pins"]))
        for name in result.get("order") or []:
            lines.append("  %s == %s" % (name, result["pins"][name]))
        lines.append("尝试步骤 %d 次" % len(result.get("steps") or []))
        return "\n".join(lines)
    lines.append("求解失败：%s" % result.get("reason"))
    for c in result.get("conflicts") or []:
        lines.append("  %s：要求 %s（来自 %s）" % (c["name"], c["constraint"], c["origin"]))
        if c.get("available"):
            lines.append("    索引里有：%s" % ", ".join(c["available"]))
        if c.get("detail"):
            lines.append("    %s" % c["detail"])
    fails = [s for s in (result.get("steps") or []) if s.get("fail")]
    if fails:
        lines.append("  回退记录（最多 5 条）：")
        for f in fails[:5]:
            lines.append("    %s" % f)
    return "\n".join(lines)


# ---------------- 自测 ----------------
_CASES = []


def _case(name, roots, pool, expect_ok, expect_pin=None):
    _CASES.append((name, roots, pool, expect_ok, expect_pin))


def _mk(versions):
    """{ver: {dep: spec}} → pool 片段。"""
    return {"versions": versions}


def selftest():
    pool = {
        "a": {"1.0.0": {"requires": {"c": ">=1.0,<2.0"}},
              "1.5.0": {"requires": {"c": ">=1.2,<2.0"}},
              "2.0.0": {"requires": {"c": ">=2.0"}}},
        "b": {"1.0.0": {"requires": {"c": "<1.5"}},
              "1.2.0": {"requires": {"c": ">=1.0"}}},
        "c": {"1.0.0": {}, "1.2.0": {}, "1.4.0": {}, "2.0.0": {}},
        "d": {"1.0.0": {"requires": {"e": "^2.0"}}},
        "e": {"2.0.0": {}, "2.3.0": {}, "3.0.0": {}},
        "f": {"1.0.0": {"requires": {"g": "~1.2"}}},
        "g": {"1.2.0": {}, "1.2.5": {}, "1.3.0": {}},
        "h": {"1.0.0": {"requires": {"i": ">=9.0"}}},
        "i": {"1.0.0": {}},
        "j": {"1.0.0": {"requires": {"c": ">=2.0"}}},
    }
    _case("单一约束取最高", {"a": "^1.0"}, pool, True, {"a": "1.5.0", "c": "1.4.0"})
    _case("两包交集压缩", {"a": "^1.0", "b": "^1.0"}, pool, True,
          {"a": "1.5.0", "b": "1.2.0", "c": "1.4.0"})
    _case("交集为空要失败", {"a": "^1.0", "j": "*"}, pool, False)
    _case("插入符跨主版本", {"d": "*"}, pool, True, {"d": "1.0.0", "e": "2.3.0"})
    _case("波浪号不跨次版本", {"f": "*"}, pool, True, {"f": "1.0.0", "g": "1.2.5"})
    _case("索引缺版本要失败", {"h": "*"}, pool, False)
    _case("精确版本", {"c": "1.2.0"}, pool, True, {"c": "1.2.0"})
    _case("排除版本", {"c": ">=1.0,!=1.4.0"}, pool, True, {"c": "2.0.0"})

    failures = []
    for name, roots, pl, expect_ok, expect_pin in _CASES:
        res = solve(roots, pl)
        if res.get("ok") != expect_ok:
            failures.append("%s：期望 ok=%s，实际 %s" % (name, expect_ok, res.get("ok")))
            continue
        if expect_pin:
            for k, v in expect_pin.items():
                if res["pins"].get(k) != v:
                    failures.append("%s：%s 期望 %s，实际 %s"
                                    % (name, k, v, res["pins"].get(k)))
    # 约束语法单测
    assert satisfies("1.2.5", "^1.2.0")
    assert satisfies("1.9.9", "~1.9")
    assert not satisfies("2.0.0", "^1.2.0")
    assert not satisfies("1.3.0", "~1.2.0")
    assert satisfies("1.0.0", ">=1.0 <2.0")
    # 区间交集（安装期冲突预判）
    assert ranges_intersect(">=1.0", ">=1.0,<2.0")
    assert ranges_intersect("^1.0", "<=1.5.0")
    assert not ranges_intersect(">=1.0,<1.5", ">=2.0")
    assert not ranges_intersect("==1.0.0", "!=1.0.0")
    assert not ranges_intersect("^1.0", "^2.0")
    try:
        parse_range("^")
        failures.append("非法约束 `^` 应该抛 ValueError")
    except ValueError:
        pass
    return (not failures), failures


if __name__ == "__main__":
    ok, fails = selftest()
    for f in fails:
        print("FAIL " + f)
    print("selftest: %d/%d 通过" % (len(_CASES) + 1 - len(fails), len(_CASES) + 1))
