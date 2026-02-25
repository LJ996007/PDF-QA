@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\dev-down.ps1"
set EXIT_CODE=%ERRORLEVEL%
if not "%EXIT_CODE%"=="0" (
  echo.
  echo [DEV-DOWN] Failed with exit code %EXIT_CODE%.
)
exit /b %EXIT_CODE%
