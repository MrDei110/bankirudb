@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1" -Mode Database
if errorlevel 1 (pause & exit /b 1)
