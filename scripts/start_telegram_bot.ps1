$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Test-Path ".env")) {
    Write-Host "Missing .env. Add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID first."
    exit 1
}

New-Item -ItemType Directory -Force -Path "data" | Out-Null
Start-Transcript -Path "data/telegram_bot.log" -Append | Out-Null

Write-Host "Starting Arbitration Telegram bot..."
Write-Host "Project: $(Get-Location)"
Write-Host "Log: data/telegram_bot.log"
Write-Host "Keep this window open. Press Ctrl+C to stop."

python -m output.telegram_bot `
    --listen-commands `
    --notify-startup `
    --send-on-start `
    --daily-control `
    --backtest-input data/spread_history_enriched_sane.csv `
    --paper-db data/paper_trading.sqlite3 `
    --account-checks `
    --auto-report-interval 86400
