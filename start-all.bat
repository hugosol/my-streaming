@echo off
echo Starting My Streaming...
echo.

REM Find Python - try py first, then python
set PYTHON=python
where py >nul 2>nul && set PYTHON=py

REM Start worker in background
start "My Streaming Worker" %PYTHON% worker.py

REM Wait a moment for worker to start
timeout /t 2 /nobreak >nul

REM Start streaming server (pass video dir from config if specified)
%PYTHON% server.py %*

echo.
echo Server and worker started. Press Ctrl+C to stop.
pause
