@echo off
echo ============================================
echo Space Mouse Diagnostic Tool
echo ============================================
echo.
echo Stopping 3Dconnexion driver...
taskkill /F /IM 3DxService.exe 2>nul
taskkill /F /IM 3dxpiemenus.exe 2>nul
taskkill /F /IM 3DxSmartUi.exe 2>nul
taskkill /F /IM Mgl3DCtlrRPCService.exe 2>nul
timeout /t 2 /nobreak >nul

echo.
echo Running diagnostic...
echo Move and TWIST/TILT the Space Mouse during the test!
echo.

cd /d "E:\AI\Houdini_MCP"
.venv\Scripts\python.exe spacemouse_network_pan\dev\diagnose_spacemouse.py

echo.
pause
