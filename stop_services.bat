@echo off
setlocal
chcp 65001 >nul

echo Stopping PDFQA2 services...

taskkill /FI "WINDOWTITLE eq PDFQA2 Backend" /T /F >nul 2>nul
taskkill /FI "WINDOWTITLE eq PDFQA2 Frontend" /T /F >nul 2>nul

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ports = @(8000, 3000, 5173); foreach ($port in $ports) { try { Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction Stop | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue } } catch {} }"

echo Stop commands sent.
exit /b 0
