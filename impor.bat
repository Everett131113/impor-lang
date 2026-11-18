@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0impor-fast.exe" (
  "%~dp0impor-fast.exe" %*
  exit /b
)
python -S -I -X utf8 "%~dp0Impor.py" %*
exit /b
