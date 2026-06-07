@echo off
setlocal EnableDelayedExpansion
title Inkwell — Installer
color 0B

echo.
echo  ============================================================
echo    Inkwell — One-Time Setup
echo  ============================================================
echo.
echo  This will:
echo    1. Check / install Python 3.11
echo    2. Create a virtual environment
echo    3. Install all dependencies
echo    4. Create a desktop shortcut to launch the app
echo.
echo  This only runs once. After this, double-click
echo  the "Inkwell" shortcut on your desktop.
echo.
pause

:: ── Step 1: Check Python ─────────────────────────────────────────────────────
echo.
echo  [1/4] Checking Python...

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  Python not found. Downloading Python 3.11...
    echo.
    set PYTHON_URL=https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe
    set PYTHON_INSTALLER=%TEMP%\python_installer.exe
    powershell -Command "& {[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%PYTHON_URL%' -OutFile '%PYTHON_INSTALLER%'}"
    if not exist "%PYTHON_INSTALLER%" (
        echo.
        echo  [ERROR] Could not download Python automatically.
        echo  Please install Python 3.11 manually from: https://python.org/downloads
        echo  Then run this installer again.
        pause
        exit /b 1
    )
    echo  Installing Python 3.11 (1-2 minutes)...
    "%PYTHON_INSTALLER%" /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1
    del "%PYTHON_INSTALLER%" >nul 2>&1
    call refreshenv >nul 2>&1
    python --version >nul 2>&1
    if %errorlevel% neq 0 (
        echo.
        echo  [ERROR] Python installation may need a restart.
        echo  Please restart your computer and run this installer again.
        pause
        exit /b 1
    )
)

for /f "tokens=*" %%i in ('python --version') do set PYVER=%%i
echo  Found: %PYVER%

:: ── Step 2: Virtual environment ───────────────────────────────────────────────
echo.
echo  [2/4] Creating virtual environment...

set VENV_DIR=%~dp0venv

if exist "%VENV_DIR%" (
    echo  Virtual environment already exists, skipping.
) else (
    python -m venv "%VENV_DIR%"
    if %errorlevel% neq 0 (
        echo  [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo  Created: %VENV_DIR%
)

:: ── Step 3: Install dependencies ──────────────────────────────────────────────
echo.
echo  [3/4] Installing dependencies (3-5 minutes)...
echo  Please wait...
echo.

call "%VENV_DIR%\Scripts\activate.bat"
pip install --upgrade pip --quiet
pip install -r "%~dp0requirements.txt" --quiet

if %errorlevel% neq 0 (
    echo.
    echo  [ERROR] Dependency installation failed.
    echo  Check your internet connection and try again.
    pause
    exit /b 1
)

echo  All dependencies installed successfully.

:: ── Step 4: Desktop shortcut ──────────────────────────────────────────────────
echo.
echo  [4/4] Creating desktop shortcut...

set SHORTCUT_PATH=%USERPROFILE%\Desktop\Inkwell.lnk
set START_SCRIPT=%~dp0start.bat

powershell -Command "& { $ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%SHORTCUT_PATH%'); $s.TargetPath = '%START_SCRIPT%'; $s.WorkingDirectory = '%~dp0'; $s.WindowStyle = 7; $s.Description = 'Launch Inkwell'; $s.Save() }" >nul 2>&1

if exist "%SHORTCUT_PATH%" (
    echo  Shortcut created: "Inkwell" on your Desktop.
) else (
    echo.
    echo  [NOTE] Could not create desktop shortcut automatically.
    echo  This sometimes happens on managed/work computers.
    echo.
    echo  You can still launch the app by double-clicking:
    echo  %~dp0start.bat
    echo.
    echo  Or right-click start.bat ^> "Send to" ^> "Desktop (create shortcut)"
    echo  to create it manually.
)

:: ── Done ──────────────────────────────────────────────────────────────────────
echo.
echo  ============================================================
echo    Setup Complete!
echo  ============================================================
echo.
echo  Next steps:
echo.
echo    1. Double-click "Inkwell" on your Desktop
echo       (or double-click start.bat in this folder)
echo.
echo    2. Your browser opens automatically
echo.
echo    3. Enter your API keys in the setup screen
echo       Get them from:
echo       - Anthropic: https://console.anthropic.com
echo       - Voyage AI: https://dashboard.voyageai.com
echo.
echo    4. Upload your PDF books and click "Start Ingestion"
echo.
echo  ============================================================
echo.
pause