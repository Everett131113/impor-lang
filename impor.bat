@echo off
setlocal
set "IMPOR_CALLER_CWD=%CD%"
cd /d "%~dp0"
if exist "%~dp0impor-fast.exe" goto fast
python -S -I -X utf8 "%~dp0Impor.py" %*
exit /b
:fast
"%~dp0impor-fast.exe" %*
exit /b
