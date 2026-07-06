from __future__ import annotations

import argparse

from .common import (
    Fees,
    MarketSnapshot,
    ensure,
    http_get_json,
    http_get_json_any,
    orderbook_from_rows,
    print_snapshot,
    to_float,
    to_int,
)


EXCHANGE = "binance"
BASE_URL = "https://fapi.binance.com"


def normalize_symbol(symbol: str) -> str:
    return symbol.upper().strip().replace("_", "").replace("-", "").removesuffix("SWAP")


def fetch_snapshot(symbol: str, depth: int = 20, timeout: float = 10.0) -> MarketSnapshot:
    symbol = normalize_symbol(symbol)
    ticker_payload, ticker_latency = http_get_json(
        BASE_URL,
        "/fapi/v1/ticker/24hr",
        {"symbol": symbol},
        timeout=timeout,
    )
    book_ticker_payload, book_ticker_latency = http_get_json(
        BASE_URL,
        "/fapi/v1/ticker/bookTicker",
        {"symbol": symbol},
        timeout=timeout,
    )
    premium_payload, premium_latency = http_get_json(
        BASE_URL,
        "/fapi/v1/premiumIndex",
        {"symbol": symbol},
        timeout=timeout,
    )
    book_payload, book_latency = http_get_json(
        BASE_URL,
        "/fapi/v1/depth",
        {"symbol": symbol, "limit": depth},
        timeout=timeout,
    )
    orderbook = orderbook_from_rows(book_payload.get("bids"), book_payload.get("asks"), to_int(book_payload.get("T")))

    return MarketSnapshot(
        exchange=EXCHANGE,
        symbol=symbol,
        bid=to_float(book_ticker_payload.get("bidPrice")) or (orderbook.bids[0].price if orderbook.bids else None),
        ask=to_float(book_ticker_payload.get("askPrice")) or (orderbook.asks[0].price if orderbook.asks else None),
        last_price=to_float(ticker_payload.get("lastPrice")),
        mark_price=to_float(premium_payload.get("markPrice")),
        index_price=to_float(premium_payload.get("indexPrice")),
        funding_rate=to_float(premium_payload.get("lastFundingRate")),
        next_funding_time_ms=to_int(premium_payload.get("nextFundingTime")),
        open_interest=None,
        volume_24h=to_float(ticker_payload.get("volume")),
        quote_volume_24h=to_float(ticker_payload.get("quoteVolume")),
        fees=Fees(),
        latency_ms=round(ticker_latency + book_ticker_latency + premium_latency + book_latency, 3),
        timestamp_ms=to_int(ticker_payload.get("closeTime")) or to_int(premium_payload.get("time")),
        orderbook=orderbook,
        raw={"ticker": ticker_payload, "book_ticker": book_ticker_payload, "premium": premium_payload},
    )


def fetch_universe_payloads(timeout: float = 10.0) -> tuple[list[dict], dict[str, dict], dict[str, dict], float]:
    tickers_payload, ticker_latency = http_get_json_any(
        BASE_URL,
        "/fapi/v1/ticker/24hr",
        timeout=timeout,
    )
    book_payload, book_latency = http_get_json_any(
        BASE_URL,
        "/fapi/v1/ticker/bookTicker",
        timeout=timeout,
    )
    premium_payload, premium_latency = http_get_json_any(
        BASE_URL,
        "/fapi/v1/premiumIndex",
        timeout=timeout,
    )
    ensure(isinstance(tickers_payload, list), "Binance ticker universe returned non-list", tickers_payload)
    ensure(isinstance(book_payload, list), "Binance book ticker universe returned non-list", book_payload)
    ensure(isinstance(premium_payload, list), "Binance premium universe returned non-list", premium_payload)
    books = {str(item.get("symbol", "")).upper(): item for item in book_payload if isinstance(item, dict)}
    premiums = {str(item.get("symbol", "")).upper(): item for item in premium_payload if isinstance(item, dict)}
    return [item for item in tickers_payload if isinstance(item, dict)], books, premiums, ticker_latency + book_latency + premium_latency


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch a Binance USD-M futures market snapshot.")
    parser.add_argument("symbol", nargs="?", default="BTCUSDT")
    parser.add_argument("--depth", type=int, default=20)
    args = parser.parse_args()
    print_snapshot(fetch_snapshot(args.symbol, args.depth))


if __name__ == "__main__":
    main()

