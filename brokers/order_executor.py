from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
from time import sleep, time
from typing import Any
from urllib.parse import urlencode

from .account_check import load_env_file
from .common import ExchangeAPIError, hmac_sha256, http_request_json_any, to_float


SUPPORTED_ORDER_EXCHANGES = {"bybit", "binance"}


@dataclass(frozen=True)
class LegOrder:
    exchange: str
    symbol: str
    side: str
    quantity: float
    aggressive_limit_price: float
    reduce_only: bool = False


@dataclass(frozen=True)
class OrderState:
    exchange: str
    symbol: str
    order_id: str
    status: str
    filled_qty: float
    average_price: float | None
    raw: dict[str, Any]

    @property
    def filled(self) -> bool:
        return self.status.lower() in {"filled", "fill"} or self.filled_qty > 0


@dataclass(frozen=True)
class TwoLegExecutionResult:
    long_limit: OrderState
    short_limit: OrderState
    fallback_market: OrderState | None
    status: str
    reason: str


class ExchangeOrderClient:
    def place_limit(self, leg: LegOrder) -> OrderState:
        raise NotImplementedError

    def place_market(self, leg: LegOrder) -> OrderState:
        raise NotImplementedError

    def get_order(self, symbol: str, order_id: str) -> OrderState:
        raise NotImplementedError

    def cancel_order(self, symbol: str, order_id: str) -> None:
        raise NotImplementedError


def execute_two_leg_entry(
    long_leg: LegOrder,
    short_leg: LegOrder,
    clients: dict[str, ExchangeOrderClient],
    timeout_seconds: float = 5.0,
    poll_interval_seconds: float = 0.5,
    dry_run: bool = True,
) -> TwoLegExecutionResult:
    if dry_run:
        return dry_run_result(long_leg, short_leg)

    long_client = clients[long_leg.exchange]
    short_client = clients[short_leg.exchange]
    long_order = long_client.place_limit(long_leg)
    short_order = short_client.place_limit(short_leg)
    deadline = time() + timeout_seconds
    long_state = long_order
    short_state = short_order

    while time() < deadline:
        long_state = long_client.get_order(long_leg.symbol, long_order.order_id)
        short_state = short_client.get_order(short_leg.symbol, short_order.order_id)
        if long_state.filled and short_state.filled:
            return TwoLegExecutionResult(long_state, short_state, None, "filled", "both_limits_filled")
        sleep(poll_interval_seconds)

    long_state = long_client.get_order(long_leg.symbol, long_order.order_id)
    short_state = short_client.get_order(short_leg.symbol, short_order.order_id)
    fallback: OrderState | None = None
    if long_state.filled and not short_state.filled:
        short_client.cancel_order(short_leg.symbol, short_order.order_id)
        fallback = short_client.place_market(short_leg)
        reason = "short_market_fallback_after_long_fill"
    elif short_state.filled and not long_state.filled:
        long_client.cancel_order(long_leg.symbol, long_order.order_id)
        fallback = long_client.place_market(long_leg)
        reason = "long_market_fallback_after_short_fill"
    elif not long_state.filled and not short_state.filled:
        long_client.cancel_order(long_leg.symbol, long_order.order_id)
        short_client.cancel_order(short_leg.symbol, short_order.order_id)
        return TwoLegExecutionResult(long_state, short_state, None, "not_filled", "both_limits_cancelled")
    else:
        reason = "both_limits_filled_after_timeout"

    return TwoLegExecutionResult(long_state, short_state, fallback, "fallback_used", reason)


def dry_run_result(long_leg: LegOrder, short_leg: LegOrder) -> TwoLegExecutionResult:
    long_state = OrderState(
        exchange=long_leg.exchange,
        symbol=long_leg.symbol,
        order_id="dry-run-long-limit",
        status="dry_run",
        filled_qty=0.0,
        average_price=None,
        raw={"leg": long_leg.__dict__},
    )
    short_state = OrderState(
        exchange=short_leg.exchange,
        symbol=short_leg.symbol,
        order_id="dry-run-short-limit",
        status="dry_run",
        filled_qty=0.0,
        average_price=None,
        raw={"leg": short_leg.__dict__},
    )
    return TwoLegExecutionResult(long_state, short_state, None, "dry_run", "orders_not_sent")


class BybitOrderClient(ExchangeOrderClient):
    def __init__(self, api_key: str, api_secret: str, testnet: bool = True, timeout: float = 10.0) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.timeout = timeout
        self.base_url = "https://api-testnet.bybit.com" if testnet else "https://api.bybit.com"

    def place_limit(self, leg: LegOrder) -> OrderState:
        payload = {
            "category": "linear",
            "symbol": leg.symbol,
            "side": bybit_side(leg.side),
            "orderType": "Limit",
            "qty": format_float(leg.quantity),
            "price": format_float(leg.aggressive_limit_price),
            "timeInForce": "IOC",
            "reduceOnly": leg.reduce_only,
        }
        data = self._signed_request("POST", "/v5/order/create", payload)
        order_id = str(data.get("result", {}).get("orderId") or "")
        return self.get_order(leg.symbol, order_id)

    def place_market(self, leg: LegOrder) -> OrderState:
        payload = {
            "category": "linear",
            "symbol": leg.symbol,
            "side": bybit_side(leg.side),
            "orderType": "Market",
            "qty": format_float(leg.quantity),
            "reduceOnly": leg.reduce_only,
        }
        data = self._signed_request("POST", "/v5/order/create", payload)
        order_id = str(data.get("result", {}).get("orderId") or "")
        return self.get_order(leg.symbol, order_id)

    def get_order(self, symbol: str, order_id: str) -> OrderState:
        data = self._signed_request(
            "GET",
            "/v5/order/realtime",
            {"category": "linear", "symbol": symbol, "orderId": order_id},
        )
        item = first_dict(data.get("result", {}).get("list")) or {}
        return OrderState(
            exchange="bybit",
            symbol=symbol,
            order_id=order_id,
            status=str(item.get("orderStatus") or "unknown"),
            filled_qty=to_float(item.get("cumExecQty")) or 0.0,
            average_price=to_float(item.get("avgPrice")),
            raw=item,
        )

    def cancel_order(self, symbol: str, order_id: str) -> None:
        self._signed_request(
            "POST",
            "/v5/order/cancel",
            {"category": "linear", "symbol": symbol, "orderId": order_id},
        )

    def _signed_request(self, method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        timestamp = str(int(time() * 1000))
        recv_window = "5000"
        body = None
        params = None
        if method.upper() == "GET":
            params = payload
            signing_payload = urlencode({key: value for key, value in payload.items() if value is not None})
        else:
            signing_payload = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
            body = signing_payload.encode("utf-8")
        signature = hmac_sha256(self.api_secret, timestamp + self.api_key + recv_window + signing_payload)
        data, _ = http_request_json_any(
            method,
            self.base_url,
            path,
            params=params,
            body=body,
            timeout=self.timeout,
            headers={
                "X-BAPI-API-KEY": self.api_key,
                "X-BAPI-TIMESTAMP": timestamp,
                "X-BAPI-RECV-WINDOW": recv_window,
                "X-BAPI-SIGN": signature,
            },
        )
        if not isinstance(data, dict) or data.get("retCode") != 0:
            raise ExchangeAPIError(f"Bybit order request failed: {str(data)[:400]}")
        return data


class BinanceOrderClient(ExchangeOrderClient):
    def __init__(self, api_key: str, api_secret: str, testnet: bool = True, timeout: float = 10.0) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.timeout = timeout
        self.base_url = "https://testnet.binancefuture.com" if testnet else "https://fapi.binance.com"

    def place_limit(self, leg: LegOrder) -> OrderState:
        data = self._signed_request(
            "POST",
            "/fapi/v1/order",
            {
                "symbol": leg.symbol,
                "side": binance_side(leg.side),
                "type": "LIMIT",
                "quantity": format_float(leg.quantity),
                "price": format_float(leg.aggressive_limit_price),
                "timeInForce": "IOC",
                "reduceOnly": "true" if leg.reduce_only else None,
            },
        )
        return binance_order_state(data)

    def place_market(self, leg: LegOrder) -> OrderState:
        data = self._signed_request(
            "POST",
            "/fapi/v1/order",
            {
                "symbol": leg.symbol,
                "side": binance_side(leg.side),
                "type": "MARKET",
                "quantity": format_float(leg.quantity),
                "reduceOnly": "true" if leg.reduce_only else None,
            },
        )
        return binance_order_state(data)

    def get_order(self, symbol: str, order_id: str) -> OrderState:
        data = self._signed_request(
            "GET",
            "/fapi/v1/order",
            {"symbol": symbol, "orderId": order_id},
        )
        return binance_order_state(data)

    def cancel_order(self, symbol: str, order_id: str) -> None:
        self._signed_request(
            "DELETE",
            "/fapi/v1/order",
            {"symbol": symbol, "orderId": order_id},
        )

    def _signed_request(self, method: str, path: str, params: dict[str, Any]) -> dict[str, Any]:
        signed_params = {key: value for key, value in params.items() if value is not None}
        signed_params["timestamp"] = int(time() * 1000)
        query = urlencode(signed_params)
        signed_params["signature"] = hmac_sha256(self.api_secret, query)
        data, _ = http_request_json_any(
            method,
            self.base_url,
            path,
            params=signed_params,
            headers={"X-MBX-APIKEY": self.api_key},
            timeout=self.timeout,
        )
        if not isinstance(data, dict):
            raise ExchangeAPIError(f"Binance order request returned non-object: {str(data)[:400]}")
        if "code" in data and int(data.get("code") or 0) < 0:
            raise ExchangeAPIError(f"Binance order request failed: {str(data)[:400]}")
        return data


def build_clients(exchanges: set[str], testnet: bool, timeout: float) -> dict[str, ExchangeOrderClient]:
    clients: dict[str, ExchangeOrderClient] = {}
    for exchange in exchanges:
        key = os.getenv(f"{exchange.upper()}_API_KEY")
        secret = os.getenv(f"{exchange.upper()}_API_SECRET")
        if not key or not secret:
            raise ExchangeAPIError(f"{exchange.upper()}_API_KEY/API_SECRET are not set")
        if exchange == "bybit":
            clients[exchange] = BybitOrderClient(key, secret, testnet=testnet, timeout=timeout)
        elif exchange == "binance":
            clients[exchange] = BinanceOrderClient(key, secret, testnet=testnet, timeout=timeout)
        else:
            raise ExchangeAPIError(f"Order executor is not implemented for {exchange}")
    return clients


def bybit_side(side: str) -> str:
    return "Buy" if side.lower() == "buy" else "Sell"


def binance_side(side: str) -> str:
    return "BUY" if side.lower() == "buy" else "SELL"


def binance_order_state(data: dict[str, Any]) -> OrderState:
    return OrderState(
        exchange="binance",
        symbol=str(data.get("symbol") or ""),
        order_id=str(data.get("orderId") or ""),
        status=str(data.get("status") or "unknown"),
        filled_qty=to_float(data.get("executedQty")) or 0.0,
        average_price=to_float(data.get("avgPrice")),
        raw=data,
    )


def first_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value[0]
    return None


def format_float(value: float) -> str:
    return f"{value:.12f}".rstrip("0").rstrip(".")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Testnet two-leg aggressive limit executor.")
    parser.add_argument("--env", type=Path, default=Path(".env"))
    parser.add_argument("--testnet", action="store_true", help="Required unless --dry-run is used.")
    parser.add_argument("--dry-run", action="store_true", help="Print the execution plan without sending orders.")
    parser.add_argument("--allow-real-orders", action="store_true", help="Dangerous: allow non-testnet endpoints.")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--order-timeout", type=float, default=5.0)
    parser.add_argument("--long-exchange", required=True, choices=sorted(SUPPORTED_ORDER_EXCHANGES))
    parser.add_argument("--long-symbol", required=True)
    parser.add_argument("--long-qty", type=float, required=True)
    parser.add_argument("--long-limit-price", type=float, required=True)
    parser.add_argument("--short-exchange", required=True, choices=sorted(SUPPORTED_ORDER_EXCHANGES))
    parser.add_argument("--short-symbol", required=True)
    parser.add_argument("--short-qty", type=float, required=True)
    parser.add_argument("--short-limit-price", type=float, required=True)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    load_env_file(args.env)
    if not args.dry_run and not args.testnet and not args.allow_real_orders:
        raise SystemExit("Refusing to send orders: use --testnet or --dry-run.")
    long_leg = LegOrder(args.long_exchange, args.long_symbol.upper(), "buy", args.long_qty, args.long_limit_price)
    short_leg = LegOrder(args.short_exchange, args.short_symbol.upper(), "sell", args.short_qty, args.short_limit_price)
    clients = {} if args.dry_run else build_clients(
        {long_leg.exchange, short_leg.exchange},
        testnet=args.testnet,
        timeout=args.timeout,
    )
    result = execute_two_leg_entry(
        long_leg,
        short_leg,
        clients,
        timeout_seconds=args.order_timeout,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, default=lambda item: item.__dict__, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
