# Telegram output

`output/telegram_bot.py` отправляет spread-сигналы и контрольные отчеты в Telegram.

Сейчас бот работает как CLI-процесс: команды запускаются в терминале, а бот отправляет сообщения в Telegram. Slash-команды внутри чата (`/start`, `/paper`, `/daily`) пока не реализованы.

## Подключение

1. Открыть `@BotFather` в Telegram.
2. Создать бота командой `/newbot`.
3. Скопировать bot token.
4. Написать созданному боту любое сообщение.
5. Получить `chat_id`:

```bash
TELEGRAM_BOT_TOKEN="YOUR_TOKEN" python -m output.telegram_bot --get-chat-id
```

6. Создать `.env` в корне проекта:

```bash
TELEGRAM_BOT_TOKEN=YOUR_TOKEN
TELEGRAM_CHAT_ID=YOUR_CHAT_ID
```

## Поддерживаемые команды запуска

Проверка без отправки:

```bash
python -m output.telegram_bot --once --dry-run
```

Разовая отправка текущих сигналов:

```bash
python -m output.telegram_bot --once
```

Постоянный режим, по умолчанию каждые 300 секунд:

```bash
python -m output.telegram_bot
```

Daily control:

```bash
python -m output.telegram_bot --once --daily-control
```

Daily control с открытыми paper-сделками из SQLite:

```bash
python -m output.telegram_bot \
  --once \
  --daily-control \
  --paper-db data/paper_trading.sqlite3
```

Daily control с автоматическим пересчетом backtest из свежей истории:

```bash
python -m output.telegram_bot \
  --once \
  --daily-control \
  --backtest-input data/spread_history_enriched_sane.csv \
  --paper-db data/paper_trading.sqlite3
```

Daily control каждый день:

```bash
python -m output.telegram_bot \
  --daily-control \
  --paper-db data/paper_trading.sqlite3 \
  --interval 86400
```

Добавить backtest report:

```bash
python -m output.telegram_bot \
  --once \
  --daily-control \
  --backtest-trades data/backtest_trades.csv \
  --backtest-skipped data/backtest_skipped_signals.csv
```

Paper trading с Telegram-уведомлениями об открытии/закрытии виртуальных сделок:

```bash
python -m paper.paper_trader \
  --db data/paper_trading.sqlite3 \
  --interval 60 \
  --top 1000 \
  --limit 50 \
  --min-spread 0.5 \
  --min-volume 400000 \
  --capture-orderbooks \
  --telegram-notify
```

При открытии сделки бот присылает base, биржи, LONG/SHORT ноги, spread, размер позиции, funding, expected net и TradingView формулу.

Показывать только сделки, где funding явно в нашу сторону:

```bash
python -m output.telegram_bot --require-funding-favorable
```

Ослабить фильтры для теста:

```bash
python -m output.telegram_bot \
  --once \
  --dry-run \
  --daily-control \
  --min-spread 0.5 \
  --max-spread 10 \
  --min-volume 0 \
  --limit 10
```

## Что приходит в Telegram

Обычный отчет по сигналам:

- base asset;
- spread percent;
- LONG leg: биржа, символ, ask;
- SHORT leg: биржа, символ, bid;
- min 24h volume;
- funding edge и funding status;
- TradingView формула спреда.

Daily control:

- API status по каждой бирже;
- количество сигналов после фильтров;
- funding summary;
- backtest report, если переданы CSV;
- открытые paper trades, если передан `--paper-db`;
- статус исполнения реальных заявок.

## TradingView формула

Для каждого сигнала бот добавляет строку:

```text
TV: (EXPENSIVE_EXCHANGE:SYMBOL/CHEAP_EXCHANGE:SYMBOL-1)*100
```

Пример:

```text
TV: (BINANCE:BTCUSDT.P/BYBIT:BTCUSDT.P-1)*100
```

Это показывает тот же spread, который использует модель:

```text
(цена дорогой биржи / цена дешевой биржи - 1) * 100
```

Для открытых paper trades формула также выводится в блоке `Open paper trades`.

## Основные параметры

- `--exchanges` - список бирж через запятую. По умолчанию: `bybit,bitget,okx,mexc,binance,hyperliquid,aster`.
- `--top` - сколько рынков брать с каждой биржи.
- `--limit` - сколько строк показывать в Telegram.
- `--min-spread` - минимальный spread.
- `--max-spread` - максимальный spread.
- `--min-volume` - минимальный 24h quote volume.
- `--interval` - пауза между отчетами в секундах.
- `--paper-db` - SQLite база paper trading.
- `--csv` - куда сохранить последнюю таблицу сигналов.
