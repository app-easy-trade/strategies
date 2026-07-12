from __future__ import annotations

import unittest
import csv
from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from backtest.report import build_report, format_report
from backtest.simulator import (
    BacktestConfig,
    SpreadSnapshot,
    funding_events_crossed,
    read_snapshots,
    run_backtest,
)
from backtest.optimizer import optimize
from core.funding_engine import estimate_funding
from core.risk_engine import evaluate_risk
from core.signal_engine import evaluate_signal
from core.spread_engine import SpreadFeatureEngine
from src.spread_history_collector import append_history
from src.spread_scanner import SpreadRow, sane_spread
from src.universe import DEFAULT_EXCHANGES, exchange_names
from output.telegram_bot import build_alert_text, build_daily_control_text, spread_tv_formula
from storage.sqlite_store import SQLiteStore


class BacktestSimulatorTest(unittest.TestCase):
    def test_sample_history_generates_target_and_stop_trades(self) -> None:
        snapshots = read_snapshots(Path("tests/fixtures/sample_spread_history.csv"))
        trades, stats, skipped = run_backtest(
            snapshots,
            BacktestConfig(min_zscore=0, max_funding_against_usd=1.0),
        )

        self.assertEqual(2, len(trades))
        self.assertEqual(0, len(skipped))
        self.assertEqual("final_spread_target", trades[0].exit_reason)
        self.assertEqual("max_adverse_spread", trades[1].exit_reason)
        self.assertEqual(2, stats.trades)
        self.assertEqual(1, stats.wins)
        self.assertEqual(1, stats.losses)

    def test_open_trade_closes_on_last_snapshot_at_end_of_data(self) -> None:
        snapshots = [
            make_snapshot("2026-01-01T00:00:00+00:00", 4.0),
            make_snapshot("2026-01-01T00:05:00+00:00", 3.0),
        ]

        trades, _, _ = run_backtest(snapshots, BacktestConfig(min_zscore=0))

        self.assertEqual(1, len(trades))
        self.assertEqual("end_of_data", trades[0].exit_reason)
        self.assertEqual(3.0, trades[0].exit_spread_pct)
        self.assertEqual(300.0, trades[0].holding_seconds)

    def test_history_collector_appends_timestamped_rows(self) -> None:
        row = SpreadRow(
            base="BTC",
            pair="bybit/mexc",
            long_exchange="bybit",
            short_exchange="mexc",
            long_symbol="BTCUSDT",
            short_symbol="BTC_USDT",
            long_ask=100.0,
            short_bid=103.0,
            spread_pct=3.0,
            long_quote_volume_24h=2_000_000.0,
            short_quote_volume_24h=1_500_000.0,
            min_quote_volume_24h=1_500_000.0,
            long_funding_rate=0.0001,
            short_funding_rate=0.0002,
            funding_edge_pct=0.01,
            funding_status="favorable",
            long_timestamp_ms=1,
            short_timestamp_ms=2,
        )

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.csv"
            append_history(path, "2026-01-01T00:00:00Z", [row])
            append_history(path, "2026-01-01T00:01:00Z", [row])

            with path.open(newline="", encoding="utf-8") as file:
                rows = list(csv.DictReader(file))

        self.assertEqual(2, len(rows))
        self.assertEqual("2026-01-01T00:00:00Z", rows[0]["timestamp"])
        self.assertEqual("BTC", rows[0]["base"])
        self.assertEqual("2026-01-01T00:01:00Z", rows[1]["timestamp"])

    def test_signal_engine_uses_zscore_velocity_and_net_expected_profit(self) -> None:
        config = BacktestConfig(
            min_spread_pct=2,
            min_zscore=1,
            min_velocity_pct_per_minute=0.2,
            rolling_window=3,
        )
        engine = SpreadFeatureEngine(config)
        for index, spread in enumerate([1.0, 1.1, 1.2]):
            engine.update(make_snapshot(f"2026-01-01T00:0{index}:00+00:00", spread))

        features = engine.update(make_snapshot("2026-01-01T00:03:00+00:00", 3.0))
        signal = evaluate_signal(features, config)

        self.assertTrue(signal.should_enter)
        self.assertEqual("enter", signal.reason)
        self.assertGreaterEqual(features.zscore or 0, 1)
        self.assertGreaterEqual(features.velocity_pct_per_minute or 0, 0.2)
        self.assertGreater(signal.net_expected_profit_usd, 0)

    def test_signal_engine_records_skip_reason(self) -> None:
        snapshots = [make_snapshot("2026-01-01T00:00:00+00:00", 0.5)]

        trades, _, skipped = run_backtest(snapshots, BacktestConfig(min_zscore=0))

        self.assertEqual([], trades)
        self.assertEqual(1, len(skipped))
        self.assertEqual("spread_below_final_exit", skipped[0].reason)

    def test_extreme_spread_is_categorized_not_rejected_by_default(self) -> None:
        snapshots = [
            make_snapshot("2026-01-01T00:00:00+00:00", 12.0),
            make_snapshot("2026-01-01T00:05:00+00:00", 0.4),
        ]

        trades, _, skipped = run_backtest(snapshots, BacktestConfig(min_zscore=0))

        self.assertEqual(1, len(trades))
        self.assertEqual("extreme", trades[0].entry_category)
        self.assertEqual([], skipped)

    def test_funding_events_are_counted_only_when_crossed(self) -> None:
        config = BacktestConfig(funding_interval_hours=8)
        entry = datetime(2026, 1, 1, 1, 0, tzinfo=timezone.utc)
        before_funding = datetime(2026, 1, 1, 7, 59, tzinfo=timezone.utc)
        after_funding = datetime(2026, 1, 1, 8, 1, tzinfo=timezone.utc)

        self.assertEqual(0, funding_events_crossed(entry, before_funding, config))
        self.assertEqual(1, funding_events_crossed(entry, after_funding, config))

    def test_execution_simulator_skips_unfilled_leg(self) -> None:
        snapshot = make_snapshot("2026-01-01T00:00:00+00:00", 4.0)
        snapshot = SpreadSnapshot(
            **{
                **snapshot.__dict__,
                "long_depth_usd_200": 200.0,
                "short_depth_usd_200": 0.0,
            }
        )

        trades, _, skipped = run_backtest(
            [snapshot],
            BacktestConfig(min_zscore=0, position_notional_usd=200),
        )

        self.assertEqual([], trades)
        self.assertEqual("one_leg_unfilled", skipped[0].reason)

    def test_execution_simulator_skips_deteriorated_entry_spread(self) -> None:
        snapshot = make_snapshot("2026-01-01T00:00:00+00:00", 3.0)
        snapshot = SpreadSnapshot(
            **{
                **snapshot.__dict__,
                "executable_spread_200_pct": 1.9,
                "long_depth_usd_200": 200.0,
                "short_depth_usd_200": 200.0,
            }
        )

        trades, _, skipped = run_backtest(
            [snapshot],
            BacktestConfig(
                min_zscore=0,
                position_notional_usd=200,
                max_entry_spread_deterioration_pct=0.5,
            ),
        )

        self.assertEqual([], trades)
        self.assertEqual("entry_spread_deteriorated", skipped[0].reason)

    def test_risk_engine_rejects_insufficient_margin_buffer(self) -> None:
        config = BacktestConfig(
            position_notional_usd=200,
            leverage=5,
            available_margin_usd=20,
            min_zscore=0,
        )
        features = SpreadFeatureEngine(config).update(
            make_snapshot("2026-01-01T00:00:00+00:00", 3.0)
        )

        decision = evaluate_risk(features, config)
        signal = evaluate_signal(features, config)

        self.assertFalse(decision.passed)
        self.assertEqual("insufficient_margin_buffer", decision.reason)
        self.assertFalse(signal.should_enter)
        self.assertEqual("insufficient_margin_buffer", signal.reason)

    def test_risk_engine_rejects_invalid_leverage(self) -> None:
        config = BacktestConfig(leverage=0, min_zscore=0)
        features = SpreadFeatureEngine(config).update(
            make_snapshot("2026-01-01T00:00:00+00:00", 3.0)
        )

        decision = evaluate_risk(features, config)

        self.assertFalse(decision.passed)
        self.assertEqual("invalid_leverage", decision.reason)

    def test_backtest_rejects_portfolio_margin_overuse(self) -> None:
        snapshots = [
            make_snapshot_for_base("SOL", "2026-01-01T00:00:00+00:00", 3.0),
            make_snapshot_for_base("ETH", "2026-01-01T00:00:00+00:00", 3.0),
            make_snapshot_for_base("SOL", "2026-01-01T00:05:00+00:00", 0.4),
        ]

        trades, _, skipped = run_backtest(
            snapshots,
            BacktestConfig(
                min_zscore=0,
                available_margin_usd=100,
                position_notional_usd=200,
                leverage=5,
                max_margin_usage_pct=90,
                max_open_trades=5,
                max_total_position_notional_usd=1000,
            ),
        )

        self.assertEqual(1, len(trades))
        self.assertEqual("portfolio_margin_insufficient", skipped[0].reason)
        self.assertGreater(trades[0].entry_margin_required_usd, 0)
        self.assertGreater(trades[0].account_equity_after_usd, 0)

    def test_funding_engine_marks_favorable_edge(self) -> None:
        config = BacktestConfig(position_notional_usd=200)
        snapshot = make_snapshot(
            "2026-01-01T00:00:00+00:00",
            3.0,
            long_funding_rate=0.0001,
            short_funding_rate=0.0003,
        )

        estimate = estimate_funding(snapshot, config)

        self.assertEqual("favorable", estimate.status)
        self.assertAlmostEqual(0.0002, estimate.edge_rate or 0)
        self.assertAlmostEqual(0.04, estimate.expected_edge_usd)

    def test_default_exchange_names_include_new_connectors(self) -> None:
        self.assertEqual(list(DEFAULT_EXCHANGES), exchange_names(None))
        self.assertIn("binance", DEFAULT_EXCHANGES)
        self.assertIn("hyperliquid", DEFAULT_EXCHANGES)
        self.assertIn("aster", DEFAULT_EXCHANGES)

    def test_sanity_filter_keeps_extreme_but_drops_incompatible_contracts(self) -> None:
        self.assertTrue(sane_spread(12.0, 100.0))
        self.assertFalse(sane_spread(56_516.0, 100.0))
        self.assertTrue(sane_spread(56_516.0, 0.0))

    def test_optimizer_returns_ranked_results(self) -> None:
        results = optimize(
            input_path=Path("tests/fixtures/sample_spread_history.csv"),
            base_config=BacktestConfig(min_zscore=0),
            min_spreads=[2, 5],
            tp1_retraces=[0.5],
            max_adverse_spreads=[5],
            leverages=[3, 5],
        )

        self.assertEqual(4, len(results))
        self.assertGreaterEqual(results[0].score, results[-1].score)

    def test_backtest_report_formats_trade_csv(self) -> None:
        report = build_report(
            Path("tests/fixtures/sample_backtest_trades.csv"),
            Path("tests/fixtures/sample_backtest_skipped.csv"),
        )
        text = format_report(report)

        self.assertEqual(2, report.stats.trades)
        self.assertIn("Backtest report", text)
        self.assertIn("max_adverse_spread", text)

    def test_telegram_alert_text_is_readable(self) -> None:
        text = build_alert_text(
            rows=[],
            limit=10,
            started_at=datetime(2026, 1, 1),
            top=10,
            min_spread=0.5,
            max_spread=5,
            min_volume=1_000_000,
            require_funding_favorable=False,
        )

        self.assertIn("found spreads 0", text)
        self.assertIn("No spreads above filters", text)

    def test_tradingview_spread_formula_uses_expensive_over_cheap(self) -> None:
        formula = spread_tv_formula("binance", "BTCUSDT", "bybit", "BTCUSDT")

        self.assertEqual("(BINANCE:BTCUSDT.P/BYBIT:BTCUSDT.P-1)*100", formula)

    def test_daily_control_text_includes_execution_status(self) -> None:
        args = Namespace(
            exchanges="bybit",
            quote="USDT",
            top=1,
            health_top=1,
            timeout=20,
            limit=2,
            backtest_trades=None,
            backtest_skipped=None,
            paper_db=None,
            paper_limit=5,
        )

        text = build_daily_control_text(args, [])

        self.assertIn("Daily Control", text)
        self.assertIn("real orders: disabled", text)

    def test_sqlite_store_persists_open_paper_trade(self) -> None:
        config = BacktestConfig(min_zscore=0)
        features = SpreadFeatureEngine(config).update(
            make_snapshot("2026-01-01T00:00:00+00:00", 3.0)
        )
        signal = evaluate_signal(features, config)

        with TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "paper.sqlite3")
            try:
                trade_id = store.insert_open_trade("2026-01-01T00:00:00Z", "SOL|bybit|mexc|SOLUSDT|SOL_USDT", signal)
                store.commit()
                trades = store.list_open_trades()
            finally:
                store.close()

        self.assertGreater(trade_id, 0)
        self.assertEqual(1, len(trades))
        self.assertEqual("SOL", trades[0].base)


def make_snapshot(
    timestamp: str,
    spread_pct: float,
    long_funding_rate: float | None = None,
    short_funding_rate: float | None = None,
) -> SpreadSnapshot:
    return SpreadSnapshot(
        timestamp=datetime.fromisoformat(timestamp).astimezone(timezone.utc),
        base="SOL",
        pair="bybit/mexc",
        long_exchange="bybit",
        short_exchange="mexc",
        long_symbol="SOLUSDT",
        short_symbol="SOL_USDT",
        long_ask=100.0,
        short_bid=100.0 * (1 + spread_pct / 100),
        spread_pct=spread_pct,
        min_quote_volume_24h=1_000_000.0,
        long_funding_rate=long_funding_rate,
        short_funding_rate=short_funding_rate,
    )


def make_snapshot_for_base(base: str, timestamp: str, spread_pct: float) -> SpreadSnapshot:
    snapshot = make_snapshot(timestamp, spread_pct)
    return SpreadSnapshot(
        **{
            **snapshot.__dict__,
            "base": base,
            "long_symbol": f"{base}USDT",
            "short_symbol": f"{base}_USDT",
        }
    )


if __name__ == "__main__":
    unittest.main()
