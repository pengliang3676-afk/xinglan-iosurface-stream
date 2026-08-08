@echo off
setlocal
set "PROJECT_DIR=%~dp0"
set "PYTHON=%PROJECT_DIR%..\..\runtime\python\python.exe"
if not exist "%PYTHON%" (
  echo 缺少便携Python：%PYTHON%
  pause
  exit /b 1
)
"%PYTHON%" "%PROJECT_DIR%app.py" --max-devices 10
if errorlevel 1 pause

