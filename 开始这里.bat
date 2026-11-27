@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist "%~dp0ImporPortable.exe" goto use_portable
python "%~dp0Impor.py" run "examples\quickstart\hello.impor"
goto after_run
:use_portable
"%~dp0ImporPortable.exe" run "examples\quickstart\hello.impor"
:after_run
type "examples\quickstart\_welcome.txt"
pause
