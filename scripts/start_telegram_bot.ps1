$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Test-Path ".env")) {
    Write-Host "Missing .env. Add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID first."
    exit 1
}

New-Item -ItemType Directory -Force -Path "data" | Out-Null

python -m output.telegram_bot `
    --listen-commands `
    --notify-startup `
    --send-on-start `
    --daily-control `
    --backtest-input data/spread_history_enriched_sane.csv `
    --paper-db data/paper_trading.sqlite3 `
    --auto-report-interval 86400 `
    *> data/telegram_bot.log
