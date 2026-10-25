@echo off
chcp 65001 >nul
cd /d "%~dp0"
title CareCard Route Backend - port 8000

echo ============================================================
echo  CareCard navigation backend
echo  board endpoint: http://^<this-PC-IP^>:8000/api/transit^?from^=demo^&to^=hospital
echo  keep this window OPEN while demoing. close it = backend stops.
echo ============================================================
echo.

if not exist "tencent_key.txt" (
  if "%TENCENT_KEY%"=="" (
    echo [WARN] no tencent_key.txt and no TENCENT_KEY env var.
    echo        /api/transit will answer code=3.
    echo.
  )
)

"C:\Users\geraltxu\.workbuddy\binaries\python\versions\3.13.12\python.exe" -u route_server.py --port 8000

echo.
if errorlevel 1 (
  echo [ERROR] backend exited with an error.
  echo         port 8000 busy? run: netstat -ano ^| findstr :8000
) else (
  echo [INFO] backend stopped.
)
pause
