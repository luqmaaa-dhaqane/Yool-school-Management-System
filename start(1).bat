@echo off
title SIS Server
cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
  echo Virtual environment not found in %cd%
  echo Run the setup steps first: python -m venv .venv
  pause
  exit /b
)

echo Stopping any old SIS server on port 8001...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8001 " ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1

call .venv\Scripts\activate.bat
echo.
echo Running from: %cd%
echo Open http://127.0.0.1:8001/admin/  (close this window to stop)
echo.
start "" cmd /c "timeout /t 4 >nul & start http://127.0.0.1:8001/admin/"
python manage.py runserver 8001
pause
