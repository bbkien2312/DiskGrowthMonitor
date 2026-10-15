@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONNOUSERSITE=1
set DGM_HOME=%~dp0
if not exist "%~dp0data\tmp" mkdir "%~dp0data\tmp"
set TEMP=%~dp0data\tmp
set TMP=%TEMP%
if not exist "%~dp0.venv\Scripts\python.exe" call "%~dp0install-local.cmd"
if not exist "%~dp0.venv\Scripts\python.exe" exit /b 1
"%~dp0.venv\Scripts\python.exe" -m dgm %*
if "%~1"=="" pause

