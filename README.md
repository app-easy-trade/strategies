# Crypto Exchange Spread Arbitrage

Проект для поиска и проверки межбиржевого spread-arbitrage на crypto futures/perpetual рынках.

Логика модели: покупаем там, где инструмент дешевле, и продаем там, где он дороже. Прибыль ожидается не от направления рынка, а от сужения спреда между двумя биржами.

```text
spread_pct = (price_expensive / price_cheap - 1) * 100
```

Сейчас проект находится в режиме research/backtest/paper trading. Реальные заявки намеренно отключены.

## Что уже есть

- Биржи: Bybit, Bitget, OKX, MEXC, Binance, Hyperliquid, Aster.
- Scanner спредов между подключенными биржами.
- Historical spread collector в CSV.
- Execution-aware backtest: fees, slippage, order book depth, executable spread, TP1, dynamic exit, funding exits, stop, max holding.
- Portfolio risk в backtest: проверка маржи, общей загрузки счета, количества открытых сделок и общего notional.
- SQLite storage для snapshots, signals, skipped signals, paper trades и order book impact.
- Paper trading loop: реальные API данные, но виртуальные входы/выходы без реальных заявок.
- Telegram bot: автономные slash-команды, daily control, backtest report, открытые paper trades, уведомления об open/close paper-сделок.
- TradingView формулы для ручной проверки спреда.

## Быстрый старт

Проверка подключений:

```bash
python -m src.connection_check --top 5 --timeout 20
```

Scanner:

```bash
python -m src.spread_scanner --top 50 --limit 20 --min-spread 0.5 --min-volume 400000
```

Сбор истории:

```bash
python -m src.spread_history_collector --interval 60 --top 1000 --min-spread 0.5 --min-volume 400000 --capture-orderbooks --output data/spread_history.csv
```

Backtest:

```bash
python -m backtest.simulator --input data/spread_history.csv --trades-csv data/backtest_trades.csv --skipped-csv data/backtest_skipped_signals.csv
python -m backtest.report --trades-csv data/backtest_trades.csv --skipped-csv data/backtest_skipped_signals.csv
```

Основной config: `config/backtest.yaml`.

## Telegram bot

Создать `.env` в корне проекта:

```bash
TELEGRAM_BOT_TOKEN=YOUR_TOKEN
TELEGRAM_CHAT_ID=YOUR_CHAT_ID
```

Автономный запуск:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start_telegram_bot.ps1
```

После запуска бот должен сам прислать `Arbitration bot is running`. Затем в Telegram доступны команды:

```text
/start  - подписаться и включить автономные отчеты
/daily  - scanner/backtest/paper отчет сейчас
/paper  - открытые paper trades
/status - статус процесса
/stop   - отключить отчеты для этого чата
/help   - список команд
```

Логи: `data/telegram_bot.log`.

## Paper trading

```bash
python -m paper.paper_trader --db data/paper_trading.sqlite3 --interval 60 --top 1000 --limit 50 --min-spread 0.5 --min-volume 400000 --capture-orderbooks --telegram-notify
```

При открытии/закрытии виртуальной сделки Telegram получает base, биржи, LONG/SHORT ноги, spread, размер позиции, funding, expected net, PnL и TradingView формулу.

## Funding

В сигнале используется:

```text
funding_edge = short_funding_rate - long_funding_rate
```

Если edge положительный, funding помогает сделке. Если отрицательный, funding идет против сделки. Жесткий фильтр можно включить через `--require-funding-favorable` или настройки `config/backtest.yaml`.

## Перед реальной торговлей

Текущая система может решать, какие paper trades открыть, и проверять их на истории. Она еще не должна отправлять реальные ордера.

Перед real trading нужны:

- authenticated read-only checks: balances, margin, positions;
- testnet/demo order executor;
- order id tracking;
- partial fill handling;
- cancel/replace;
- one-leg emergency close;
- tick size, step size и min size по биржам;
- kill switch;
- max daily loss и max open exposure;
- восстановление live state после рестарта.

Подробно: `docs/trading_readiness.md`.
