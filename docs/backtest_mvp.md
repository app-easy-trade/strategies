# Backtest MVP

## What it does

The current MVP backtests two-leg spread convergence trades from an append-only CSV
history produced by `src.spread_history_collector`. Entry decisions go through the
core trading model in `core/`.

It accounts for:

- entry by spread range and minimum 24h quote volume;
- LONG on the cheap exchange and SHORT on the expensive exchange;
- TP1: close part of the position after a configured spread retrace;
- final exit by target spread;
- stop by maximum adverse spread expansion;
- maximum holding time;
- maker/taker fees;
- slippage;
- funding edge between the long and short legs;
- spread category;
- rolling mean/std and z-score;
- spread velocity and acceleration;
- net expected profit before entry;
- trade CSV export;
- skipped signal CSV export with rejection reasons;
- win rate, profit factor, drawdown, average PnL and average holding time.
- grid optimization over spread, TP1, stop and leverage.

## What is not complete yet

- Real historical exchange downloads are not implemented yet. The collector starts
  accumulating history from the moment it is launched.
- Order book depth is approximated by 24h volume filters. Full depth/slippage
  simulation needs stored order book snapshots.
- Limit-order waiting and partial fills are modeled as costs, not as a full
  execution event stream.
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
exit reasons, skipped reasons and PnL by pair/coin.

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

## Optimize parameters

```powershell
python -m backtest.optimizer --input data/spread_history.csv --output data/optimization_results.csv --min-spreads 1,2,3,5 --tp1-retraces 0.3,0.5,0.7 --max-adverse-spreads 3,5,8 --leverages 3,5,10
```
