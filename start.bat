@echo off
setlocal
title Inkwell
color 0B

set "SCRIPT_DIR=%~dp0"
set "VENV_ACTIVATE=%SCRIPT_DIR%venv\Scripts\activate.bat"

if not exist "%VENV_ACTIVATE%" (
    echo.
    echo  [ERROR] Setup is not complete.
    echo  Please run install.bat first.
    echo.
    pause
    exit /b 1
)

netstat -ano 2>nul | findstr ":8000 " | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo.
    echo  Inkwell is already running.
    echo  Opening browser...
    start http://localhost:8000
    exit /b 0
)

call "%VENV_ACTIVATE%"

echo.
echo  ============================================================
echo    Inkwell
echo  ============================================================
echo.
echo  Starting server...
echo  Your browser will open in a moment.
echo.
echo  To stop: close this window or press Ctrl+C
echo  ============================================================
echo.

start "" /b cmd /c "timeout /t 6 /nobreak >nul && start http://localhost:8000"
python "%SCRIPT_DIR%app.py"

echo.
echo  Server stopped. Press any key to close.
pause >nul
