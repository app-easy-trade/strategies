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


def optimize(
    input_path: Path,
    base_config: StrategyConfig,
    min_spreads: list[float],
    tp1_retraces: list[float],
    max_adverse_spreads: list[float],
    leverages: list[float],
    min_trades: int = 1,
) -> list[OptimizationResult]:
    snapshots = read_snapshots(input_path)
    results: list[OptimizationResult] = []
    for min_spread, tp1_retrace, max_adverse, leverage in product(
        min_spreads,
        tp1_retraces,
        max_adverse_spreads,
        leverages,
    ):
        config = replace(
            base_config,
            min_spread_pct=min_spread,
            tp1_retrace_fraction=tp1_retrace,
            max_adverse_spread_pct=max_adverse,
            leverage=leverage,
        )
        _, stats, _ = run_backtest(snapshots, config)
        score = score_stats(stats.net_profit_usd, stats.max_drawdown_usd, stats.trades, min_trades)
        results.append(
            OptimizationResult(
                min_spread_pct=min_spread,
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
            f"min_spread={result.min_spread_pct} tp1={result.tp1_retrace_fraction} "
            f"stop={result.max_adverse_spread_pct} lev={result.leverage}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Optimize spread-arbitrage backtest parameters.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=Path("config/backtest.yaml"))
    parser.add_argument("--output", type=Path, default=Path("data/optimization_results.csv"))
    parser.add_argument("--min-spreads", default="1,2,3,5")
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

