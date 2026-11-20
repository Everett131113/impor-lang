@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "ROOT=%~dp0..\.."
if exist "%ROOT%\ImporPortable.exe" (
  "%ROOT%\ImporPortable.exe" run "hello.impor"
) else (
  python "%ROOT%\Impor.py" run "hello.impor"
)
echo.
pause
