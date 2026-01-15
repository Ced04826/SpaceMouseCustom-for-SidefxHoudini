@echo off
title Space Mouse Network Editor Pan
echo Script: %~f0
echo ============================================
echo  Space Mouse Network Editor Pan for Houdini
echo ============================================
echo.

:: Check for admin rights (needed to kill processes)
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [!] This script needs admin rights to kill 3Dconnexion processes.
    echo [!] Please right-click and "Run as administrator"
    echo.
    pause
    exit /b 1
)

:: Set the project directory (this script's directory: spacemouse_network_pan)
set PROJECT_DIR=%~dp0
pushd "%PROJECT_DIR%"

echo [1/4] Checking Python environment...

:: Use the venv Python
set PYTHON_CMD=%PROJECT_DIR%\.venv\Scripts\python.exe

if not exist "%PYTHON_CMD%" (
    echo [!] Virtual environment not found at %PROJECT_DIR%\.venv
    echo [!] Please create it first: uv venv
    popd
    pause
    exit /b 1
)

:: Check if hidapi is installed
"%PYTHON_CMD%" -c "import hid" >nul 2>&1
if %errorLevel% neq 0 (
    echo      Installing hidapi via uv...
    uv pip install hidapi >nul 2>&1
    if %errorLevel% neq 0 (
        echo [!] Failed to install hidapi
        echo [!] Try manually: uv pip install hidapi
        popd
        pause
        exit /b 1
    )
    echo      hidapi installed!
) else (
    echo      Python and hidapi ready
)
echo.

echo [2/4] Stopping 3Dconnexion processes...
taskkill /F /IM 3DxService.exe >nul 2>&1
taskkill /F /IM 3dxpiemenus.exe >nul 2>&1
taskkill /F /IM 3DxSmartUi.exe >nul 2>&1
taskkill /F /IM 3DxVirtualLCD.exe >nul 2>&1
taskkill /F /IM 3DxProfileServer.exe >nul 2>&1
taskkill /F /IM Mgl3DCtlrRPCService.exe >nul 2>&1
echo      Done!
echo.

echo [3/4] Waiting for processes to fully stop...
timeout /t 2 /nobreak >nul
echo      Done!
echo.

echo [4/4] Ready to start!
echo.
echo ============================================
echo  IMPORTANT: In Houdini Python Shell, run:
echo.
echo  import sys
echo  sys.path.append(r"%PROJECT_DIR%")
echo  from spacemouse_network_pan.spacemouse_standalone import start_receiver
echo  start_receiver()
echo.
echo  (Port 19879 will be used for communication)
echo ============================================
echo.
echo (Starting reader immediately)
echo.
echo Starting Space Mouse reader (Ctrl+C to stop)...
echo.

:: Run the standalone script (forward args from launcher, e.g. --houdini-pid)
"%PYTHON_CMD%" "%PROJECT_DIR%\spacemouse_standalone.py" %*

popd

echo.
echo ============================================
echo  Space Mouse reader stopped.
echo  Run restore_3dconnexion.bat to restore driver
echo ============================================
exit /b 0
