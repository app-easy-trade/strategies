from __future__ import annotations

import argparse
import os
from pathlib import Path

from .account import AccountSnapshot, fetch_account_snapshot


SUPPORTED_EXCHANGES = ("bybit", "binance")


def load_env_file(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_credentials(exchange: str) -> tuple[str | None, str | None, str | None, bool, bool]:
    prefix = exchange.upper()
    key = os.getenv(f"{prefix}_API_KEY")
    secret = os.getenv(f"{prefix}_API_SECRET")
    passphrase = os.getenv(f"{prefix}_API_PASSPHRASE")
    testnet = os.getenv(f"{prefix}_TESTNET", "").lower() in {"1", "true", "yes"}
    demo = os.getenv(f"{prefix}_DEMO", "").lower() in {"1", "true", "yes"}
    return key, secret, passphrase, testnet, demo


def check_accounts(exchanges: list[str], timeout: float) -> list[tuple[str, AccountSnapshot | None, str | None]]:
    results: list[tuple[str, AccountSnapshot | None, str | None]] = []
    for exchange in exchanges:
        key, secret, passphrase, testnet, demo = load_credentials(exchange)
        if not key or not secret:
            results.append((exchange, None, "missing API key/secret"))
            continue
        try:
            snapshot = fetch_account_snapshot(
                exchange,
                key,
                secret,
                passphrase=passphrase,
                testnet=testnet,
                demo=demo,
                timeout=timeout,
            )
        except Exception as exc:
            results.append((exchange, None, f"{type(exc).__name__}: {exc}"))
        else:
            results.append((exchange, snapshot, None))
    return results


def format_account_report(results: list[tuple[str, AccountSnapshot | None, str | None]]) -> str:
    lines = ["Read-only account checks"]
    for exchange, snapshot, error in results:
        if error:
            lines.append(f"- {exchange}: SKIP/FAIL {error}")
            continue
        assert snapshot is not None
        usage = "n/a" if snapshot.margin_usage_pct is None else f"{snapshot.margin_usage_pct:.2f}%"
        equity = format_usd(snapshot.equity_usd)
        available = format_usd(snapshot.available_balance_usd)
        upnl = format_usd(snapshot.unrealized_pnl_usd)
        lines.append(
            f"- {exchange}: equity={equity} available={available} "
            f"margin_usage={usage} uPnL={upnl} positions={len(snapshot.positions)}"
        )
        for position in snapshot.positions[:10]:
            lines.append(
                f"  {position.symbol} {position.side} size={position.size:g} "
                f"notional={format_usd(position.notional_usd)} "
                f"entry={format_number(position.entry_price)} mark={format_number(position.mark_price)} "
                f"liq={format_number(position.liquidation_price)}"
            )
        if len(snapshot.positions) > 10:
            lines.append(f"  hidden positions: {len(snapshot.positions) - 10}")
    return "\n".join(lines)


def format_usd(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}"


def format_number(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.8g}"


def parse_exchanges(value: str | None) -> list[str]:
    if not value:
        return list(SUPPORTED_EXCHANGES)
    return [item.strip().lower() for item in value.split(",") if item.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only balance, margin and position checks.")
    parser.add_argument("--exchanges", default=",".join(SUPPORTED_EXCHANGES))
    parser.add_argument("--env", type=Path, default=Path(".env"))
    parser.add_argument("--timeout", type=float, default=10.0)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    load_env_file(args.env)
    results = check_accounts(parse_exchanges(args.exchanges), args.timeout)
    print(format_account_report(results))


if __name__ == "__main__":
    main()
