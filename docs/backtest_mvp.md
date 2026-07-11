# Backtest v2

## What it does

The backtest validates two-leg spread convergence trades from an append-only CSV
history produced by `src.spread_history_collector` or any compatible historical
dataset. Entry decisions go through the core trading model in `core/`.

It accounts for:

- entry by spread range and minimum 24h quote volume;
- LONG on the cheap exchange and SHORT on the expensive exchange;
- TP1: close part of the position after a configured spread retrace;
- final exit by target spread;
- dynamic exit after TP1 when spread rebounds after a profitable compression;
- funding exit before a bad funding event;
- optional hold through a near positive funding event;
- execution simulation with depth-aware full/partial/failed fills when depth
  columns are available;
- one-leg failure detection when one side has no executable depth;
- stop by maximum adverse spread expansion;
- maximum holding time as an emergency limit;
- maker/taker fees;
- slippage;
- funding edge between the long and short legs, counted only when the trade
  actually crosses a funding timestamp;
- spread category, including `extreme` for 10%+ spreads;
- optional `spot_futures` and `futures_futures` market modes;
- optional executable spread columns for $100/$200/$500/$1000 sizing;
- rolling mean/std and z-score;
- spread velocity and acceleration;
- net expected profit before entry;
- trade CSV export;
- skipped signal CSV export with rejection reasons;
- win rate, profit factor, drawdown, average PnL and average holding time.
- report breakdown by pair, coin and spread category.
- grid optimization over spread, min volume, max slippage, funding mode,
  dynamic exit mode, position size, TP1, stop and leverage.

## What is not complete yet

- Real historical exchange downloads are not implemented yet. The collector starts
  accumulating history from the moment it is launched.
- Order book depth is used only if the CSV contains executable spread/depth
  columns. Without those columns, the model falls back to top-of-book spread.
- Limit-order waiting, order moving, partial fills and one-leg failure are still
  modeled as costs/checks, not as a full execution event stream.
- Spot-futures is supported in the backtest schema, but the live scanner still
  needs dedicated spot connectors/history collection.
- ML probability scoring is not implemented yet. Current decisions are rule-based
  and explainable.
- TradingView is not used as a data source. It should be an analytics and charting
  layer over exchange API history.

## Collect history

```powershell
python -m src.spread_history_collector --interval 60 --top 1000 --min-spread 0.5 --min-volume 400000 --output data/spread_history.csv
```

For a single smoke-test snapshot:

```powershell
python -m src.spread_history_collector --once --top 20 --limit 10 --min-spread 0 --min-volume 0 --output data/test_spread_history.csv
```

For execution-aware history with order book depth:

```powershell
python -m src.spread_history_collector --interval 60 --top 1000 --limit 50 --min-spread 0.5 --min-volume 400000 --capture-orderbooks --orderbook-limit 20 --output data/spread_history_enriched.csv
```

This writes executable spread and depth columns for $100/$200/$500/$1000:

- `executable_spread_100_pct`;
- `executable_spread_200_pct`;
- `executable_spread_500_pct`;
- `executable_spread_1000_pct`;
- `long_depth_usd_100`, `short_depth_usd_100`;
- `long_depth_usd_200`, `short_depth_usd_200`;
- `long_depth_usd_500`, `short_depth_usd_500`;
- `long_depth_usd_1000`, `short_depth_usd_1000`.

## Run backtest

```powershell
python -m backtest.simulator --input data/spread_history.csv --trades-csv data/backtest_trades.csv
```

Parameters are in `config/backtest.yaml`.

To also choose a skipped-signal output path:

```powershell
python -m backtest.simulator --input data/spread_history.csv --trades-csv data/backtest_trades.csv --skipped-csv data/backtest_skipped_signals.csv
```

## Inspect backtest quality

```powershell
python -m backtest.report --trades-csv data/backtest_trades.csv --skipped-csv data/backtest_skipped_signals.csv
```

Look at trade count first. A backtest with only a few trades is a plumbing check,
not a strategy validation. Then inspect win rate, profit factor, max drawdown,
exit reasons, skipped reasons, PnL by pair/coin and PnL by spread category.

The trade CSV includes:

- `market_mode`;
- `entry_executable_spread_pct`;
- `exit_executable_spread_pct`;
- `execution_status`;
- `execution_reason`;
- `execution_fill_fraction`;
- `execution_cost_usd`;
- `funding_events`;
- `seconds_to_first_funding`.

The report includes:

- net PnL, win rate, profit factor and max drawdown;
- expectancy, median PnL, p10/p90 PnL, best/worst trade;
- average win/loss, payoff ratio, max win/loss streak;
- average/median holding time;
- total fees, slippage, execution cost and funding PnL;
- TP1 count and average execution fill;
- PnL by exit reason, pair, coin, spread category and market mode.

## Telegram reports

Spread alerts:

```powershell
python -m output.telegram_bot --once --dry-run --min-spread 0.5 --min-volume 400000
```

Daily control report:

```powershell
python -m output.telegram_bot --once --dry-run --daily-control --backtest-trades data/backtest_trades.csv --backtest-skipped data/backtest_skipped_signals.csv
```

Remove `--dry-run` and set `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` in `.env`
to send messages.

## Paper trading

Paper trading uses live exchange APIs, but never sends real orders. It writes
spread snapshots, signals, virtual trades and order book impact estimates to
SQLite.

Run one live-data tick:

```powershell
python -m paper.paper_trader --once --db data/paper_trading.sqlite3 --top 10 --limit 10 --min-spread 0.5 --max-spread 10 --min-volume 0 --capture-orderbooks
```

Run continuously:

```powershell
python -m paper.paper_trader --db data/paper_trading.sqlite3 --interval 60 --top 1000 --limit 50 --min-spread 0.5 --min-volume 400000 --capture-orderbooks
```

Show open paper trades in Telegram daily control:

```powershell
python -m output.telegram_bot --once --dry-run --daily-control --paper-db data/paper_trading.sqlite3
```

The paper loop follows open trades using the full scan, even if the spread later
falls below the entry filter. Entry filters only apply to new trades.

## Optimize parameters

```powershell
python -m backtest.optimizer --input data/spread_history.csv --output data/optimization_results.csv --min-spreads 1,2,3,5 --min-volumes 0,400000,1000000 --max-slippages 0.1,0.2 --funding-modes true,false --dynamic-exit-modes true,false --position-sizes 100,200,500 --tp1-retraces 0.3,0.5,0.7 --max-adverse-spreads 3,5,8 --leverages 3,5,10
```
