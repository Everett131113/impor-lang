@echo off
setlocal
cd /d "%~dp0.."
if not exist "impor\tcc\win32\tcc.exe" (
  echo missing impor\tcc\win32\tcc.exe
  exit /b 1
)
"impor\tcc\win32\tcc.exe" -O2 -o "impor-fast.exe" "launcher\impor_fast.c"
exit /b %errorlevel%
