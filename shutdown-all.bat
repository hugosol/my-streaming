@echo off
echo Shutting down My Streaming...
echo.

echo Killing worker processes on port 8899...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8899 ^| findstr LISTENING') do (
    taskkill /F /T /PID %%a 2>nul && echo   Killed PID %%a (tree)
)

echo Killing server processes on port 8888...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8888 ^| findstr LISTENING') do (
    taskkill /F /T /PID %%a 2>nul && echo   Killed PID %%a (tree)
)

echo.
echo Signaling server window to close...
echo. > "%TEMP%\mystreaming_shutdown"

echo All stopped. Window will close in 2 seconds...
timeout /t 2 /nobreak >nul
