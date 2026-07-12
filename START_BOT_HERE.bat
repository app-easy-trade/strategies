@echo off
cd /d "%~dp0"
powershell -NoExit -ExecutionPolicy Bypass -File "scripts\start_telegram_bot.ps1"
