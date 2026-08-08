@echo off & set "XINGLAN_HWACCEL=software" & "%~dp0..\..\runtime\python\python.exe" "%~dp0app.py" --max-devices 60 & if errorlevel 1 pause
