# -*- coding: utf-8 -*-
"""Impor (I++) v4.6 自举闭环验证
================================
验证「编译器用 Impor 语言自身编写，不再依赖 Python」：

  种子  bootstrap/imporc.impor     —— Impor 语言写成的编译器（读 imporc_args.txt → out2.c/out2.exe）
  A     impor/imporc.exe           —— 纯 C 原生编译器（预编译引导器）
  B     imporc_self.exe            —— 用 A 产出的 out2.c + 64 位 gcc 编出的编译器
  C     由 B 编译种子得到的编译器（out2.exe）
  D     由 C 编译种子得到的编译器

v4.6 的关键改动：**自举链全程 64 位**。
  32 位 tcc 产物只有 2GB 用户地址空间，而 Impor 版编译器编译本种子时
  Private 峰值实测约 2.01GB（拼接 C 源码的 O(n²) 复制），正好撞上限，
  报 `MemoryError: 内存不足 [E1013]`（第 0 行、调用栈只有 <main>）。
  改法：A 产出的 out2.c 用 64 位 gcc 编成 B；种子内编译产物改走
  bootstrap/cc64.bat（优先 gcc，找不到再回退 tcc）。

自举成立判据：B / C / D 对同一输入（种子源码）产生逐字节相同的 C 产物。
运行：cd 仓库根；py -3.11 bootstrap/selfhost_check.py
"""
import io
import os
import shutil
import struct
import subprocess
import sys
import time

STACK = 64 * 1024 * 1024


def patch_stack_exe(path, size=STACK):
    """把 PE 头 SizeOfStackReserve 调到 64MB（默认 1–2MB，编译大文件会栈溢出）。"""
    try:
        with open(path, 'rb+') as f:
            head = f.read(0x200)
            e_lfanew = struct.unpack_from('<I', head, 0x3C)[0]
            opt = e_lfanew + 4 + 20
            magic = struct.unpack_from('<H', head, opt)[0]
            if magic == 0x10B:
                off, fmt = opt + 0x48, '<I'
            elif magic == 0x20B:
                off, fmt = opt + 0x48, '<Q'
            else:
                return False
            f.seek(off)
            f.write(struct.pack(fmt, size))
            return True
    except Exception:
        return False


BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 仓库根
SEED = os.path.join(BASE, 'bootstrap', 'imporc.impor')
IMPORC = os.path.join(BASE, 'impor', 'imporc.exe')
SELF = os.path.join(BASE, 'imporc_self.exe')
ARG = os.path.join(BASE, 'arg_script.impor')
OUT2C = os.path.join(BASE, 'out2.c')
OUT2EXE = os.path.join(BASE, 'out2.exe')
T = os.environ.get('TEMP', '.')
GCC = os.environ.get('IMPOR_GCC') or shutil.which('gcc')


def run(cmd):
    last = None
    for _ in range(3):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, cwd=BASE, timeout=1800)
        except PermissionError as exc:
            last = exc
            time.sleep(2)
    raise last


def prep_seed():
    with io.open(os.path.join(BASE, 'imporc_args.txt'), 'w', encoding='utf-8') as f:
        f.write('build arg_script.impor\n')
    shutil.copyfile(SEED, ARG)


def main():
    os.chdir(BASE)
    results = []

    def step(name, ok, detail=''):
        results.append(ok)
        print(('✓' if ok else '✗') + ' ' + name + ('  ' + detail if detail else ''))

    if not GCC:
        print('✗ 找不到 64 位 gcc：自举链无法绕过 32 位 2GB 地址空间（可用 IMPOR_GCC 指定）')
        return 1
    os.environ['PATH'] = os.path.dirname(GCC) + os.pathsep + os.environ.get('PATH', '')
    print('使用 gcc：%s' % GCC)

    # ---- Step 1：A 产出种子 C 源码，用 gcc 编成 64 位 B ----
    prep_seed()
    r = run([IMPORC])
    ok = r.returncode == 0 and os.path.exists(OUT2C)
    if ok:
        r2 = run([GCC, '-std=c99', '-O0', '-o', SELF, OUT2C])
        ok = r2.returncode == 0 and os.path.exists(SELF)
        if ok:
            ok = patch_stack_exe(SELF)
        else:
            print('  gcc:', (r2.stderr or '')[-800:])
    step('A 产出 out2.c → gcc 编 64 位 B（imporc_self.exe）', ok,
         '(%d 字节 C)' % os.path.getsize(OUT2C) if os.path.exists(OUT2C) else '')
    if not ok:
        print('  rc:', r.returncode, 'stdout:', (r.stdout or '')[-800:], 'stderr:', (r.stderr or '')[-800:])
        return 1

    # ---- Step 2：B 编译种子 → out2.c / out2.exe（C） ----
    prep_seed()
    r = run([SELF])
    ok = r.returncode == 0 and os.path.exists(OUT2C) and os.path.exists(OUT2EXE)
    if ok:
        ok = patch_stack_exe(OUT2EXE)
    step('B 编译种子 → C（out2.exe，64 位）', ok)
    if not ok:
        print('  rc:', r.returncode)
        print('  stderr:', (r.stderr or '')[-800:])
        return 1
    b_size = os.path.getsize(OUT2C)
    shutil.copyfile(OUT2C, os.path.join(T, 'sb_b.c'))
    shutil.copyfile(OUT2EXE, os.path.join(T, 'sb_c.exe'))
    patch_stack_exe(os.path.join(T, 'sb_c.exe'))
    with io.open(OUT2C, encoding='utf-8', errors='replace') as f:
        nfn = f.read().count('static Val c_')
    print('  B 产物 out2.c：%d 字节，%d 个 static Val c_ 函数' % (b_size, nfn))

    # ---- Step 3：C 编译种子 → 新 out2.c，与 B 产物逐字节对比 ----
    prep_seed()
    r = run([os.path.join(T, 'sb_c.exe')])
    ok = r.returncode == 0 and os.path.exists(OUT2C)
    if ok:
        shutil.copyfile(OUT2C, os.path.join(T, 'sb_c.c'))
        a = io.open(os.path.join(T, 'sb_b.c'), encoding='utf-8', errors='replace').read()
        b = io.open(os.path.join(T, 'sb_c.c'), encoding='utf-8', errors='replace').read()
        same = a == b
        step('C 编译种子产物与 B 逐字节一致（自举成立）', same, '(%d 字节)' % len(a))
    else:
        step('C 编译种子 → 产物', False)
        print('  stderr:', (r.stderr or '')[-800:])
        return 1

    # ---- Step 4：D（C 的产物）编译种子，跨代稳定 ----
    shutil.copyfile(OUT2EXE, os.path.join(T, 'sb_d.exe'))
    patch_stack_exe(os.path.join(T, 'sb_d.exe'))
    prep_seed()
    r = run([os.path.join(T, 'sb_d.exe')])
    ok = r.returncode == 0 and os.path.exists(OUT2C)
    if ok:
        shutil.copyfile(OUT2C, os.path.join(T, 'sb_d.c'))
        a = io.open(os.path.join(T, 'sb_c.c'), encoding='utf-8', errors='replace').read()
        b = io.open(os.path.join(T, 'sb_d.c'), encoding='utf-8', errors='replace').read()
        step('D 编译种子产物与 C 逐字节一致（跨代稳定）', a == b)
    else:
        step('D 编译种子 → 产物', False)
        print('  stderr:', (r.stderr or '')[-800:])
        return 1

    # ---- 清理现场 ----
    for f in ('out2.c', 'out2.exe', 'imporc_self.exe', 'arg_script.impor',
              'imporc_args.txt', 'out2_tcc.exe'):
        p = os.path.join(BASE, f)
        if os.path.exists(p):
            os.remove(p)

    all_ok = all(results)
    print('\n自举闭环：', '✅ 全部通过 —— B/C/D 三代产物逐字节一致（64 位工具链）'
          if all_ok else '❌ 存在失败')
    return 0 if all_ok else 1


if __name__ == '__main__':
    sys.exit(main())
