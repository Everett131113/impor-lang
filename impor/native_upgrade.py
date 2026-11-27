# -*- coding: utf-8 -*-
"""4.8.0 后台满档编译助手（由 impor/cli.py::_spawn_bg_upgrade 以独立进程拉起）。

输入是快速档留下的 .c；用满档选项（原 opt + LTO）编到**另一个文件名**，
成功后原子替换、删掉 .c。任何失败都静默退出：前台已经用快速档正常跑完了，
这里只是把下次运行的速度再抬一档，不该产生任何可见副作用。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RC_ARG = 2
RC_BUILD = 4
RC_REPLACE = 5


def main():
    if len(sys.argv) < 6:
        return RC_ARG
    cc, kind, cpath, out = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    try:
        opt = int(sys.argv[5])
    except ValueError:
        return RC_ARG
    if not os.path.isfile(cpath) or not os.path.isfile(cc):
        return 0

    import subprocess
    from impor.cli import _cc_build_cmd, _cc_env

    tmp = out + ".upgrading"
    try:
        if os.path.isfile(tmp):
            os.unlink(tmp)
    except OSError:
        pass

    cmd = _cc_build_cmd(cc, kind, cpath, tmp, opt)
    built = False
    for _ in range(3):
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=300,
                               env=_cc_env(cc))
        except Exception:
            return RC_BUILD
        if p.returncode == 0 and os.path.isfile(tmp):
            built = True
            break
        time.sleep(0.2)
    if not built:
        try:
            os.unlink(cpath)
        except OSError:
            pass
        return RC_BUILD

    for _ in range(6):
        try:
            os.replace(tmp, out)
            break
        except OSError:
            # 旧满档产物正在被另一个进程执行时，Windows 会拒绝替换；稍等重试。
            time.sleep(0.5)
    else:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return RC_REPLACE

    try:
        os.unlink(cpath)
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
