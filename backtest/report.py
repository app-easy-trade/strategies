from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from .statistics import BacktestStats, calculate_stats


@dataclass(frozen=True)
class BacktestReport:
    stats: BacktestStats
    skipped_signals: int
    total_fees_usd: float
    total_slippage_usd: float
    total_execution_cost_usd: float
    total_funding_pnl_usd: float
    tp1_count: int
    average_execution_fill_fraction: float
    average_entry_margin_usage_pct: float
    max_entry_margin_usage_pct: float
    final_account_equity_usd: float
    top_exit_reasons: list[tuple[str, int]]
    top_skip_reasons: list[tuple[str, int]]
    pnl_by_exit_reason: list[tuple[str, float]]
    pnl_by_pair: list[tuple[str, float]]
    pnl_by_base: list[tuple[str, float]]
    pnl_by_category: list[tuple[str, float]]
    trades_by_category: list[tuple[str, int]]
    pnl_by_market_mode: list[tuple[str, float]]
    trades_by_market_mode: list[tuple[str, int]]


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


def build_report(trades_csv: Path, skipped_csv: Path | None = None) -> BacktestReport:
    trades = read_csv(trades_csv)
    skipped = read_csv(skipped_csv) if skipped_csv else []
    pnls = [float(row.get("net_pnl_usd") or 0) for row in trades]
    holding = [float(row.get("holding_seconds") or 0) for row in trades]
    exit_reasons = Counter(row.get("exit_reason") or "unknown" for row in trades)
    skip_reasons = Counter(row.get("reason") or "unknown" for row in skipped)
    total_fees = sum(float(row.get("fees_usd") or 0) for row in trades)
    total_slippage = sum(float(row.get("slippage_usd") or 0) for row in trades)
    total_execution = sum(float(row.get("execution_cost_usd") or 0) for row in trades)
    total_funding = sum(float(row.get("funding_pnl_usd") or 0) for row in trades)
    tp1_count = sum(1 for row in trades if truthy(row.get("tp1_done")))
    fill_fractions = [float(row.get("execution_fill_fraction") or 1) for row in trades]
    margin_usages = [float(row.get("entry_margin_usage_pct") or 0) for row in trades]
    final_equity = float(trades[-1].get("account_equity_after_usd") or 0) if trades else 0.0

    pnl_by_pair: defaultdict[str, float] = defaultdict(float)
    pnl_by_base: defaultdict[str, float] = defaultdict(float)
    pnl_by_category: defaultdict[str, float] = defaultdict(float)
    pnl_by_exit_reason: defaultdict[str, float] = defaultdict(float)
    pnl_by_market_mode: defaultdict[str, float] = defaultdict(float)
    trades_by_category: Counter[str] = Counter()
    trades_by_market_mode: Counter[str] = Counter()
    for row in trades:
        pnl = float(row.get("net_pnl_usd") or 0)
        pnl_by_pair[row.get("pair") or "unknown"] += pnl
        pnl_by_base[row.get("base") or "unknown"] += pnl
        category = row.get("entry_category") or "unknown"
        pnl_by_category[category] += pnl
        trades_by_category[category] += 1
        exit_reason = row.get("exit_reason") or "unknown"
        pnl_by_exit_reason[exit_reason] += pnl
        market_mode = row.get("market_mode") or "unknown"
        pnl_by_market_mode[market_mode] += pnl
        trades_by_market_mode[market_mode] += 1

    return BacktestReport(
        stats=calculate_stats(pnls, holding),
        skipped_signals=len(skipped),
        total_fees_usd=total_fees,
        total_slippage_usd=total_slippage,
        total_execution_cost_usd=total_execution,
        total_funding_pnl_usd=total_funding,
        tp1_count=tp1_count,
        average_execution_fill_fraction=(
            sum(fill_fractions) / len(fill_fractions)
        ) if fill_fractions else 0.0,
        average_entry_margin_usage_pct=(
            sum(margin_usages) / len(margin_usages)
        ) if margin_usages else 0.0,
        max_entry_margin_usage_pct=max(margin_usages) if margin_usages else 0.0,
        final_account_equity_usd=final_equity,
        top_exit_reasons=exit_reasons.most_common(5),
        top_skip_reasons=skip_reasons.most_common(5),
        pnl_by_exit_reason=sorted(
            pnl_by_exit_reason.items(),
            key=lambda item: item[1],
            reverse=True,
        ),
        pnl_by_pair=sorted(pnl_by_pair.items(), key=lambda item: item[1], reverse=True)[:5],
        pnl_by_base=sorted(pnl_by_base.items(), key=lambda item: item[1], reverse=True)[:5],
        pnl_by_category=sorted(pnl_by_category.items(), key=lambda item: item[1], reverse=True),
        trades_by_category=trades_by_category.most_common(),
        pnl_by_market_mode=sorted(
            pnl_by_market_mode.items(),
            key=lambda item: item[1],
            reverse=True,
        ),
        trades_by_market_mode=trades_by_market_mode.most_common(),
    )


def format_report(report: BacktestReport) -> str:
    stats = report.stats
    profit_factor = "inf" if stats.profit_factor is None else f"{stats.profit_factor:.2f}"
    payoff = "inf" if stats.payoff_ratio is None else f"{stats.payoff_ratio:.2f}"
    lines = [
        "Backtest report",
        f"Trades: {stats.trades} | skipped signals: {report.skipped_signals}",
        f"Net PnL: {stats.net_profit_usd:.4f} USD | win rate: {stats.win_rate_pct:.2f}%",
        f"Profit factor: {profit_factor} | max DD: {stats.max_drawdown_usd:.4f} USD",
        f"Avg PnL: {stats.average_pnl_usd:.4f} USD | expectancy: {stats.expectancy_usd:.4f} USD",
        f"Median PnL: {stats.median_pnl_usd:.4f} USD | "
        f"p10/p90: {stats.pnl_p10_usd:.4f}/{stats.pnl_p90_usd:.4f}",
        f"Best/worst: {stats.best_trade_usd:.4f}/{stats.worst_trade_usd:.4f} USD",
        f"Avg win/loss: {stats.average_win_usd:.4f}/{stats.average_loss_usd:.4f} USD",
        f"Payoff: {payoff} | "
        f"max W/L streak: {stats.max_consecutive_wins}/{stats.max_consecutive_losses}",
        f"Avg/median hold: {stats.average_holding_seconds:.0f}/{stats.median_holding_seconds:.0f}s",
        f"Costs: fees={report.total_fees_usd:.4f}, "
        f"slippage={report.total_slippage_usd:.4f}, "
        f"execution={report.total_execution_cost_usd:.4f}",
        f"Funding PnL: {report.total_funding_pnl_usd:.4f} USD | TP1 trades: {report.tp1_count}",
        f"Avg execution fill: {report.average_execution_fill_fraction:.2%}",
        f"Margin usage avg/max: {report.average_entry_margin_usage_pct:.2f}%/{report.max_entry_margin_usage_pct:.2f}%",
        f"Final account equity: {report.final_account_equity_usd:.4f} USD",
    ]
    if report.top_exit_reasons:
        lines.append("")
        lines.append("Exit reasons:")
        lines.extend(f"- {reason}: {count}" for reason, count in report.top_exit_reasons)
    if report.top_skip_reasons:
        lines.append("")
        lines.append("Skip reasons:")
        lines.extend(f"- {reason}: {count}" for reason, count in report.top_skip_reasons)
    if report.pnl_by_exit_reason:
        lines.append("")
        lines.append("PnL by exit reason:")
        lines.extend(f"- {reason}: {pnl:.4f}" for reason, pnl in report.pnl_by_exit_reason)
    if report.pnl_by_pair:
        lines.append("")
        lines.append("PnL by pair:")
        lines.extend(f"- {pair}: {pnl:.4f}" for pair, pnl in report.pnl_by_pair)
    if report.pnl_by_base:
        lines.append("")
        lines.append("PnL by coin:")
        lines.extend(f"- {base}: {pnl:.4f}" for base, pnl in report.pnl_by_base)
    if report.pnl_by_category:
        lines.append("")
        lines.append("PnL by spread category:")
        lines.extend(f"- {category}: {pnl:.4f}" for category, pnl in report.pnl_by_category)
    if report.trades_by_category:
        lines.append("")
        lines.append("Trades by spread category:")
        lines.extend(f"- {category}: {count}" for category, count in report.trades_by_category)
    if report.pnl_by_market_mode:
        lines.append("")
        lines.append("PnL by market mode:")
        lines.extend(f"- {mode}: {pnl:.4f}" for mode, pnl in report.pnl_by_market_mode)
    if report.trades_by_market_mode:
        lines.append("")
        lines.append("Trades by market mode:")
        lines.extend(f"- {mode}: {count}" for mode, count in report.trades_by_market_mode)
    return "\n".join(lines)


def truthy(value: str | None) -> bool:
    return str(value).lower() in {"1", "true", "yes"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a compact backtest report from CSV exports.")
    parser.add_argument("--trades-csv", type=Path, required=True)
    parser.add_argument("--skipped-csv", type=Path, default=None)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    print(format_report(build_report(args.trades_csv, args.skipped_csv)))


if __name__ == "__main__":
    main()

