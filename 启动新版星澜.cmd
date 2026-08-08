@echo off & "%~dp0..\..\runtime\python\python.exe" "%~dp0app.py" --max-devices 10 & if errorlevel 1 pause
