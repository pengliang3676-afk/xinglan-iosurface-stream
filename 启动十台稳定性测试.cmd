@echo off & set "XINGLAN_HWACCEL=software" & "%~dp0..\..\runtime\python\python.exe" "%~dp0app.py" --max-devices 10 --stability-minutes 30 & if errorlevel 1 pause
