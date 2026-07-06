from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from time import sleep

from .spread_scanner import DEFAULT_TOP, SpreadRow, format_funding_edge, scan_spreads, write_csv
from .universe import exchange_names


def filter_rows(
    rows: list[SpreadRow],
    min_spread: float,
    max_spread: float | None,
    min_volume: float | None,
    require_funding_favorable: bool = False,
) -> list[SpreadRow]:
    filtered = [row for row in rows if row.spread_pct >= min_spread]
    if max_spread is not None:
        filtered = [row for row in filtered if row.spread_pct <= max_spread]
    if min_volume is not None:
        filtered = [row for row in filtered if row.min_quote_volume_24h >= min_volume]
    if require_funding_favorable:
        filtered = [row for row in filtered if row.funding_status == "favorable"]
    return filtered


def print_spread_alerts(rows: list[SpreadRow], limit: int, started_at: datetime) -> None:
    timestamp = started_at.strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[{timestamp}] Найдены разлеты spread: {len(rows)}")
    if not rows:
        print("Разлетов выше заданных фильтров нет.")
        return

    for index, row in enumerate(rows[:limit], start=1):
        print(
            f"{index:>3}. {row.base}: buy/long {row.long_exchange} "
            f"({row.long_symbol}) ask={row.long_ask:.8g} -> "
            f"sell/short {row.short_exchange} ({row.short_symbol}) "
            f"bid={row.short_bid:.8g} | spread={row.spread_pct:.4f}% "
            f"| funding={format_funding_edge(row)} {row.funding_status} "
            f"| minVol24h={row.min_quote_volume_24h:.2f}"
        )

    if len(rows) > limit:
        print(f"... еще {len(rows) - limit} строк скрыто лимитом вывода.")


def run_once(args: argparse.Namespace) -> list[SpreadRow]:
    started_at = datetime.now()
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
    print_spread_alerts(rows, args.limit, started_at)
    if args.csv:
        write_csv(Path(args.csv), rows)
        print(f"CSV saved: {args.csv}")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Monitor pairwise exchange spreads every N seconds."
    )
    parser.add_argument("--exchanges", help="Comma-separated list, default: bybit,bitget,okx,mexc,binance,hyperliquid,aster")
    parser.add_argument("--quote", default="USDT")
    parser.add_argument("--top", type=int, default=DEFAULT_TOP)
    parser.add_argument("--interval", type=int, default=300, help="Seconds between checks.")
    parser.add_argument("--limit", type=int, default=50, help="Max terminal rows per check.")
    parser.add_argument("--min-spread", type=float, default=0.5)
    parser.add_argument("--max-spread", type=float, default=5.0)
    parser.add_argument("--min-volume", type=float, default=1_000_000)
    parser.add_argument("--require-funding-favorable", action="store_true")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--csv", default="data/spreads_latest.csv")
    parser.add_argument("--once", action="store_true", help="Run one check and exit.")
    args = parser.parse_args()

    if args.once:
        run_once(args)
        return

    print(
        "Spread monitor started: "
        f"top={args.top}, interval={args.interval}s, "
        f"min_spread={args.min_spread}%, max_spread={args.max_spread}%, "
        f"min_volume={args.min_volume}, "
        f"require_funding_favorable={args.require_funding_favorable}"
    )
    while True:
        try:
            run_once(args)
        except Exception as exc:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n[{timestamp}] Monitor check failed: {type(exc).__name__}: {exc}")
        sleep(args.interval)


if __name__ == "__main__":
    main()
