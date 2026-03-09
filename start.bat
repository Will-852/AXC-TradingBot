@echo off
chcp 65001 >nul 2>&1
title OpenClaw Dashboard

echo ============================================
echo   OpenClaw — Starting...
echo ============================================
echo.

:: ─── Resolve script directory ───
cd /d "%~dp0"

:: ─── Check Python ───
where python >nul 2>&1
if %errorlevel% neq 0 (
    where py >nul 2>&1
    if %errorlevel% neq 0 (
        echo [ERROR] Python not found.
        echo.
        echo Download Python 3.11+ from https://www.python.org/downloads/
        echo IMPORTANT: Check "Add python.exe to PATH" during installation.
        echo.
        pause
        exit /b 1
    )
    set PY=py
) else (
    set PY=python
)

for /f "tokens=*" %%i in ('%PY% --version 2^>^&1') do echo [OK] %%i

:: ─── Install dependencies if needed ───
%PY% -c "import dotenv, numpy" >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo [SETUP] Installing dependencies...
    %PY% -m pip install -r "%~dp0axc_requirements.txt" -q
    echo [OK] Dependencies installed
)

:: ─── Setup secrets\.env on first run ───
if not exist "%~dp0secrets\.env" (
    echo.
    echo ============================================
    echo   First Run Setup
    echo ============================================
    echo.
    if not exist "%~dp0secrets" mkdir "%~dp0secrets"
    copy "%~dp0docs\friends\.env.example" "%~dp0secrets\.env" >nul
    echo Created secrets\.env from template.
    echo.
    set /p KEY="Paste your PROXY_API_KEY (or press Enter to skip): "
    if defined KEY (
        %PY% -c "import sys; p='%~dp0secrets\.env'; t=open(p).read(); open(p,'w').write(t.replace('PROXY_API_KEY=sk-ant-你的key','PROXY_API_KEY='+sys.argv[1]))" "%KEY%"
        echo [OK] API key saved
    ) else (
        echo [SKIP] Edit secrets\.env later with your API key
    )
    echo.
)

:: ─── Check if dashboard already running ───
netstat -ano 2>nul | findstr ":5555 " | findstr "LISTENING" >nul 2>&1
if %errorlevel% equ 0 (
    echo.
    echo [INFO] Dashboard already running on port 5555
    start http://localhost:5555
    echo.
    pause
    exit /b 0
)

:: ─── Launch Dashboard ───
echo.
echo [LAUNCH] Starting Dashboard on http://localhost:5555
echo [INFO] Press Ctrl+C to stop
echo.

:: Open browser after short delay
start /b cmd /c "timeout /t 2 /nobreak >nul & start http://localhost:5555"

set AXC_HOME=%~dp0
%PY% scripts\dashboard.py

pause
