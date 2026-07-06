from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed

from .spread_scanner import DEFAULT_TOP
from .universe import exchange_names, fetch_exchange_universe, top_liquid_tickers, valid_tickers


def check_exchange(exchange: str, quote: str, top: int, timeout: float) -> tuple[str, str]:
    universe = fetch_exchange_universe(exchange, quote=quote, timeout=timeout)
    valid = valid_tickers(universe.tickers)
    liquid = top_liquid_tickers(universe.tickers, top)
    top_symbol = liquid[0].symbol if liquid else "-"
    top_volume = liquid[0].quote_volume_24h if liquid else 0.0
    return (
        exchange,
        (
            f"OK all={len(universe.tickers)} valid={len(valid)} "
            f"top={len(liquid)} latency_ms={universe.latency_ms:.1f} "
            f"top_symbol={top_symbol} top_quote_volume_24h={top_volume:.2f}"
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Check public market-data connectivity.")
    parser.add_argument("--exchanges", help="Comma-separated list, default: bybit,bitget,okx,mexc,binance,hyperliquid,aster")
    parser.add_argument("--quote", default="USDT")
    parser.add_argument("--top", type=int, default=DEFAULT_TOP)
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()

    names = exchange_names(args.exchanges)
    print(f"Checking public {args.quote} perpetual market data...")

    with ThreadPoolExecutor(max_workers=len(names)) as executor:
        futures = {
            executor.submit(check_exchange, name, args.quote.upper(), args.top, args.timeout): name
            for name in names
        }
        results: dict[str, str] = {}
        for future in as_completed(futures):
            name = futures[future]
            try:
                exchange, message = future.result()
            except Exception as exc:
                results[name] = f"FAIL {type(exc).__name__}: {exc}"
            else:
                results[exchange] = message

    for name in names:
        print(f"{name:6} {results[name]}")


if __name__ == "__main__":
    main()
