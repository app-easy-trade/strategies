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
    execution: ExecutionSimulation
    remaining_fraction: float = 1.0
    realized_pnl_usd: float = 0.0
    tp1_done: bool = False
    max_spread_pct: float = 0.0
    min_spread_pct: float = 0.0

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
    execution_cost_usd: float
    net_pnl_usd: float
    exit_reason: str
    tp1_done: bool
    entry_zscore: float | None
    entry_velocity_pct_per_minute: float | None
    entry_category: str
    market_mode: str
    entry_executable_spread_pct: float
    exit_executable_spread_pct: float
    execution_status: str
    execution_reason: str
    execution_fill_fraction: float
    funding_events: int
    seconds_to_first_funding: float | None
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


@dataclass(frozen=True)
class ExecutionSimulation:
    passed: bool
    status: str
    reason: str
    fill_fraction: float
    cost_usd: float


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
        market_mode=row.get("market_mode") or infer_market_mode(row),
        executable_spread_100_pct=optional_float(row.get("executable_spread_100_pct")),
        executable_spread_200_pct=optional_float(row.get("executable_spread_200_pct")),
        executable_spread_500_pct=optional_float(row.get("executable_spread_500_pct")),
        executable_spread_1000_pct=optional_float(row.get("executable_spread_1000_pct")),
        long_depth_usd_100=optional_float(row.get("long_depth_usd_100")),
        short_depth_usd_100=optional_float(row.get("short_depth_usd_100")),
        long_depth_usd_200=optional_float(row.get("long_depth_usd_200")),
        short_depth_usd_200=optional_float(row.get("short_depth_usd_200")),
        long_depth_usd_1000=optional_float(row.get("long_depth_usd_1000")),
        short_depth_usd_1000=optional_float(row.get("short_depth_usd_1000")),
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
            execution = simulate_entry_execution(snapshot, config)
            if signal.should_enter and execution.passed:
                open_trades[snapshot.key] = OpenTrade(
                    entry_signal=signal,
                    execution=execution,
                    max_spread_pct=snapshot.spread_pct,
                    min_spread_pct=snapshot.spread_pct,
                )
            else:
                skipped.append(skipped_signal(signal, execution))
            continue

        trade.max_spread_pct = max(trade.max_spread_pct, snapshot.spread_pct)
        trade.min_spread_pct = min(trade.min_spread_pct, snapshot.spread_pct)
        if should_take_tp1(trade, snapshot, config):
            trade.realized_pnl_usd += trade_pnl_usd(
                trade.entry,
                snapshot,
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


def simulate_entry_execution(
    snapshot: SpreadSnapshot,
    config: BacktestConfig,
) -> ExecutionSimulation:
    if not config.execution_simulation_enabled:
        return ExecutionSimulation(True, "full", "simulation_disabled", 1.0, 0.0)

    long_depth = snapshot.depth_usd_for("long", config.position_notional_usd)
    short_depth = snapshot.depth_usd_for("short", config.position_notional_usd)
    if long_depth is None and short_depth is None:
        executable_spread = snapshot.executable_spread_pct_for(config.position_notional_usd)
        return ExecutionSimulation(
            passed=True,
            status="full",
            reason="top_of_book_fallback",
            fill_fraction=1.0,
            cost_usd=execution_spread_cost_usd(snapshot.spread_pct, executable_spread, config),
        )

    long_fill = fill_fraction(long_depth, config.position_notional_usd)
    short_fill = fill_fraction(short_depth, config.position_notional_usd)
    if long_fill <= 0 or short_fill <= 0:
        return ExecutionSimulation(
            passed=False,
            status="failed",
            reason="one_leg_unfilled",
            fill_fraction=0.0,
            cost_usd=config.position_notional_usd * config.one_leg_failure_penalty_pct / 100,
        )

    fill = min(long_fill, short_fill)
    executable_spread = snapshot.executable_spread_pct_for(config.position_notional_usd)
    cost = execution_spread_cost_usd(snapshot.spread_pct, executable_spread, config)
    if fill < config.min_execution_fill_fraction:
        return ExecutionSimulation(
            passed=False,
            status="partial",
            reason="partial_fill_below_min",
            fill_fraction=fill,
            cost_usd=cost,
        )
    status = "partial" if fill < 1 else "full"
    return ExecutionSimulation(True, status, "execution_ok", fill, cost)


def fill_fraction(depth_usd: float | None, notional_usd: float) -> float:
    if depth_usd is None:
        return 1.0
    if notional_usd <= 0:
        return 0.0
    return max(0.0, min(1.0, depth_usd / notional_usd))


def execution_spread_cost_usd(
    top_spread_pct: float,
    executable_spread_pct: float,
    config: BacktestConfig,
) -> float:
    lost_edge_pct = max(0.0, top_spread_pct - executable_spread_pct)
    return lost_edge_pct / 100 * config.position_notional_usd


def exit_reason(
    trade: OpenTrade,
    snapshot: SpreadSnapshot,
    config: BacktestConfig,
) -> str | None:
    holding_seconds = (snapshot.timestamp - trade.entry.timestamp).total_seconds()
    if snapshot.spread_pct >= trade.entry.spread_pct + config.max_adverse_spread_pct:
        return "max_adverse_spread"
    if should_exit_before_bad_funding(trade, snapshot, config):
        return "funding_avoid_bad"
    if should_dynamic_exit_after_tp1(trade, snapshot, config):
        return "dynamic_exit_after_tp1"
    if snapshot.spread_pct <= config.final_exit_spread_pct and not should_hold_for_good_funding(
        trade,
        snapshot,
        config,
    ):
        return "final_spread_target"
    if holding_seconds >= config.max_holding_seconds:
        return "max_holding_time"
    return None


def should_exit_before_bad_funding(
    trade: OpenTrade,
    snapshot: SpreadSnapshot,
    config: BacktestConfig,
) -> bool:
    if not config.funding_exit_enabled:
        return False
    edge_rate = funding_edge_rate(trade.entry)
    if edge_rate is None or edge_rate >= 0:
        return False
    seconds_left = seconds_to_next_funding(snapshot.timestamp, config)
    if seconds_left is None or seconds_left > config.funding_bad_exit_window_seconds:
        return False
    current_profit_pct = trade.entry.spread_pct - snapshot.spread_pct
    return current_profit_pct >= config.dynamic_exit_min_profit_pct or trade.tp1_done


def should_hold_for_good_funding(
    trade: OpenTrade,
    snapshot: SpreadSnapshot,
    config: BacktestConfig,
) -> bool:
    if not config.funding_exit_enabled:
        return False
    edge_rate = funding_edge_rate(trade.entry)
    if edge_rate is None or edge_rate <= 0:
        return False
    expected_event_usd = edge_rate * config.position_notional_usd * trade.remaining_fraction
    if expected_event_usd < config.funding_good_hold_min_edge_usd:
        return False
    seconds_left = seconds_to_next_funding(snapshot.timestamp, config)
    if seconds_left is None:
        return False
    return seconds_left <= config.funding_good_hold_window_seconds


def should_dynamic_exit_after_tp1(
    trade: OpenTrade,
    snapshot: SpreadSnapshot,
    config: BacktestConfig,
) -> bool:
    if not config.dynamic_exit_enabled or not trade.tp1_done:
        return False
    if should_hold_for_good_funding(trade, snapshot, config):
        return False
    current_profit_pct = trade.entry.spread_pct - snapshot.spread_pct
    if current_profit_pct < config.dynamic_exit_min_profit_pct:
        return False
    return snapshot.spread_pct >= trade.min_spread_pct + config.dynamic_exit_rebound_pct


def close_trade(
    trade: OpenTrade,
    exit_snapshot: SpreadSnapshot,
    config: BacktestConfig,
    reason: str,
) -> ClosedTrade:
    remaining_pnl = trade_pnl_usd(
        trade.entry,
        exit_snapshot,
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
    execution_cost = trade.execution.cost_usd
    funding_events = funding_events_crossed(
        trade.entry.timestamp,
        exit_snapshot.timestamp,
        config,
    )
    funding = funding_pnl_usd(trade.entry, funding_events, config)
    net_pnl = gross_pnl + funding - fees - slippage - execution_cost
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
        execution_cost_usd=execution_cost,
        net_pnl_usd=net_pnl,
        exit_reason=reason,
        tp1_done=trade.tp1_done,
        entry_zscore=trade.entry_signal.features.zscore,
        entry_velocity_pct_per_minute=trade.entry_signal.features.velocity_pct_per_minute,
        entry_category=trade.entry_signal.features.category,
        market_mode=trade.entry.market_mode,
        entry_executable_spread_pct=trade.entry.executable_spread_pct_for(
            config.position_notional_usd,
        ),
        exit_executable_spread_pct=exit_snapshot.executable_spread_pct_for(
            config.position_notional_usd,
        ),
        execution_status=trade.execution.status,
        execution_reason=trade.execution.reason,
        execution_fill_fraction=trade.execution.fill_fraction,
        funding_events=funding_events,
        seconds_to_first_funding=seconds_to_next_funding(trade.entry.timestamp, config),
        entry_net_expected_profit_usd=trade.entry_signal.net_expected_profit_usd,
    )


def skipped_signal(
    signal: TradeSignal,
    execution: ExecutionSimulation | None = None,
) -> SkippedSignal:
    features = signal.features
    snapshot = features.snapshot
    reason = signal.reason
    if signal.should_enter and execution is not None and not execution.passed:
        reason = execution.reason
    return SkippedSignal(
        timestamp=format_time(snapshot.timestamp),
        base=snapshot.base,
        pair=snapshot.pair,
        long_exchange=snapshot.long_exchange,
        short_exchange=snapshot.short_exchange,
        spread_pct=snapshot.spread_pct,
        min_quote_volume_24h=snapshot.min_quote_volume_24h,
        reason=reason,
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


def trade_pnl_usd(
    entry: SpreadSnapshot,
    exit_snapshot: SpreadSnapshot,
    notional_usd: float,
    fraction: float,
) -> float:
    long_qty = notional_usd / entry.long_ask
    short_qty = notional_usd / entry.short_bid
    long_pnl = (exit_snapshot.long_ask - entry.long_ask) * long_qty
    short_pnl = (entry.short_bid - exit_snapshot.short_bid) * short_qty
    return (long_pnl + short_pnl) * fraction


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
    funding_events: int,
    config: BacktestConfig,
) -> float:
    if snapshot.long_funding_rate is None or snapshot.short_funding_rate is None:
        return 0.0
    edge_rate = snapshot.short_funding_rate - snapshot.long_funding_rate
    return edge_rate * config.position_notional_usd * funding_events


def funding_edge_rate(snapshot: SpreadSnapshot) -> float | None:
    if snapshot.long_funding_rate is None or snapshot.short_funding_rate is None:
        return None
    return snapshot.short_funding_rate - snapshot.long_funding_rate


def funding_events_crossed(
    entry_time: datetime,
    exit_time: datetime,
    config: BacktestConfig,
) -> int:
    holding_seconds = max(0.0, (exit_time - entry_time).total_seconds())
    first_event_seconds = seconds_to_next_funding(entry_time, config)
    if first_event_seconds is None or holding_seconds < first_event_seconds:
        return 0
    interval_seconds = funding_interval_seconds(config)
    return 1 + int((holding_seconds - first_event_seconds) // interval_seconds)


def seconds_to_next_funding(value: datetime, config: BacktestConfig) -> float | None:
    interval_seconds = funding_interval_seconds(config)
    if interval_seconds <= 0:
        return None
    elapsed = value.timestamp() % interval_seconds
    if elapsed == 0:
        return interval_seconds
    return interval_seconds - elapsed


def funding_interval_seconds(config: BacktestConfig) -> float:
    return config.funding_interval_hours * 3600


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
    print(f"expectancy_usd={stats.expectancy_usd:.4f}")
    print(f"median_pnl_usd={stats.median_pnl_usd:.4f}")
    print(f"best_trade_usd={stats.best_trade_usd:.4f}")
    print(f"worst_trade_usd={stats.worst_trade_usd:.4f}")
    if stats.profit_factor is None:
        print("profit_factor=inf")
    else:
        print(f"profit_factor={stats.profit_factor:.4f}")
    print(f"max_drawdown_usd={stats.max_drawdown_usd:.4f}")
    print(f"max_consecutive_wins={stats.max_consecutive_wins}")
    print(f"max_consecutive_losses={stats.max_consecutive_losses}")
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


def infer_market_mode(row: dict[str, str]) -> str:
    long_market = (row.get("long_market_type") or row.get("long_type") or "").lower()
    short_market = (row.get("short_market_type") or row.get("short_type") or "").lower()
    futures_markers = ("future", "perp", "swap")
    has_spot = "spot" in {long_market, short_market}
    has_futures = any(marker in long_market or marker in short_market for marker in futures_markers)
    if has_spot and has_futures:
        return "spot_futures"
    return "futures_futures"


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

