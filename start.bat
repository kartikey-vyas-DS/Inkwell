@echo off
setlocal EnableDelayedExpansion
title Inkwell
color 0B

:: ── Sanity check ─────────────────────────────────────────────────────────
if not exist "%~dp0venv\Scripts\activate.bat" (
    echo.
    echo  [ERROR] Setup not complete.
    echo  Please run install.bat first.
    echo.
    pause
    exit /b 1
)

:: ── Check if already running ──────────────────────────────────────────────
netstat -ano 2>nul | findstr ":8000 " | findstr "LISTENING" >nul 2>&1
if %errorlevel% equ 0 (
    echo.
    echo  Inkwell is already running.
    echo  Opening browser...
    start http://localhost:8000
    exit /b 0
)

:: ── Activate virtual environment ──────────────────────────────────────────
call "%~dp0venv\Scripts\activate.bat"

:: ── Start server ──────────────────────────────────────────────────────────
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

:: Open browser after 6 seconds (extra time for slow machines / antivirus scan)
start "" /b cmd /c "timeout /t 6 /nobreak >nul && start http://localhost:8000"

:: Start server
python "%~dp0app.py"

echo.
echo  Server stopped. Press any key to close.
pause >nul