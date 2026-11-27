# -*- coding: utf-8 -*-
"""把「稳定性：脚本级执行预算」一章插进桌面《Impor（I++）4.0.0 功能总览.docx》。

数据全部取自 docs/稳定性对照-4.0.json（由 tools/stability_compare.py 实测生成），
表格样式复制文档里已有的 5 列表格，保持字体/边框/宽度一致。
"""
import copy
import json
import os

import docx
from docx.oxml.ns import qn

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCX = r"C:\Users\41482\Desktop\Impor(I++)4.0.0功能总览.docx"
DATA = os.path.join(ROOT, "docs", "稳定性对照-4.0.json")

KIND_CN = {
    "contained": "结构化熔断",
    "hang": "未停（被强杀）",
    "crash": "原生崩溃",
    "output": "刷屏被掐断",
    "completed": "正常退出",
    "error": "普通错误",
    "unavailable": "—",
}
ENGINE_CN = {
    "impor_stable": "Impor --stable",
    "impor_default": "Impor 默认",
    "python": "Python 3.13",
    "c": "C (tcc -O2)",
    "node": "Node.js",
}
CASE_CN = {
    "loop": "无限循环",
    "recurse": "无限递归",
    "alloc": "一次要 100M 元素",
    "flood": "无上限刷屏",
    "divzero": "除零",
}


def clone_table(doc, template_idx, nrows, ncols):
    src = doc.tables[template_idx]._tbl
    tbl = copy.deepcopy(src)
    trs = tbl.findall(qn("w:tr"))
    while len(trs) > nrows:
        tbl.remove(trs[-1])
        trs.pop()
    while len(trs) < nrows:
        tr = copy.deepcopy(trs[-1])
        tbl.append(tr)
        trs.append(tr)
    grid = tbl.find(qn("w:tblGrid"))
    cols = grid.findall(qn("w:gridCol"))
    while len(cols) < ncols:
        grid.append(copy.deepcopy(cols[-1]))
        cols = grid.findall(qn("w:gridCol"))
    while len(cols) > ncols:
        grid.remove(cols[-1])
        cols = grid.findall(qn("w:gridCol"))
    for tr in trs:
        tcs = tr.findall(qn("w:tc"))
        while len(tcs) < ncols:
            tc = copy.deepcopy(tcs[-1])
            tr.append(tc)
            tcs = tr.findall(qn("w:tc"))
        while len(tcs) > ncols:
            tr.remove(tcs[-1])
            tcs = tr.findall(qn("w:tc"))
    return tbl


def set_cell(cell, text):
    p = cell.paragraphs[0]
    runs = p.runs
    if runs:
        runs[0].text = text
        for r in runs[1:]:
            r._element.getparent().remove(r._element)
    else:
        p.add_run(text)
    for extra in cell.paragraphs[1:]:
        extra._element.getparent().remove(extra._element)


def fill(table, rows):
    for tr, vals in zip(table.rows, rows):
        for cell, v in zip(tr.cells, vals):
            set_cell(cell, v)


def main():
    d = docx.Document(DOCX)
    data = json.load(open(DATA, encoding="utf-8"))
    table_map = {e: data["table"][e] for e in data["table"]}
    bench = data.get("bench") or {}
    floors = data.get("floors") or {}
    gen = data.get("generated", "")

    anchor = next(p for p in d.paragraphs if p.text.strip() == "附录 A 保留字（55）")

    def h(text, style):
        return anchor.insert_paragraph_before(text, style)

    def cap(text):
        return anchor.insert_paragraph_before(text, "Caption")

    def body(text):
        return anchor.insert_paragraph_before(text, "Normal")

    def table(rows, template_idx=21, ncols=None):
        ncols = ncols or len(rows[0])
        tbl = clone_table(d, template_idx, len(rows), ncols)
        anchor._p.addprevious(tbl)
        t = docx.table.Table(tbl, d)
        fill(t, rows)
        return t

    h("11 稳定性：脚本级执行预算", "Heading 1")

    h("11.1 定位：把「崩」降级成「一条错误」", "Heading 2")
    body("这一层不追求让程序永不出错，它追求的是让失败**发生在脚本内部、发生得早、并且说人话**。"
         "开启稳定模式后，解释器在每条语句入口过一道预算判断，在每次分配之前、每次输出之后核对额度；"
         "越界时不抛裸异常，而是给出带错误码（E1020–E1024）、行号、为什么和修复建议的普通错误，进程照常退出。")
    body("默认关闭。不打开时，4.0.0 的行为与之前逐字节一致——不计量、不拦截、不改输出；"
         "稳定模式是可选加固，不是新的默认语义。")

    h("11.2 五道闸门", "Heading 2")
    body("五个上限互相独立，任何一个先到就先熔断。两个实现细节决定闸门是不是「真的」："
         "其一，数组与字符串在真正分配内存**之前**按元素数预检，不等系统 OOM；"
         "其二，输出按 UTF-8 字节计数，中文一个字算 3 字节，额度等于真实的磁盘/管道压力。")
    cap("表39  稳定模式的五道闸门")
    table([
        ["闸门", "计量什么", "默认档", "严格档", "错误码"],
        ["语句条数", "执行过的语句数（三后端可比）", "5,000,000", "2,000,000", "E1020"],
        ["墙钟时长", "秒，含 C 后端与外调", "30 s", "10 s", "E1021"],
        ["内存", "进程工作集 RSS", "768 MB", "256 MB", "E1022"],
        ["输出", "已写出的字节", "8192 KB", "1024 KB", "E1023"],
        ["单次分配", "单次请求的元素个数", "8,000,000", "1,000,000", "E1024"],
    ])

    h("11.3 用法：命令行、环境变量、父进程围栏", "Heading 2")
    body("作业批改、在线运行台这类场景用严格档；批量任务与编辑器走环境变量，一次设置全程生效。"
         "命令行参数覆盖环境变量，环境变量覆盖档位默认值——三者都生效，不会出现「设了却没生效」的静默失效。")
    anchor.insert_paragraph_before(
        "impor run --stable app.impor                 // 默认档\n"
        "impor run --stable=strict app.impor          // 批改/评测档\n"
        "impor run --stable --max-seconds=120 ai.impor // 单项放宽\n"
        "set IMPOR_STABLE=1 & set IMPOR_LIMIT_STEPS=20000000  // 环境变量\n"
        "impor stability                              // 8 个敌意用例自检\n"
        "impor stability --limits                     // 打印两档额度", "CodeBlock")
    body("解释器内部的闸门管不到两类事：解释器自身的 bug，以及调进来的原生代码。这两类靠父进程围栏 "
         "run_isolated：子进程 + 采集线程 + 超时强杀，输出超限判 output-cap，退出码固定为 "
         "0 正常 / 1 脚本错误 / 2 语法 / 3 熔断 / 4 原生崩溃 / 5 超时 / 6 输出超限。")

    h("11.4 与其他语言的实测对照", "Heading 2")
    body("同一台机器、同一批敌意脚本、外部 10 秒 watchdog、stdout 读取上限 1 MB；"
         "判定只看失败方式，不看谁跑得快。C 用内置 tcc -O2，Python 3.13，Node v22.23.2，"
         "Impor 走解释器（稳定档与默认档各跑一遍）。对照脚本 tools/stability_compare.py 可复现，"
         "明细见 docs/稳定性对照-4.0.md。")
    cap("表40  五引擎在同一批敌意脚本上的失败方式（%s 实测）" % gen)
    rows = [["用例", "Impor --stable", "Impor 默认", "Python 3.13", "C (tcc -O2)", "Node.js"]]
    for case in ("loop", "recurse", "alloc", "flood", "divzero"):
        row = [CASE_CN[case]]
        for e in ("impor_stable", "impor_default", "python", "c", "node"):
            row.append(KIND_CN.get(table_map.get(e, {}).get(case, "unavailable"), "—"))
        rows.append(row)
    table(rows, template_idx=21, ncols=6)
    body("读法：Impor 稳定档在五类敌意输入上全部结构化熔断、零挂起、零原生崩溃，最慢一例不到一秒；"
         "其余四家都至少有一类只能靠外部强杀，C 还在递归栈溢出与整数除零上直接以原生异常退出"
         "（0xC00000FD / 0xC0000094）——那是 C「未定义行为不做检查」的既定规则，不是编译器的缺陷。"
         "Python 与 Node 的死循环同样是「语言自身不提供预算」，靠容器或超时兜住，这是定位差异而非优劣。")
    if floors:
        body("启动地板（各引擎跑一个空程序）已从对照表的耗时中扣除："
             + "；".join("%s %.2fs" % (ENGINE_CN.get(k, k), v) for k, v in sorted(floors.items()))
             + "。不扣地板会把 C 的启动开销误判成「挂起」。")

    h("11.5 代价：不白拿", "Heading 2")
    st = bench.get("impor_stable", {})
    df = bench.get("impor_default", {})
    py = bench.get("python", {})
    if st and df and st.get("seconds") and df.get("seconds"):
        body("同一段正常负载（20 万次整数循环 + 3000 次字符串拼接），稳定档耗时 %.2f 秒、默认档 %.2f 秒，"
             "约慢 %.1f 倍；这段开销来自每条语句一次预算判断，不是重复执行。"
             "作为参照，Python 在同一负载上 %.2f 秒——Impor 本身是树遍历解释器，吞吐比 Python 慢一个量级，"
             "这是架构的固有成本，与稳定性无关，两笔账要分开算。"
             % (st["seconds"], df["seconds"], st["seconds"] / df["seconds"], py.get("seconds", 0)))
    body("换句话说：稳定模式买到的是「坏脚本在一秒内变成一条可读错误」，代价是常数级的常数开销；"
         "它不会让慢脚本变快，也不改变任何输出。")

    h("11.6 边界与自检", "Heading 2")
    body("四条边界写在这里，避免把「失败可控」说成「永不失败」："
         "① 内存是每 4096 条语句采样一次 RSS，不是硬配额，极端情况会越过上限几十 MB 才熔断，要硬上限请用容器；"
         "② 解释器自身或 FFI 的原生崩溃，内部闸门管不到，靠 run_isolated 或容器兜；"
         "③ 熔断只结束这一次执行，额度按次重置，同一解释器可继续跑下一个脚本，跨执行熔断次数单独累计；"
         "④ 不改变语义、不重排、不优化。")
    body("自检：impor stability 跑 8 个敌意用例（死循环、卡死、刷屏、巨型分配、内存累积、无限递归、除零、"
         "大整数不误报），全过返回 0；回归 tests/test_v40_stability.py 共 17 项，覆盖「默认关闭不计量、"
         "熔断后可继续、额度按次重置、6 次反复熔断内存不累积、环境变量与命令行都生效、围栏超时分类」。")

    d.save(DOCX)
    print("已写入：", DOCX)
    print("段落数：", len(d.paragraphs), "表格数：", len(d.tables))


if __name__ == "__main__":
    main()
