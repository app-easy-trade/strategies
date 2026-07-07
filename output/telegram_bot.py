from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from time import sleep
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from backtest.report import build_report, format_report
from src.connection_check import check_exchange
from src.spread_monitor import filter_rows
from src.spread_scanner import DEFAULT_TOP, SpreadRow, format_funding_edge, scan_spreads, write_csv
from src.universe import exchange_names
from storage.sqlite_store import SQLiteStore


TELEGRAM_API_BASE = "https://api.telegram.org"
MAX_TELEGRAM_MESSAGE_LEN = 4096


TV_EXCHANGE_PREFIXES = {
    "aster": "ASTERDEX",
    "binance": "BINANCE",
    "bitget": "BITGET",
    "bybit": "BYBIT",
    "hyperliquid": "HYPERLIQUID",
    "mexc": "MEXC",
    "okx": "OKX",
}


class TelegramBotError(RuntimeError):
    """Raised when Telegram Bot API returns an unusable response."""


def load_env_file(path: Path = Path(".env")) -> None:
    if not path.exists():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def telegram_request(
    token: str,
    method: str,
    payload: dict[str, Any] | None = None,
    timeout: float = 20.0,
) -> dict[str, Any]:
    url = f"{TELEGRAM_API_BASE}/bot{token}/{method}"
    body = json.dumps(payload or {}).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "spread-arbitrage-telegram-output/0.2",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise TelegramBotError(
            f"Telegram {method} failed with HTTP {exc.code}: {error_body[:500]}"
        ) from exc
    except URLError as exc:
        raise TelegramBotError(f"Telegram {method} failed: {exc.reason}") from exc

    try:
        data = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise TelegramBotError(f"Telegram {method} returned invalid JSON") from exc

    if not isinstance(data, dict) or data.get("ok") is not True:
        raise TelegramBotError(f"Telegram {method} returned error: {data}")
    return data


def send_message(token: str, chat_id: str, text: str, timeout: float = 20.0) -> None:
    for chunk in split_message(text):
        telegram_request(
            token,
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": chunk,
                "disable_web_page_preview": True,
            },
            timeout=timeout,
        )


def get_chat_ids(token: str, timeout: float = 20.0) -> list[tuple[str, str]]:
    data = telegram_request(token, "getUpdates", timeout=timeout)
    chats: dict[str, str] = {}
    for update in data.get("result", []):
        if not isinstance(update, dict):
            continue
        message = update.get("message") or update.get("channel_post")
        if not isinstance(message, dict):
            continue
        chat = message.get("chat")
        if not isinstance(chat, dict) or chat.get("id") is None:
            continue
        chat_id = str(chat["id"])
        title = chat.get("title") or chat.get("username") or chat.get("first_name") or "chat"
        chats[chat_id] = str(title)
    return sorted(chats.items(), key=lambda item: item[1])


def collect_rows(args: argparse.Namespace) -> list[SpreadRow]:
    rows, _ = scan_spreads(
        exchanges=exchange_names(args.exchanges),
        quote=args.quote.upper(),
        top=args.top,
        timeout=args.timeout,
    )
    return filter_rows(
        rows,
        min_spread=args.min_spread,
        max_spread=args.max_spread,
        min_volume=args.min_volume,
        require_funding_favorable=args.require_funding_favorable,
    )


def build_alert_text(
    rows: list[SpreadRow],
    limit: int,
    started_at: datetime,
    top: int,
    min_spread: float,
    max_spread: float | None,
    min_volume: float | None,
    require_funding_favorable: bool,
) -> str:
    lines = [
        f"Spread Arbitrage | {started_at.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Top-{top} scan: found spreads {len(rows)}",
        f"Filters: spread >= {min_spread:.4g}%, max <= {max_spread if max_spread is not None else 'off'}%, minVol24h >= {min_volume if min_volume is not None else 'off'}",
        f"Funding filter: {'favorable only' if require_funding_favorable else 'off'}",
        "",
    ]
    shown = rows[:limit]
    if not shown:
        lines.append("No spreads above filters right now.")
        return "\n".join(lines)

    for index, row in enumerate(shown, start=1):
        lines.append(f"{index}. {row.base} | spread {row.spread_pct:.4f}%")
        lines.append(f"   LONG {row.long_exchange} {row.long_symbol} ask={row.long_ask:.8g}")
        lines.append(f"   SHORT {row.short_exchange} {row.short_symbol} bid={row.short_bid:.8g}")
        lines.append(f"   minVol24h={row.min_quote_volume_24h:.2f}")
        lines.append(f"   funding={format_funding_edge(row)} {row.funding_status}")
        lines.append(
            f"   TV: {spread_tv_formula(row.short_exchange, row.short_symbol, row.long_exchange, row.long_symbol)}"
        )

    if len(rows) > limit:
        lines.append("")
        lines.append(f"More rows hidden by Telegram limit: {len(rows) - limit}")
    return "\n".join(lines)


def build_daily_control_text(args: argparse.Namespace, rows: list[SpreadRow]) -> str:
    names = exchange_names(args.exchanges)
    lines = [
        f"Daily Control | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "API status:",
    ]
    for name in names:
        try:
            _, message = check_exchange(name, args.quote.upper(), min(args.top, args.health_top), args.timeout)
        except Exception as exc:
            lines.append(f"- {name}: FAIL {type(exc).__name__}: {exc}")
        else:
            lines.append(f"- {name}: {message}")

    lines.extend(["", f"Signals above filters: {len(rows)}"])
    for index, row in enumerate(rows[: args.limit], start=1):
        lines.append(
            f"{index}. {row.base} {row.long_exchange}->{row.short_exchange} "
            f"spread={row.spread_pct:.4f}% funding={format_funding_edge(row)} "
            f"{row.funding_status} vol={row.min_quote_volume_24h:.0f}"
        )
        lines.append(
            f"   TV: {spread_tv_formula(row.short_exchange, row.short_symbol, row.long_exchange, row.long_symbol)}"
        )

    funding_counts = {
        "favorable": len([row for row in rows if row.funding_status == "favorable"]),
        "against": len([row for row in rows if row.funding_status == "against"]),
        "unknown": len([row for row in rows if row.funding_status == "unknown"]),
    }
    lines.extend(
        [
            "",
            "Funding summary:",
            f"- favorable: {funding_counts['favorable']}",
            f"- against: {funding_counts['against']}",
            f"- unknown: {funding_counts['unknown']}",
        ]
    )

    if args.backtest_trades:
        report = build_report(
            Path(args.backtest_trades),
            Path(args.backtest_skipped) if args.backtest_skipped else None,
        )
        lines.extend(["", format_report(report)])

    if args.paper_db:
        lines.extend(["", build_paper_trades_text(Path(args.paper_db), args.paper_limit)])

    lines.extend(
        [
            "",
            "Execution status:",
            "- real orders: disabled",
            "- current mode: scanner/backtest/paper-ready model only",
        ]
    )
    return "\n".join(lines)


def build_paper_trades_text(db_path: Path, limit: int) -> str:
    if not db_path.exists():
        return f"Open paper trades:\n- database not found: {db_path}"
    store = SQLiteStore(db_path)
    try:
        trades = store.list_open_trades()
    finally:
        store.close()

    lines = [f"Open paper trades: {len(trades)}"]
    if not trades:
        lines.append("- none")
        return "\n".join(lines)

    for index, trade in enumerate(trades[:limit], start=1):
        lines.append(
            f"{index}. {trade.base} {trade.long_exchange}->{trade.short_exchange} "
            f"entry={trade.entry_spread_pct:.4f}% max={trade.max_spread_pct:.4f}% "
            f"remaining={trade.remaining_fraction:.2f} tp1={trade.tp1_done} "
            f"ev={trade.entry_net_expected_profit_usd:.4f} entry_time={trade.entry_time}"
        )
        lines.append(
            f"   TV: {spread_tv_formula(trade.short_exchange, trade.short_symbol, trade.long_exchange, trade.long_symbol)}"
        )
    if len(trades) > limit:
        lines.append(f"- hidden: {len(trades) - limit}")
    return "\n".join(lines)


def spread_tv_formula(
    expensive_exchange: str,
    expensive_symbol: str,
    cheap_exchange: str,
    cheap_symbol: str,
) -> str:
    expensive = tradingview_symbol(expensive_exchange, expensive_symbol)
    cheap = tradingview_symbol(cheap_exchange, cheap_symbol)
    return f"({expensive}/{cheap}-1)*100"


def tradingview_symbol(exchange: str, symbol: str) -> str:
    prefix = TV_EXCHANGE_PREFIXES.get(exchange.lower(), exchange.upper())
    normalized = normalize_tv_symbol(symbol)
    if exchange.lower() in {"aster", "binance", "bitget", "bybit", "mexc", "okx"}:
        return f"{prefix}:{normalized}.P"
    return f"{prefix}:{normalized}"


def normalize_tv_symbol(symbol: str) -> str:
    value = symbol.upper().strip()
    value = value.removesuffix("-SWAP").removesuffix("_SWAP")
    return value.replace("-", "").replace("_", "")


def run_once(args: argparse.Namespace, token: str | None, chat_id: str | None) -> list[SpreadRow]:
    started_at = datetime.now()
    rows = collect_rows(args)
    if args.daily_control:
        text = build_daily_control_text(args, rows)
    else:
        text = build_alert_text(
            rows=rows,
            limit=args.limit,
            started_at=started_at,
            top=args.top,
            min_spread=args.min_spread,
            max_spread=args.max_spread,
            min_volume=args.min_volume,
            require_funding_favorable=args.require_funding_favorable,
        )

    print(text)
    if args.csv:
        write_csv(Path(args.csv), rows)
        print(f"CSV saved: {args.csv}")

    if args.dry_run:
        print("Dry run: Telegram message was not sent.")
        return rows

    if not token:
        raise TelegramBotError("TELEGRAM_BOT_TOKEN is not set")
    if not chat_id:
        raise TelegramBotError("TELEGRAM_CHAT_ID is not set")

    send_message(token, chat_id, text, timeout=args.telegram_timeout)
    print("Telegram message sent.")
    return rows


def split_message(text: str) -> list[str]:
    if len(text) <= MAX_TELEGRAM_MESSAGE_LEN:
        return [text]

    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > MAX_TELEGRAM_MESSAGE_LEN:
            if current:
                chunks.append(current.rstrip())
                current = ""
            while len(line) > MAX_TELEGRAM_MESSAGE_LEN:
                chunks.append(line[:MAX_TELEGRAM_MESSAGE_LEN])
                line = line[MAX_TELEGRAM_MESSAGE_LEN:]
        current += line

    if current:
        chunks.append(current.rstrip())
    return chunks


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Send spread alerts or daily control reports to Telegram."
    )
    parser.add_argument("--exchanges", help="Comma-separated list, default: bybit,bitget,okx,mexc,binance,hyperliquid,aster")
    parser.add_argument("--quote", default="USDT")
    parser.add_argument("--top", type=int, default=DEFAULT_TOP)
    parser.add_argument("--interval", type=int, default=300, help="Seconds between Telegram alerts.")
    parser.add_argument("--limit", type=int, default=10, help="Max tickers per Telegram message.")
    parser.add_argument("--min-spread", type=float, default=0.5)
    parser.add_argument("--max-spread", type=float, default=5.0)
    parser.add_argument("--min-volume", type=float, default=1_000_000)
    parser.add_argument("--require-funding-favorable", action="store_true")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--health-top", type=int, default=25, help="Top markets per exchange for daily API checks.")
    parser.add_argument("--telegram-timeout", type=float, default=20.0)
    parser.add_argument("--csv", default="data/telegram_spreads_latest.csv")
    parser.add_argument("--daily-control", action="store_true", help="Send API/funding/signals/backtest control report.")
    parser.add_argument("--backtest-trades", default=None, help="Optional backtest trades CSV for daily report.")
    parser.add_argument("--backtest-skipped", default=None, help="Optional skipped signals CSV for daily report.")
    parser.add_argument("--paper-db", default=None, help="Optional paper trading SQLite DB for open trade report.")
    parser.add_argument("--paper-limit", type=int, default=10, help="Max open paper trades in daily report.")
    parser.add_argument("--env", type=Path, default=Path(".env"))
    parser.add_argument("--bot-token", default=None)
    parser.add_argument("--chat-id", default=None)
    parser.add_argument("--once", action="store_true", help="Send one Telegram alert/report and exit.")
    parser.add_argument("--dry-run", action="store_true", help="Print message without sending it.")
    parser.add_argument("--get-chat-id", action="store_true", help="Print chat IDs from recent bot updates.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    load_env_file(args.env)

    token = args.bot_token or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = args.chat_id or os.getenv("TELEGRAM_CHAT_ID")

    if args.get_chat_id:
        if not token:
            raise TelegramBotError("TELEGRAM_BOT_TOKEN is not set")
        chats = get_chat_ids(token, timeout=args.telegram_timeout)
        if not chats:
            print("No chats found. Send any message to the bot, then run this again.")
            return
        for found_chat_id, title in chats:
            print(f"{found_chat_id} {title}")
        return

    if args.once:
        run_once(args, token, chat_id)
        return

    print(
        "Telegram bot started: "
        f"daily_control={args.daily_control}, top={args.top}, interval={args.interval}s, "
        f"limit={args.limit}, min_spread={args.min_spread}%, "
        f"max_spread={args.max_spread}%, min_volume={args.min_volume}"
    )
    while True:
        try:
            run_once(args, token, chat_id)
        except Exception as exc:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{timestamp}] Telegram bot check failed: {type(exc).__name__}: {exc}")
        sleep(args.interval)


if __name__ == "__main__":
    main()

