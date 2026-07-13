@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0.palworld-relay\scripts\Pull-And-Swap.ps1"
if errorlevel 1 pause
