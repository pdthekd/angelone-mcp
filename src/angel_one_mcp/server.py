from .type import BuyStockSLL, BuyStockSLM, CancelOrder, SellStockSLL, SellStockSLM, StockInput, TargetSell
from .utils.retry_helper_decorator import retry_with_backoff
from .utils.getTokens import getTokenFromAngelMaster, getTokenFromName
from .utils.sessionManager import SessionManager
from mcp.server.fastmcp import FastMCP
from mcp import McpError

session_manager = SessionManager()
mcp = FastMCP("angel_one_mcp")

@retry_with_backoff(max_retries=3, base_delay=2)
def make_api_call(smartApi, method_name, *args, **kwargs):
    method = getattr(smartApi, method_name)
    return method(*args, **kwargs)


@mcp.resource("exchanges://current")
async def get_exchanges():
    """
    Get the list of available exchanges for the user. It would return exchanges like NSE, BSE etc.
    """
    try:
        refresh_token = session_manager.refresh_token
        smart_api = session_manager.get_api()
        res = make_api_call(smart_api, 'getProfile', refresh_token)
        return {"exchanges": res['data']['exchanges']}
    except Exception as e:
        raise McpError(f"Failed to get available exchanges: {str(e)}")
    

@mcp.resource("holdings://current")
async def current_holdings():
    """
    Get the current holdings of the user.
    """
    try:
        smart_api = session_manager.get_api()
        holdings = make_api_call(smart_api, 'allholding')
        holdings_list = (holdings.get("data", {}).get("holdings", []))
        totals = holdings.get("data", {}).get("totalholding", {})
        #return holdings
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
        raise McpError(f"Failed to get current holdings: {str(e)}")

@mcp.tool()
async def get_pending_orders():
    """
    Get the list of pending orders. Pending orders are those which are not yet executed or cancelled. Like any buy or sell order placed to be executed.
    """
    try:
        smart_api = session_manager.get_api()
        orders = make_api_call(smart_api, 'orderBook')
        if orders is None:
            return {"success": False, "error": "Orders are None"}
        if not orders.get('status'):
            return {"success": False, "error": "Failed to fetch orders"}
        
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
        return {"success": False, "error": str(e)}

@mcp.tool()
async def get_stock_details(param: StockInput):
    """
    Get historical stock details like OHLCV data for a given stock symbol or name within a specified date range and interval. Follow the dateformat as specified in the input.
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
            return {"success": False, "error": "No such company registered in NSE or BSE"}
        historicParam={
            "exchange": exch,
            "symboltoken": token,
            "interval": param.interval.value,
            "fromdate": fromdate_str, 
            "todate": todate_str
        }
        candle_data = make_api_call(smart_api, 'getCandleData', historicParam)
        if len(candle_data["data"]) == 0 or len(candle_data["data"][0]) < 6:
            return {"success": False, "error": "No candle data present for this stock and given time frame"}
        response = {}
        datas = []
        for data in candle_data["data"]:
            datas.append({"timestamp": data[0], "open": data[1], "high": data[2], "low": data[3], "close": data[4], "volume": data[5]})
        response["data"] = datas
        response["stock"] = param.entity
        response["token"] = token
        response["fromDate"] = fromdate_str
        response["toDate"] = todate_str
        response["interval"] = param.interval.value
        return response
    except Exception as e:
        raise McpError(f"Failed to get stock details: {str(e)}")



@mcp.tool()
async def buy_stock_sll(param: BuyStockSLL):
    """
    Place a Stop Loss Limit (SL-L) Buy order for a specified stock symbol or name with given trigger and limit prices.
    """
    try:
        smart_api = session_manager.get_api()
        symbol = None
        if param.isSymbol:
            token, symbol, exch = getTokenFromAngelMaster(param.entity)
        else:
            token, symbol, exch = getTokenFromName(param.entity, threshold=int(session_manager.threshold))
        if not token or not exch:
            return {"success": False, "error": "No such company registered in NSE or BSE"}
        
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
        order_response = make_api_call(smart_api, 'placeOrder', orderparams)
        if isinstance(order_response, str):
            return {
                "success": True,
                "order_id": order_response,
                "message": "SL-L Buy order placed successfully",
                "details": {
                    "entity": param.entity,
                    "symbol": symbol,
                    "quantity": param.quantity,
                    "trigger_price": param.trigger_price,
                    "limit_price": param.limit_price,
                    "exchange": exch
                }
            }
        elif isinstance(order_response, dict) and order_response.get('status'):
            return {
                "success": True,
                "order_id": order_response.get('data', {}).get('orderid'),
                "message": "SL-L Buy order placed successfully",
                "details": {
                    "entity": param.entity,
                    "symbol": symbol,
                    "quantity": param.quantity,
                    "trigger_price": param.trigger_price,
                    "limit_price": param.limit_price,
                    "exchange": exch
                }
            }
        else:
            error_msg = order_response.get('message', 'Order placement failed') if isinstance(order_response, dict) else str(order_response)
            return {"success": False, "error": error_msg}            
    except Exception as e:
        raise McpError(f"Failed to place SL-L Buy order: {str(e)}")


@mcp.tool()
async def buy_stock_slm(param: BuyStockSLM):
    """
    Place a Stop Loss Market (SL-M) Buy order for a specified stock symbol or name with a given trigger price.
    """
    try:
        smart_api = session_manager.get_api()
        symbol = None
        if param.isSymbol:
            token, symbol, exch = getTokenFromAngelMaster(param.entity)
        else:
            token, symbol, exch = getTokenFromName(param.entity, threshold=int(session_manager.threshold))
        
        if not token or not exch:
            return {"success": False, "error": "No such company registered in NSE or BSE"}
        
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
        
        order_response = make_api_call(smart_api, 'placeOrder', orderparams)

        if isinstance(order_response, str):
            return {
                "success": True,
                "order_id": order_response,
                "message": "SL-M Buy order placed successfully",
                "details": {
                    "entity": param.entity,
                    "symbol": symbol,
                    "quantity": param.quantity,
                    "trigger_price": param.trigger_price,
                    "exchange": exch
                }
            }
        elif isinstance(order_response, dict) and order_response.get('status'):
            return {
                "success": True,
                "order_id": order_response.get('data', {}).get('orderid'),
                "message": "SL-M Buy order placed successfully",
                "details": {
                    "entity": param.entity,
                    "symbol": symbol,
                    "quantity": param.quantity,
                    "trigger_price": param.trigger_price,
                    "exchange": exch
                }
            }
        else:
            error_msg = order_response.get('message', 'Order placement failed') if isinstance(order_response, dict) else str(order_response)
            return {"success": False, "error": error_msg}
    except Exception as e:
        raise McpError(f"Failed to place SL-M Buy order: {str(e)}")


@mcp.tool()
async def sell_stock_sll(param: SellStockSLL):
    """
    Place a Stop Loss Limit (SL-L) Sell order for a specified stock symbol or name with given trigger and limit prices.
    """
    try:
        smart_api = session_manager.get_api()
        symbol = None
        if param.isSymbol:
            token, symbol, exch = getTokenFromAngelMaster(param.entity)
        else:
            token, symbol, exch = getTokenFromName(param.entity, threshold=int(session_manager.threshold))
        
        if not token or not exch:
            return {"success": False, "error": "No such company registered in NSE or BSE"}
        
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
            return {
                "success": False, 
                "error": f"No holdings found for {param.entity}"
            }
        
        sell_quantity = int(current_quantity) if param.sell_all else param.quantity
        
        if current_quantity < sell_quantity:
            return {
                "success": False, 
                "error": f"Insufficient quantity. You have {current_quantity} but trying to sell {sell_quantity}"
            }
        
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
        
        order_response = make_api_call(smart_api, 'placeOrder', orderparams)

        if isinstance(order_response, str):
            return {
                "success": True,
                "order_id": order_response,
                "message": "SL-L Sell order placed successfully",
                "details": {
                    "entity": param.entity,
                    "symbol": symbol,
                    "quantity": sell_quantity,
                    "trigger_price": param.trigger_price,
                    "limit_price": param.limit_price,
                    "exchange": exch
                }
            }
        elif isinstance(order_response, dict) and order_response.get('status'):
            return {
                "success": True,
                "order_id": order_response.get('data', {}).get('orderid'),
                "message": "SL-L Sell order placed successfully",
                "details": {
                    "entity": param.entity,
                    "symbol": symbol,
                    "quantity": sell_quantity,
                    "trigger_price": param.trigger_price,
                    "limit_price": param.limit_price,
                    "exchange": exch
                }
            }
        else:
            if order_response is None:
                error_msg = "Order placement failed: No response from server"
            else:
                error_msg = order_response.get('message', 'Order placement failed') if isinstance(order_response, dict) else str(order_response)
            return {"success": False, "error": error_msg}
            
    except Exception as e:
        raise McpError(f"Failed to place SL-L Sell order: {str(e)}")


@mcp.tool()
async def sell_stock_slm(param: SellStockSLM):
    """
    Place a Stop Loss Market (SL-M) Sell order for a specified stock symbol or name with a given trigger price.
    """
    try:
        smart_api = session_manager.get_api()
        symbol = None
        if param.isSymbol:
            token, symbol, exch = getTokenFromAngelMaster(param.entity)
        else:
            token, symbol, exch = getTokenFromName(param.entity, threshold=int(session_manager.threshold))
        if not token or not exch:
            return {"success": False, "error": "No such company registered in NSE or BSE"}
        
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
            return {
                "success": False, 
                "error": f"No holdings found for {param.entity}"
            }
        sell_quantity = int(current_quantity) if param.sell_all else param.quantity
        
        if current_quantity < sell_quantity:
            return {
                "success": False, 
                "error": f"Insufficient quantity. You have {current_quantity} but trying to sell {sell_quantity}"
            }
        
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
        
        order_response = make_api_call(smart_api, 'placeOrder', orderparams)

        if isinstance(order_response, str):
            return {
                "success": True,
                "order_id": order_response,
                "message": "SL-M Sell order placed successfully",
                "details": {
                    "entity": param.entity,
                    "symbol": symbol,
                    "quantity": sell_quantity,
                    "trigger_price": param.trigger_price,
                    "exchange": exch
                }
            }
        elif isinstance(order_response, dict) and order_response.get('status'):
            return {
                "success": True,
                "order_id": order_response.get('data', {}).get('orderid'),
                "message": "SL-M Sell order placed successfully",
                "details": {
                    "entity": param.entity,
                    "symbol": symbol,
                    "quantity": sell_quantity,
                    "trigger_price": param.trigger_price,
                    "exchange": exch
                }
            }
        else:
            if order_response is None:
                error_msg = "Order placement failed: No response from server"
            else:
                error_msg = order_response.get('message', 'Order placement failed') if isinstance(order_response, dict) else str(order_response)
            return {"success": False, "error": error_msg}
            
    except Exception as e:
        raise McpError(f"Failed to place SL-M Sell order: {str(e)}")


@mcp.tool()
async def target_sell(param: TargetSell):
    """
    Target Sell order for a specified stock symbol or name with a given target price.
    """
    try:
        smart_api = session_manager.get_api()
        symbol = None
        if param.isSymbol:
            token, symbol, exch = getTokenFromAngelMaster(param.entity)
        else:
            token, symbol, exch = getTokenFromName(param.entity, threshold=int(session_manager.threshold))
        
        if not token or not exch:
            return {"success": False, "error": "No such company registered in NSE or BSE"}
        
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
            return {
                "success": False, 
                "error": f"No holdings found for {param.entity}"
            }
        sell_quantity = int(current_quantity) if param.sell_all else param.quantity
        if current_quantity < sell_quantity:
            return {
                "success": False, 
                "error": f"Insufficient quantity. You have {current_quantity} but trying to sell {sell_quantity}"
            }
        
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
        
        order_response = make_api_call(smart_api, 'placeOrder', orderparams)

        if isinstance(order_response, str):
            return {
                "success": True,
                "order_id": order_response,
                "message": "Target Sell order placed successfully",
                "details": {
                    "entity": param.entity,
                    "symbol": symbol,
                    "quantity": sell_quantity,
                    "target_price": param.target_price,
                    "exchange": exch
                }
            }
        elif isinstance(order_response, dict) and order_response.get('status'):
            return {
                "success": True,
                "order_id": order_response.get('data', {}).get('orderid'),
                "message": "Target Sell order placed successfully",
                "details": {
                    "entity": param.entity,
                    "symbol": symbol,
                    "quantity": sell_quantity,
                    "target_price": param.target_price,
                    "exchange": exch
                }
            }
        else:
            if order_response is None:
                error_msg = "Order placement failed: No response from server"
            else:
                error_msg = order_response.get('message', 'Order placement failed') if isinstance(order_response, dict) else str(order_response)
            return {"success": False, "error": error_msg}
            
    except Exception as e:
        raise McpError(f"Failed to place Target Sell order: {str(e)}")
    
@mcp.tool()
async def cancel_order(param: CancelOrder):
    """
    Cancel an existing order using its order ID and variety.
    """
    try:
        smart_api = session_manager.get_api()
        cancel_response = make_api_call(smart_api, 'cancelOrder', param.order_id, param.variety)
        
        if isinstance(cancel_response, str):
            return {
                "success": True,
                "message": "Order cancelled successfully",
                "order_id": param.order_id
            }
        elif isinstance(cancel_response, dict) and cancel_response.get('status'):
            return {
                "success": True,
                "message": "Order cancelled successfully",
                "order_id": param.order_id
            }
        else:
            error_msg = cancel_response.get('message', 'Order cancellation failed') if isinstance(cancel_response, dict) else str(cancel_response)
            return {"success": False, "error": error_msg}
            
    except Exception as e:
        raise McpError(f"Failed to cancel order: {str(e)}")
    
def main():
    mcp.run(transport='stdio')

if __name__ == "__main__":
    main()