@echo off
rem 一键安装 Impor (I++) 到全局：安装后任意目录可直接使用 impor 命令
chcp 65001 >nul
cd /d "%~dp0"
echo 正在安装 Impor (I++) 命令行工具...

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 -m pip install -e . --no-build-isolation --disable-pip-version-check
) else (
    python -m pip install -e .
)
if %errorlevel% neq 0 (
    echo 安装失败，请确认已安装 Python 3.8+ 并加入 PATH。
    pause
    exit /b 1
)
echo.
echo 安装完成！现在可以在任意目录使用：
echo   impor repl            交互式解释器
echo   impor run 脚本.impor  运行脚本
echo   impor compile 脚本.impor -o 输出.py   编译为 Python
echo   impor compile 脚本.impor --exe        打包成 exe（需先 pip install pyinstaller）
echo   impor                 打开图形化编辑器
pause
