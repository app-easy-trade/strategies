@echo off
cd /d "%~dp0\.."
echo Pushing local commits to GitHub...
git status --short --branch
git push origin main
if errorlevel 1 (
  echo.
  echo Push failed. Check internet connection and GitHub login.
  echo If Git asks for login, complete it and run this file again.
  pause
  exit /b 1
)
echo.
echo Push complete.
pause
