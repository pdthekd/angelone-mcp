# src/angel_one_mcp/server.py
# -*- coding: utf-8 -*-
import re
import uuid
import os
import hmac
import hashlib
import json
import threading
from pathlib import Path
from typing import Optional

from .type import (
    BuyStockSLL,
    BuyStockSLM,
    CancelGTTRule,
    CancelOrder,
    CreateGTTRule,
    EstimateChargesRequest,
    LTPRequest,
    MarketQuoteRequest,
    ModifyOrder,
    SearchScripRequest,
    ModifyGTTRule,
    SellStockSLL,
    SellStockSLM,
    TrailGTTRules,
    StockInput,
    TargetSell,
)
from .utils.retry_helper_decorator import retry_with_backoff
from .utils.getTokens import getTokenFromAngelMaster, getTokenFromName
from .utils.sessionManager import SessionManager
from .utils import db as db_utils
from .utils.sanitizer import redact_sensitive_keys
from mcp.server.fastmcp import FastMCP

session_manager = SessionManager()
mcp = FastMCP("angel_one_mcp")

# Compute the absolute root path of the repository
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = os.getenv("TRADE_DB_PATH", str(BASE_DIR / "trades_audit.db"))
db_utils.init_db(DB_PATH)


@retry_with_backoff(max_retries=3, base_delay=2)
def make_api_call(smartApi, method_name, *args, **kwargs):
    method = getattr(smartApi, method_name)
    return method(*args, **kwargs)


def safe_error_message(e: Exception) -> str:
    """
    Redact sensitive pieces from exception messages.
    """
    try:
        msg = str(e)
    except Exception:
        return "<UNRECOGNIZABLE_ERROR>"

    key_pattern = re.compile(
        r'(?i)\b(api_key|password|pwd|token|client_code|refresh_token|refreshtoken|clientid|username|pin)\b\s*(?:=|:)\s*["\']?([^\s,"\']+)["\']?'
    )
    msg = key_pattern.sub(r'\1=<REDACTED>', msg)

    long_token_pattern = re.compile(r'\b[a-zA-Z0-9]{25,}\b')
    msg = long_token_pattern.sub('<REDACTED_TOKEN>', msg)

    totp_pattern = re.compile(r'\b\d{6}\b')
    msg = totp_pattern.sub('<REDACTED_TOTP>', msg)

    if len(msg) > 1000:
        msg = msg[:1000] + '...<TRUNCATED>'

    return msg


# SmartAPI error codes -> human-readable description (see errorcode.md)
ANGEL_ERROR_CODES = {
    "AG8001": "Invalid session token", "AG8002": "Session token expired", "AG8003": "Session token missing",
    "AB8050": "Invalid refresh token", "AB8051": "Refresh token expired",
    "AB1000": "Invalid email or password", "AB1001": "Invalid email", "AB1002": "Invalid password length",
    "AB1003": "Client already exists", "AB1004": "Angel One servers are having issues, try again shortly",
    "AB1005": "User type must be USER", "AB1006": "Account is blocked for trading",
    "AB1007": "Exchange (AMX) error", "AB1008": "Invalid order variety", "AB1009": "Symbol not found on exchange",
    "AB1010": "Exchange session expired", "AB1011": "Client not logged in", "AB1012": "Invalid product type",
    "AB1013": "Order not found", "AB1014": "Trade not found", "AB1015": "Holding not found",
    "AB1016": "Position not found", "AB1017": "Position conversion failed", "AB1018": "Failed to get symbol details",
    "AB2000": "Unspecified broker error", "AB2001": "Internal error, try again shortly",
    "AB1031": "Old password mismatch", "AB1032": "User not found", "AB2002": "ROBO (bracket) orders are blocked",
    "AB4008": "Order tag must be under 20 characters",
    "AB9000": "GTT: internal server error", "AB9001": "GTT: invalid parameters", "AB9002": "GTT: method not allowed",
    "AB9003": "GTT: invalid client ID", "AB9004": "GTT: invalid status array size", "AB9005": "GTT: invalid session ID",
    "AB9006": "GTT: invalid order quantity", "AB9007": "GTT: invalid disclosed quantity", "AB9008": "GTT: invalid price",
    "AB9009": "GTT: invalid trigger price", "AB9010": "GTT: invalid exchange segment", "AB9011": "GTT: invalid symbol token",
    "AB9012": "GTT: invalid trading symbol", "AB9013": "GTT: invalid rule ID", "AB9014": "GTT: invalid order side",
    "AB9015": "GTT: invalid product type", "AB9016": "GTT: invalid time period", "AB9017": "GTT: invalid page value",
    "AB9018": "GTT: invalid count value",
}


def _raise_for_broker_error(response, context: str = ""):
    """Raise a descriptive exception if a broker response indicates failure."""
    if not isinstance(response, dict):
        raise Exception(f"{context}: unexpected response format from broker" if context else "Unexpected response format from broker")
    if response.get("status"):
        return
    errorcode = response.get("errorcode") or ""
    message = response.get("message") or "Unknown broker error"
    description = ANGEL_ERROR_CODES.get(errorcode)
    detail = f"{message} (errorcode={errorcode}" + (f": {description}" if description else "") + ")"
    prefix = f"{context}: " if context else ""
    raise Exception(f"{prefix}{detail}")


# Intent types that mean "place a new order". Anything else unrecognised is refused
# by approve_trade rather than silently treated as a new order.
PLACE_ORDER_INTENT_TYPES = {
    "placeOrder", "buy_stock_sll", "buy_stock_slm", "sell_stock_sll", "sell_stock_slm", "target_sell",
}


def _create_intent_and_audit(action: str, orderparams: dict, meta: Optional[dict] = None) -> str:
    request_id = uuid.uuid4().hex[:8]
    db_utils.insert_trade_intent(request_id, orderparams, meta)
    db_utils.log_audit_event("intent_created", {"request_id": request_id, "action": action, "meta": meta, "orderparams": orderparams})
    return request_id


#
# Read tools
#
@mcp.tool()
async def get_exchanges():
    try:
        smart_api = session_manager.get_api()
        res = make_api_call(smart_api, "getProfile", session_manager.refresh_token)
        return redact_sensitive_keys(res.get("data", {}).get("exchanges", []))
    except Exception as e:
        raise RuntimeError(f"Error: {safe_error_message(e)}")


@mcp.tool()
async def current_holdings():
    try:
        smart_api = session_manager.get_api()
        holdings = make_api_call(smart_api, "allholding")
        holdings = redact_sensitive_keys(holdings)
        holdings_list = (holdings.get("data", {}).get("holdings", []))
        totals = holdings.get("data", {}).get("totalholding", {})
        enriched = []
        for h in holdings_list:
            enriched.append({
                "symbol": h.get("tradingsymbol"),
                "exchange": h.get("exchange"),
                "quantity": h.get("quantity"),
                "avg_price": h.get("averageprice"),
                "ltp": h.get("ltp"),
                "pnl": h.get("profitandloss"),
                "pnl_percentage": h.get("pnlpercentage"),
                "product": h.get("product")
            })
        return {
            "success": True,
            "holdings": enriched,
            "portfolio_summary": {
                "total_value": totals.get("totalholdingvalue"),
                "invested_value": totals.get("totalinvvalue"),
                "total_pnl": totals.get("totalprofitandloss"),
                "total_pnl_percentage": totals.get("totalpnlpercentage"),
            }
        }
    except Exception as e:
        raise RuntimeError(f"Error: {safe_error_message(e)}")


@mcp.tool()
async def get_pending_orders():
    try:
        smart_api = session_manager.get_api()
        orders = make_api_call(smart_api, "orderBook")
        orders = redact_sensitive_keys(orders)
        if orders is None:
            raise Exception("Orders are None")
        if not orders.get("status"):
            raise Exception("Failed to fetch orders")
        orders_list = orders.get("data", []) or []
        pending_orders = []
        for order in orders_list:
            status = (order.get("status") or "").lower()
            if status in ["open", "trigger pending", "pending"]:
                pending_orders.append({
                    "order_id": order.get("orderid"),
                    "symbol": order.get("tradingsymbol"),
                    "exchange": order.get("exchange"),
                    "transaction_type": order.get("transactiontype"),
                    "order_type": order.get("ordertype"),
                    "product_type": order.get("producttype"),
                    "quantity": order.get("quantity"),
                    "price": order.get("price"),
                    "trigger_price": order.get("triggerprice"),
                    "status": order.get("status"),
                    "variety": order.get("variety"),
                    "order_time": order.get("updatetime")
                })
        return {"success": True, "pending_orders": pending_orders, "count": len(pending_orders)}
    except Exception as e:
        raise RuntimeError(f"Error: {safe_error_message(e)}")


@mcp.tool()
async def get_order_history():
    """Fetch the full order book for the day, across all statuses (complete, rejected, cancelled, open, etc.).

    Use this to verify whether a specific order actually executed, rather than
    guessing from a holdings snapshot.
    """
    try:
        smart_api = session_manager.get_api()
        orders = make_api_call(smart_api, "orderBook")
        orders = redact_sensitive_keys(orders)
        _raise_for_broker_error(orders, "Failed to fetch order book")

        orders_list = orders.get("data") or []
        history = [
            {
                "order_id": o.get("orderid"),
                "unique_order_id": o.get("uniqueorderid"),
                "symbol": o.get("tradingsymbol"),
                "exchange": o.get("exchange"),
                "transaction_type": o.get("transactiontype"),
                "order_type": o.get("ordertype"),
                "product_type": o.get("producttype"),
                "quantity": o.get("quantity"),
                "filled_shares": o.get("filledshares"),
                "unfilled_shares": o.get("unfilledshares"),
                "average_price": o.get("averageprice"),
                "price": o.get("price"),
                "trigger_price": o.get("triggerprice"),
                "status": o.get("status"),
                "order_status": o.get("orderstatus"),
                "text": o.get("text"),
                "update_time": o.get("updatetime"),
            }
            for o in orders_list
        ]
        return {"success": True, "orders": history, "count": len(history)}
    except Exception as e:
        raise RuntimeError(f"Error: {safe_error_message(e)}")


@mcp.tool()
async def get_order_status(unique_order_id: str):
    """Fetch the exact status of a single order by its unique_order_id (returned when the order was placed/modified/cancelled)."""
    try:
        if not unique_order_id:
            raise Exception("Missing unique_order_id")
        smart_api = session_manager.get_api()
        res = make_api_call(smart_api, "individual_order_details", unique_order_id)
        if isinstance(res, str):
            res = json.loads(res)
        res = redact_sensitive_keys(res)
        _raise_for_broker_error(res, "Failed to fetch order status")
        return {"success": True, "order": res.get("data") or {}}
    except Exception as e:
        raise RuntimeError(f"Error: {safe_error_message(e)}")


@mcp.tool()
async def get_ltp(param: LTPRequest):
    """Fetch the last traded price for a single stock, without pulling the full holdings payload."""
    try:
        smart_api = session_manager.get_api()
        if param.isSymbol:
            token, symbol, exch = getTokenFromAngelMaster(param.entity)
        else:
            threshold_val = int(session_manager.threshold) if session_manager.threshold else 80
            token, symbol, exch = getTokenFromName(param.entity, threshold=threshold_val)
        if not token or not exch:
            raise Exception("No such company registered in NSE or BSE")

        res = make_api_call(smart_api, "ltpData", exch, symbol, token)
        if isinstance(res, str):
            res = json.loads(res)
        res = redact_sensitive_keys(res)
        _raise_for_broker_error(res, "Failed to fetch LTP")
        return {"success": True, "quote": res.get("data") or {}}
    except Exception as e:
        raise RuntimeError(f"Error: {safe_error_message(e)}")


@mcp.tool()
async def get_positions():
    """Today's open positions (intraday and carried F&O). Read-only. Returns an empty list when there are none."""
    try:
        res = make_api_call(session_manager.get_api(), "position")
        res = redact_sensitive_keys(res)
        _raise_for_broker_error(res, "Failed to fetch positions")
        return {"success": True, "positions": res.get("data") or []}
    except Exception as e:
        raise RuntimeError(f"Error: {safe_error_message(e)}")


@mcp.tool()
async def get_rms_limit():
    """Funds and margin: available cash, net, collateral, utilised debits and M2M. Read-only."""
    try:
        res = make_api_call(session_manager.get_api(), "rmsLimit")
        res = redact_sensitive_keys(res)
        _raise_for_broker_error(res, "Failed to fetch funds")
        return {"success": True, "funds": res.get("data") or {}}
    except Exception as e:
        raise RuntimeError(f"Error: {safe_error_message(e)}")


@mcp.tool()
async def get_market_quote(param: MarketQuoteRequest):
    """Quotes for up to 50 symbols in one call (mode LTP, OHLC or FULL). Read-only.
    Symbols the broker could not find are listed under `unfetched`."""
    try:
        tokens: dict = {}
        unresolved = []
        for entity in param.entities:
            token, symbol, exch = getTokenFromAngelMaster(entity)
            if not token or not exch:
                unresolved.append(entity)
                continue
            tokens.setdefault(exch, []).append(token)
        if not tokens:
            raise Exception(f"None of the symbols could be resolved: {unresolved}")
        res = make_api_call(session_manager.get_api(), "getMarketData", param.mode, tokens)
        res = redact_sensitive_keys(res)
        _raise_for_broker_error(res, "Failed to fetch quotes")
        data = res.get("data") or {}
        return {
            "success": True,
            "mode": param.mode,
            "quotes": data.get("fetched") or [],
            "unfetched": (data.get("unfetched") or []) + [{"symbol": s, "reason": "not found in scrip master"} for s in unresolved],
        }
    except Exception as e:
        raise RuntimeError(f"Error: {safe_error_message(e)}")


@mcp.tool()
async def search_scrip(param: SearchScripRequest):
    """Find trading symbols and tokens matching a text query on NSE or BSE. Read-only."""
    try:
        res = make_api_call(session_manager.get_api(), "_postRequest", "api.search.scrip",
                            {"exchange": param.exchange.value, "searchscrip": param.query})
        res = redact_sensitive_keys(res)
        _raise_for_broker_error(res, "Scrip search failed")
        return {"success": True, "matches": res.get("data") or []}
    except Exception as e:
        raise RuntimeError(f"Error: {safe_error_message(e)}")


@mcp.tool()
async def estimate_charges(param: EstimateChargesRequest):
    """Estimate brokerage, exchange fees, STT and other taxes for hypothetical orders.
    Read-only: nothing is placed and no intent is created."""
    try:
        orders = []
        for o in param.orders:
            token, symbol, exch = getTokenFromAngelMaster(o.entity)
            if not token or not exch:
                raise Exception(f"Unknown symbol: {o.entity}")
            orders.append({
                "product_type": o.product_type.value,
                "transaction_type": o.transaction_type.value,
                "quantity": str(o.quantity),
                "price": str(o.price),
                "exchange": exch,
                "symbol_name": symbol,
                "token": token,
            })
        res = make_api_call(session_manager.get_api(), "_postRequest", "api.estimateCharges", {"orders": orders})
        res = redact_sensitive_keys(res)
        _raise_for_broker_error(res, "Charge estimate failed")
        return {"success": True, "charges": res.get("data") or {}}
    except Exception as e:
        raise RuntimeError(f"Error: {safe_error_message(e)}")


@mcp.tool()
async def get_gtt_rules(status: Optional[list[str]] = None, page: int = 1, count: int = 50):
    """List GTT (Good Till Trigger) rules. status filters by e.g. NEW, ACTIVE, CANCELLED, SENTTOEXCHANGE (default: all)."""
    try:
        smart_api = session_manager.get_api()
        status_list = status or ["NEW", "ACTIVE", "SENTTOEXCHANGE", "CANCELLED", "FORALL"]
        res = make_api_call(smart_api, "gttLists", status_list, page, count)
        if isinstance(res, str):
            res = json.loads(res)
        res = redact_sensitive_keys(res)
        _raise_for_broker_error(res, "Failed to fetch GTT rules")
        rules = res.get("data") or []
        return {"success": True, "rules": rules, "count": len(rules)}
    except Exception as e:
        raise RuntimeError(f"Error: {safe_error_message(e)}")


@mcp.tool()
async def get_gtt_rule_details(rule_id: str):
    """Fetch full details of a single GTT rule by its rule_id."""
    try:
        if not rule_id:
            raise Exception("Missing rule_id")
        smart_api = session_manager.get_api()
        res = make_api_call(smart_api, "gttDetails", rule_id)
        if isinstance(res, str):
            res = json.loads(res)
        res = redact_sensitive_keys(res)
        _raise_for_broker_error(res, "Failed to fetch GTT rule details")
        return {"success": True, "rule": res.get("data") or {}}
    except Exception as e:
        raise RuntimeError(f"Error: {safe_error_message(e)}")


@mcp.tool()
async def get_stock_details(param: StockInput):
    try:
        smart_api = session_manager.get_api()
        fromdate_str = param.fromdate.strftime("%Y-%m-%d %H:%M")
        todate_str = param.todate.strftime("%Y-%m-%d %H:%M")
        if param.isSymbol:
            token, symbol, exch = getTokenFromAngelMaster(param.entity)
        else:
            threshold_val = int(session_manager.threshold) if session_manager.threshold else 80
            token, symbol, exch = getTokenFromName(param.entity, threshold=threshold_val)
        if not token or not exch:
            raise Exception("No such company registered in NSE or BSE")
        historicParam = {
            "exchange": exch,
            "symboltoken": token,
            "interval": param.interval.value,
            "fromdate": fromdate_str,
            "todate": todate_str
        }
        candle_data = make_api_call(smart_api, "getCandleData", historicParam)
        candle_data = redact_sensitive_keys(candle_data)
        if not candle_data or len(candle_data.get("data", [])) == 0 or len(candle_data["data"][0]) < 6:
            raise Exception("No candle data present for this stock and given time frame")
        datas = []
        for data in candle_data["data"]:
            datas.append({"timestamp": data[0], "open": data[1], "high": data[2], "low": data[3], "close": data[4], "volume": data[5]})
        response = {
            "data": datas,
            "stock": param.entity,
            "token": token,
            "fromDate": fromdate_str,
            "toDate": todate_str,
            "interval": param.interval.value
        }
        return response
    except Exception as e:
        raise RuntimeError(f"Error: {safe_error_message(e)}")


#
# Write tools (create pending trade intents instead of executing)
#
@mcp.tool()
async def buy_stock_sll(param: BuyStockSLL):
    try:
        if param.isSymbol:
            token, symbol, exch = getTokenFromAngelMaster(param.entity)
        else:
            threshold_val = int(session_manager.threshold) if session_manager.threshold else 80
            token, symbol, exch = getTokenFromName(param.entity, threshold=threshold_val)
        if not token or not exch:
            raise Exception("No such company registered in NSE or BSE")
        orderparams = {
            "variety": "STOPLOSS",
            "tradingsymbol": symbol,
            "symboltoken": token,
            "transactiontype": "BUY",
            "exchange": exch,
            "ordertype": "STOPLOSS_LIMIT",
            "producttype": "INTRADAY" if param.product_type == "INTRADAY" else "DELIVERY",
            "duration": "DAY",
            "price": param.limit_price,
            "triggerprice": param.trigger_price,
            "quantity": param.quantity
        }
        meta = {"type": "buy_stock_sll", "entity": param.entity, "symbol": symbol, "exchange": exch, "quantity": param.quantity}
        request_id = _create_intent_and_audit("placeOrder", orderparams, meta)
        return f"⚠️ TRADE INTENT CREATED. ID: {request_id}. To execute, you MUST call the `approve_trade` tool."
    except Exception as e:
        raise RuntimeError(f"Error: {safe_error_message(e)}")


@mcp.tool()
async def buy_stock_slm(param: BuyStockSLM):
    try:
        if param.isSymbol:
            token, symbol, exch = getTokenFromAngelMaster(param.entity)
        else:
            threshold_val = int(session_manager.threshold) if session_manager.threshold else 80
            token, symbol, exch = getTokenFromName(param.entity, threshold=threshold_val)
        if not token or not exch:
            raise Exception("No such company registered in NSE or BSE")
        orderparams = {
            "variety": "STOPLOSS",
            "tradingsymbol": symbol,
            "symboltoken": token,
            "transactiontype": "BUY",
            "exchange": exch,
            "ordertype": "STOPLOSS_MARKET",
            "producttype": "INTRADAY" if param.product_type == "INTRADAY" else "DELIVERY",
            "duration": "DAY",
            "price": "0",
            "triggerprice": param.trigger_price,
            "quantity": param.quantity
        }
        meta = {"type": "buy_stock_slm", "entity": param.entity, "symbol": symbol, "exchange": exch, "quantity": param.quantity}
        request_id = _create_intent_and_audit("placeOrder", orderparams, meta)
        return f"⚠️ TRADE INTENT CREATED. ID: {request_id}. To execute, you MUST call the `approve_trade` tool."
    except Exception as e:
        raise RuntimeError(f"Error: {safe_error_message(e)}")


@mcp.tool()
async def sell_stock_sll(param: SellStockSLL):
    try:
        if param.isSymbol:
            token, symbol, exch = getTokenFromAngelMaster(param.entity)
        else:
            threshold_val = int(session_manager.threshold) if session_manager.threshold else 80
            token, symbol, exch = getTokenFromName(param.entity, threshold=threshold_val)
        if not token or not exch:
            raise Exception("No such company registered in NSE or BSE")
        smart_api = session_manager.get_api()
        holdings = make_api_call(smart_api, "allholding")
        holdings = redact_sensitive_keys(holdings)
        holdings_list = holdings.get("data", {}).get("holdings", [])
        current_quantity = 0
        symbol_temp = symbol
        if symbol and symbol.endswith("-EQ"):
            symbol = symbol[:-3]
        for h in holdings_list:
            if (h.get("tradingsymbol") == symbol or h.get("tradingsymbol") == (symbol + "-EQ")) and h.get("exchange") == exch:
                current_quantity = float(h.get("quantity", 0))
                break
        symbol = symbol_temp
        if current_quantity == 0:
            raise Exception(f"No holdings found for {param.entity}")
        sell_quantity = int(current_quantity) if param.sell_all else param.quantity
        if current_quantity < sell_quantity:
            raise Exception(f"Insufficient quantity. You have {current_quantity} but trying to sell {sell_quantity}")
        orderparams = {
            "variety": "STOPLOSS",
            "tradingsymbol": symbol,
            "symboltoken": token,
            "transactiontype": "SELL",
            "exchange": exch,
            "ordertype": "STOPLOSS_LIMIT",
            "producttype": "DELIVERY",
            "duration": "DAY",
            "price": param.limit_price,
            "triggerprice": param.trigger_price,
            "quantity": sell_quantity
        }
        meta = {"type": "sell_stock_sll", "entity": param.entity, "symbol": symbol, "exchange": exch, "quantity": sell_quantity}
        request_id = _create_intent_and_audit("placeOrder", orderparams, meta)
        return f"⚠️ TRADE INTENT CREATED. ID: {request_id}. To execute, you MUST call the `approve_trade` tool."
    except Exception as e:
        raise RuntimeError(f"Error: {safe_error_message(e)}")


@mcp.tool()
async def sell_stock_slm(param: SellStockSLM):
    try:
        if param.isSymbol:
            token, symbol, exch = getTokenFromAngelMaster(param.entity)
        else:
            threshold_val = int(session_manager.threshold) if session_manager.threshold else 80
            token, symbol, exch = getTokenFromName(param.entity, threshold=threshold_val)
        if not token or not exch:
            raise Exception("No such company registered in NSE or BSE")
        smart_api = session_manager.get_api()
        holdings = make_api_call(smart_api, "allholding")
        holdings = redact_sensitive_keys(holdings)
        holdings_list = holdings.get("data", {}).get("holdings", [])
        current_quantity = 0
        symbol_temp = symbol
        if symbol and symbol.endswith("-EQ"):
            symbol = symbol[:-3]
        for h in holdings_list:
            if (h.get("tradingsymbol") == symbol or h.get("tradingsymbol") == (symbol + "-EQ")) and h.get("exchange") == exch:
                current_quantity = float(h.get("quantity", 0))
                break
        symbol = symbol_temp
        if current_quantity == 0:
            raise Exception(f"No holdings found for {param.entity}")
        sell_quantity = int(current_quantity) if param.sell_all else param.quantity
        if current_quantity < sell_quantity:
            raise Exception(f"Insufficient quantity. You have {current_quantity} but trying to sell {sell_quantity}")
        orderparams = {
            "variety": "STOPLOSS",
            "tradingsymbol": symbol,
            "symboltoken": token,
            "transactiontype": "SELL",
            "exchange": exch,
            "ordertype": "STOPLOSS_MARKET",
            "producttype": "DELIVERY",
            "duration": "DAY",
            "price": "0",
            "triggerprice": param.trigger_price,
            "quantity": sell_quantity
        }
        meta = {"type": "sell_stock_slm", "entity": param.entity, "symbol": symbol, "exchange": exch, "quantity": sell_quantity}
        request_id = _create_intent_and_audit("placeOrder", orderparams, meta)
        return f"⚠️ TRADE INTENT CREATED. ID: {request_id}. To execute, you MUST call the `approve_trade` tool."
    except Exception as e:
        raise RuntimeError(f"Error: {safe_error_message(e)}")


@mcp.tool()
async def target_sell(param: TargetSell):
    try:
        if param.isSymbol:
            token, symbol, exch = getTokenFromAngelMaster(param.entity)
        else:
            threshold_val = int(session_manager.threshold) if session_manager.threshold else 80
            token, symbol, exch = getTokenFromName(param.entity, threshold=threshold_val)
        if not token or not exch:
            raise Exception("No such company registered in NSE or BSE")
        smart_api = session_manager.get_api()
        holdings = make_api_call(smart_api, "allholding")
        holdings = redact_sensitive_keys(holdings)
        holdings_list = holdings.get("data", {}).get("holdings", [])
        current_quantity = 0
        symbol_temp = symbol
        if symbol and symbol.endswith("-EQ"):
            symbol = symbol[:-3]
        for h in holdings_list:
            if (h.get("tradingsymbol") == symbol or h.get("tradingsymbol") == (symbol + "-EQ")) and h.get("exchange") == exch:
                current_quantity = float(h.get("quantity", 0))
                break
        symbol = symbol_temp
        if current_quantity == 0:
            raise Exception(f"No holdings found for {param.entity}")
        sell_quantity = int(current_quantity) if param.sell_all else param.quantity
        if current_quantity < sell_quantity:
            raise Exception(f"Insufficient quantity. You have {current_quantity} but trying to sell {sell_quantity}")
        orderparams = {
            "variety": "NORMAL",
            "tradingsymbol": symbol,
            "symboltoken": token,
            "transactiontype": "SELL",
            "exchange": exch,
            "ordertype": "LIMIT",
            "producttype": "DELIVERY",
            "duration": "DAY",
            "price": param.target_price,
            "quantity": sell_quantity
        }
        meta = {"type": "target_sell", "entity": param.entity, "symbol": symbol, "exchange": exch, "quantity": sell_quantity}
        request_id = _create_intent_and_audit("placeOrder", orderparams, meta)
        return f"⚠️ TRADE INTENT CREATED. ID: {request_id}. To execute, you MUST call the `approve_trade` tool."
    except Exception as e:
        raise RuntimeError(f"Error: {safe_error_message(e)}")


@mcp.tool()
async def modify_order(param: ModifyOrder):
    """Create an intent to change quantity, price and/or trigger price of an open order.
    Unchanged fields are filled from the live order. Does NOT execute: a human must
    approve it with request_trade_approval, then approve_trade."""
    try:
        if param.quantity is None and param.price is None and param.trigger_price is None:
            raise Exception("Nothing to change: pass quantity, price and/or trigger_price")
        book = make_api_call(session_manager.get_api(), "orderBook")
        book = redact_sensitive_keys(book)
        _raise_for_broker_error(book, "Failed to fetch order book")
        current = next((o for o in (book.get("data") or []) if o.get("orderid") == param.order_id), None)
        if not current:
            raise Exception(f"Order {param.order_id} not found in today's order book")
        if (current.get("status") or "").lower() not in ("open", "trigger pending", "pending"):
            raise Exception(f"Order {param.order_id} is '{current.get('status')}', only open orders can be modified")

        orderparams = {
            "variety": current.get("variety") or "NORMAL",
            "orderid": param.order_id,
            "ordertype": current.get("ordertype"),
            "producttype": current.get("producttype"),
            "duration": current.get("duration") or "DAY",
            "price": param.price if param.price is not None else current.get("price"),
            "quantity": param.quantity if param.quantity is not None else current.get("quantity"),
            "tradingsymbol": current.get("tradingsymbol"),
            "symboltoken": current.get("symboltoken"),
            "exchange": current.get("exchange"),
        }
        trigger = param.trigger_price if param.trigger_price is not None else current.get("triggerprice")
        if trigger not in (None, "", 0, 0.0, "0", "0.0", "0.00"):
            orderparams["triggerprice"] = trigger
        meta = {
            "type": "modifyOrder", "symbol": current.get("tradingsymbol"), "order_id": param.order_id,
            "quantity": orderparams["quantity"],
            "before": {"quantity": current.get("quantity"), "price": current.get("price"), "triggerprice": current.get("triggerprice")},
        }
        request_id = _create_intent_and_audit("modifyOrder", orderparams, meta)
        return f"⚠️ MODIFY INTENT CREATED. ID: {request_id}. To execute, call `request_trade_approval` then `approve_trade`."
    except Exception as e:
        raise RuntimeError(f"Error: {safe_error_message(e)}")


@mcp.tool()
async def cancel_order(param: CancelOrder):
    try:
        order_id = param.order_id
        variety = param.variety or "NORMAL"
        orderparams = {"order_id": order_id, "variety": variety}
        meta = {"type": "cancel_order", "order_id": order_id, "variety": variety}
        request_id = _create_intent_and_audit("cancelOrder", orderparams, meta)
        return f"⚠️ CANCEL INTENT CREATED. ID: {request_id}. To execute, you MUST call the `approve_trade` tool."
    except Exception as e:
        raise RuntimeError(f"Error: {safe_error_message(e)}")


@mcp.tool()
async def create_gtt_rule(param: CreateGTTRule):
    """Create a GTT (Good Till Trigger) rule — a standing conditional order that persists for up to a year,
    unlike a regular limit order which expires at end of day. Useful for target sell ladders.
    Creates a pending intent; call approve_trade to actually place it with the broker.
    """
    try:
        if param.isSymbol:
            token, symbol, exch = getTokenFromAngelMaster(param.entity)
        else:
            threshold_val = int(session_manager.threshold) if session_manager.threshold else 80
            token, symbol, exch = getTokenFromName(param.entity, threshold=threshold_val)
        if not token or not exch:
            raise Exception("No such company registered in NSE or BSE")
        if exch not in ("NSE", "BSE"):
            raise Exception("GTT currently only supports NSE and BSE")

        if not param.allow_duplicate:
            duplicates = []

            local_matches = db_utils.find_open_gtt_create_intents(symbol, exch, param.transaction_type.value)
            for m in local_matches:
                duplicates.append(
                    f"local intent {m['request_id']} ({m['status']}): price={m['price']} trigger={m['trigger_price']} qty={m['qty']}"
                )

            smart_api = session_manager.get_api()
            live = make_api_call(smart_api, "gttLists", ["NEW", "ACTIVE", "SENTTOEXCHANGE"], 1, 100)
            live = redact_sensitive_keys(live) if isinstance(live, dict) else live
            if isinstance(live, dict) and live.get("status"):
                for rule in (live.get("data") or []):
                    if (
                        rule.get("tradingsymbol") == symbol
                        and rule.get("exchange") == exch
                        and rule.get("transactiontype") == param.transaction_type.value
                    ):
                        duplicates.append(
                            f"live rule id={rule.get('id')} ({rule.get('status')}): price={rule.get('price')} trigger={rule.get('triggerprice')} qty={rule.get('qty')}"
                        )

            if duplicates:
                raise Exception(
                    f"A GTT {param.transaction_type.value} rule already exists for {symbol} on {exch}: "
                    + "; ".join(duplicates)
                    + ". Review/cancel it with cancel_gtt_rule, adjust it with modify_gtt_rule, "
                    "or pass allow_duplicate=true to create another one anyway."
                )

        gttparams = {
            "tradingsymbol": symbol,
            "symboltoken": token,
            "exchange": exch,
            "transactiontype": param.transaction_type.value,
            "producttype": param.product_type.value,
            "price": param.price,
            "qty": param.quantity,
            "triggerprice": param.trigger_price,
            "disclosedqty": param.disclosed_qty,
        }
        meta = {"type": "gttCreateRule", "entity": param.entity, "symbol": symbol, "exchange": exch, "quantity": param.quantity}
        request_id = _create_intent_and_audit("gttCreateRule", gttparams, meta)
        return f"⚠️ GTT CREATE INTENT CREATED. ID: {request_id}. To execute, you MUST call the `approve_trade` tool."
    except Exception as e:
        raise RuntimeError(f"Error: {safe_error_message(e)}")


@mcp.tool()
async def modify_gtt_rule(param: ModifyGTTRule):
    """Modify an existing GTT rule's price/quantity/trigger price.
    Creates a pending intent; call approve_trade to actually apply it with the broker.
    """
    try:
        smart_api = session_manager.get_api()
        existing = make_api_call(smart_api, "gttDetails", param.rule_id)
        if isinstance(existing, str):
            existing = json.loads(existing)
        existing = redact_sensitive_keys(existing)
        _raise_for_broker_error(existing, "Failed to fetch existing GTT rule")
        rule = existing.get("data") or {}
        if not rule:
            raise Exception(f"GTT rule {param.rule_id} not found")

        gttparams = {
            "id": param.rule_id,
            "symboltoken": rule.get("symboltoken"),
            "exchange": rule.get("exchange"),
            "price": param.price,
            "qty": param.quantity,
            "triggerprice": param.trigger_price,
            "disclosedqty": param.disclosed_qty,
        }
        meta = {"type": "gttModifyRule", "rule_id": param.rule_id, "symbol": rule.get("tradingsymbol"), "exchange": rule.get("exchange")}
        request_id = _create_intent_and_audit("gttModifyRule", gttparams, meta)
        return f"⚠️ GTT MODIFY INTENT CREATED. ID: {request_id}. To execute, you MUST call the `approve_trade` tool."
    except Exception as e:
        raise RuntimeError(f"Error: {safe_error_message(e)}")


def _tick(x: float) -> float:
    return round(round(x / 0.05) * 0.05, 2)


def _plan_trail(rule: dict, ltp: float, p) -> dict:
    """Pure planning for one active SELL GTT rule. Triggers only ever move UP.
    trigger below LTP = stop-loss -> trail to LTP - sl_trail_pct.
    trigger at/above LTP = target -> once LTP is within target_proximity_pct of it, raise to LTP + target_gap_pct.
    The existing price-minus-trigger offset is preserved."""
    trigger = float(rule.get("triggerprice") or 0)
    price = float(rule.get("price") or 0)
    if trigger <= 0 or ltp <= 0:
        return {"skip": "missing trigger or LTP"}
    if trigger < ltp:
        kind, new_trigger = "stop_loss", _tick(ltp * (1 - p.sl_trail_pct / 100))
    elif ltp >= trigger * (1 - p.target_proximity_pct / 100):
        kind, new_trigger = "target", _tick(ltp * (1 + p.target_gap_pct / 100))
    else:
        return {"skip": f"target {trigger} is more than {p.target_proximity_pct}% above LTP {ltp}"}
    if new_trigger < trigger * (1 + p.min_step_pct / 100):
        return {"skip": f"{kind}: move to {new_trigger} is below the {p.min_step_pct}% minimum step"}
    return {"kind": kind, "new_trigger": new_trigger, "new_price": _tick(new_trigger + (price - trigger))}


@mcp.tool()
async def trail_gtt_rules(param: TrailGTTRules):
    """Plan (and optionally create intents for) trailing your active SELL GTT stop-loss and target
    rules to the current market. Triggers only move UP, never down. By default it only SHOWS the
    plan. With create_intents=true it creates modify intents (one per rule) that do NOT execute:
    approve them with request_batch_trade_approval, then approve_trade for each."""
    try:
        smart_api = session_manager.get_api()
        res = make_api_call(smart_api, "gttLists", ["NEW", "ACTIVE"], 1, 100)
        if isinstance(res, str):
            res = json.loads(res)
        res = redact_sensitive_keys(res)
        _raise_for_broker_error(res, "Failed to fetch GTT rules")
        wanted = {s.upper() for s in param.symbols} if param.symbols else None
        rules = [r for r in (res.get("data") or [])
                 if r.get("transactiontype") == "SELL" and (not wanted or (r.get("tradingsymbol") or "").upper() in wanted)]
        if not rules:
            return {"success": True, "proposals": [], "skipped": [], "note": "No matching active SELL rules"}

        tokens: dict = {}
        for r in rules:
            tokens.setdefault(r["exchange"], set()).add(str(r["symboltoken"]))
        q = make_api_call(smart_api, "getMarketData", "LTP", {e: sorted(t) for e, t in tokens.items()})
        q = redact_sensitive_keys(q)
        _raise_for_broker_error(q, "Failed to fetch LTPs")
        ltps = {(x.get("exchange"), str(x.get("symbolToken"))): x.get("ltp") for x in (q.get("data") or {}).get("fetched", [])}

        open_modify = {str((i.get("meta") or {}).get("rule_id")) for i in db_utils.list_pending_intents()
                       if (i.get("meta") or {}).get("type") == "gttModifyRule"}
        proposals, skipped = [], []
        for r in rules:
            sym, rid = r.get("tradingsymbol"), str(r.get("id"))
            ltp = ltps.get((r["exchange"], str(r["symboltoken"])))
            plan = _plan_trail(r, float(ltp or 0), param)
            if "skip" in plan:
                skipped.append({"rule_id": rid, "symbol": sym, "reason": plan["skip"]})
                continue
            if rid in open_modify:
                skipped.append({"rule_id": rid, "symbol": sym, "reason": "a modify intent for this rule is already pending"})
                continue
            item = {"rule_id": rid, "symbol": sym, "kind": plan["kind"], "ltp": ltp, "qty": r.get("qty"),
                    "old_trigger": r.get("triggerprice"), "old_price": r.get("price"),
                    "new_trigger": plan["new_trigger"], "new_price": plan["new_price"]}
            if param.create_intents:
                gttparams = {"id": rid, "symboltoken": r["symboltoken"], "exchange": r["exchange"],
                             "price": plan["new_price"], "qty": r.get("qty"), "triggerprice": plan["new_trigger"],
                             "disclosedqty": r.get("disclosedqty") or 0}
                meta = {"type": "gttModifyRule", "rule_id": rid, "symbol": sym, "exchange": r["exchange"], "trail": plan["kind"]}
                item["request_id"] = _create_intent_and_audit("gttModifyRule", gttparams, meta)
            proposals.append(item)
        return {"success": True, "intents_created": param.create_intents, "proposals": proposals, "skipped": skipped}
    except Exception as e:
        raise RuntimeError(f"Error: {safe_error_message(e)}")


@mcp.tool()
async def cancel_gtt_rule(param: CancelGTTRule):
    """Cancel an existing GTT rule.
    Creates a pending intent; call approve_trade to actually cancel it with the broker.
    """
    try:
        smart_api = session_manager.get_api()
        existing = make_api_call(smart_api, "gttDetails", param.rule_id)
        if isinstance(existing, str):
            existing = json.loads(existing)
        existing = redact_sensitive_keys(existing)
        _raise_for_broker_error(existing, "Failed to fetch existing GTT rule")
        rule = existing.get("data") or {}
        if not rule:
            raise Exception(f"GTT rule {param.rule_id} not found")

        gttparams = {
            "id": param.rule_id,
            "symboltoken": rule.get("symboltoken"),
            "exchange": rule.get("exchange"),
        }
        meta = {"type": "gttCancelRule", "rule_id": param.rule_id, "symbol": rule.get("tradingsymbol"), "exchange": rule.get("exchange")}
        request_id = _create_intent_and_audit("gttCancelRule", gttparams, meta)
        return f"⚠️ GTT CANCEL INTENT CREATED. ID: {request_id}. To execute, you MUST call the `approve_trade` tool."
    except Exception as e:
        raise RuntimeError(f"Error: {safe_error_message(e)}")


#
# Human-in-the-loop approval: a local, out-of-band PIN prompt.
#
# The PIN never passes through an MCP tool argument or return value, so it
# never enters the calling LLM's context. request_trade_approval() pops a
# native OS dialog on the operator's own desktop (outside the chat/MCP
# channel entirely); the PIN is captured and verified inside this server
# process, and only the resulting PENDING -> HUMAN_APPROVED transition is
# visible to callers. approve_trade() then requires that transition to have
# already happened before it will touch the broker.
#
def _prompt_for_pin_and_approve(request_id: str, summary: str):
    """Runs in a background thread. Shows a native PIN dialog and verifies it
    server-side. The PIN itself is never returned to the MCP caller."""
    import tkinter as tk
    from tkinter import simpledialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        pin = simpledialog.askstring(
            "Approve Trade",
            f"Trade request {request_id}\n{summary}\n\nEnter approval PIN to authorize:",
            show="*",
            parent=root,
        )
    finally:
        root.destroy()

    if pin is None:
        db_utils.log_audit_event("human_approval_cancelled", {"request_id": request_id})
        return

    human_pin = os.getenv("HUMAN_APPROVAL_PIN")
    if not human_pin:
        db_utils.log_audit_event("approval_error", {"request_id": request_id, "reason": "HUMAN_APPROVAL_PIN not configured"})
        return

    if hmac.compare_digest(human_pin, pin):
        db_utils.mark_human_approved(request_id)
        db_utils.log_audit_event("human_approval_granted", {"request_id": request_id})
    else:
        db_utils.log_audit_event("human_approval_denied", {"request_id": request_id})


@mcp.tool()
async def request_trade_approval(request_id: str):
    """
    Show a local PIN-entry dialog on the operator's own desktop to authorize a
    pending trade intent. The PIN is typed directly into that dialog, never into
    this chat, and is verified inside this server process — it is never passed
    as a tool argument or returned to the caller.

    Returns immediately; the dialog appears in the background. After the human
    responds, call approve_trade(request_id, operator) to actually execute it
    (or get_trade_intent_status(request_id) to check first).
    """
    try:
        if not request_id:
            raise Exception("Missing request_id")
        intent = db_utils.get_trade_intent(request_id)
        if not intent:
            raise Exception("Invalid or unknown request_id")
        if intent.get("status") != "PENDING":
            raise Exception(f"Intent not in PENDING state (current: {intent.get('status')})")

        try:
            import tkinter  # noqa: F401
        except ImportError:
            raise Exception("tkinter is not available on this system; cannot show a local PIN prompt")

        meta = intent.get("meta") or {}
        p = intent.get("order_params") or {}
        summary = (
            f"{meta.get('type', 'order')} {p.get('tradingsymbol') or meta.get('symbol', '')} "
            f"{p.get('transactiontype', '')} qty={p.get('qty') or p.get('quantity') or meta.get('quantity', '')} "
            f"price={p.get('price', '')} trigger={p.get('triggerprice', '')}"
        ).strip()

        db_utils.log_audit_event("human_approval_requested", {"request_id": request_id, "summary": summary})
        threading.Thread(target=_prompt_for_pin_and_approve, args=(request_id, summary), daemon=True).start()

        return {
            "success": True,
            "message": f"PIN prompt shown on the operator's screen for request {request_id}. "
                       f"Waiting for the human to respond there — call approve_trade once they do."
        }
    except Exception as e:
        raise RuntimeError(f"Error: {safe_error_message(e)}")


def _prompt_for_pin_and_approve_batch(request_ids: list, summaries: list):
    """Runs in a background thread. Shows ONE native dialog listing every pending
    trade in the batch plus a single PIN field, so approving many trades at once
    doesn't require a separate popup (and separate PIN entry) per trade."""
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)

    dialog = tk.Toplevel(root)
    dialog.title("Approve Trades")
    dialog.attributes("-topmost", True)

    tk.Label(dialog, text=f"Approve {len(request_ids)} trade(s)?", font=("Segoe UI", 10, "bold")).pack(padx=12, pady=(12, 4))

    text = tk.Text(dialog, width=70, height=min(20, len(summaries) + 1), wrap="none")
    text.insert("1.0", "\n".join(summaries))
    text.config(state="disabled")
    text.pack(padx=12, pady=4)

    tk.Label(dialog, text="Enter approval PIN to authorize ALL listed trades:").pack(padx=12, pady=(8, 0))
    pin_var = tk.StringVar()
    entry = tk.Entry(dialog, textvariable=pin_var, show="*")
    entry.pack(padx=12, pady=4)
    entry.focus()

    result = {"pin": None}

    def on_ok():
        result["pin"] = pin_var.get()
        dialog.destroy()

    def on_cancel():
        result["pin"] = None
        dialog.destroy()

    btn_frame = tk.Frame(dialog)
    btn_frame.pack(pady=(4, 12))
    tk.Button(btn_frame, text="Approve All", command=on_ok).pack(side="left", padx=5)
    tk.Button(btn_frame, text="Cancel", command=on_cancel).pack(side="left", padx=5)
    dialog.protocol("WM_DELETE_WINDOW", on_cancel)
    entry.bind("<Return>", lambda e: on_ok())

    dialog.grab_set()
    root.wait_window(dialog)
    root.destroy()

    pin = result["pin"]
    if pin is None:
        for rid in request_ids:
            db_utils.log_audit_event("human_approval_cancelled", {"request_id": rid, "batch": True})
        return

    human_pin = os.getenv("HUMAN_APPROVAL_PIN")
    if not human_pin:
        for rid in request_ids:
            db_utils.log_audit_event("approval_error", {"request_id": rid, "reason": "HUMAN_APPROVAL_PIN not configured", "batch": True})
        return

    if hmac.compare_digest(human_pin, pin):
        for rid in request_ids:
            db_utils.mark_human_approved(rid)
            db_utils.log_audit_event("human_approval_granted", {"request_id": rid, "batch": True})
    else:
        for rid in request_ids:
            db_utils.log_audit_event("human_approval_denied", {"request_id": rid, "batch": True})


@mcp.tool()
async def request_batch_trade_approval(request_ids: list[str]):
    """
    Show ONE local PIN-entry dialog listing every given pending trade intent, so
    the operator reviews the whole batch and approves it with a single PIN entry
    instead of one popup per trade. Intents that aren't currently PENDING are
    skipped and reported back rather than silently dropped.

    Returns immediately; the dialog appears in the background. After the human
    responds, call approve_trade(request_id, operator) for each included
    request_id to actually execute it.
    """
    try:
        if not request_ids:
            raise Exception("Missing request_ids")

        valid_ids = []
        summaries = []
        skipped = []
        for rid in request_ids:
            intent = db_utils.get_trade_intent(rid)
            if not intent or intent.get("status") != "PENDING":
                skipped.append({"request_id": rid, "status": intent.get("status") if intent else "NOT_FOUND"})
                continue
            params = intent.get("order_params") or {}
            meta = intent.get("meta") or {}
            symbol = params.get("tradingsymbol") or meta.get("symbol") or "?"
            side = params.get("transactiontype", "")
            qty = params.get("qty") or params.get("quantity") or meta.get("quantity") or ""
            price = params.get("price", "")
            trigger = params.get("triggerprice", "")
            summaries.append(f"{rid}  {symbol:14s} {side:5s} qty={qty!s:>5} price={price!s:>8} trigger={trigger!s:>8}")
            valid_ids.append(rid)

        if not valid_ids:
            raise Exception("None of the given request_ids are PENDING")

        try:
            import tkinter  # noqa: F401
        except ImportError:
            raise Exception("tkinter is not available on this system; cannot show a local PIN prompt")

        db_utils.log_audit_event("human_approval_requested", {"request_ids": valid_ids, "batch": True, "count": len(valid_ids)})
        threading.Thread(target=_prompt_for_pin_and_approve_batch, args=(valid_ids, summaries), daemon=True).start()

        return {
            "success": True,
            "message": f"One PIN prompt shown for {len(valid_ids)} trade(s). Waiting for the human to respond.",
            "included": valid_ids,
            "skipped": skipped,
        }
    except Exception as e:
        raise RuntimeError(f"Error: {safe_error_message(e)}")


@mcp.tool()
async def get_trade_intent_status(request_id: str):
    """Check a trade intent's status: PENDING, HUMAN_APPROVED, EXECUTED, or FAILED."""
    try:
        if not request_id:
            raise Exception("Missing request_id")
        intent = db_utils.get_trade_intent(request_id)
        if not intent:
            raise Exception("Invalid or unknown request_id")
        return {"success": True, "request_id": request_id, "status": intent.get("status"), "created_at": intent.get("created_at")}
    except Exception as e:
        raise RuntimeError(f"Error: {safe_error_message(e)}")


@mcp.tool()
async def discard_trade_intent(request_id: str, reason: str):
    """Mark a PENDING or HUMAN_APPROVED trade intent as DISCARDED so it stops
    cluttering the pending list. This only touches the local intent DB — it
    never calls the broker, so it's safe to use for intents that were superseded
    (e.g. the position was placed manually elsewhere) or created by mistake.
    Cannot discard an intent that has already been EXECUTED — that's a real
    trade and the audit record for it must stay intact.
    """
    try:
        if not request_id:
            raise Exception("Missing request_id")
        if not reason:
            raise Exception("Missing reason")
        intent = db_utils.get_trade_intent(request_id)
        if not intent:
            raise Exception("Invalid or unknown request_id")
        if intent.get("status") not in ("PENDING", "HUMAN_APPROVED"):
            raise Exception(f"Cannot discard an intent in status {intent.get('status')} (only PENDING/HUMAN_APPROVED can be discarded)")

        db_utils.update_trade_intent_status(request_id, "DISCARDED", {"reason": reason})
        db_utils.log_audit_event("intent_discarded", {"request_id": request_id, "reason": reason})
        return {"success": True, "request_id": request_id, "status": "DISCARDED"}
    except Exception as e:
        raise RuntimeError(f"Error: {safe_error_message(e)}")


@mcp.tool()
async def approve_trade(request_id: str, operator: str):
    """
    Executes a trade intent that a human has already approved via request_trade_approval.
    Records operator identity and requires ALLOWED_OPERATORS check if configured.
    """
    try:
        if not request_id:
            raise Exception("Missing request_id")
        if not operator:
            raise Exception("Missing operator identity")

        # Validate operator against ALLOWED_OPERATORS if configured
        allowed = os.getenv("ALLOWED_OPERATORS", "").strip()
        if allowed:
            allowed_set = {s.strip() for s in allowed.split(",") if s.strip()}
            if operator not in allowed_set:
                db_utils.log_audit_event("approval_failed_operator", {"request_id": request_id, "operator": operator})
                raise Exception("Operator not allowed")

        intent = db_utils.get_trade_intent(request_id)
        if not intent:
            raise Exception("Invalid or unknown request_id")
        if intent.get("status") != "HUMAN_APPROVED":
            raise Exception(
                f"Trade not yet approved by a human (current status: {intent.get('status')}). "
                f"Call request_trade_approval first and wait for the PIN prompt to be completed."
            )

        # Atomic claim: only one caller can move HUMAN_APPROVED -> EXECUTING, so a
        # retry or concurrent call can never send the same order twice. Approvals
        # also expire so a forgotten approval cannot fire days later.
        ttl = int(os.getenv("APPROVAL_TTL_SECONDS", "900"))
        claim = db_utils.claim_for_execution(request_id, ttl)
        if claim == "EXPIRED":
            db_utils.log_audit_event("approval_expired", {"request_id": request_id, "operator": operator})
            raise Exception(f"Approval expired (older than {ttl}s). Call request_trade_approval again.")
        if claim != "OK":
            raise Exception("Trade is not in an approved state (already executing or executed?)")

        db_utils.log_audit_event("approval_succeeded", {"request_id": request_id, "operator": operator})

        action = intent.get("meta", {}).get("type", "placeOrder")
        params = intent.get("order_params") or intent.get("order_params")  # defensive
        smart_api = session_manager.get_api()

        try:
            if action in ("cancel_order", "cancelOrder"):
                cancel_response = make_api_call(smart_api, "cancelOrder", params.get("order_id"), params.get("variety"))
                cancel_response = redact_sensitive_keys(cancel_response)
                if isinstance(cancel_response, dict):
                    _raise_for_broker_error(cancel_response, "Order cancel failed")
                db_utils.update_trade_intent_status(request_id, "EXECUTED", {"result": cancel_response})
                db_utils.log_audit_event("intent_executed", {"request_id": request_id, "action": "cancelOrder", "result": cancel_response, "operator": operator})
                return {"success": True, "message": "Order cancelled successfully (see audit logs for details)"}
            elif action == "modifyOrder":
                modify_response = make_api_call(smart_api, "modifyOrder", dict(params))
                modify_response = redact_sensitive_keys(modify_response)
                if not isinstance(modify_response, dict):
                    raise Exception("Unexpected response from broker on modify")
                _raise_for_broker_error(modify_response, "Order modify failed")
                db_utils.update_trade_intent_status(request_id, "EXECUTED", {"result": modify_response})
                db_utils.log_audit_event("intent_executed", {"request_id": request_id, "action": "modifyOrder", "result": modify_response, "operator": operator})
                return {"success": True, "order_id": (modify_response.get("data") or {}).get("orderid"), "message": "Order modified successfully (see audit logs for details)"}
            elif action == "gttCreateRule":
                # NOTE: SmartApi's gttCreateRule() wrapper returns response['data']['id'] directly
                # and throws an unhandled TypeError on failure (data is null). Call _postRequest
                # directly instead so we get the full envelope and can surface a real error message.
                gtt_response = make_api_call(smart_api, "_postRequest", "api.gtt.create", params)
                gtt_response = redact_sensitive_keys(gtt_response)
                _raise_for_broker_error(gtt_response, "GTT create failed")
                rule_id = (gtt_response.get("data") or {}).get("id")
                db_utils.update_trade_intent_status(request_id, "EXECUTED", {"result": gtt_response})
                db_utils.log_audit_event("intent_executed", {"request_id": request_id, "action": "gttCreateRule", "result": gtt_response, "operator": operator})
                return {"success": True, "rule_id": rule_id, "message": "GTT rule created successfully (see audit logs for details)"}
            elif action == "gttModifyRule":
                # Same reasoning as gttCreateRule above: bypass the wrapper's unsafe ['data']['id'] unwrap.
                gtt_response = make_api_call(smart_api, "_postRequest", "api.gtt.modify", params)
                gtt_response = redact_sensitive_keys(gtt_response)
                _raise_for_broker_error(gtt_response, "GTT modify failed")
                db_utils.update_trade_intent_status(request_id, "EXECUTED", {"result": gtt_response})
                db_utils.log_audit_event("intent_executed", {"request_id": request_id, "action": "gttModifyRule", "result": gtt_response, "operator": operator})
                return {"success": True, "message": "GTT rule modified successfully (see audit logs for details)"}
            elif action == "gttCancelRule":
                gtt_response = make_api_call(smart_api, "gttCancelRule", params)
                gtt_response = redact_sensitive_keys(gtt_response)
                _raise_for_broker_error(gtt_response, "GTT cancel failed")
                db_utils.update_trade_intent_status(request_id, "EXECUTED", {"result": gtt_response})
                db_utils.log_audit_event("intent_executed", {"request_id": request_id, "action": "gttCancelRule", "result": gtt_response, "operator": operator})
                return {"success": True, "message": "GTT rule cancelled successfully (see audit logs for details)"}
            elif action in PLACE_ORDER_INTENT_TYPES:
                order_response = make_api_call(smart_api, "placeOrder", params)
                order_response = redact_sensitive_keys(order_response)
                order_id = None
                if isinstance(order_response, str):
                    order_id = order_response
                elif isinstance(order_response, dict):
                    _raise_for_broker_error(order_response, "Order failed")
                    order_id = (order_response.get("data") or {}).get("orderid")
                if not order_id:
                    raise Exception("Broker returned no order id")
                db_utils.update_trade_intent_status(request_id, "EXECUTED", {"result": order_response})
                db_utils.log_audit_event("intent_executed", {"request_id": request_id, "action": "placeOrder", "result": order_response, "operator": operator})
                return {"success": True, "order_id": order_id, "message": "Order executed successfully (see audit logs for details)"}
            else:
                raise Exception(f"Unknown intent type '{action}'; refusing to execute")
        except Exception as ex:
            err_msg = safe_error_message(ex)
            db_utils.update_trade_intent_status(request_id, "FAILED", {"error": err_msg})
            db_utils.log_audit_event("intent_failed", {"request_id": request_id, "error": err_msg, "operator": operator})
            raise Exception(f"Execution failed: {err_msg}")
    except Exception as e:
        raise RuntimeError(f"Error: {safe_error_message(e)}")


@mcp.tool()
async def get_trade_book():
    """
    Fetch all executed trades for the day from Angel One.
    """
    try:
        smart_api = session_manager.get_api()
        res = make_api_call(smart_api, "tradeBook")

        # Ensure response is a dict
        if isinstance(res, str):
            res = json.loads(res)

        res = redact_sensitive_keys(res)

        if not isinstance(res, dict):
            return {"success": False, "message": "Unexpected response format", "trades": []}

        trades = res.get("data")
        # Handle case where API returns None or empty data
        if trades is None:
            trades = []
        elif not isinstance(trades, list):
            trades = []

        return {"success": True, "trades": trades, "count": len(trades)}
    except Exception as e:
        raise RuntimeError(f"Error: {safe_error_message(e)}")


@mcp.tool()
async def get_audit_history(limit: int = 50):
    """
    Fetch recent local audit log entries from trades_audit.db.
    """
    try:
        events = db_utils.get_recent_audit_events(limit=limit)
        return {"success": True, "events": events, "count": len(events)}
    except Exception as e:
        raise RuntimeError(f"Error: {safe_error_message(e)}")


@mcp.tool()
async def get_portfolio_analysis():
    """
    Calculate weightage and concentration across all holdings.
    """
    try:
        smart_api = session_manager.get_api()
        res = make_api_call(smart_api, "allholding")

        # Ensure response is parsed JSON dict
        if isinstance(res, str):
            res = json.loads(res)

        res = redact_sensitive_keys(res)

        if not isinstance(res, dict):
            raise Exception("Invalid response received from broker API")

        data = res.get("data", {})
        if not isinstance(data, dict):
            data = {}

        holdings_list = data.get("holdings", []) or []
        totals = data.get("totalholding", {}) or {}

        # Calculate total portfolio value (current market value of all holdings)
        total_portfolio_value = sum(
            float(h.get("quantity", 0)) * float(h.get("ltp", 0))
            for h in holdings_list if isinstance(h, dict)
        )
        # Fallback: use API-provided total if calculation returns 0
        if total_portfolio_value == 0:
            try:
                total_portfolio_value = float(totals.get("totalholdingvalue", 0))
            except (ValueError, TypeError):
                total_portfolio_value = 0

        if total_portfolio_value <= 0:
            return {
                "success": True,
                "total_portfolio_value": 0,
                "holdings_weightage": [],
                "top_5_concentrated": []
            }

        analyzed_holdings = []
        for h in holdings_list:
            if not isinstance(h, dict):
                continue

            try:
                qty = float(h.get("quantity", 0))
                ltp = float(h.get("ltp", 0))
                holding_val = qty * ltp
            except (ValueError, TypeError):
                holding_val = 0.0

            weight_pct = round((holding_val / total_portfolio_value) * 100, 2)

            analyzed_holdings.append({
                "symbol": h.get("tradingsymbol", "UNKNOWN"),
                "exchange": h.get("exchange", ""),
                "quantity": qty,
                "ltp": ltp,
                "current_value": round(holding_val, 2),
                "weight_percentage": weight_pct,
                "pnl": h.get("profitandloss", 0),
                "pnl_percentage": h.get("pnlpercentage", 0)
            })

        # Sort from highest weight to lowest weight
        analyzed_holdings.sort(key=lambda x: x["weight_percentage"], reverse=True)

        return {
            "success": True,
            "total_portfolio_value": round(total_portfolio_value, 2),
            "total_invested_value": totals.get("totalinvvalue", 0),
            "total_pnl": totals.get("totalprofitandloss", 0),
            "holdings_weightage": analyzed_holdings,
            "top_5_concentrated": analyzed_holdings[:5]
        }
    except Exception as e:
        raise RuntimeError(f"Error: {safe_error_message(e)}")


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()