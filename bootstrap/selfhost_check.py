# -*- coding: utf-8 -*-
"""Impor (I++) v2.15 自举闭环验证
================================
验证「编译器用 Impor 语言自身编写，不再依赖 Python」：

  种子  bootstrap/imporc.impor     —— Impor 语言写成的编译器（读 arg_script.impor → out2.c/out2.exe）
  A     impor/imporc.exe           —— 纯 C 原生编译器（ccomp.c 由 tcc 编译）
  B     imporc_self.exe            —— A 编译种子得到的 C 编译器
  C     由 B 编译种子得到的 C 编译器（out2.exe）
  D     由 C 编译种子得到的 C 编译器

自举成立判据：B / C / D 对同一输入（种子源码）产生逐字节相同的 C 产物。

运行：cd ImporLang 根目录；python bootstrap/selfhost_check.py
"""
import io
import os
import shutil
import struct
import subprocess
import sys
import time


def patch_stack_exe(path, size=8 * 1024 * 1024):
    """把 PE 头 SizeOfStackReserve 调到 8MB（tcc 默认 1MB，编译大文件会栈溢出）"""
    try:
        with open(path, 'rb+') as f:
            head = f.read(0x200)
            e_lfanew = struct.unpack_from('<I', head, 0x3C)[0]
            opt = e_lfanew + 4 + 20
            magic = struct.unpack_from('<H', head, opt)[0]
            if magic == 0x10B:
                off, fmt = opt + 0x48, '<I'
            elif magic == 0x20B:
                off, fmt = opt + 0x30, '<Q'
            else:
                return False
            f.seek(off)
            f.write(struct.pack(fmt, size))
            return True
    except Exception:
        return False

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # ImporLang 根
SEED = os.path.join(BASE, 'bootstrap', 'imporc.impor')
IMPORC = os.path.join(BASE, 'impor', 'imporc.exe')
SELF = os.path.join(BASE, 'imporc_self.exe')
ARG = os.path.join(BASE, 'arg_script.impor')
T = os.environ.get('TEMP', '.')


def run(cmd):
    # 新生成的 exe 可能被 Windows 安全软件短暂扫描锁定；等待后重试
    last = None
    for _ in range(3):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, cwd=BASE, timeout=600)
        except PermissionError as exc:
            last = exc
            time.sleep(2)
    raise last


def main():
    os.chdir(BASE)
    results = []

    def step(name, ok, detail=''):
        results.append(ok)
        print(('✓' if ok else '✗') + ' ' + name + ('  ' + detail if detail else ''))

    # 自举种子（v2.19 起）从 imporc_args.txt 读命令行参数；
    # 编译器（imporc.exe / B/C/D）运行前必须写入该文件
    argfile = os.path.join(BASE, 'imporc_args.txt')

    # ---- Step 1：编译器（读 imporc_args.txt）编译种子 -> B ----
    with io.open(argfile, 'w', encoding='utf-8') as f:
        f.write('build %s -o %s\n' % (os.path.relpath(SEED, BASE), os.path.relpath(SELF, BASE)))
    r = run([IMPORC])
    ok = r.returncode == 0 and os.path.exists(SELF)
    if ok:
        ok = patch_stack_exe(SELF)
    step('A（imporc.exe）编译种子 → B（imporc_self.exe）', ok)
    if not ok:
        print('  rc:', r.returncode)
        print('  stdout:', (r.stdout or '')[-1200:])
        print('  stderr:', (r.stderr or '')[-1200:])
        return 1

    # ---- Step 2：B 编译种子 -> out2.c / out2.exe（C） ----
    with io.open(argfile, 'w', encoding='utf-8') as f:
        f.write('build arg_script.impor\n')
    shutil.copyfile(SEED, ARG)
    r = run([SELF])
    ok = r.returncode == 0 and os.path.exists(os.path.join(BASE, 'out2.c'))
    if ok:
        patch_stack_exe(os.path.join(BASE, 'out2.exe'))
    step('B 编译种子 → C（out2.exe）', ok)
    if not ok:
        print('  stderr:', r.stderr[-400:])
        return 1
    b_size = os.path.getsize(os.path.join(BASE, 'out2.c'))
    shutil.copyfile(os.path.join(BASE, 'out2.c'), os.path.join(T, 'sb_b.c'))
    shutil.copyfile(os.path.join(BASE, 'out2.exe'), os.path.join(T, 'sb_c.exe'))
    patch_stack_exe(os.path.join(T, 'sb_c.exe'))
    print(f'  B 产物 out2.c：{b_size} 字节，{open(os.path.join(T,"sb_b.c"),encoding="utf-8",errors="replace").read().count("static Val c_")} 个函数')

    # ---- Step 3：C 编译种子 -> 新 out2.c，与 B 产物逐字节对比 ----
    with io.open(argfile, 'w', encoding='utf-8') as f:
        f.write('build arg_script.impor\n')
    shutil.copyfile(SEED, ARG)
    r = run([os.path.join(T, 'sb_c.exe')])
    ok = r.returncode == 0 and os.path.exists(os.path.join(BASE, 'out2.c'))
    if ok:
        shutil.copyfile(os.path.join(BASE, 'out2.c'), os.path.join(T, 'sb_c.c'))
        a = io.open(os.path.join(T, 'sb_b.c'), encoding='utf-8', errors='replace').read()
        b = io.open(os.path.join(T, 'sb_c.c'), encoding='utf-8', errors='replace').read()
        same = a == b
        step('C 编译种子产物与 B 逐字节一致（自举成立）', same, f'({len(a)} 字节)')
    else:
        step('C 编译种子 → 产物', False)
        print('  stderr:', r.stderr[-400:])
        return 1

    # ---- Step 4：D（C 的产物）编译种子，稳定性复验 ----
    shutil.copyfile(os.path.join(BASE, 'out2.exe'), os.path.join(T, 'sb_d.exe'))
    patch_stack_exe(os.path.join(T, 'sb_d.exe'))
    with io.open(argfile, 'w', encoding='utf-8') as f:
        f.write('build arg_script.impor\n')
    shutil.copyfile(SEED, ARG)
    r = run([os.path.join(T, 'sb_d.exe')])
    ok = r.returncode == 0 and os.path.exists(os.path.join(BASE, 'out2.c'))
    if ok:
        shutil.copyfile(os.path.join(BASE, 'out2.c'), os.path.join(T, 'sb_d.c'))
        a = io.open(os.path.join(T, 'sb_c.c'), encoding='utf-8', errors='replace').read()
        b = io.open(os.path.join(T, 'sb_d.c'), encoding='utf-8', errors='replace').read()
        same = a == b
        step('D 编译种子产物与 C 逐字节一致（跨代稳定）', same)
    else:
        step('D 编译种子 → 产物', False)
        return 1

    # ---- 清理现场 ----
    for f in ('out2.c', 'out2.exe', 'imporc_self.exe', 'arg_script.impor', 'imporc_args.txt'):
        p = os.path.join(BASE, f)
        if os.path.exists(p):
            os.remove(p)

    all_ok = all(results)
    print('\n自举闭环：', '✅ 全部通过 —— B/C/D 三代产物逐字节一致，编译器已用自身语言实现' if all_ok else '❌ 存在失败')
    return 0 if all_ok else 1


if __name__ == '__main__':
    sys.exit(main())
