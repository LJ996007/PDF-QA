@echo off
setlocal
chcp 65001 >nul

set "ROOT_DIR=%~dp0"
if "%ROOT_DIR:~-1%"=="\" set "ROOT_DIR=%ROOT_DIR:~0,-1%"
cd /d "%ROOT_DIR%"

where npm >nul 2>nul
if errorlevel 1 (
  echo [ERROR] npm was not found. Please install Node.js and ensure npm is in PATH.
  pause
  exit /b 1
)

start "PDFQA2 Backend" "%ComSpec%" /k call "%ROOT_DIR%\_start_backend.bat"
start "PDFQA2 Frontend" "%ComSpec%" /k call "%ROOT_DIR%\_start_frontend.bat"

set "FRONTEND_URL=http://localhost:3000/"
set "FRONTEND_READY="

for /L %%I in (1,1,20) do (
  powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "try { $resp = Invoke-WebRequest -UseBasicParsing -Uri '%FRONTEND_URL%' -TimeoutSec 2; if ($resp.StatusCode -ge 200) { exit 0 } else { exit 1 } } catch { exit 1 }"
  if not errorlevel 1 (
    set "FRONTEND_READY=1"
    goto :open_browser
  )
  timeout /t 1 /nobreak >nul
)

:open_browser
start "" "%FRONTEND_URL%"

echo Services started.
echo Backend window title: PDFQA2 Backend
echo Frontend window title: PDFQA2 Frontend
if defined FRONTEND_READY (
  echo Browser opened: %FRONTEND_URL%
) else (
  echo Browser opened before readiness check completed: %FRONTEND_URL%
)
exit /b 0
