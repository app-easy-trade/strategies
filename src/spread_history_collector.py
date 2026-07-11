from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from time import sleep

from brokers import aster, binance, bitget, bybit, hyperliquid, mexc, okx
from brokers.common import MarketSnapshot
from core.liquidity_engine import estimate_price_impact

from .spread_monitor import filter_rows
from .spread_scanner import (
    DEFAULT_MAX_SANE_SPREAD_PCT,
    DEFAULT_TOP,
    SPREAD_FIELDNAMES,
    SpreadRow,
    scan_spreads,
)
from .universe import exchange_names


DEPTH_NOTIONALS = [100.0, 200.0, 500.0, 1_000.0]
DEPTH_FIELDNAMES = [
    "market_mode",
    "entry_book_status",
    "entry_book_error",
    "long_book_timestamp_ms",
    "short_book_timestamp_ms",
    "long_book_latency_ms",
    "short_book_latency_ms",
    "executable_spread_100_pct",
    "executable_spread_200_pct",
    "executable_spread_500_pct",
    "executable_spread_1000_pct",
    "long_depth_usd_100",
    "short_depth_usd_100",
    "long_depth_usd_200",
    "short_depth_usd_200",
    "long_depth_usd_500",
    "short_depth_usd_500",
    "long_depth_usd_1000",
    "short_depth_usd_1000",
]
HISTORY_FIELDNAMES = ["timestamp"] + SPREAD_FIELDNAMES + DEPTH_FIELDNAMES


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def append_history(path: Path, timestamp: str, rows: list[SpreadRow | dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists() or path.stat().st_size == 0
    if not write_header:
        validate_history_header(path)

    with path.open("a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=HISTORY_FIELDNAMES)
        if write_header:
            writer.writeheader()
        for row in rows:
            payload = {"timestamp": timestamp}
            payload.update(row if isinstance(row, dict) else build_history_payload(row))
            writer.writerow(payload)


def collect_once(args: argparse.Namespace) -> list[dict[str, object]]:
    exchanges = exchange_names(args.exchanges)
    rows, _ = scan_spreads(
        exchanges=exchanges,
        quote=args.quote.upper(),
        top=args.top,
        timeout=args.timeout,
        max_sane_spread_pct=args.max_sane_spread,
    )
    rows = filter_rows(
        rows,
        min_spread=args.min_spread,
        max_spread=args.max_spread,
        min_volume=args.min_volume,
        require_funding_favorable=args.require_funding_favorable,
    )
    if args.limit is not None:
        rows = rows[: args.limit]

    timestamp = utc_now_iso()
    payloads = [build_history_payload(row) for row in rows]
    if args.capture_orderbooks:
        payloads = enrich_with_orderbooks(
            rows,
            payloads,
            max_rows=args.orderbook_limit,
            timeout=args.timeout,
        )
    append_history(Path(args.output), timestamp, payloads)
    print(
        f"[{timestamp}] appended {len(rows)} rows to {args.output} "
        f"(spread >= {args.min_spread}%, minVol24h >= {args.min_volume})"
    )
    return payloads


def build_history_payload(row: SpreadRow) -> dict[str, object]:
    payload: dict[str, object] = asdict(row)
    payload.update(empty_depth_payload())
    payload["market_mode"] = "futures_futures"
    return payload


def empty_depth_payload() -> dict[str, object]:
    return {field: "" for field in DEPTH_FIELDNAMES}


def enrich_with_orderbooks(
    rows: list[SpreadRow],
    payloads: list[dict[str, object]],
    max_rows: int,
    timeout: float,
) -> list[dict[str, object]]:
    enriched = list(payloads)
    for index, row in enumerate(rows[:max_rows]):
        payload = dict(enriched[index])
        try:
            payload.update(build_orderbook_payload(row, timeout))
        except Exception as exc:
            payload["entry_book_status"] = "error"
            payload["entry_book_error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        enriched[index] = payload
    return enriched


def build_orderbook_payload(row: SpreadRow, timeout: float) -> dict[str, object]:
    long_snapshot = fetch_snapshot(row.long_exchange, row.long_symbol, timeout)
    short_snapshot = fetch_snapshot(row.short_exchange, row.short_symbol, timeout)
    payload = {
        "market_mode": "futures_futures",
        "entry_book_status": "ok",
        "entry_book_error": "",
        "long_book_timestamp_ms": long_snapshot.orderbook.timestamp_ms or long_snapshot.timestamp_ms or "",
        "short_book_timestamp_ms": short_snapshot.orderbook.timestamp_ms or short_snapshot.timestamp_ms or "",
        "long_book_latency_ms": round(long_snapshot.latency_ms, 4),
        "short_book_latency_ms": round(short_snapshot.latency_ms, 4),
    }
    for notional in DEPTH_NOTIONALS:
        long_impact = estimate_price_impact(long_snapshot.orderbook, "buy", notional)
        short_impact = estimate_price_impact(short_snapshot.orderbook, "sell", notional)
        suffix = notional_suffix(notional)
        if long_impact.average_price and short_impact.average_price:
            payload[f"executable_spread_{suffix}_pct"] = (
                short_impact.average_price / long_impact.average_price - 1
            ) * 100
        else:
            payload[f"executable_spread_{suffix}_pct"] = ""
        payload[f"long_depth_usd_{suffix}"] = round(long_impact.filled_notional_usd, 8)
        payload[f"short_depth_usd_{suffix}"] = round(short_impact.filled_notional_usd, 8)
    return payload


def notional_suffix(value: float) -> str:
    return str(int(value))


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


def validate_history_header(path: Path) -> None:
    with path.open(newline="", encoding="utf-8") as file:
        reader = csv.reader(file)
        header = next(reader, [])
    if header != HISTORY_FIELDNAMES:
        raise ValueError(
            f"History CSV schema mismatch for {path}. Use a new output file, "
            "for example data/spread_history_enriched.csv"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect spread snapshots into an append-only CSV history."
    )
    parser.add_argument("--exchanges", help="Comma-separated list, default: bybit,bitget,okx,mexc,binance,hyperliquid,aster")
    parser.add_argument("--quote", default="USDT")
    parser.add_argument("--top", type=int, default=DEFAULT_TOP)
    parser.add_argument("--interval", type=int, default=60, help="Seconds between snapshots.")
    parser.add_argument("--limit", type=int, default=None, help="Optional max rows saved per snapshot.")
    parser.add_argument("--min-spread", type=float, default=0.5)
    parser.add_argument("--max-spread", type=float, default=None)
    parser.add_argument("--min-volume", type=float, default=400_000)
    parser.add_argument("--require-funding-favorable", action="store_true")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument(
        "--max-sane-spread",
        type=float,
        default=DEFAULT_MAX_SANE_SPREAD_PCT,
        help="Drop likely incompatible contracts above this spread percent; <=0 disables.",
    )
    parser.add_argument("--output", default="data/spread_history.csv")
    parser.add_argument("--capture-orderbooks", action="store_true")
    parser.add_argument(
        "--orderbook-limit",
        type=int,
        default=20,
        help="Max rows per snapshot enriched with order books.",
    )
    parser.add_argument("--once", action="store_true", help="Collect one snapshot and exit.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.once:
        collect_once(args)
        return

    print(
        "Spread history collector started: "
        f"top={args.top}, interval={args.interval}s, output={args.output}"
    )
    while True:
        try:
            collect_once(args)
        except Exception as exc:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{timestamp}] Collector failed: {type(exc).__name__}: {exc}")
        sleep(args.interval)


if __name__ == "__main__":
    main()
