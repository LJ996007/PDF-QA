@echo off
setlocal
chcp 65001 >nul

set "ROOT_DIR=%~dp0"
cd /d "%ROOT_DIR%"

call "%ROOT_DIR%stop_services.bat"
timeout /t 2 /nobreak >nul
call "%ROOT_DIR%start_services.bat"

exit /b 0
