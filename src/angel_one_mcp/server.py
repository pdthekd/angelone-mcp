
```python name=src/angel_one_mcp/server.py
# -*- coding: utf-8 -*-
import re
import uuid
import os
import hmac
import hashlib
import pyotp
from .type import (
    BuyStockSLL,
    BuyStockSLM,
    CancelOrder,
    SellStockSLL,
    SellStockSLM,
    StockInput,
    TargetSell,
    ApproveTrade,
)
from .utils.retry_helper_decorator import retry_with_backoff
from .utils.getTokens import getTokenFromAngelMaster, getTokenFromName
from .utils.sessionManager import SessionManager
from mcp.server.fastmcp import FastMCP
from mcp import McpError

session_manager = SessionManager()
mcp = FastMCP("angel_one_mcp")

# Global pending trades store
PENDING_TRADES = {}  # request_id -> { "action": "placeOrder"|"cancelOrder", "orderparams": {...}, "meta": {...} }


@retry_with_backoff(max_retries=3, base_delay=2)
def make_api_call(smartApi, method_name, *args, **kwargs):
    method = getattr(smartApi, method_name)
    return method(*args, **kwargs)


def safe_error_message(e: Exception) -> str:
    """
    Redact sensitive pieces from exception messages:
    - Long alphanumeric strings (>25 chars)
    - 6-digit TOTP pins
    - Values following keywords like api_key, password, pwd, token, client_code, refresh_token, username
    """
    try:
        msg = str(e)
    except Exception:
        return "<UNRECOGNIZABLE_ERROR>"

    # redact key=value or key: "value" patterns
    key_pattern = re.compile(
        r'(?i)\b(api_key|password|pwd|token|client_code|refresh_token|refreshtoken|clientid|username)\b\s*(?:=|:)\s*["\']?([^\s,"\']+)["\']?'
    )
    msg = key_pattern.sub(r'\1=<REDACTED>', msg)

    # redact very long alphanumeric tokens
    long_token_pattern = re.compile(r'\b[a-zA-Z0-9]{25,}\b')
    msg = long_token_pattern.sub('<REDACTED_TOKEN>', msg)

    # redact standalone 6-digit numbers (possible TOTP)
    totp_pattern = re.compile(r'\b\d{6}\b')
    msg = totp_pattern.sub('<REDACTED_TOTP>', msg)

    # Additionally, limit length of returned error
    if len(msg) > 1000:
        msg = msg[:1000] + '...<TRUNCATED>'

    return msg


#
# Read tools
#
@mcp.tool()
async def get_exchanges():
    """
    Get the list of available exchanges for the user.
    """
    try:
        refresh_token = session_manager.refresh_token
        smart_api = session_manager.get_api()
        res = make_api_call(smart_api, 'getProfile', refresh_token)
        return {"exchanges": res.get('data', {}).get('exchanges', [])}
    except Exception as e:
        raise McpError(f"Error: {safe_error_message(e)}")


@mcp.tool()
async def current_holdings():
    """
    Get the current holdings of the user.
    """
    try:
        smart_api = session_manager.get_api()
        holdings = make_api_call(smart_api, 'allholding')
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
    """
    Get the list of pending orders. Pending orders are those which are not yet executed or cancelled.
    """
    try:
        smart_api = session_manager.get_api()
        orders = make_api_call(smart_api, 'orderBook')
        if orders is None:
            raise Exception("Orders are None")
        if not orders.get('status'):
            raise Exception("Failed to fetch orders")

        orders_list = orders.get("data", [])
        if orders_list is None:
            orders_list = []
        pending_orders = []

        for order in orders_list:
            status = order.get("status", "").lower()
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

        return {
            "success": True,
            "pending_orders": pending_orders,
            "count": len(pending_orders)
        }
    except Exception as e:
        raise McpError(f"Error: {safe_error_message(e)}")


@mcp.tool()
async def get_stock_details(param: StockInput):
    """
    Get historical stock details like OHLCV data for a given stock symbol or name within a specified date range and interval.
    """
    try:
        smart_api = session_manager.get_api()
        fromdate_str = param.fromdate.strftime("%Y-%m-%d %H:%M")
        todate_str = param.todate.strftime("%Y-%m-%d %H:%M")
        if param.isSymbol:
            token, symbol, exch = getTokenFromAngelMaster(param.entity)
        else:
            token, symbol, exch = getTokenFromName(param.entity, threshold=int(session_manager.threshold))
        if not token or not exch:
            raise Exception("No such company registered in NSE or BSE")
        historicParam = {
            "exchange": exch,
            "symboltoken": token,
            "interval": param.interval.value,
            "fromdate": fromdate_str,
            "todate": todate_str
        }
        candle_data = make_api_call(smart_api, 'getCandleData', historicParam)
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
def _store_pending_trade(action: str, orderparams: dict, meta: dict = None) -> str:
    request_id = uuid.uuid4().hex[:8]
    PENDING_TRADES[request_id] = {
        "action": action,
        "orderparams": orderparams,
        "meta": meta or {},
    }
    return request_id


@mcp.tool()
async def buy_stock_sll(param: BuyStockSLL):
    """
    Create a Stop Loss Limit (SL-L) Buy order intent. DOES NOT EXECUTE.
    """
    try:
        symbol = None
        if param.isSymbol:
            token, symbol, exch = getTokenFromAngelMaster(param.entity)
        else:
            token, symbol, exch = getTokenFromName(param.entity, threshold=int(session_manager.threshold))

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

        meta = {
            "type": "buy_stock_sll",
            "entity": param.entity,
            "symbol": symbol,
            "exchange": exch,
            "quantity": param.quantity
        }
        request_id = _store_pending_trade("placeOrder", orderparams, meta)
        return f"⚠️ TRADE INTENT CREATED. ID: {request_id}. To execute, you MUST call the `approve_trade` tool."
    except Exception as e:
        raise McpError(f"Error: {safe_error_message(e)}")


@mcp.tool()
async def buy_stock_slm(param: BuyStockSLM):
    """
    Create a Stop Loss Market (SL-M) Buy order intent. DOES NOT EXECUTE.
    """
    try:
        symbol = None
        if param.isSymbol:
            token, symbol, exch = getTokenFromAngelMaster(param.entity)
        else:
            token, symbol, exch = getTokenFromName(param.entity, threshold=int(session_manager.threshold))

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

        meta = {
            "type": "buy_stock_slm",
            "entity": param.entity,
            "symbol": symbol,
            "exchange": exch,
            "quantity": param.quantity
        }
        request_id = _store_pending_trade("placeOrder", orderparams, meta)
        return f"⚠️ TRADE INTENT CREATED. ID: {request_id}. To execute, you MUST call the `approve_trade` tool."
    except Exception as e:
        raise McpError(f"Error: {safe_error_message(e)}")


@mcp.tool()
async def sell_stock_sll(param: SellStockSLL):
    """
    Create a Stop Loss Limit (SL-L) Sell order intent. DOES NOT EXECUTE.
    """
    try:
        symbol = None
        if param.isSymbol:
            token, symbol, exch = getToken
