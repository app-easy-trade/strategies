from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS spread_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    base TEXT NOT NULL,
    pair TEXT NOT NULL,
    long_exchange TEXT NOT NULL,
    short_exchange TEXT NOT NULL,
    long_symbol TEXT NOT NULL,
    short_symbol TEXT NOT NULL,
    long_ask REAL NOT NULL,
    short_bid REAL NOT NULL,
    spread_pct REAL NOT NULL,
    min_quote_volume_24h REAL NOT NULL,
    long_funding_rate REAL,
    short_funding_rate REAL,
    raw_json TEXT
);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    base TEXT NOT NULL,
    pair TEXT NOT NULL,
    should_enter INTEGER NOT NULL,
    reason TEXT NOT NULL,
    spread_pct REAL NOT NULL,
    zscore REAL,
    velocity_pct_per_minute REAL,
    category TEXT NOT NULL,
    gross_edge_usd REAL NOT NULL,
    estimated_cost_usd REAL NOT NULL,
    funding_edge_usd REAL NOT NULL,
    net_expected_profit_usd REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_key TEXT NOT NULL,
    status TEXT NOT NULL,
    base TEXT NOT NULL,
    pair TEXT NOT NULL,
    long_exchange TEXT NOT NULL,
    short_exchange TEXT NOT NULL,
    long_symbol TEXT NOT NULL,
    short_symbol TEXT NOT NULL,
    entry_time TEXT NOT NULL,
    exit_time TEXT,
    entry_spread_pct REAL NOT NULL,
    exit_spread_pct REAL,
    max_spread_pct REAL NOT NULL,
    remaining_fraction REAL NOT NULL,
    tp1_done INTEGER NOT NULL,
    realized_pnl_usd REAL NOT NULL,
    net_pnl_usd REAL,
    exit_reason TEXT,
    entry_net_expected_profit_usd REAL NOT NULL,
    entry_long_funding_rate REAL,
    entry_short_funding_rate REAL,
    raw_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_paper_trades_status ON paper_trades(status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_trades_open_key
ON paper_trades(trade_key)
WHERE status = 'open';

CREATE TABLE IF NOT EXISTS orderbook_impacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    trade_key TEXT,
    base TEXT NOT NULL,
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    notional_usd REAL NOT NULL,
    reference_price REAL,
    average_price REAL,
    impact_pct REAL,
    filled_notional_usd REAL NOT NULL,
    fillable INTEGER NOT NULL
);
"""


@dataclass(frozen=True)
class OpenPaperTrade:
    id: int
    trade_key: str
    base: str
    pair: str
    long_exchange: str
    short_exchange: str
    long_symbol: str
    short_symbol: str
    entry_time: str
    entry_spread_pct: float
    max_spread_pct: float
    remaining_fraction: float
    tp1_done: bool
    realized_pnl_usd: float
    entry_net_expected_profit_usd: float
    entry_long_funding_rate: float | None
    entry_short_funding_rate: float | None


@dataclass(frozen=True)
class PaperTradeRecord:
    id: int
    trade_key: str
    status: str
    base: str
    pair: str
    long_exchange: str
    short_exchange: str
    long_symbol: str
    short_symbol: str
    entry_time: str
    exit_time: str | None
    entry_spread_pct: float
    exit_spread_pct: float | None
    max_spread_pct: float
    remaining_fraction: float
    tp1_done: bool
    realized_pnl_usd: float
    net_pnl_usd: float | None
    exit_reason: str | None
    entry_net_expected_profit_usd: float


class SQLiteStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def insert_spread_snapshot(self, timestamp: str, snapshot: Any, raw: dict[str, Any] | None = None) -> None:
        self.conn.execute(
            """
            INSERT INTO spread_history (
                timestamp, base, pair, long_exchange, short_exchange, long_symbol,
                short_symbol, long_ask, short_bid, spread_pct, min_quote_volume_24h,
                long_funding_rate, short_funding_rate, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                timestamp,
                snapshot.base,
                snapshot.pair,
                snapshot.long_exchange,
                snapshot.short_exchange,
                snapshot.long_symbol,
                snapshot.short_symbol,
                snapshot.long_ask,
                snapshot.short_bid,
                snapshot.spread_pct,
                snapshot.min_quote_volume_24h,
                snapshot.long_funding_rate,
                snapshot.short_funding_rate,
                json.dumps(raw or {}, ensure_ascii=False),
            ),
        )

    def insert_signal(self, timestamp: str, signal: Any) -> None:
        features = signal.features
        snapshot = features.snapshot
        self.conn.execute(
            """
            INSERT INTO signals (
                timestamp, base, pair, should_enter, reason, spread_pct, zscore,
                velocity_pct_per_minute, category, gross_edge_usd, estimated_cost_usd,
                funding_edge_usd, net_expected_profit_usd
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                timestamp,
                snapshot.base,
                snapshot.pair,
                int(signal.should_enter),
                signal.reason,
                snapshot.spread_pct,
                features.zscore,
                features.velocity_pct_per_minute,
                features.category,
                signal.gross_edge_usd,
                signal.estimated_cost_usd,
                signal.funding_edge_usd,
                signal.net_expected_profit_usd,
            ),
        )

    def insert_open_trade(self, timestamp: str, trade_key: str, signal: Any) -> int:
        snapshot = signal.features.snapshot
        cursor = self.conn.execute(
            """
            INSERT INTO paper_trades (
                trade_key, status, base, pair, long_exchange, short_exchange,
                long_symbol, short_symbol, entry_time, entry_spread_pct, max_spread_pct,
                remaining_fraction, tp1_done, realized_pnl_usd, entry_net_expected_profit_usd,
                entry_long_funding_rate, entry_short_funding_rate, raw_json
            ) VALUES (?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, 1.0, 0, 0.0, ?, ?, ?, ?)
            """,
            (
                trade_key,
                snapshot.base,
                snapshot.pair,
                snapshot.long_exchange,
                snapshot.short_exchange,
                snapshot.long_symbol,
                snapshot.short_symbol,
                timestamp,
                snapshot.spread_pct,
                snapshot.spread_pct,
                signal.net_expected_profit_usd,
                snapshot.long_funding_rate,
                snapshot.short_funding_rate,
                json.dumps({"entry_reason": signal.reason}, ensure_ascii=False),
            ),
        )
        return int(cursor.lastrowid)

    def get_open_trade_by_key(self, trade_key: str) -> OpenPaperTrade | None:
        row = self.conn.execute(
            "SELECT * FROM paper_trades WHERE status = 'open' AND trade_key = ?",
            (trade_key,),
        ).fetchone()
        return open_trade_from_row(row) if row else None

    def list_open_trades(self) -> list[OpenPaperTrade]:
        rows = self.conn.execute(
            "SELECT * FROM paper_trades WHERE status = 'open' ORDER BY entry_time"
        ).fetchall()
        return [open_trade_from_row(row) for row in rows]

    def list_recent_trades(self, since_timestamp: str, limit: int) -> list[PaperTradeRecord]:
        rows = self.conn.execute(
            """
            SELECT * FROM paper_trades
            WHERE entry_time >= ? OR (exit_time IS NOT NULL AND exit_time >= ?)
            ORDER BY COALESCE(exit_time, entry_time) DESC
            LIMIT ?
            """,
            (since_timestamp, since_timestamp, limit),
        ).fetchall()
        return [paper_trade_record_from_row(row) for row in rows]

    def update_open_trade(
        self,
        trade_id: int,
        max_spread_pct: float,
        remaining_fraction: float,
        tp1_done: bool,
        realized_pnl_usd: float,
    ) -> None:
        self.conn.execute(
            """
            UPDATE paper_trades
            SET max_spread_pct = ?, remaining_fraction = ?, tp1_done = ?, realized_pnl_usd = ?
            WHERE id = ?
            """,
            (max_spread_pct, remaining_fraction, int(tp1_done), realized_pnl_usd, trade_id),
        )

    def close_trade(
        self,
        trade_id: int,
        exit_time: str,
        exit_spread_pct: float,
        max_spread_pct: float,
        net_pnl_usd: float,
        exit_reason: str,
        remaining_fraction: float,
        tp1_done: bool,
        realized_pnl_usd: float,
    ) -> None:
        self.conn.execute(
            """
            UPDATE paper_trades
            SET status = 'closed', exit_time = ?, exit_spread_pct = ?, max_spread_pct = ?,
                net_pnl_usd = ?, exit_reason = ?, remaining_fraction = ?, tp1_done = ?,
                realized_pnl_usd = ?
            WHERE id = ?
            """,
            (
                exit_time,
                exit_spread_pct,
                max_spread_pct,
                net_pnl_usd,
                exit_reason,
                remaining_fraction,
                int(tp1_done),
                realized_pnl_usd,
                trade_id,
            ),
        )

    def insert_orderbook_impact(
        self,
        timestamp: str,
        trade_key: str | None,
        base: str,
        exchange: str,
        symbol: str,
        impact: Any,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO orderbook_impacts (
                timestamp, trade_key, base, exchange, symbol, side, notional_usd,
                reference_price, average_price, impact_pct, filled_notional_usd, fillable
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                timestamp,
                trade_key,
                base,
                exchange,
                symbol,
                impact.side,
                impact.notional_usd,
                impact.reference_price,
                impact.average_price,
                impact.impact_pct,
                impact.filled_notional_usd,
                int(impact.fillable),
            ),
        )

    def commit(self) -> None:
        self.conn.commit()


def open_trade_from_row(row: sqlite3.Row) -> OpenPaperTrade:
    return OpenPaperTrade(
        id=int(row["id"]),
        trade_key=str(row["trade_key"]),
        base=str(row["base"]),
        pair=str(row["pair"]),
        long_exchange=str(row["long_exchange"]),
        short_exchange=str(row["short_exchange"]),
        long_symbol=str(row["long_symbol"]),
        short_symbol=str(row["short_symbol"]),
        entry_time=str(row["entry_time"]),
        entry_spread_pct=float(row["entry_spread_pct"]),
        max_spread_pct=float(row["max_spread_pct"]),
        remaining_fraction=float(row["remaining_fraction"]),
        tp1_done=bool(row["tp1_done"]),
        realized_pnl_usd=float(row["realized_pnl_usd"]),
        entry_net_expected_profit_usd=float(row["entry_net_expected_profit_usd"]),
        entry_long_funding_rate=row["entry_long_funding_rate"],
        entry_short_funding_rate=row["entry_short_funding_rate"],
    )


def paper_trade_record_from_row(row: sqlite3.Row) -> PaperTradeRecord:
    return PaperTradeRecord(
        id=int(row["id"]),
        trade_key=str(row["trade_key"]),
        status=str(row["status"]),
        base=str(row["base"]),
        pair=str(row["pair"]),
        long_exchange=str(row["long_exchange"]),
        short_exchange=str(row["short_exchange"]),
        long_symbol=str(row["long_symbol"]),
        short_symbol=str(row["short_symbol"]),
        entry_time=str(row["entry_time"]),
        exit_time=str(row["exit_time"]) if row["exit_time"] is not None else None,
        entry_spread_pct=float(row["entry_spread_pct"]),
        exit_spread_pct=float(row["exit_spread_pct"]) if row["exit_spread_pct"] is not None else None,
        max_spread_pct=float(row["max_spread_pct"]),
        remaining_fraction=float(row["remaining_fraction"]),
        tp1_done=bool(row["tp1_done"]),
        realized_pnl_usd=float(row["realized_pnl_usd"]),
        net_pnl_usd=float(row["net_pnl_usd"]) if row["net_pnl_usd"] is not None else None,
        exit_reason=str(row["exit_reason"]) if row["exit_reason"] is not None else None,
        entry_net_expected_profit_usd=float(row["entry_net_expected_profit_usd"]),
    )
