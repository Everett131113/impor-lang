# -*- mode: python ; coding: utf-8 -*-
"""把 Impor CLI 打成单文件便携版，供没有 Python 的 Windows 用户使用。

构建：python -m PyInstaller --noconfirm ImporPortable.spec
"""
a = Analysis(
    ['portable_entry.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('VERSION', '.'),
        ('impor/c_runtime.c', 'impor'),
        ('impor/tcc', 'impor/tcc'),
        ('impor/assets/icons/impor.ico', 'impor/assets/icons'),
        ('impor-lang.ico', '.'),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['pytest', 'numpy', 'PIL'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='ImporPortable',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['impor-lang.ico'],
)
