@echo off
setlocal
set "PROJECT_DIR=%~dp0"
set "PYTHON=%PROJECT_DIR%..\..\runtime\python\python.exe"
"%PYTHON%" "%PROJECT_DIR%doctor.py"
pause

