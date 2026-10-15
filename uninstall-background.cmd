@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
"%~dp0.venv\Scripts\python.exe" -m dgm schedule task remove
"%~dp0.venv\Scripts\python.exe" -m dgm schedule service stop
"%~dp0.venv\Scripts\python.exe" -m dgm schedule service uninstall

