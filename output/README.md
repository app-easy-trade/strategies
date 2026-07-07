# Telegram output

Папка `output` отвечает за отправку найденных spread-разлетов наружу.

## Что нужно подключить

1. В Telegram открыть `@BotFather`.
2. Создать бота командой `/newbot`.
3. Скопировать bot token.
4. Написать созданному боту любое сообщение, например `start`.
5. Получить `chat_id` командой:

```bash
TELEGRAM_BOT_TOKEN="YOUR_TOKEN" python3 -m output.telegram_bot --get-chat-id
```

6. Создать `.env` в корне проекта:

```bash
TELEGRAM_BOT_TOKEN=YOUR_TOKEN
TELEGRAM_CHAT_ID=YOUR_CHAT_ID
```

## Разовый тест без отправки

```bash
python3 -m output.telegram_bot --once --dry-run
```

## Разовая отправка в Telegram

```bash
python3 -m output.telegram_bot --once
```

## Отправка каждые 5 минут

```bash
python3 -m output.telegram_bot
```

По умолчанию бот:

- каждые `300` секунд выгружает до top-1000 тикеров;
- считает spread между всеми парами бирж;
- отправляет top-10 разлетов;
- фильтрует `spread >= 0.5%`, `spread <= 5%`, `minVol24h >= 1_000_000`;
- добавляет в сообщение funding edge: `short_funding_rate - long_funding_rate`.

Funding edge сейчас показывает направление текущего funding rate. Он еще не нормализует разные интервалы funding и не прогнозирует, сколько начислений позиция успеет пересечь.

Только сделки, где funding явно в нашу сторону:

```bash
python3 -m output.telegram_bot --require-funding-favorable
```

Более шумный режим:

```bash
python3 -m output.telegram_bot --min-spread 0 --max-spread 100 --min-volume 0 --limit 20
```

## Daily control with paper trades

```bash
python3 -m output.telegram_bot --once --dry-run --daily-control --paper-db data/paper_trading.sqlite3
```

Remove `--dry-run` after checking the message locally.
