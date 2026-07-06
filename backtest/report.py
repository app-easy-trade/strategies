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
    top_exit_reasons: list[tuple[str, int]]
    top_skip_reasons: list[tuple[str, int]]
    pnl_by_pair: list[tuple[str, float]]
    pnl_by_base: list[tuple[str, float]]


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

    pnl_by_pair: defaultdict[str, float] = defaultdict(float)
    pnl_by_base: defaultdict[str, float] = defaultdict(float)
    for row in trades:
        pnl = float(row.get("net_pnl_usd") or 0)
        pnl_by_pair[row.get("pair") or "unknown"] += pnl
        pnl_by_base[row.get("base") or "unknown"] += pnl

    return BacktestReport(
        stats=calculate_stats(pnls, holding),
        skipped_signals=len(skipped),
        top_exit_reasons=exit_reasons.most_common(5),
        top_skip_reasons=skip_reasons.most_common(5),
        pnl_by_pair=sorted(pnl_by_pair.items(), key=lambda item: item[1], reverse=True)[:5],
        pnl_by_base=sorted(pnl_by_base.items(), key=lambda item: item[1], reverse=True)[:5],
    )


def format_report(report: BacktestReport) -> str:
    stats = report.stats
    profit_factor = "inf" if stats.profit_factor is None else f"{stats.profit_factor:.2f}"
    lines = [
        "Backtest report",
        f"Trades: {stats.trades} | skipped signals: {report.skipped_signals}",
        f"Net PnL: {stats.net_profit_usd:.4f} USD | win rate: {stats.win_rate_pct:.2f}%",
        f"Profit factor: {profit_factor} | max DD: {stats.max_drawdown_usd:.4f} USD",
        f"Avg PnL: {stats.average_pnl_usd:.4f} USD | avg hold: {stats.average_holding_seconds:.0f}s",
    ]
    if report.top_exit_reasons:
        lines.append("")
        lines.append("Exit reasons:")
        lines.extend(f"- {reason}: {count}" for reason, count in report.top_exit_reasons)
    if report.top_skip_reasons:
        lines.append("")
        lines.append("Skip reasons:")
        lines.extend(f"- {reason}: {count}" for reason, count in report.top_skip_reasons)
    if report.pnl_by_pair:
        lines.append("")
        lines.append("PnL by pair:")
        lines.extend(f"- {pair}: {pnl:.4f}" for pair, pnl in report.pnl_by_pair)
    if report.pnl_by_base:
        lines.append("")
        lines.append("PnL by coin:")
        lines.extend(f"- {base}: {pnl:.4f}" for base, pnl in report.pnl_by_base)
    return "\n".join(lines)


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

