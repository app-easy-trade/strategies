from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from core.config import StrategyConfig, load_strategy_config
from core.models import SpreadSnapshot, TradeSignal
from core.signal_engine import evaluate_signal
from core.spread_engine import SpreadFeatureEngine

from .statistics import BacktestStats, calculate_stats


BacktestConfig = StrategyConfig
load_config = load_strategy_config


@dataclass
class OpenTrade:
    entry_signal: TradeSignal
    remaining_fraction: float = 1.0
    realized_pnl_usd: float = 0.0
    tp1_done: bool = False
    max_spread_pct: float = 0.0

    @property
    def entry(self) -> SpreadSnapshot:
        return self.entry_signal.features.snapshot


@dataclass(frozen=True)
class ClosedTrade:
    base: str
    pair: str
    long_exchange: str
    short_exchange: str
    long_symbol: str
    short_symbol: str
    entry_time: str
    exit_time: str
    entry_spread_pct: float
    exit_spread_pct: float
    max_spread_pct: float
    holding_seconds: float
    position_notional_usd: float
    gross_pnl_usd: float
    fees_usd: float
    slippage_usd: float
    funding_pnl_usd: float
    net_pnl_usd: float
    exit_reason: str
    entry_zscore: float | None
    entry_velocity_pct_per_minute: float | None
    entry_category: str
    entry_net_expected_profit_usd: float


@dataclass(frozen=True)
class SkippedSignal:
    timestamp: str
    base: str
    pair: str
    long_exchange: str
    short_exchange: str
    spread_pct: float
    min_quote_volume_24h: float
    reason: str
    zscore: float | None
    velocity_pct_per_minute: float | None
    acceleration_pct_per_minute2: float | None
    category: str
    gross_edge_usd: float
    estimated_cost_usd: float
    funding_edge_usd: float
    net_expected_profit_usd: float


def read_snapshots(path: Path) -> list[SpreadSnapshot]:
    with path.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        rows = [snapshot_from_row(row) for row in reader]
    return sorted(rows, key=lambda item: (item.timestamp, item.key))


def snapshot_from_row(row: dict[str, str]) -> SpreadSnapshot:
    long_ask = as_float(row, "long_ask")
    short_bid = as_float(row, "short_bid")
    spread = optional_float(row.get("spread_pct"))
    if spread is None:
        spread = (short_bid / long_ask - 1) * 100
    return SpreadSnapshot(
        timestamp=parse_timestamp(row.get("timestamp") or row.get("timestamp_ms") or ""),
        base=required(row, "base"),
        pair=row.get("pair") or f"{required(row, 'long_exchange')}/{required(row, 'short_exchange')}",
        long_exchange=required(row, "long_exchange"),
        short_exchange=required(row, "short_exchange"),
        long_symbol=row.get("long_symbol") or required(row, "base"),
        short_symbol=row.get("short_symbol") or required(row, "base"),
        long_ask=long_ask,
        short_bid=short_bid,
        spread_pct=spread,
        min_quote_volume_24h=as_float(row, "min_quote_volume_24h"),
        long_funding_rate=optional_float(row.get("long_funding_rate")),
        short_funding_rate=optional_float(row.get("short_funding_rate")),
    )


def run_backtest(
    snapshots: Iterable[SpreadSnapshot],
    config: BacktestConfig,
) -> tuple[list[ClosedTrade], BacktestStats, list[SkippedSignal]]:
    feature_engine = SpreadFeatureEngine(config)
    open_trades: dict[tuple[str, str, str, str, str], OpenTrade] = {}
    last_snapshot_by_key: dict[tuple[str, str, str, str, str], SpreadSnapshot] = {}
    closed: list[ClosedTrade] = []
    skipped: list[SkippedSignal] = []

    for snapshot in snapshots:
        last_snapshot_by_key[snapshot.key] = snapshot
        features = feature_engine.update(snapshot)
        trade = open_trades.get(snapshot.key)
        if trade is None:
            signal = evaluate_signal(features, config)
            if signal.should_enter:
                open_trades[snapshot.key] = OpenTrade(
                    entry_signal=signal,
                    max_spread_pct=snapshot.spread_pct,
                )
            else:
                skipped.append(skipped_signal(signal))
            continue

        trade.max_spread_pct = max(trade.max_spread_pct, snapshot.spread_pct)
        if should_take_tp1(trade, snapshot, config):
            trade.realized_pnl_usd += leg_pnl_usd(
                trade.entry.spread_pct,
                snapshot.spread_pct,
                config.position_notional_usd,
                config.tp1_close_fraction,
            )
            trade.remaining_fraction -= config.tp1_close_fraction
            trade.tp1_done = True

        reason = exit_reason(trade, snapshot, config)
        if reason:
            closed.append(close_trade(trade, snapshot, config, reason))
            del open_trades[snapshot.key]

    for trade in open_trades.values():
        exit_snapshot = last_snapshot_by_key.get(trade.entry.key, trade.entry)
        closed.append(close_trade(trade, exit_snapshot, config, "end_of_data"))

    stats = calculate_stats(
        [trade.net_pnl_usd for trade in closed],
        [trade.holding_seconds for trade in closed],
    )
    return closed, stats, skipped


def should_take_tp1(
    trade: OpenTrade,
    snapshot: SpreadSnapshot,
    config: BacktestConfig,
) -> bool:
    if trade.tp1_done:
        return False
    target = trade.entry.spread_pct * (1 - config.tp1_retrace_fraction)
    return snapshot.spread_pct <= target


def exit_reason(
    trade: OpenTrade,
    snapshot: SpreadSnapshot,
    config: BacktestConfig,
) -> str | None:
    holding_seconds = (snapshot.timestamp - trade.entry.timestamp).total_seconds()
    if snapshot.spread_pct >= trade.entry.spread_pct + config.max_adverse_spread_pct:
        return "max_adverse_spread"
    if snapshot.spread_pct <= config.final_exit_spread_pct:
        return "final_spread_target"
    if holding_seconds >= config.max_holding_seconds:
        return "max_holding_time"
    return None


def close_trade(
    trade: OpenTrade,
    exit_snapshot: SpreadSnapshot,
    config: BacktestConfig,
    reason: str,
) -> ClosedTrade:
    remaining_pnl = leg_pnl_usd(
        trade.entry.spread_pct,
        exit_snapshot.spread_pct,
        config.position_notional_usd,
        trade.remaining_fraction,
    )
    gross_pnl = trade.realized_pnl_usd + remaining_pnl
    holding_seconds = max(
        0.0,
        (exit_snapshot.timestamp - trade.entry.timestamp).total_seconds(),
    )
    fees = fee_cost_usd(config, trade.tp1_done)
    slippage = slippage_cost_usd(config)
    funding = funding_pnl_usd(trade.entry, holding_seconds, config)
    net_pnl = gross_pnl + funding - fees - slippage
    return ClosedTrade(
        base=trade.entry.base,
        pair=trade.entry.pair,
        long_exchange=trade.entry.long_exchange,
        short_exchange=trade.entry.short_exchange,
        long_symbol=trade.entry.long_symbol,
        short_symbol=trade.entry.short_symbol,
        entry_time=format_time(trade.entry.timestamp),
        exit_time=format_time(exit_snapshot.timestamp),
        entry_spread_pct=trade.entry.spread_pct,
        exit_spread_pct=exit_snapshot.spread_pct,
        max_spread_pct=trade.max_spread_pct,
        holding_seconds=holding_seconds,
        position_notional_usd=config.position_notional_usd,
        gross_pnl_usd=gross_pnl,
        fees_usd=fees,
        slippage_usd=slippage,
        funding_pnl_usd=funding,
        net_pnl_usd=net_pnl,
        exit_reason=reason,
        entry_zscore=trade.entry_signal.features.zscore,
        entry_velocity_pct_per_minute=trade.entry_signal.features.velocity_pct_per_minute,
        entry_category=trade.entry_signal.features.category,
        entry_net_expected_profit_usd=trade.entry_signal.net_expected_profit_usd,
    )


def skipped_signal(signal: TradeSignal) -> SkippedSignal:
    features = signal.features
    snapshot = features.snapshot
    return SkippedSignal(
        timestamp=format_time(snapshot.timestamp),
        base=snapshot.base,
        pair=snapshot.pair,
        long_exchange=snapshot.long_exchange,
        short_exchange=snapshot.short_exchange,
        spread_pct=snapshot.spread_pct,
        min_quote_volume_24h=snapshot.min_quote_volume_24h,
        reason=signal.reason,
        zscore=features.zscore,
        velocity_pct_per_minute=features.velocity_pct_per_minute,
        acceleration_pct_per_minute2=features.acceleration_pct_per_minute2,
        category=features.category,
        gross_edge_usd=signal.gross_edge_usd,
        estimated_cost_usd=signal.estimated_cost_usd,
        funding_edge_usd=signal.funding_edge_usd,
        net_expected_profit_usd=signal.net_expected_profit_usd,
    )


def leg_pnl_usd(
    entry_spread_pct: float,
    exit_spread_pct: float,
    notional_usd: float,
    fraction: float,
) -> float:
    return (entry_spread_pct - exit_spread_pct) / 100 * notional_usd * fraction


def fee_cost_usd(config: BacktestConfig, had_tp1: bool) -> float:
    open_cost = 2 * config.position_notional_usd * config.maker_fee_pct / 100
    close_turnover = config.position_notional_usd
    if had_tp1:
        close_turnover += config.position_notional_usd * config.tp1_close_fraction
    close_cost = 2 * close_turnover * config.taker_fee_pct / 100
    return open_cost + close_cost


def slippage_cost_usd(config: BacktestConfig) -> float:
    return 4 * config.position_notional_usd * config.slippage_pct / 100


def funding_pnl_usd(
    snapshot: SpreadSnapshot,
    holding_seconds: float,
    config: BacktestConfig,
) -> float:
    if snapshot.long_funding_rate is None or snapshot.short_funding_rate is None:
        return 0.0
    periods = holding_seconds / (config.funding_interval_hours * 3600)
    edge_rate = snapshot.short_funding_rate - snapshot.long_funding_rate
    return edge_rate * config.position_notional_usd * periods


def write_trades(path: Path, trades: list[ClosedTrade]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(ClosedTrade.__dataclass_fields__))
        writer.writeheader()
        writer.writerows(asdict(trade) for trade in trades)


def write_skipped_signals(path: Path, signals: list[SkippedSignal]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(SkippedSignal.__dataclass_fields__))
        writer.writeheader()
        writer.writerows(asdict(signal) for signal in signals)


def print_report(
    stats: BacktestStats,
    skipped_count: int,
    trades_path: Path | None,
    skipped_path: Path | None,
) -> None:
    print("Backtest complete")
    print(f"trades={stats.trades}")
    print(f"skipped_signals={skipped_count}")
    print(f"win_rate={stats.win_rate_pct:.2f}%")
    print(f"net_profit_usd={stats.net_profit_usd:.4f}")
    if stats.profit_factor is None:
        print("profit_factor=inf")
    else:
        print(f"profit_factor={stats.profit_factor:.4f}")
    print(f"max_drawdown_usd={stats.max_drawdown_usd:.4f}")
    print(f"average_holding_seconds={stats.average_holding_seconds:.2f}")
    if trades_path:
        print(f"trades_csv={trades_path}")
    if skipped_path:
        print(f"skipped_csv={skipped_path}")


def required(row: dict[str, str], key: str) -> str:
    value = row.get(key)
    if value is None or value == "":
        raise ValueError(f"Missing required column: {key}")
    return value


def as_float(row: dict[str, str], key: str) -> float:
    value = optional_float(row.get(key))
    if value is None:
        raise ValueError(f"Missing numeric column: {key}")
    return value


def optional_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def parse_timestamp(value: str) -> datetime:
    value = value.strip()
    if not value:
        raise ValueError("Missing timestamp")
    if value.isdigit():
        number = int(value)
        if number > 10_000_000_000:
            number = number / 1000
        return datetime.fromtimestamp(number, tz=timezone.utc)
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run spread arbitrage CSV backtest.")
    parser.add_argument("--input", type=Path, required=True, help="CSV with spread snapshots.")
    parser.add_argument("--config", type=Path, default=Path("config/backtest.yaml"))
    parser.add_argument("--trades-csv", type=Path, default=Path("data/backtest_trades.csv"))
    parser.add_argument("--skipped-csv", type=Path, default=Path("data/backtest_skipped_signals.csv"))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = load_config(args.config if args.config.exists() else None)
    snapshots = read_snapshots(args.input)
    trades, stats, skipped = run_backtest(snapshots, config)
    write_trades(args.trades_csv, trades)
    write_skipped_signals(args.skipped_csv, skipped)
    print_report(stats, len(skipped), args.trades_csv, args.skipped_csv)


if __name__ == "__main__":
    main()

