@echo off
cd /d "%~dp0"
if not exist ".env" (
  copy ".env.example" ".env" >nul
)
notepad ".env"
echo.
echo Fill TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID, save the file, then run START_BOT_HERE.bat
pause
