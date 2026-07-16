@echo off
setlocal
cd /d "%~dp0"
set "RELAY_SCRIPT=%~dp0.palworld-relay\scripts\Pull-And-Swap.ps1"
if not exist "%RELAY_SCRIPT%" (
  echo ERROR: Missing relay script: %RELAY_SCRIPT%
  set "RELAY_EXIT=1"
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%RELAY_SCRIPT%"
  set "RELAY_EXIT=%ERRORLEVEL%"
)
echo.
if "%RELAY_EXIT%"=="0" (
  echo Pull and host preparation completed successfully.
) else (
  echo Pull and host preparation stopped with exit code %RELAY_EXIT%.
  echo Read the message and log path above. Do not open Palworld after a validation failure.
)
if /I not "%PALWORLD_RELAY_NO_PAUSE%"=="1" pause
exit /b %RELAY_EXIT%
