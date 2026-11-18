# -*- coding: utf-8 -*-
"""把 .ico 写入 Windows PE 的 RT_ICON / RT_GROUP_ICON 资源。

tcc 不带 windres，也没有资源编译器；这里用 kernel32 的
BeginUpdateResource / UpdateResource / EndUpdateResource 直接改 PE 资源段，
不依赖任何外部工具。
"""
import ctypes
import os
import struct
import subprocess
import sys

RT_ICON = 3
RT_GROUP_ICON = 14
LANG_NEUTRAL = 0

_ICONDIR = struct.Struct("<HHH")          # reserved, type, count
_ICONDIRENTRY = struct.Struct("<BBBBHHII")  # w,h,colors,reserved,planes,bits,bytes,offset
_GRPICONDIRENTRY = struct.Struct("<BBBBHHIH")  # 同 ICONDIRENTRY，末位改为 nID


class IconError(Exception):
    pass


def _load_ico(path):
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < _ICONDIR.size:
        raise IconError("ICO 文件过短")
    reserved, typ, count = _ICONDIR.unpack_from(data, 0)
    if reserved != 0 or typ != 1 or count <= 0:
        raise IconError("不是有效的 ICO 文件")
    images = []
    for i in range(count):
        off = _ICONDIR.size + i * _ICONDIRENTRY.size
        if off + _ICONDIRENTRY.size > len(data):
            raise IconError("ICO 目录越界")
        w, h, colors, rsv, planes, bits, nbytes, ioff = _ICONDIRENTRY.unpack_from(data, off)
        if ioff + nbytes > len(data):
            raise IconError("ICO 图像数据越界")
        images.append({
            "w": w, "h": h, "colors": colors, "planes": planes,
            "bits": bits, "bytes": data[ioff:ioff + nbytes],
        })
    return images


def _group_blob(images):
    """构造 GRPICONDIR：每帧指向 RT_ICON id = 序号（从 1 开始）。"""
    out = bytearray(struct.pack("<HHH", 0, 1, len(images)))
    for idx, img in enumerate(images, 1):
        out += _GRPICONDIRENTRY.pack(
            img["w"], img["h"], img["colors"], 0,
            img["planes"], img["bits"], len(img["bytes"]), idx,
        )
    return bytes(out)


def _update_inplace(exe_path, images):
    """对指定 PE 文件原地写资源；失败时丢弃未提交的更新。"""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.BeginUpdateResourceW.argtypes = [ctypes.c_wchar_p, ctypes.c_bool]
    kernel32.BeginUpdateResourceW.restype = ctypes.c_void_p
    kernel32.UpdateResourceW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                         ctypes.c_ushort, ctypes.c_void_p, ctypes.c_uint]
    kernel32.UpdateResourceW.restype = ctypes.c_bool
    kernel32.EndUpdateResourceW.argtypes = [ctypes.c_void_p, ctypes.c_bool]
    kernel32.EndUpdateResourceW.restype = ctypes.c_bool

    try:
        if not os.access(exe_path, os.W_OK):
            os.chmod(exe_path, os.stat(exe_path).st_mode | 0o200)
    except OSError:
        pass

    handle = kernel32.BeginUpdateResourceW(os.path.abspath(exe_path), False)
    if not handle:
        raise IconError("无法打开 exe 资源（可能正在运行或被占用）：%s" % exe_path)

    discard = False
    try:
        for idx, img in enumerate(images, 1):
            buf = ctypes.create_string_buffer(img["bytes"], len(img["bytes"]))
            if not kernel32.UpdateResourceW(handle, ctypes.c_void_p(RT_ICON),
                                            ctypes.c_void_p(idx), LANG_NEUTRAL,
                                            buf, len(img["bytes"])):
                raise IconError("写入 RT_ICON %d 失败，错误码 %d" % (idx, ctypes.get_last_error()))
        grp = _group_blob(images)
        gbuf = ctypes.create_string_buffer(grp, len(grp))
        if not kernel32.UpdateResourceW(handle, ctypes.c_void_p(RT_GROUP_ICON),
                                        ctypes.c_void_p(1), LANG_NEUTRAL,
                                        gbuf, len(grp)):
            raise IconError("写入 RT_GROUP_ICON 失败，错误码 %d" % ctypes.get_last_error())
    except BaseException:
        discard = True
        raise
    finally:
        if not kernel32.EndUpdateResourceW(handle, discard):
            raise IconError("提交资源失败，错误码 %d" % ctypes.get_last_error())


def _apply_inprocess(exe_path, ico_path=None):
    """在调用进程内直接写资源；仅供子进程入口使用。"""
    if ico_path is None:
        ico_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "assets", "icons", "impor.ico")
    images = _load_ico(ico_path)
    if not images:
        raise IconError("ICO 没有可用帧")
    _update_inplace(os.path.abspath(exe_path), images)
    return True


def apply_icon(exe_path, ico_path=None):
    """给 exe 嵌入图标；成功返回 True，失败抛 IconError。

    资源更新放到独立短生命周期子进程执行：即使 UpdateResource 在进程内留下
    文件句柄，也会随子进程退出释放，调用方随后可以立即运行产物。
    """
    if sys.platform != "win32":
        raise IconError("仅 Windows PE 支持资源图标")
    if not os.path.exists(exe_path):
        raise IconError("exe 不存在：%s" % exe_path)
    if ico_path is None:
        ico_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "assets", "icons", "impor.ico")
    if not os.path.exists(ico_path):
        raise IconError("图标不存在：%s" % ico_path)

    pkg_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ)
    env["PYTHONPATH"] = pkg_root + os.pathsep + env.get("PYTHONPATH", "")
    try:
        r = subprocess.run(
            [sys.executable, "-m", "impor.peicon", os.path.abspath(exe_path),
             os.path.abspath(ico_path)],
            capture_output=True, text=True, timeout=30, env=env,
        )
    except subprocess.TimeoutExpired:
        raise IconError("写入图标超时")
    if r.returncode != 0:
        detail = (r.stderr or r.stdout or "").strip().splitlines()
        raise IconError(detail[-1] if detail else "图标写入失败")
    return True


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python -m impor.peicon EXE [ICO]", file=sys.stderr)
        sys.exit(2)
    try:
        _apply_inprocess(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

