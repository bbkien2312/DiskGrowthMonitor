@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONNOUSERSITE=1
set DGM_SOURCE=1
set DGM_HOME=%~dp0data\parquet-preview
if not exist "%~dp0.venv\Scripts\pythonw.exe" exit /b 1
start "Disk Growth Monitor - Parquet Preview" "%~dp0.venv\Scripts\pythonw.exe" -m dgm --home "%DGM_HOME%" fast gui
