from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from time import sleep

from .spread_monitor import filter_rows
from .spread_scanner import DEFAULT_TOP, SPREAD_FIELDNAMES, SpreadRow, scan_spreads
from .universe import exchange_names


HISTORY_FIELDNAMES = ["timestamp"] + SPREAD_FIELDNAMES


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def append_history(path: Path, timestamp: str, rows: list[SpreadRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists() or path.stat().st_size == 0

    with path.open("a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=HISTORY_FIELDNAMES)
        if write_header:
            writer.writeheader()
        for row in rows:
            payload = {"timestamp": timestamp}
            payload.update(asdict(row))
            writer.writerow(payload)


def collect_once(args: argparse.Namespace) -> list[SpreadRow]:
    exchanges = exchange_names(args.exchanges)
    rows, _ = scan_spreads(
        exchanges=exchanges,
        quote=args.quote.upper(),
        top=args.top,
        timeout=args.timeout,
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
    append_history(Path(args.output), timestamp, rows)
    print(
        f"[{timestamp}] appended {len(rows)} rows to {args.output} "
        f"(spread >= {args.min_spread}%, minVol24h >= {args.min_volume})"
    )
    return rows


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
    parser.add_argument("--output", default="data/spread_history.csv")
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
