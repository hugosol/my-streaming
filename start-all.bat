@echo off
title My Streaming Server
echo Starting My Streaming...
echo.

REM Prefer project virtual environment if present
if exist "%~dp0.venv\Scripts\python.exe" (
    set "PYTHON=%~dp0.venv\Scripts\python.exe"
    goto :found_python
)

REM Find real Python (skip WindowsApps stubs that open Microsoft Store)
set PYTHON=python
for /f "delims=" %%i in ('where python 2^>nul ^| findstr /v /i "WindowsApps Microsoft"') do (
    set PYTHON=%%i
    goto :found_python
)
:found_python
echo Using Python: %PYTHON%

REM Start worker in its own window
start "My Streaming Worker" "%PYTHON%" worker.py

REM Wait a moment for worker to start
timeout /t 2 /nobreak >nul

REM Start streaming server in its own window (pass video dir from config if specified)
start "My Streaming Server" "%PYTHON%" server.py %*

echo.
echo Both windows started. Close them individually or run shutdown-all.bat to stop.
echo.
timeout /t 3 /nobreak >nul
