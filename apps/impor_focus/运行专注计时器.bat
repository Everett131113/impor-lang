@echo off
cd /d "%~dp0"
python "..\..\Impor.py" run focus.impor
if errorlevel 1 pause
