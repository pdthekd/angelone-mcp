# -*- coding: utf-8 -*-
import re
import uuid
from .type import BuyStockSLL, BuyStockSLM, CancelOrder, SellStockSLL, SellStockSLM, StockInput, TargetSell
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
    Get the list of available exchanges for the user. It would return exchanges like NSE, BSE etc.
    """
    try:
        refresh_token = session_manager.refresh_token
        smart_api = session_manager.get_api()
        res = make_api_call(smart_api, 'getProfile', refresh_token)
        # sanitize response shape minimally before returning
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
            token, symbol, exch = getTokenFromAngelMaster(param.entity)
        else:
            token, symbol, exch = getTokenFromName(param.entity, threshold=int(session_manager.threshold))

        if not token or not exch:
            raise Exception("No such company registered in NSE or BSE")

        # validate holdings for sell_all or quantity
        smart_api = session_manager.get_api()
        holdings = make_api_call(smart_api, 'allholding')
        holdings_list = holdings.get("data", {}).get("holdings", [])
        current_quantity = 0
        symbol_temp = symbol
        if symbol.endswith('-EQ'):
            symbol = symbol[:-3]
        for h in holdings_list:
            if (h.get("tradingsymbol") == symbol or h.get("tradingsymbol") == symbol + '-EQ') and h.get("exchange") == exch:
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

        meta = {
            "type": "sell_stock_sll",
            "entity": param.entity,
            "symbol": symbol,
            "exchange": exch,
            "quantity": sell_quantity
        }
        request_id = _store_pending_trade("placeOrder", orderparams, meta)
        return f"⚠️ TRADE INTENT CREATED. ID: {request_id}. To execute, you MUST call the `approve_trade` tool."
    except Exception as e:
        raise McpError(f"Error: {safe_error_message(e)}")


@mcp.tool()
async def sell_stock_slm(param: SellStockSLM):
    """
    Create a Stop Loss Market (SL-M) Sell order intent. DOES NOT EXECUTE.
    """
    try:
        symbol = None
        if param.isSymbol:
            token, symbol, exch = getTokenFromAngelMaster(param.entity)
        else:
            token, symbol, exch = getTokenFromName(param.entity, threshold=int(session_manager.threshold))

        if not token or not exch:
            raise Exception("No such company registered in NSE or BSE")

        smart_api = session_manager.get_api()
        holdings = make_api_call(smart_api, 'allholding')
        holdings_list = holdings.get("data", {}).get("holdings", [])
        current_quantity = 0
        symbol_temp = symbol
        if symbol.endswith('-EQ'):
            symbol = symbol[:-3]
        for h in holdings_list:
            if (h.get("tradingsymbol") == symbol or h.get("tradingsymbol") == symbol + '-EQ') and h.get("exchange") == exch:
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

        meta = {
            "type": "sell_stock_slm",
            "entity": param.entity,
            "symbol": symbol,
            "exchange": exch,
            "quantity": sell_quantity
        }
        request_id = _store_pending_trade("placeOrder", orderparams, meta)
        return f"⚠️ TRADE INTENT CREATED. ID: {request_id}. To execute, you MUST call the `approve_trade` tool."
    except Exception as e:
        raise McpError(f"Error: {safe_error_message(e)}")


@mcp.tool()
async def target_sell(param: TargetSell):
    """
    Create a Target Sell order intent. DOES NOT EXECUTE.
    """
    try:
        symbol = None
        if param.isSymbol:
            token, symbol, exch = getTokenFromAngelMaster(param.entity)
        else:
            token, symbol, exch = getTokenFromName(param.entity, threshold=int(session_manager.threshold))

        if not token or not exch:
            raise Exception("No such company registered in NSE or BSE")

        smart_api = session_manager.get_api()
        holdings = make_api_call(smart_api, 'allholding')
        holdings_list = holdings.get("data", {}).get("holdings", [])
        current_quantity = 0
        symbol_temp = symbol
        if symbol.endswith('-EQ'):
            symbol = symbol[:-3]
        for h in holdings_list:
            if (h.get("tradingsymbol") == symbol or h.get("tradingsymbol") == symbol + '-EQ') and h.get("exchange") == exch:
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

        meta = {
            "type": "target_sell",
            "entity": param.entity,
            "symbol": symbol,
            "exchange": exch,
            "quantity": sell_quantity
        }
        request_id = _store_pending_trade("placeOrder", orderparams, meta)
        return f"⚠️ TRADE INTENT CREATED. ID: {request_id}. To execute, you MUST call the `approve_trade` tool."
    except Exception as e:
        raise McpError(f"Error: {safe_error_message(e)}")


@mcp.tool()
async def cancel_order(param: CancelOrder):
    """
    Create a cancel-order intent. DOES NOT EXECUTE.
    """
    try:
        # store cancel intent for approval
        order_id = param.order_id
        variety = param.variety or "NORMAL"
        orderparams = {
            "order_id": order_id,
            "variety": variety
        }
        meta = {
            "type": "cancel_order",
            "order_id": order_id,
            "variety": variety
        }
        request_id = _store_pending_trade("cancelOrder", orderparams, meta)
        return f"⚠️ CANCEL INTENT CREATED. ID: {request_id}. To execute, you MUST call the `approve_trade` tool."
    except Exception as e:
        raise McpError(f"Error: {safe_error_message(e)}")


#
# Approval tool: executes the pending trade (only when human/operator calls this)
#
@mcp.tool()
async def approve_trade(request_id: str):
    """
    Approves and executes a previously created trade intent.
    This tool looks up the request_id in PENDING_TRADES, removes it atomically,
    and then executes the corresponding SmartAPI call (placeOrder or cancelOrder).
    """
    try:
        if not request_id or request_id not in PENDING_TRADES:
            raise Exception("Invalid or unknown request_id")

        # pop to avoid double execution
        intent = PENDING_TRADES.pop(request_id, None)
        if intent is None:
            raise Exception("Intent not found or already executed")

        action = intent.get("action")
        params = intent.get("orderparams", {})
        smart_api = session_manager.get_api()

        if action == "placeOrder":
            # Execute placeOrder and return minimal sanitized result
            order_response = make_api_call(smart_api, 'placeOrder', params)
            # Attempt to extract an order id safely
            if isinstance(order_response, str):
                order_id = order_response
            elif isinstance(order_response, dict) and order_response.get('status'):
                order_id = order_response.get('data', {}).get('orderid')
            else:
                # if we get a dict with error message, raise with safe content
                err_msg = order_response.get('message') if isinstance(order_response, dict) else str(order_response)
                raise Exception(f"Order placement failed: {err_msg}")

            return {"success": True, "order_id": order_id, "message": "Order executed successfully (see audit logs for details)"}

        elif action == "cancelOrder":
            cancel_response = make_api_call(smart_api, 'cancelOrder', params.get("order_id"), params.get("variety"))
            if isinstance(cancel_response, str):
                return {"success": True, "message": "Order cancelled successfully", "order_id": params.get("order_id")}
            elif isinstance(cancel_response, dict) and cancel_response.get('status'):
                return {"success": True, "message": "Order cancelled successfully", "order_id": params.get("order_id")}
            else:
                err_msg = cancel_response.get('message') if isinstance(cancel_response, dict) else str(cancel_response)
                raise Exception(f"Order cancellation failed: {err_msg}")
        else:
            raise Exception("Unsupported intent action")
    except Exception as e:
        raise McpError(f"Error: {safe_error_message(e)}")


def main():
    mcp.run(transport='stdio')


if __name__ == "__main__":
    main()
