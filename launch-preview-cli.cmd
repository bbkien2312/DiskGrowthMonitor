@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONNOUSERSITE=1
set DGM_SOURCE=1
set DGM_HOME=%~dp0data\parquet-preview
"%~dp0.venv\Scripts\python.exe" -m dgm --home "%DGM_HOME%" fast %*
