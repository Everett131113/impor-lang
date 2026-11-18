# -*- coding: utf-8 -*-
"""Impor 12 课教学系统（v7.3）。

每课包含目标、讲解、起步代码、参考答案与自动判题期望输出。
命令：
  impor teach                   课程表
  impor teach 3                 第 3 课讲义与练习
  impor teach 3 --exercise      写出练习文件 lesson03_exercise.impor
  impor teach 3 --answer        写出参考答案 lesson03_answer.impor
  impor teach 3 --write         同时写出练习与答案
  impor teach grade 3 FILE      运行 FILE，与第 3 课期望输出比对
"""
import contextlib
import io
import os
import sys

LESSONS = [
    {
        "id": 1, "title": "第一行输出", "goal": "认识 print 与字符串",
        "teach": "print 会把参数按空格连接后输出；字符串用双引号包裹。",
        "starter": '# 目标：输出 你好，Impor\n# TODO: 把下面替换成正确的 print\nprint("TODO")\n',
        "answer": 'print("你好，Impor")\n',
        "expect": "你好，Impor",
    },
    {
        "id": 2, "title": "变量与算术", "goal": "声明变量并做四则运算",
        "teach": "var 声明变量；+ - * / 是四则运算，整数除法可用 div。",
        "starter": 'var a = 7\nvar b = 5\n# TODO: 输出 和/差/积\n',
        "answer": 'var a = 7\nvar b = 5\nprint("和:", a + b)\nprint("差:", a - b)\nprint("积:", a * b)\n',
        "expect": "和: 12\n差: 2\n积: 35",
    },
    {
        "id": 3, "title": "条件分支", "goal": "用 if 做分数分级",
        "teach": "if 条件后接缩进块；比较运算 == > < >= <= 返回布尔值。",
        "starter": 'var score = 85\n# TODO: 90 分以上输出“优秀”，否则输出“继续加油”\n',
        "answer": 'var score = 85\nif score >= 90\n    print("优秀")\nif score < 90\n    print("继续加油")\n',
        "expect": "继续加油",
    },
    {
        "id": 4, "title": "循环累加", "goal": "用 for 与 range 求和",
        "teach": "for x in range(1, 11) 会依次取 1 到 10；累加要用 var 保存。",
        "starter": 'var total = 0\n# TODO: 累加 1..10 并输出\n',
        "answer": 'var total = 0\nfor i in range(1, 11)\n    total = total + i\nprint("总和:", total)\n',
        "expect": "总和: 55",
    },
    {
        "id": 5, "title": "数组", "goal": "创建数组、取长度、求和与最大值",
        "teach": "数组用 [ ]；len 取长度，arr[i] 取元素，sum_arr / max_arr 做统计。",
        "starter": 'var data = [3, 1, 4, 1, 5]\n# TODO: 输出长度、总和、最大值\n',
        "answer": 'var data = [3, 1, 4, 1, 5]\nprint("长度:", len(data))\nprint("总和:", sum_arr(data))\nprint("最大值:", max_arr(data))\n',
        "expect": "长度: 5\n总和: 14\n最大值: 5",
    },
    {
        "id": 6, "title": "函数", "goal": "定义函数、传参与返回",
        "teach": "func 定义函数；return 返回值；调用时按位置传参。",
        "starter": '# TODO: 定义 square(x)，返回 x*x，并输出 square(7)\n',
        "answer": 'func square(x)\n    return x * x\nprint("平方:", square(7))\n',
        "expect": "平方: 49",
    },
    {
        "id": 7, "title": "字符串处理", "goal": "大小写、长度与切片",
        "teach": "upper / lower / len 可处理字符串；s[0:3] 取前三个字符。",
        "starter": 'var s = "impor"\n# TODO: 输出大写、长度、前三个字符\n',
        "answer": 'var s = "impor"\nprint("大写:", upper(s))\nprint("长度:", len(s))\nprint("前三:", s[0:3])\n',
        "expect": "大写: IMPOR\n长度: 5\n前三: imp",
    },
    {
        "id": 8, "title": "字典", "goal": "键值读写与 get",
        "teach": "字典用 {键: 值}；stock[key] 读写，get 在键缺失时给默认值。",
        "starter": 'var stock = {"apple": 3, "pear": 5}\n# TODO: 输出苹果数量、香蕉数量（默认 0）\n',
        "answer": 'var stock = {"apple": 3, "pear": 5}\nprint("苹果:", stock.get("apple", 0))\nprint("香蕉:", stock.get("banana", 0))\n',
        "expect": "苹果: 3\n香蕉: 0",
    },
    {
        "id": 9, "title": "类与对象", "goal": "定义类、构造函数与方法",
        "teach": "class 内用 def __init__ 初始化，self 访问字段，new 创建实例。",
        "starter": 'class Counter\n    def __init__()\n        set self.n = 0\n# TODO: 增加 add 方法并输出 count\n',
        "answer": 'class Counter\n    def __init__()\n        set self.n = 0\n    def add(x)\n        set self.n = self.n + x\n    def value()\n        return self.n\n\nvar c = new Counter()\nc.add(3)\nc.add(4)\nprint("计数:", c.value())\n',
        "expect": "计数: 7",
    },
    {
        "id": 10, "title": "枚举与匹配", "goal": "enum + match 的穷尽分支",
        "teach": "enum 定义常量；match 按分支执行；没有 else 时类型检查会要求覆盖全部成员。",
        "starter": 'enum Color { RED, GREEN, BLUE }\nvar c = Color.GREEN\n# TODO: 用 match 输出 红/绿/蓝\n',
        "answer": 'enum Color { RED, GREEN, BLUE }\nvar c = Color.GREEN\nmatch c\n    case Color.RED:\n        print("红")\n    case Color.GREEN:\n        print("绿")\n    case Color.BLUE:\n        print("蓝")\n',
        "expect": "绿",
    },
    {
        "id": 11, "title": "异常处理", "goal": "抛出与捕获自定义异常",
        "teach": "exception 定义异常，raise 抛出，try / catch / finally 捕获与收尾。",
        "starter": 'exception ScoreError\n# TODO: 抛出并捕获，输出 捕获异常\n',
        "answer": 'exception ScoreError\nfunc check(score)\n    if score > 100\n        raise ScoreError("非法成绩")\n    return score\n\ntry\n    check(120)\ncatch ScoreError e\n    print("捕获异常:", e.msg)\nfinally\n    print("检查结束")\n',
        "expect": "捕获异常: 非法成绩\n检查结束",
    },
    {
        "id": 12, "title": "闭包与高阶函数", "goal": "返回函数并在调用之间保留状态",
        "teach": "外层函数的局部变量会被内层函数捕获；make_counter 每次调用都保留自己的计数。",
        "starter": 'func make_counter()\n    var n = 0\n    # TODO: 返回一个每次 +1 的函数\n',
        "answer": 'func make_counter()\n    var n = 0\n    func next()\n        set n = n + 1\n        return n\n    return next\n\nvar c = make_counter()\nprint(c())\nprint(c())\n',
        "expect": "1\n2",
    },
]


def get_lesson(n):
    try:
        n = int(n)
    except (TypeError, ValueError):
        return None
    for lesson in LESSONS:
        if lesson["id"] == n:
            return lesson
    return None


def list_lessons():
    print("Impor 12 课：从第一行输出到闭包")
    print("-" * 64)
    for lesson in LESSONS:
        print("  %2d  %-12s  %s" % (lesson["id"], lesson["title"], lesson["goal"]))
    print("-" * 64)
    print("用法：impor teach 3    impor teach 3 --write    impor teach grade 3 FILE")


def show_lesson(n):
    lesson = get_lesson(n)
    if not lesson:
        print("没有第 %s 课（共 12 课）" % n, file=sys.stderr)
        return 2
    print("第 %d 课 · %s" % (lesson["id"], lesson["title"]))
    print("目标：%s" % lesson["goal"])
    print("讲解：%s" % lesson["teach"])
    print("\n练习：")
    print(lesson["starter"].rstrip())
    print("\n提示：先让程序能运行，再用 impor teach grade %d 你的文件.impor 判题。" % lesson["id"])
    return 0


def write_lesson(n, which, out_dir=None):
    lesson = get_lesson(n)
    if not lesson:
        print("没有第 %s 课" % n, file=sys.stderr)
        return 2
    out_dir = out_dir or os.getcwd()
    paths = []
    if which in ("exercise", "both"):
        p = os.path.join(out_dir, "lesson%02d_exercise.impor" % lesson["id"])
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(lesson["starter"])
        paths.append(p)
    if which in ("answer", "both"):
        p = os.path.join(out_dir, "lesson%02d_answer.impor" % lesson["id"])
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(lesson["answer"])
        paths.append(p)
    for p in paths:
        print("已写出：%s" % p)
    return 0


def run_source(src, path=""):
    """在进程内运行一段 Impor 源码，返回 (stdout, error)。"""
    from .interpreter import Interpreter, parse_source
    lines = []
    old_argv = sys.argv[:]
    sys.argv = [path or "teach.impor"]
    try:
        tree = parse_source(src)
        Interpreter(log_cb=lines.append, filename=path or None).run(tree)
    except Exception as e:
        return "\n".join(lines), str(e)
    finally:
        sys.argv = old_argv
    return "\n".join(lines), ""


def grade(n, path):
    lesson = get_lesson(n)
    if not lesson:
        print("没有第 %s 课" % n, file=sys.stderr)
        return 2
    if not os.path.isfile(path):
        print("找不到文件：%s" % path, file=sys.stderr)
        return 2
    with open(path, encoding="utf-8") as f:
        src = f.read()
    out, err = run_source(src, path)
    if err:
        print("运行失败：%s" % err, file=sys.stderr)
        print("判题结果：未通过（程序没有正常结束）", file=sys.stderr)
        return 1
    got = "\n".join(x.rstrip() for x in out.replace("\r\n", "\n").split("\n")).strip()
    want = "\n".join(x.rstrip() for x in lesson["expect"].split("\n")).strip()
    if got == want:
        print("第 %d 课判题通过 ✓" % lesson["id"])
        print("期望输出：")
        print(want)
        return 0
    print("第 %d 课判题未通过 ✗" % lesson["id"])
    print("期望输出：")
    print(want)
    print("实际输出：")
    print(got if got else "（无输出）")
    return 1
