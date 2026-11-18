@echo off
setlocal
cd /d "%~dp0"
if exist "..\..\Impor.py" (
  python ..\..\Impor.py build --lib lib.impor -o lib.dll
) else (
  impor build --lib lib.impor -o lib.dll
)
if errorlevel 1 exit /b 1
..\..\impor\tcc\win32\tcc.exe demo.c lib.dll -o demo.exe
if errorlevel 1 exit /b 1
demo.exe
