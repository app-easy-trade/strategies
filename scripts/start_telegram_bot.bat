@echo off
cd /d "%~dp0\.."
powershell -NoExit -ExecutionPolicy Bypass -File "%~dp0start_telegram_bot.ps1"
