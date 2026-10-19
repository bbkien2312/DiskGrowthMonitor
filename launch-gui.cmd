@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONNOUSERSITE=1
set DGM_SOURCE=1
set DGM_HOME=%~dp0
if not exist "%~dp0data\tmp" mkdir "%~dp0data\tmp"
set TEMP=%~dp0data\tmp
set TMP=%TEMP%
if not exist "%~dp0.venv\Scripts\pythonw.exe" call "%~dp0install-local.cmd"
if not exist "%~dp0.venv\Scripts\pythonw.exe" exit /b 1
start "Disk Growth Monitor" "%~dp0.venv\Scripts\pythonw.exe" -m dgm gui %*

