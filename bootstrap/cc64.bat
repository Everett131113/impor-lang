@echo off
rem 自举链的 C 编译器入口（v4.6）：
rem   优先用 PATH 里的 64 位 gcc —— 32 位 tcc 产物的地址空间只有 2GB，
rem   而 Impor 版编译器编译自身种子时峰值约 2.0GB，会撞上限报 MemoryError[E1013]；
rem   找不到 gcc 时回退内置 tcc（产物 32 位，行为与旧版一致）。
rem 调用形态（与 tcc 兼容）：cc64.bat out2.c -o out2_tcc.exe
setlocal
where gcc >nul 2>nul
if errorlevel 1 goto fallback
gcc -std=c99 -O0 -o "%~3" "%~1"
exit /b %errorlevel%
:fallback
"%~dp0..\impor\tcc\win32\tcc.exe" %*
exit /b %errorlevel%
