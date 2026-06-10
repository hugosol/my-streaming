@echo off
echo Shutting down My Streaming...
echo.

echo Killing worker processes on port 8899...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8899 ^| findstr LISTENING') do (
    taskkill /F /PID %%a 2>nul && echo   Killed PID %%a
)

echo Killing server processes on port 8888...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8888 ^| findstr LISTENING') do (
    taskkill /F /PID %%a 2>nul && echo   Killed PID %%a
)

echo.
echo All stopped.
pause
