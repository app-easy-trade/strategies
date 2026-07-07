# Crypto Exchange Spread Arbitrage

Проект для поиска и проверки межбиржевого спред-арбитража на perpetual/futures рынках.

Логика модели: покупаем там, где инструмент дешевле, и продаем там, где он дороже. Прибыль ожидается не от направления рынка, а от сужения спреда между двумя биржами.

```text
spread_pct = (price_expensive / price_cheap - 1) * 100
```

Сейчас проект находится в режиме research/backtest/paper trading. Реальные заявки намеренно отключены.

## Что уже есть

- Подключения к биржам: Bybit, Bitget, OKX, MEXC, Binance, Hyperliquid, Aster.
- Сканер спредов между всеми парами подключенных бирж.
- Сборщик исторических spread snapshots в CSV.
- Backtest-движок с входами, TP1, финальным выходом, stop по adverse spread, max holding time.
- Учет комиссий, проскальзывания, funding edge, маржи и риска.
- SQLite storage для истории спредов, сигналов, пропущенных сигналов, paper trades и order book impact.
- Paper trading loop: реальные API данные, но виртуальные входы/выходы без заявок.
- Telegram отчеты: сигналы, daily control, API status, funding summary, backtest report, открытые paper trades.
- TradingView формулы для проверки спреда руками.

## Биржи и funding

Funding тянется из публичных API:

- Bybit, Bitget, MEXC: из ticker/universe данных.
- Binance: через `premiumIndex`.
- Aster: через `premiumIndex`.
- Hyperliquid: через `metaAndAssetCtxs`.
- OKX: отдельным enrichment-запросом `funding-rate` для топовых инструментов, потому что OKX требует конкретный `instId`.

В сигнале используется funding edge:

```text
funding_edge = short_funding_rate - long_funding_rate
```

Если edge положительный, funding помогает сделке. Если отрицательный, funding идет против сделки. Жесткий фильтр можно включить параметром `--require-funding-favorable` в Telegram/scanner режимах или настройками backtest config.

## Быстрый старт

```bash
python -m src.connection_check --top 5 --timeout 20
```

```bash
python -m src.spread_scanner --top 50 --limit 20 --min-spread 0.5 --min-volume 400000
```

## Сбор исторических данных

```bash
python -m src.spread_history_collector \
  --interval 60 \
  --top 1000 \
  --min-spread 0.5 \
  --min-volume 400000 \
  --output data/spread_history.csv
```

История нужна для нормального backtest. Чем длиннее сбор, тем честнее статистика.

## Backtest

```bash
python -m backtest.simulator \
  --input data/spread_history.csv \
  --trades-csv data/backtest_trades.csv \
  --skipped-csv data/backtest_skipped_signals.csv
```

Отчет:

```bash
python -m backtest.report \
  --trades-csv data/backtest_trades.csv \
  --skipped-csv data/backtest_skipped_signals.csv
```

Оптимизация параметров:

```bash
python -m backtest.optimizer --input data/spread_history.csv
```

Основной config: `config/backtest.yaml`.

## Paper trading

Paper trading берет реальные данные с бирж, но не выставляет заявки. Сделки пишутся в SQLite.

```bash
python -m paper.paper_trader \
  --db data/paper_trading.sqlite3 \
  --interval 60 \
  --top 1000 \
  --limit 50 \
  --min-spread 0.5 \
  --min-volume 400000 \
  --capture-orderbooks
```

Order book impact сохраняется для размеров $200, $500 и $1000.

## Telegram bot

Бот сейчас работает как процесс-отправитель отчетов. То есть команды запускаются в терминале, а сообщения приходят в Telegram. Slash-команды внутри Telegram чата пока не включены.

Получить `chat_id`:

```bash
TELEGRAM_BOT_TOKEN="YOUR_TOKEN" python -m output.telegram_bot --get-chat-id
```

`.env` в корне проекта:

```bash
TELEGRAM_BOT_TOKEN=YOUR_TOKEN
TELEGRAM_CHAT_ID=YOUR_CHAT_ID
```

Разовый сухой прогон без отправки:

```bash
python -m output.telegram_bot --once --dry-run
```

Разовая отправка:

```bash
python -m output.telegram_bot --once
```

Постоянные сигналы каждые 5 минут:

```bash
python -m output.telegram_bot
```

Daily control с открытыми paper trades:

```bash
python -m output.telegram_bot \
  --daily-control \
  --paper-db data/paper_trading.sqlite3 \
  --interval 86400
```

Разовый daily control для проверки:

```bash
python -m output.telegram_bot \
  --once \
  --dry-run \
  --daily-control \
  --paper-db data/paper_trading.sqlite3
```

Поддерживаемые режимы Telegram CLI:

- `--get-chat-id` - показать chat id после сообщения боту.
- `--once` - выполнить один цикл и выйти.
- `--dry-run` - напечатать отчет в консоль без отправки.
- `--daily-control` - отправить расширенный контрольный отчет.
- `--paper-db` - добавить открытые paper trades из SQLite.
- `--backtest-trades` и `--backtest-skipped` - добавить backtest report.
- `--require-funding-favorable` - показывать только сделки с funding в нашу сторону.
- `--min-spread`, `--max-spread`, `--min-volume`, `--limit`, `--top`, `--interval` - управлять фильтрами и частотой.

## TradingView формула

В Telegram для каждой сделки добавляется строка:

```text
TV: (EXPENSIVE_EXCHANGE:SYMBOL/CHEAP_EXCHANGE:SYMBOL-1)*100
```

Пример:

```text
TV: (BINANCE:BTCUSDT.P/BYBIT:BTCUSDT.P-1)*100
```

Это соответствует нашей формуле спреда:

```text
(цена дорогой биржи / цена дешевой биржи - 1) * 100
```

Иногда TradingView использует другие тикеры для отдельных бирж или инструментов. Маппинг сделан централизованно в `output/telegram_bot.py`, чтобы его можно было быстро поправить.

## Порядок перед реальной торговлей

1. Собрать историю через `spread_history_collector`.
2. Прогнать backtest и optimizer.
3. Запустить paper trading на реальных API данных.
4. Проверить Telegram daily reports и открытые virtual trades.
5. Сравнить топ-сделки вручную в TradingView по формуле из бота.
6. Подключить read-only authenticated checks: balances, margin, positions.
7. Только после этого делать order executor: testnet/demo, затем минимальный real size.

## Важно

Реальные заявки сейчас отключены. Проект пока не должен торговать настоящими деньгами без отдельного блока order executor, testnet/demo проверки, контроля частичных исполнений и аварийного закрытия одной ноги.
