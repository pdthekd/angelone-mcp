# src/angel_one_mcp/server.py
# -*- coding: utf-8 -*-
import re
import uuid
import os
import hmac
import hashlib
import json
from pathlib import Path
from typing import Optional

from .type import (
    BuyStockSLL,
    BuyStockSLM,
    CancelGTTRule,
    CancelOrder,
    CreateGTTRule,
    LTPRequest,
    ModifyGTTRule,
    SellStockSLL,
    SellStockSLM,
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
# Approval tool: requires HUMAN_APPROVAL_PIN and operator identity
#
@mcp.tool()
async def approve_trade(request_id: str, auth_pin: str, operator: str):
    """
    Approves and executes a previously created trade intent.
    Requires HUMAN_APPROVAL_PIN (set in environment). Uses hmac.compare_digest for constant-time compare.
    Records operator identity and requires ALLOWED_OPERATORS check if configured.
    """
    try:
        if not request_id:
            raise Exception("Missing request_id")
        if not auth_pin:
            raise Exception("Missing auth_pin")
        if not operator:
            raise Exception("Missing operator identity")

        human_pin = os.getenv("HUMAN_APPROVAL_PIN")
        if not human_pin:
            db_utils.log_audit_event("approval_error", {"request_id": request_id, "reason": "HUMAN_APPROVAL_PIN not configured", "operator": operator})
            raise Exception("Operator approval not configured on server")

        # constant-time compare for PIN
        if not hmac.compare_digest(human_pin, auth_pin):
            db_utils.log_audit_event("approval_failed_auth", {"request_id": request_id, "operator": operator})
            raise Exception("Authentication failed: invalid human PIN")

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
        if intent.get("status") != "PENDING":
            raise Exception(f"Intent not in PENDING state (current: {intent.get('status')})")

        db_utils.log_audit_event("approval_succeeded", {"request_id": request_id, "operator": operator})

        action = intent.get("meta", {}).get("type", "placeOrder")
        params = intent.get("order_params") or intent.get("order_params")  # defensive
        smart_api = session_manager.get_api()

        try:
            if action in ("cancel_order", "cancelOrder"):
                cancel_response = make_api_call(smart_api, "cancelOrder", params.get("order_id"), params.get("variety"))
                cancel_response = redact_sensitive_keys(cancel_response)
                db_utils.update_trade_intent_status(request_id, "EXECUTED", {"result": cancel_response})
                db_utils.log_audit_event("intent_executed", {"request_id": request_id, "action": "cancelOrder", "result": cancel_response, "operator": operator})
                return {"success": True, "message": "Order cancelled successfully (see audit logs for details)"}
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
            else:
                order_response = make_api_call(smart_api, "placeOrder", params)
                order_response = redact_sensitive_keys(order_response)
                order_id = None
                if isinstance(order_response, str):
                    order_id = order_response
                elif isinstance(order_response, dict) and order_response.get("status"):
                    order_id = order_response.get("data", {}).get("orderid")
                db_utils.update_trade_intent_status(request_id, "EXECUTED", {"result": order_response})
                db_utils.log_audit_event("intent_executed", {"request_id": request_id, "action": "placeOrder", "result": order_response, "operator": operator})
                return {"success": True, "order_id": order_id, "message": "Order executed successfully (see audit logs for details)"}
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