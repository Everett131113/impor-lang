@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist "%~dp0ImporPortable.exe" (
  "%~dp0ImporPortable.exe" run "examples\quickstart\hello.impor"
) else (
  python "%~dp0Impor.py" run "examples\quickstart\hello.impor"
)
echo.
echo 如果看到“你好，Impor！”，说明环境正常。
echo 下一步：双击 examples\quickstart\1-你好.bat
pause
