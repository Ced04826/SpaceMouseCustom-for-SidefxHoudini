@echo off
title Restore 3Dconnexion Driver
echo ============================================
echo  Restore 3Dconnexion Driver
echo ============================================
echo.

echo Starting 3Dconnexion services...
start "" "C:\Program Files\3Dconnexion\3DxWare\3DxWinCore\3DxService.exe"

timeout /t 2 /nobreak >nul

echo.
echo 3Dconnexion driver restored!
echo Your Space Mouse should now work normally with the 3D viewport.
echo.
pause
