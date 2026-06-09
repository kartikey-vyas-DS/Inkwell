@echo off
setlocal
title Inkwell - Installer
color 0B

set "SCRIPT_DIR=%~dp0"
set "VENV_DIR=%SCRIPT_DIR%venv"
set "PYTHON_CMD="
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

echo.
echo  ============================================================
echo    Inkwell - One-Time Setup
echo  ============================================================
echo.
echo  This will:
echo    1. Check or install Python 3.11
echo    2. Create a virtual environment
echo    3. Install all dependencies
echo    4. Create a desktop shortcut to launch the app
echo.
echo  This only runs once. After this, double-click
echo  the "Inkwell" shortcut on your desktop.
echo.
pause

echo.
echo  [1/4] Checking Python...
call :find_python

if not defined PYTHON_CMD (
    echo  Python 3.11 not found. Downloading Python 3.11...
    echo.
    set "PYTHON_URL=https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"
    set "PYTHON_INSTALLER=%TEMP%\inkwell_python_3.11.9.exe"
    powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%PYTHON_URL%' -OutFile '%PYTHON_INSTALLER%'"
    if not exist "%PYTHON_INSTALLER%" (
        echo.
        echo  [ERROR] Could not download Python automatically.
        echo  Please install Python 3.11 manually from https://python.org/downloads
        echo  Then run this installer again.
        echo.
        pause
        exit /b 1
    )

    echo  Installing Python 3.11. This can take a minute or two...
    "%PYTHON_INSTALLER%" /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1 Include_launcher=1
    del "%PYTHON_INSTALLER%" >nul 2>&1

    call :find_python
    if not defined PYTHON_CMD (
        echo.
        echo  [ERROR] Python was installed, but this terminal cannot see it yet.
        echo  Restart your computer, then run install.bat again.
        echo.
        pause
        exit /b 1
    )
)

for /f "tokens=*" %%i in ('%PYTHON_CMD% --version 2^>^&1') do set "PYVER=%%i"
echo  Found: %PYVER%

echo.
echo  [2/4] Creating virtual environment...
if exist "%VENV_DIR%\Scripts\activate.bat" (
    echo  Virtual environment already exists, skipping.
) else (
    %PYTHON_CMD% -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo.
        echo  [ERROR] Failed to create virtual environment.
        echo.
        pause
        exit /b 1
    )
    if not exist "%VENV_DIR%\Scripts\activate.bat" (
        echo.
        echo  [ERROR] Virtual environment was not created correctly.
        echo  Please install Python 3.11 manually from https://python.org/downloads
        echo  Then run install.bat again.
        echo.
        pause
        exit /b 1
    )
    echo  Created: %VENV_DIR%
)

echo.
echo  [3/4] Installing dependencies. This can take several minutes...
echo  Please wait...
echo.

if not exist "%VENV_DIR%\Scripts\activate.bat" (
    echo.
    echo  [ERROR] Virtual environment is missing.
    echo  Please delete the incomplete venv folder and run install.bat again.
    echo.
    pause
    exit /b 1
)

call "%VENV_DIR%\Scripts\activate.bat"
if errorlevel 1 (
    echo.
    echo  [ERROR] Failed to activate the virtual environment.
    echo.
    pause
    exit /b 1
)
python -m pip install --upgrade pip --quiet
if errorlevel 1 (
    echo.
    echo  [ERROR] Failed to upgrade pip.
    echo.
    pause
    exit /b 1
)

python -m pip install -r "%SCRIPT_DIR%requirements.txt" --quiet
if errorlevel 1 (
    echo.
    echo  [ERROR] Dependency installation failed.
    echo  Check your internet connection and try again.
    echo.
    pause
    exit /b 1
)

echo  All dependencies installed successfully.

echo.
echo  [4/4] Creating desktop shortcut...
set "DESKTOP_DIR=%USERPROFILE%\Desktop"
for /f "usebackq tokens=*" %%d in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "[Environment]::GetFolderPath('Desktop')"`) do set "DESKTOP_DIR=%%d"
set "SHORTCUT_PATH=%DESKTOP_DIR%\Inkwell.lnk"
set "START_SCRIPT=%SCRIPT_DIR%start.bat"
set "ICON_PATH=%SCRIPT_DIR%inkwell-logo.ico"

powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%SHORTCUT_PATH%'); $s.TargetPath = '%START_SCRIPT%'; $s.WorkingDirectory = '%SCRIPT_DIR%'; $s.IconLocation = '%ICON_PATH%'; $s.WindowStyle = 7; $s.Description = 'Launch Inkwell'; $s.Save()" >nul 2>&1

if exist "%SHORTCUT_PATH%" (
    echo  Shortcut created: %SHORTCUT_PATH%
) else (
    echo.
    echo  [NOTE] Could not create desktop shortcut automatically.
    echo  You can still launch the app by double-clicking:
    echo  %SCRIPT_DIR%start.bat
    echo.
    echo  Or right-click start.bat, choose "Send to", then "Desktop create shortcut".
)

echo.
echo  ============================================================
echo    Setup Complete!
echo  ============================================================
echo.
echo  Next steps:
echo.
echo    1. Double-click "Inkwell" on your Desktop
echo       or double-click start.bat in this folder.
echo.
echo    2. Your browser opens automatically.
echo.
echo    3. Enter your API keys in the setup screen.
echo.
echo    4. Upload your PDF books and click "Start Ingestion".
echo.
echo  ============================================================
echo.
pause
exit /b 0

:find_python
set "PYTHON_TEST="
for /f "tokens=*" %%i in ('py -3.11 -c "print('OK')" 2^>nul') do set "PYTHON_TEST=%%i"
if "%PYTHON_TEST%"=="OK" (
    set "PYTHON_CMD=py -3.11"
    exit /b 0
)

set "PYTHON_TEST="
for /f "tokens=*" %%i in ('python -c "import sys; print('OK' if sys.version_info >= (3, 11) else 'NO')" 2^>nul') do set "PYTHON_TEST=%%i"
if "%PYTHON_TEST%"=="OK" (
    set "PYTHON_CMD=python"
    exit /b 0
)
exit /b 1
