@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch_final_demo.ps1" %*
if errorlevel 1 pause
