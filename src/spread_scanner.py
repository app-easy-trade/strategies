from __future__ import annotations

import argparse
import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, replace
from itertools import combinations
from pathlib import Path

from .universe import (
    TickerQuote,
    exchange_names,
    fetch_exchange_universe,
    top_liquid_tickers,
)
from brokers import okx
from brokers.common import http_get_json, to_float, to_int


DEFAULT_TOP = 1000
OKX_FUNDING_ENRICH_LIMIT = 200


@dataclass(frozen=True)
class SpreadRow:
    base: str
    pair: str
    long_exchange: str
    short_exchange: str
    long_symbol: str
    short_symbol: str
    long_ask: float
    short_bid: float
    spread_pct: float
    long_quote_volume_24h: float
    short_quote_volume_24h: float
    min_quote_volume_24h: float
    long_funding_rate: float | None
    short_funding_rate: float | None
    funding_edge_pct: float | None
    funding_status: str
    long_timestamp_ms: int | None
    short_timestamp_ms: int | None


SPREAD_FIELDNAMES = list(SpreadRow.__dataclass_fields__.keys())


def scan_spreads(
    exchanges: list[str],
    quote: str = "USDT",
    top: int = DEFAULT_TOP,
    timeout: float = 10.0,
) -> tuple[list[SpreadRow], dict[str, list[TickerQuote]]]:
    top_by_exchange = fetch_top_tickers(exchanges, quote, top, timeout)
    rows: list[SpreadRow] = []

    for left_name, right_name in combinations(exchanges, 2):
        left = {ticker.base: ticker for ticker in top_by_exchange[left_name]}
        right = {ticker.base: ticker for ticker in top_by_exchange[right_name]}
        for base in sorted(left.keys() & right.keys()):
            best = best_direction(left[base], right[base])
            if best:
                rows.append(best)

    rows.sort(key=lambda item: item.spread_pct, reverse=True)
    return rows, top_by_exchange


def fetch_top_tickers(
    exchanges: list[str],
    quote: str,
    top: int,
    timeout: float,
) -> dict[str, list[TickerQuote]]:
    with ThreadPoolExecutor(max_workers=len(exchanges)) as executor:
        futures = {
            executor.submit(fetch_exchange_universe, name, quote, timeout): name
            for name in exchanges
        }
        top_by_exchange: dict[str, list[TickerQuote]] = {}
        for future in as_completed(futures):
            name = futures[future]
            universe = future.result()
            top_tickers = top_liquid_tickers(universe.tickers, top)
            if name == "okx":
                top_tickers = enrich_okx_funding(top_tickers, timeout)
            top_by_exchange[name] = top_tickers
    return top_by_exchange


def enrich_okx_funding(tickers: list[TickerQuote], timeout: float) -> list[TickerQuote]:
    enriched = list(tickers)
    candidates = [
        (index, ticker)
        for index, ticker in enumerate(enriched[:OKX_FUNDING_ENRICH_LIMIT])
        if ticker.funding_rate is None
    ]
    if not candidates:
        return enriched

    with ThreadPoolExecutor(max_workers=min(12, len(candidates))) as executor:
        futures = {
            executor.submit(fetch_okx_funding_rate, ticker.symbol, timeout): index
            for index, ticker in candidates
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                funding_rate, next_funding_time_ms = future.result()
            except Exception:
                continue
            raw = dict(enriched[index].raw)
            raw["funding_enriched"] = {
                "funding_rate": funding_rate,
                "next_funding_time_ms": next_funding_time_ms,
            }
            enriched[index] = replace(enriched[index], funding_rate=funding_rate, raw=raw)
    return enriched


def fetch_okx_funding_rate(symbol: str, timeout: float) -> tuple[float | None, int | None]:
    payload, _ = http_get_json(
        okx.BASE_URL,
        "/api/v5/public/funding-rate",
        {"instId": symbol},
        timeout=timeout,
    )
    if payload.get("code") != "0":
        return None, None
    data = payload.get("data")
    item = data[0] if isinstance(data, list) and data else None
    if not isinstance(item, dict):
        return None, None
    return to_float(item.get("fundingRate")), to_int(item.get("nextFundingTime"))


def best_direction(left: TickerQuote, right: TickerQuote) -> SpreadRow | None:
    options = [
        build_spread_row(long_ticker=left, short_ticker=right),
        build_spread_row(long_ticker=right, short_ticker=left),
    ]
    valid = [row for row in options if row is not None]
    if not valid:
        return None
    return max(valid, key=lambda item: item.spread_pct)


def build_spread_row(
    long_ticker: TickerQuote,
    short_ticker: TickerQuote,
) -> SpreadRow | None:
    if long_ticker.ask is None or short_ticker.bid is None:
        return None
    if long_ticker.ask <= 0 or short_ticker.bid <= 0:
        return None

    spread_pct = (short_ticker.bid / long_ticker.ask - 1) * 100
    long_volume = long_ticker.quote_volume_24h or 0.0
    short_volume = short_ticker.quote_volume_24h or 0.0
    funding_edge_pct = calculate_funding_edge_pct(
        long_ticker.funding_rate,
        short_ticker.funding_rate,
    )
    return SpreadRow(
        base=long_ticker.base,
        pair=f"{long_ticker.exchange}/{short_ticker.exchange}",
        long_exchange=long_ticker.exchange,
        short_exchange=short_ticker.exchange,
        long_symbol=long_ticker.symbol,
        short_symbol=short_ticker.symbol,
        long_ask=long_ticker.ask,
        short_bid=short_ticker.bid,
        spread_pct=spread_pct,
        long_quote_volume_24h=long_volume,
        short_quote_volume_24h=short_volume,
        min_quote_volume_24h=min(long_volume, short_volume),
        long_funding_rate=long_ticker.funding_rate,
        short_funding_rate=short_ticker.funding_rate,
        funding_edge_pct=funding_edge_pct,
        funding_status=funding_status(funding_edge_pct),
        long_timestamp_ms=long_ticker.timestamp_ms,
        short_timestamp_ms=short_ticker.timestamp_ms,
    )


def calculate_funding_edge_pct(
    long_funding_rate: float | None,
    short_funding_rate: float | None,
) -> float | None:
    if long_funding_rate is None or short_funding_rate is None:
        return None
    # Positive funding usually means longs pay shorts.
    return (short_funding_rate - long_funding_rate) * 100


def funding_status(funding_edge_pct: float | None) -> str:
    if funding_edge_pct is None:
        return "unknown"
    if funding_edge_pct > 0:
        return "favorable"
    if funding_edge_pct < 0:
        return "against"
    return "flat"


def write_csv(path: Path, rows: list[SpreadRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=SPREAD_FIELDNAMES)
        writer.writeheader()
        writer.writerows(asdict(row) for row in rows)


def print_rows(rows: list[SpreadRow], limit: int) -> None:
    header = (
        f"{'#':>3} {'base':<12} {'long':<7} {'short':<7} "
        f"{'ask':>14} {'bid':>14} {'spread%':>10} "
        f"{'funding':>10} {'minVol24h':>14}"
    )
    print(header)
    print("-" * len(header))
    for index, row in enumerate(rows[:limit], start=1):
        print(
            f"{index:>3} {row.base:<12} {row.long_exchange:<7} {row.short_exchange:<7} "
            f"{row.long_ask:>14.8g} {row.short_bid:>14.8g} "
            f"{row.spread_pct:>10.4f} {format_funding_edge(row):>10} "
            f"{row.min_quote_volume_24h:>14.2f}"
        )


def format_funding_edge(row: SpreadRow) -> str:
    if row.funding_edge_pct is None:
        return "unknown"
    return f"{row.funding_edge_pct:+.4f}%"


def summarize_top(top_by_exchange: dict[str, list[TickerQuote]]) -> None:
    for exchange, tickers in top_by_exchange.items():
        first = tickers[0] if tickers else None
        if not first:
            print(f"{exchange:6} top=0")
            continue
        print(
            f"{exchange:6} top={len(tickers)} "
            f"leader={first.symbol} quote_volume_24h={first.quote_volume_24h or 0:.2f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pairwise spread scanner for top liquid perpetual markets."
    )
    parser.add_argument("--exchanges", help="Comma-separated list, default: bybit,bitget,okx,mexc,binance,hyperliquid,aster")
    parser.add_argument("--quote", default="USDT")
    parser.add_argument("--top", type=int, default=DEFAULT_TOP)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--min-spread", type=float, default=0.5)
    parser.add_argument("--max-spread", type=float, default=None)
    parser.add_argument("--min-volume", type=float, default=None)
    parser.add_argument("--require-funding-favorable", action="store_true")
    parser.add_argument("--csv", type=Path, default=Path("data/spreads_top1000.csv"))
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()

    exchanges = exchange_names(args.exchanges)
    rows, top_by_exchange = scan_spreads(
        exchanges=exchanges,
        quote=args.quote.upper(),
        top=args.top,
        timeout=args.timeout,
    )
    if args.min_spread is not None:
        rows = [row for row in rows if row.spread_pct >= args.min_spread]
    if args.max_spread is not None:
        rows = [row for row in rows if row.spread_pct <= args.max_spread]
    if args.min_volume is not None:
        rows = [row for row in rows if row.min_quote_volume_24h >= args.min_volume]
    if args.require_funding_favorable:
        rows = [row for row in rows if row.funding_status == "favorable"]

    summarize_top(top_by_exchange)
    print(f"\nPairwise spreads: {len(rows)} rows")
    print_rows(rows, args.limit)
    write_csv(args.csv, rows)
    print(f"\nCSV saved: {args.csv}")


if __name__ == "__main__":
    main()
