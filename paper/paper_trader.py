from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from time import sleep

from brokers import aster, binance, bitget, bybit, hyperliquid, mexc, okx
from brokers.common import MarketSnapshot
from core.config import StrategyConfig, load_strategy_config
from core.liquidity_engine import estimate_book_impacts
from core.models import SpreadSnapshot
from core.signal_engine import evaluate_signal
from core.spread_engine import SpreadFeatureEngine
from src.spread_monitor import filter_rows
from src.spread_scanner import DEFAULT_MAX_SANE_SPREAD_PCT, DEFAULT_TOP, SpreadRow, scan_spreads
from src.universe import exchange_names
from storage.sqlite_store import OpenPaperTrade, SQLiteStore
from output.telegram_bot import load_env_file, send_message, spread_tv_formula


IMPACT_NOTIONALS = [200.0, 500.0, 1_000.0]


@dataclass(frozen=True)
class PaperRunSummary:
    scanned_rows: int
    opened: int
    closed: int
    updated: int
    skipped: int
    open_trades: int


@dataclass(frozen=True)
class TradeUpdate:
    status: str
    message: str | None = None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def row_to_snapshot(row: SpreadRow, timestamp: datetime) -> SpreadSnapshot:
    return SpreadSnapshot(
        timestamp=timestamp,
        base=row.base,
        pair=row.pair,
        long_exchange=row.long_exchange,
        short_exchange=row.short_exchange,
        long_symbol=row.long_symbol,
        short_symbol=row.short_symbol,
        long_ask=row.long_ask,
        short_bid=row.short_bid,
        spread_pct=row.spread_pct,
        min_quote_volume_24h=row.min_quote_volume_24h,
        long_funding_rate=row.long_funding_rate,
        short_funding_rate=row.short_funding_rate,
    )


def trade_key(row: SpreadRow) -> str:
    return "|".join(
        [
            row.base,
            row.long_exchange,
            row.short_exchange,
            row.long_symbol,
            row.short_symbol,
        ]
    )


def fetch_snapshot(exchange: str, symbol: str, timeout: float) -> MarketSnapshot:
    fetchers = {
        "aster": aster.fetch_snapshot,
        "binance": binance.fetch_snapshot,
        "bitget": bitget.fetch_snapshot,
        "bybit": bybit.fetch_snapshot,
        "hyperliquid": hyperliquid.fetch_snapshot,
        "mexc": mexc.fetch_snapshot,
        "okx": okx.fetch_snapshot,
    }
    if exchange not in fetchers:
        raise ValueError(f"Unknown exchange: {exchange}")
    return fetchers[exchange](symbol, timeout=timeout)


def store_orderbook_impacts(
    store: SQLiteStore,
    timestamp: str,
    key: str | None,
    row: SpreadRow,
    timeout: float,
) -> None:
    legs = [
        (row.long_exchange, row.long_symbol, "buy"),
        (row.short_exchange, row.short_symbol, "sell"),
    ]
    for exchange, symbol, side in legs:
        try:
            snapshot = fetch_snapshot(exchange, symbol, timeout)
        except Exception:
            continue
        for impact in estimate_book_impacts(snapshot.orderbook, side, IMPACT_NOTIONALS):
            store.insert_orderbook_impact(timestamp, key, row.base, exchange, symbol, impact)


def run_once(args: argparse.Namespace, config: StrategyConfig, store: SQLiteStore) -> PaperRunSummary:
    timestamp_dt = utc_now()
    timestamp = format_time(timestamp_dt)
    feature_engine = SpreadFeatureEngine(config)
    rows_all, _ = scan_spreads(
        exchanges=exchange_names(args.exchanges),
        quote=args.quote.upper(),
        top=args.top,
        timeout=args.timeout,
        allow_partial=True,
        max_sane_spread_pct=args.max_sane_spread,
    )
    rows_by_key = {trade_key(row): row for row in rows_all}
    entry_rows = filter_rows(
        rows_all,
        min_spread=args.min_spread,
        max_spread=args.max_spread,
        min_volume=args.min_volume,
        require_funding_favorable=args.require_funding_favorable,
    )
    if args.limit:
        entry_rows = entry_rows[: args.limit]

    opened = closed = updated = skipped = 0
    seen_keys: set[str] = set()

    for row in entry_rows:
        key = trade_key(row)
        seen_keys.add(key)
        snapshot = row_to_snapshot(row, timestamp_dt)
        store.insert_spread_snapshot(timestamp, snapshot)
        features = feature_engine.update(snapshot)
        signal = evaluate_signal(features, config)
        store.insert_signal(timestamp, signal)

        open_trade = store.get_open_trade_by_key(key)
        if open_trade:
            update = update_open_trade(store, open_trade, row, timestamp_dt, config)
            closed += int(update.status == "closed")
            updated += int(update.status == "updated")
            if update.message:
                notify_telegram(args, update.message)
            continue

        if signal.should_enter:
            trade_id = store.insert_open_trade(timestamp, key, signal)
            opened += 1
            notify_telegram(args, build_open_message(row, signal.net_expected_profit_usd, config, timestamp))
            if args.capture_orderbooks:
                store_orderbook_impacts(store, timestamp, key, row, args.timeout)
        else:
            skipped += 1

    for open_trade in store.list_open_trades():
        if open_trade.trade_key in seen_keys:
            continue
        row = rows_by_key.get(open_trade.trade_key)
        if row is not None:
            seen_keys.add(open_trade.trade_key)
            snapshot = row_to_snapshot(row, timestamp_dt)
            store.insert_spread_snapshot(timestamp, snapshot)
            update = update_open_trade(store, open_trade, row, timestamp_dt, config)
            closed += int(update.status == "closed")
            updated += int(update.status == "updated")
            if update.message:
                notify_telegram(args, update.message)
        elif args.close_missing:
            store.close_trade(
                open_trade.id,
                timestamp,
                open_trade.entry_spread_pct,
                open_trade.max_spread_pct,
                open_trade.realized_pnl_usd,
                "missing_from_scan",
                open_trade.remaining_fraction,
                open_trade.tp1_done,
                open_trade.realized_pnl_usd,
            )
            closed += 1
            notify_telegram(args, build_missing_close_message(open_trade, timestamp))

    store.commit()
    open_count = len(store.list_open_trades())
    return PaperRunSummary(
        scanned_rows=len(entry_rows),
        opened=opened,
        closed=closed,
        updated=updated,
        skipped=skipped,
        open_trades=open_count,
    )


def update_open_trade(
    store: SQLiteStore,
    trade: OpenPaperTrade,
    row: SpreadRow,
    timestamp: datetime,
    config: StrategyConfig,
) -> TradeUpdate:
    max_spread = max(trade.max_spread_pct, row.spread_pct)
    remaining = trade.remaining_fraction
    realized = trade.realized_pnl_usd
    tp1_done = trade.tp1_done

    if not tp1_done:
        tp1_target = trade.entry_spread_pct * (1 - config.tp1_retrace_fraction)
        if row.spread_pct <= tp1_target:
            realized += leg_pnl_usd(
                trade.entry_spread_pct,
                row.spread_pct,
                config.position_notional_usd,
                config.tp1_close_fraction,
            )
            remaining = max(0.0, remaining - config.tp1_close_fraction)
            tp1_done = True

    reason = exit_reason(trade, row, timestamp, config)
    if reason:
        net_pnl = close_pnl_usd(trade, row, timestamp, config, realized, remaining)
        store.close_trade(
            trade.id,
            format_time(timestamp),
            row.spread_pct,
            max_spread,
            net_pnl,
            reason,
            remaining,
            tp1_done,
            realized,
        )
        return TradeUpdate(
            "closed",
            build_close_message(trade, row, net_pnl, reason, remaining, tp1_done, config),
        )

    store.update_open_trade(trade.id, max_spread, remaining, tp1_done, realized)
    return TradeUpdate("updated")


def exit_reason(
    trade: OpenPaperTrade,
    row: SpreadRow,
    timestamp: datetime,
    config: StrategyConfig,
) -> str | None:
    holding_seconds = (timestamp - parse_time(trade.entry_time)).total_seconds()
    if row.spread_pct >= trade.entry_spread_pct + config.max_adverse_spread_pct:
        return "max_adverse_spread"
    if row.spread_pct <= config.final_exit_spread_pct:
        return "final_spread_target"
    if holding_seconds >= config.max_holding_seconds:
        return "max_holding_time"
    return None


def close_pnl_usd(
    trade: OpenPaperTrade,
    row: SpreadRow,
    timestamp: datetime,
    config: StrategyConfig,
    realized_pnl: float,
    remaining_fraction: float,
) -> float:
    remaining_pnl = leg_pnl_usd(
        trade.entry_spread_pct,
        row.spread_pct,
        config.position_notional_usd,
        remaining_fraction,
    )
    holding_seconds = max(0.0, (timestamp - parse_time(trade.entry_time)).total_seconds())
    fees = fee_cost_usd(config, trade.tp1_done)
    slippage = 4 * config.position_notional_usd * config.slippage_pct / 100
    funding = funding_pnl_usd(trade, holding_seconds, config)
    return realized_pnl + remaining_pnl + funding - fees - slippage


def leg_pnl_usd(entry_spread_pct: float, exit_spread_pct: float, notional_usd: float, fraction: float) -> float:
    return (entry_spread_pct - exit_spread_pct) / 100 * notional_usd * fraction


def fee_cost_usd(config: StrategyConfig, had_tp1: bool) -> float:
    open_cost = 2 * config.position_notional_usd * config.maker_fee_pct / 100
    close_turnover = config.position_notional_usd
    if had_tp1:
        close_turnover += config.position_notional_usd * config.tp1_close_fraction
    close_cost = 2 * close_turnover * config.taker_fee_pct / 100
    return open_cost + close_cost


def funding_pnl_usd(trade: OpenPaperTrade, holding_seconds: float, config: StrategyConfig) -> float:
    if trade.entry_long_funding_rate is None or trade.entry_short_funding_rate is None:
        return 0.0
    periods = holding_seconds / (config.funding_interval_hours * 3600)
    edge_rate = trade.entry_short_funding_rate - trade.entry_long_funding_rate
    return edge_rate * config.position_notional_usd * periods


def parse_time(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def notify_telegram(args: argparse.Namespace, text: str) -> None:
    if not args.telegram_notify:
        return
    token = args.telegram_bot_token or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = args.telegram_chat_id or os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Telegram notify skipped: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is not set")
        return
    try:
        send_message(token, chat_id, text, timeout=args.telegram_timeout)
    except Exception as exc:
        print(f"Telegram notify failed: {type(exc).__name__}: {exc}")


def build_open_message(
    row: SpreadRow,
    net_expected_profit_usd: float,
    config: StrategyConfig,
    timestamp: str,
) -> str:
    return "\n".join(
        [
            f"Paper trade opened | {timestamp}",
            f"{row.base} {row.long_exchange}->{row.short_exchange}",
            f"Spread: {row.spread_pct:.4f}%",
            f"Position: {config.position_notional_usd:.2f} USD per leg | leverage {config.leverage:g}x",
            f"LONG {row.long_exchange} {row.long_symbol} ask={row.long_ask:.8g}",
            f"SHORT {row.short_exchange} {row.short_symbol} bid={row.short_bid:.8g}",
            f"Funding: {row.funding_edge_pct if row.funding_edge_pct is not None else 'unknown'} {row.funding_status}",
            f"Expected net: {net_expected_profit_usd:.4f} USD",
            f"TV: {spread_tv_formula(row.short_exchange, row.short_symbol, row.long_exchange, row.long_symbol)}",
        ]
    )


def build_close_message(
    trade: OpenPaperTrade,
    row: SpreadRow,
    net_pnl: float,
    reason: str,
    remaining_fraction: float,
    tp1_done: bool,
    config: StrategyConfig,
) -> str:
    return "\n".join(
        [
            f"Paper trade closed | {format_time(utc_now())}",
            f"{trade.base} {trade.long_exchange}->{trade.short_exchange}",
            f"Entry spread: {trade.entry_spread_pct:.4f}%",
            f"Exit spread: {row.spread_pct:.4f}%",
            f"Reason: {reason}",
            f"Position: {config.position_notional_usd:.2f} USD per leg",
            f"Remaining: {remaining_fraction:.2f} | TP1: {tp1_done}",
            f"Net PnL: {net_pnl:.4f} USD",
            f"TV: {spread_tv_formula(trade.short_exchange, trade.short_symbol, trade.long_exchange, trade.long_symbol)}",
        ]
    )


def build_missing_close_message(trade: OpenPaperTrade, timestamp: str) -> str:
    return "\n".join(
        [
            f"Paper trade closed | {timestamp}",
            f"{trade.base} {trade.long_exchange}->{trade.short_exchange}",
            "Reason: missing_from_scan",
            f"Entry spread: {trade.entry_spread_pct:.4f}%",
            f"Remaining: {trade.remaining_fraction:.2f} | TP1: {trade.tp1_done}",
            f"TV: {spread_tv_formula(trade.short_exchange, trade.short_symbol, trade.long_exchange, trade.long_symbol)}",
        ]
    )


def print_summary(summary: PaperRunSummary) -> None:
    print(
        "Paper run: "
        f"rows={summary.scanned_rows} opened={summary.opened} updated={summary.updated} "
        f"closed={summary.closed} skipped={summary.skipped} open={summary.open_trades}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run live-data paper trading without real orders.")
    parser.add_argument("--db", type=Path, default=Path("data/paper_trading.sqlite3"))
    parser.add_argument("--config", type=Path, default=Path("config/backtest.yaml"))
    parser.add_argument("--exchanges", help="Comma-separated list, default: all configured exchanges")
    parser.add_argument("--quote", default="USDT")
    parser.add_argument("--top", type=int, default=DEFAULT_TOP)
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--min-spread", type=float, default=0.5)
    parser.add_argument("--max-spread", type=float, default=10.0)
    parser.add_argument("--min-volume", type=float, default=400_000)
    parser.add_argument("--require-funding-favorable", action="store_true")
    parser.add_argument("--require-known-funding", action="store_true")
    parser.add_argument("--capture-orderbooks", action="store_true")
    parser.add_argument("--close-missing", action="store_true")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument(
        "--max-sane-spread",
        type=float,
        default=DEFAULT_MAX_SANE_SPREAD_PCT,
        help="Drop likely incompatible contracts above this spread percent; <=0 disables.",
    )
    parser.add_argument("--telegram-notify", action="store_true", help="Send Telegram notifications on paper open/close.")
    parser.add_argument("--telegram-env", type=Path, default=Path(".env"))
    parser.add_argument("--telegram-bot-token", default=None)
    parser.add_argument("--telegram-chat-id", default=None)
    parser.add_argument("--telegram-timeout", type=float, default=20.0)
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--once", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    load_env_file(args.telegram_env)
    config = load_strategy_config(args.config if args.config.exists() else None)
    config = replace(
        config,
        min_spread_pct=args.min_spread,
        max_spread_pct=args.max_spread,
        min_volume_usd=args.min_volume,
        require_known_funding=args.require_known_funding or config.require_known_funding,
    )
    store = SQLiteStore(args.db)
    try:
        while True:
            summary = run_once(args, config, store)
            print_summary(summary)
            if args.once:
                break
            sleep(args.interval)
    finally:
        store.close()


if __name__ == "__main__":
    main()
