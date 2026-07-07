from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass, replace
from itertools import product
from pathlib import Path

from core.config import StrategyConfig, load_strategy_config

from .simulator import read_snapshots, run_backtest


@dataclass(frozen=True)
class OptimizationResult:
    min_spread_pct: float
    min_volume_usd: float
    max_slippage_pct: float
    funding_exit_enabled: bool
    dynamic_exit_enabled: bool
    position_notional_usd: float
    tp1_retrace_fraction: float
    max_adverse_spread_pct: float
    leverage: float
    trades: int
    win_rate_pct: float
    net_profit_usd: float
    profit_factor: float | None
    max_drawdown_usd: float
    average_holding_seconds: float
    score: float


def parse_float_list(value: str) -> list[float]:
    return [float(item.strip()) for item in value.split(",") if item.strip()]


def parse_bool_list(value: str) -> list[bool]:
    return [item.strip().lower() in {"1", "true", "yes", "on"} for item in value.split(",") if item.strip()]


def optimize(
    input_path: Path,
    base_config: StrategyConfig,
    min_spreads: list[float],
    min_volumes: list[float] | None = None,
    max_slippages: list[float] | None = None,
    funding_modes: list[bool] | None = None,
    dynamic_exit_modes: list[bool] | None = None,
    position_sizes: list[float] | None = None,
    tp1_retraces: list[float] | None = None,
    max_adverse_spreads: list[float] | None = None,
    leverages: list[float] | None = None,
    min_trades: int = 1,
) -> list[OptimizationResult]:
    snapshots = read_snapshots(input_path)
    results: list[OptimizationResult] = []
    min_volumes = min_volumes or [base_config.min_volume_usd]
    max_slippages = max_slippages or [base_config.max_slippage_pct]
    funding_modes = funding_modes or [base_config.funding_exit_enabled]
    dynamic_exit_modes = dynamic_exit_modes or [base_config.dynamic_exit_enabled]
    position_sizes = position_sizes or [base_config.position_notional_usd]
    tp1_retraces = tp1_retraces or [base_config.tp1_retrace_fraction]
    max_adverse_spreads = max_adverse_spreads or [base_config.max_adverse_spread_pct]
    leverages = leverages or [base_config.leverage]
    for (
        min_spread,
        min_volume,
        max_slippage,
        funding_exit_enabled,
        dynamic_exit_enabled,
        position_size,
        tp1_retrace,
        max_adverse,
        leverage,
    ) in product(
        min_spreads,
        min_volumes,
        max_slippages,
        funding_modes,
        dynamic_exit_modes,
        position_sizes,
        tp1_retraces,
        max_adverse_spreads,
        leverages,
    ):
        config = replace(
            base_config,
            min_spread_pct=min_spread,
            min_volume_usd=min_volume,
            max_slippage_pct=max_slippage,
            funding_exit_enabled=funding_exit_enabled,
            dynamic_exit_enabled=dynamic_exit_enabled,
            position_notional_usd=position_size,
            tp1_retrace_fraction=tp1_retrace,
            max_adverse_spread_pct=max_adverse,
            leverage=leverage,
        )
        _, stats, _ = run_backtest(snapshots, config)
        score = score_stats(stats.net_profit_usd, stats.max_drawdown_usd, stats.trades, min_trades)
        results.append(
            OptimizationResult(
                min_spread_pct=min_spread,
                min_volume_usd=min_volume,
                max_slippage_pct=max_slippage,
                funding_exit_enabled=funding_exit_enabled,
                dynamic_exit_enabled=dynamic_exit_enabled,
                position_notional_usd=position_size,
                tp1_retrace_fraction=tp1_retrace,
                max_adverse_spread_pct=max_adverse,
                leverage=leverage,
                trades=stats.trades,
                win_rate_pct=stats.win_rate_pct,
                net_profit_usd=stats.net_profit_usd,
                profit_factor=stats.profit_factor,
                max_drawdown_usd=stats.max_drawdown_usd,
                average_holding_seconds=stats.average_holding_seconds,
                score=score,
            )
        )
    return sorted(results, key=lambda item: item.score, reverse=True)


def score_stats(net_profit: float, max_drawdown: float, trades: int, min_trades: int) -> float:
    if trades < min_trades:
        return -1_000_000.0 + net_profit
    return net_profit - max_drawdown


def write_results(path: Path, results: list[OptimizationResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(OptimizationResult.__dataclass_fields__))
        writer.writeheader()
        writer.writerows(asdict(result) for result in results)


def print_top(results: list[OptimizationResult], limit: int) -> None:
    print(f"Optimization complete: {len(results)} runs")
    for index, result in enumerate(results[:limit], start=1):
        profit_factor = "inf" if result.profit_factor is None else f"{result.profit_factor:.4f}"
        print(
            f"{index:>3}. score={result.score:.4f} net={result.net_profit_usd:.4f} "
            f"dd={result.max_drawdown_usd:.4f} trades={result.trades} "
            f"win={result.win_rate_pct:.2f}% pf={profit_factor} "
            f"min_spread={result.min_spread_pct} min_vol={result.min_volume_usd} "
            f"slip={result.max_slippage_pct} funding_exit={result.funding_exit_enabled} "
            f"dynamic_exit={result.dynamic_exit_enabled} size={result.position_notional_usd} "
            f"tp1={result.tp1_retrace_fraction} stop={result.max_adverse_spread_pct} "
            f"lev={result.leverage}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Optimize spread-arbitrage backtest parameters.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=Path("config/backtest.yaml"))
    parser.add_argument("--output", type=Path, default=Path("data/optimization_results.csv"))
    parser.add_argument("--min-spreads", default="1,2,3,5")
    parser.add_argument("--min-volumes", default="")
    parser.add_argument("--max-slippages", default="")
    parser.add_argument("--funding-modes", default="")
    parser.add_argument("--dynamic-exit-modes", default="")
    parser.add_argument("--position-sizes", default="")
    parser.add_argument("--tp1-retraces", default="0.3,0.5,0.7")
    parser.add_argument("--max-adverse-spreads", default="3,5,8")
    parser.add_argument("--leverages", default="3,5,10")
    parser.add_argument("--min-trades", type=int, default=1)
    parser.add_argument("--limit", type=int, default=10)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = load_strategy_config(args.config if args.config.exists() else None)
    results = optimize(
        input_path=args.input,
        base_config=config,
        min_spreads=parse_float_list(args.min_spreads),
        min_volumes=parse_float_list(args.min_volumes) if args.min_volumes else None,
        max_slippages=parse_float_list(args.max_slippages) if args.max_slippages else None,
        funding_modes=parse_bool_list(args.funding_modes) if args.funding_modes else None,
        dynamic_exit_modes=parse_bool_list(args.dynamic_exit_modes) if args.dynamic_exit_modes else None,
        position_sizes=parse_float_list(args.position_sizes) if args.position_sizes else None,
        tp1_retraces=parse_float_list(args.tp1_retraces),
        max_adverse_spreads=parse_float_list(args.max_adverse_spreads),
        leverages=parse_float_list(args.leverages),
        min_trades=args.min_trades,
    )
    write_results(args.output, results)
    print_top(results, args.limit)
    print(f"optimization_csv={args.output}")


if __name__ == "__main__":
    main()

