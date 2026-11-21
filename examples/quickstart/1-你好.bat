@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "ROOT=%~dp0..\.."
if exist "%ROOT%\ImporPortable.exe" goto portable
python "%ROOT%\Impor.py" run "hello.impor"
goto done
:portable
"%ROOT%\ImporPortable.exe" run "hello.impor"
:done
echo.
pause
