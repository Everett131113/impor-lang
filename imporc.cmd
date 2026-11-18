@echo off
rem imporc.cmd - Impor self-host compiler CLI (v2.19)
rem Usage:
rem   imporc run <script.impor> [-o <out.exe>]   compile and run
rem   imporc build <script.impor> [-o <out.exe>] compile only
rem   imporc version                             show compiler version
rem impor\imporc.exe is a native C executable, no Python needed at runtime.
setlocal
cd /d "%~dp0"
if "%~1"=="" (
  echo imporc: usage: imporc run^|build script.impor [-o out.exe]   or   imporc version
  exit /b 1
)
> imporc_args.txt echo %*
impor\imporc.exe
exit /b %errorlevel%
