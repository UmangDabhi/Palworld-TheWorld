@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
set "RELAY_SCRIPT=%~dp0.palworld-relay\scripts\Push-World.ps1"
if not exist "%RELAY_SCRIPT%" (
  echo ERROR: Missing relay script: %RELAY_SCRIPT%
  set "RELAY_EXIT=1"
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%RELAY_SCRIPT%"
  set "RELAY_EXIT=!ERRORLEVEL!"
)
echo.
if "!RELAY_EXIT!"=="0" (
  echo World push completed successfully.
) else (
  echo World push stopped with exit code !RELAY_EXIT!.
  echo Read the message and log path above. Any created Git commit remains safe locally.
)
if /I not "%PALWORLD_RELAY_NO_PAUSE%"=="1" pause
exit /b !RELAY_EXIT!
