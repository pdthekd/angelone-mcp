# src/angel_one_mcp/server.py
# -*- coding: utf-8 -*-
import re
import uuid
import os
import hmac
import hashlib
import json
from typing import Optional

from .type import (
    BuyStockSLL,
    BuyStockSLM,
    CancelOrder,
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
from mcp import McpError

session_manager = SessionManager()
mcp = FastMCP("angel_one_mcp")

# Initialize DB (path configurable via TRADE_DB_PATH)
DB_PATH = os.getenv("TRADE_DB_PATH", "./trades_audit.db")
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
        raise McpError(f"Error: {safe_error_message(e)}")


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
        raise McpError(f"Error: {safe_error_message(e)}")


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
        raise McpError(f"Error: {safe_error_message(e)}")


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
        raise McpError(f"Error: {safe_error_message(e)}")


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
        raise McpError(f"Error: {safe_error_message(e)}")


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
        raise McpError(f"Error: {safe_error_message(e)}")


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
        raise McpError(f"Error: {safe_error_message(e)}")


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
        raise McpError(f"Error: {safe_error_message(e)}")


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
        raise McpError(f"Error: {safe_error_message(e)}")


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
        raise McpError(f"Error: {safe_error_message(e)}")


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
                db_utils.update_trade_intent_status(request_id, "EXECUTED", {"result": cancel_response}, approved_by=operator)
                db_utils.log_audit_event("intent_executed", {"request_id": request_id, "action": "cancelOrder", "result": cancel_response, "operator": operator})
                return {"success": True, "message": "Order cancelled successfully (see audit logs for details)"}
            else:
                order_response = make_api_call(smart_api, "placeOrder", params)
                order_response = redact_sensitive_keys(order_response)
                order_id = None
                if isinstance(order_response, str):
                    order_id = order_response
                elif isinstance(order_response, dict) and order_response.get("status"):
                    order_id = order_response.get("data", {}).get("orderid")
                db_utils.update_trade_intent_status(request_id, "EXECUTED", {"result": order_response}, approved_by=operator)
                db_utils.log_audit_event("intent_executed", {"request_id": request_id, "action": "placeOrder", "result": order_response, "operator": operator})
                return {"success": True, "order_id": order_id, "message": "Order executed successfully (see audit logs for details)"}
        except Exception as ex:
            err_msg = safe_error_message(ex)
            db_utils.update_trade_intent_status(request_id, "FAILED", {"error": err_msg}, approved_by=operator)
            db_utils.log_audit_event("intent_failed", {"request_id": request_id, "error": err_msg, "operator": operator})
            raise Exception(f"Execution failed: {err_msg}")
    except Exception as e:
        raise McpError(f"Error: {safe_error_message(e)}")


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()