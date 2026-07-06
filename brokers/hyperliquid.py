from __future__ import annotations

import argparse

from .common import (
    Fees,
    MarketSnapshot,
    ensure,
    http_post_json,
    orderbook_from_rows,
    print_snapshot,
    to_float,
    to_int,
)


EXCHANGE = "hyperliquid"
BASE_URL = "https://api.hyperliquid.xyz"


def normalize_symbol(symbol: str, quote: str = "USDT") -> str:
    value = symbol.upper().strip().replace("-", "").replace("_", "")
    if value.endswith(quote):
        return value[: -len(quote)]
    return value.removesuffix("SWAP")


def fetch_meta_and_contexts(timeout: float = 10.0) -> tuple[list[dict], list[dict], float]:
    payload, latency = http_post_json(
        BASE_URL,
        "/info",
        {"type": "metaAndAssetCtxs"},
        timeout=timeout,
    )
    ensure(isinstance(payload, list) and len(payload) >= 2, "Hyperliquid metaAndAssetCtxs returned unexpected payload", payload)
    meta = payload[0]
    contexts = payload[1]
    ensure(isinstance(meta, dict), "Hyperliquid meta is not an object", payload)
    ensure(isinstance(contexts, list), "Hyperliquid contexts are not a list", payload)
    universe = meta.get("universe", [])
    ensure(isinstance(universe, list), "Hyperliquid universe is not a list", meta)
    return [item for item in universe if isinstance(item, dict)], [item for item in contexts if isinstance(item, dict)], latency


def fetch_snapshot(symbol: str, depth: int = 20, timeout: float = 10.0) -> MarketSnapshot:
    coin = normalize_symbol(symbol)
    book_payload, book_latency = http_post_json(
        BASE_URL,
        "/info",
        {"type": "l2Book", "coin": coin},
        timeout=timeout,
    )
    ensure(isinstance(book_payload, dict), "Hyperliquid l2Book returned non-object", book_payload)
    levels = book_payload.get("levels", [])
    bids = levels[0] if isinstance(levels, list) and len(levels) > 0 else []
    asks = levels[1] if isinstance(levels, list) and len(levels) > 1 else []
    orderbook = orderbook_from_rows(
        [[item.get("px"), item.get("sz")] for item in bids[:depth] if isinstance(item, dict)],
        [[item.get("px"), item.get("sz")] for item in asks[:depth] if isinstance(item, dict)],
        to_int(book_payload.get("time")),
    )
    universe, contexts, meta_latency = fetch_meta_and_contexts(timeout=timeout)
    ctx_by_coin = {
        str(meta.get("name", "")).upper(): ctx
        for meta, ctx in zip(universe, contexts)
        if isinstance(meta, dict) and isinstance(ctx, dict)
    }
    ctx = ctx_by_coin.get(coin, {})
    mid = to_float(ctx.get("midPx")) or to_float(ctx.get("markPx"))
    return MarketSnapshot(
        exchange=EXCHANGE,
        symbol=f"{coin}USDT",
        bid=orderbook.bids[0].price if orderbook.bids else mid,
        ask=orderbook.asks[0].price if orderbook.asks else mid,
        last_price=mid,
        mark_price=to_float(ctx.get("markPx")),
        index_price=to_float(ctx.get("oraclePx")),
        funding_rate=to_float(ctx.get("funding")),
        next_funding_time_ms=None,
        open_interest=to_float(ctx.get("openInterest")),
        volume_24h=None,
        quote_volume_24h=to_float(ctx.get("dayNtlVlm")),
        fees=Fees(),
        latency_ms=round(book_latency + meta_latency, 3),
        timestamp_ms=to_int(book_payload.get("time")),
        orderbook=orderbook,
        raw={"book": book_payload, "context": ctx},
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch a Hyperliquid perp market snapshot.")
    parser.add_argument("symbol", nargs="?", default="BTC")
    parser.add_argument("--depth", type=int, default=20)
    args = parser.parse_args()
    print_snapshot(fetch_snapshot(args.symbol, args.depth))


if __name__ == "__main__":
    main()

