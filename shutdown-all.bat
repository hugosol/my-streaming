@echo off
echo Shutting down My Streaming...
echo.

echo Killing worker on port 8899...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8899 ^| findstr LISTENING') do (
    taskkill /F /T /PID %%a 2>nul && echo   Killed PID %%a (tree)
)

echo Killing server on port 8888...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8888 ^| findstr LISTENING') do (
    taskkill /F /T /PID %%a 2>nul && echo   Killed PID %%a (tree)
)

echo.
echo All stopped. Windows will close automatically.
timeout /t 1 /nobreak >nul
