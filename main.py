from fastapi import FastAPI
from SmartApi import SmartConnect
import pyotp
from dotenv import load_dotenv
import os
from type import BuyStockSLL, BuyStockSLM, CancelOrder, SellStockSLL, SellStockSLM, StockInput, TargetSell
from utils.retry_helper_decorator import retry_with_backoff
import time

from utils.getTokens import getTokenFromAngelMaster, getTokenFromName

app = FastAPI()

@retry_with_backoff(max_retries=3, base_delay=2)
def make_api_call(smartApi, method_name, *args, **kwargs):
    method = getattr(smartApi, method_name)
    return method(*args, **kwargs)


@app.get("/getExchanges")
async def root():
    try:
        load_dotenv()
        api_key = os.environ.get('api_key')
        username = os.environ.get('username')
        pwd = os.environ.get('pwd')
        token = os.environ.get('token')
        
        smartApi = SmartConnect(api_key)
        totp = pyotp.TOTP(token).now()
        # data = smartApi.generateSession(username, pwd, totp)
        data = make_api_call(smartApi, 'generateSession', username, pwd, totp)
        if data['status'] == False:
            return {"success": False, "error": "data status is false"}
        else:
            refreshToken = data['data']['refreshToken']
            res = smartApi.getProfile(refreshToken)
            smartApi.generateToken(refreshToken)
            res=res['data']['exchanges']
            return {"exchanges": res}
    except Exception as e:
        return {"success": False, "error": str(e)}
    

@app.get('/currentHoldings')
async def holdings():
    try:
        load_dotenv()
        api_key = os.environ.get('api_key')
        username = os.environ.get('username')
        pwd = os.environ.get('pwd')
        token = os.environ.get('token')
        smartApi = SmartConnect(api_key)
        totp = pyotp.TOTP(token).now()
        # data = smartApi.generateSession(username, pwd, totp)
        data = make_api_call(smartApi, 'generateSession', username, pwd, totp)
        if data['status'] == False:
            return {"success": False, "error": "data status is false"}
        else:
            holdings = smartApi.allholding()
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
        return {"success": False, "error": str(e)}
    

@app.post('/getStockDetails')
async def getCandle(param: StockInput):
    try:
        load_dotenv()
        api_key = os.environ.get('api_key')
        username = os.environ.get('username')
        pwd = os.environ.get('pwd')
        token = os.environ.get('token')
        threshold = os.environ.get('threshold')
        smartApi = SmartConnect(api_key)
        totp = pyotp.TOTP(token).now()
        # data = smartApi.generateSession(username, pwd, totp)
        data = make_api_call(smartApi, 'generateSession', username, pwd, totp)
        if data['status'] == False:
            return {"success": False, "error": "data status is false"}
        else:
            fromdate_str = param.fromdate.strftime("%Y-%m-%d %H:%M")
            todate_str = param.todate.strftime("%Y-%m-%d %H:%M")
            if param.isSymbol:
                token, symbol, exch = getTokenFromAngelMaster(param.entity)
            else:
                token, symbol, exch = getTokenFromName(param.entity, threshold=int(threshold))
            if not token or not exch:
                raise Exception("No such company registered in NSE or BSE")
            historicParam={
                "exchange": exch,
                "symboltoken": token,
                "interval": param.interval.value,
                "fromdate": fromdate_str, 
                "todate": todate_str
            }
            candle_data = smartApi.getCandleData(historicParam)
            #return candle_data
            if len(candle_data["data"]) == 0 or len(candle_data["data"][0]) < 6:
                raise Exception("No candle data present for this stock and given time frame.")
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
        return {"success": False, "error": str(e)}



@app.post('/buyStockSLL')
async def buy_stock_sll(param: BuyStockSLL):
    try:
        load_dotenv()
        api_key = os.environ.get('api_key')
        username = os.environ.get('username')
        pwd = os.environ.get('pwd')
        token = os.environ.get('token')
        threshold = os.environ.get('threshold')
        
        smartApi = SmartConnect(api_key)
        totp = pyotp.TOTP(token).now()
        data = make_api_call(smartApi, 'generateSession', username, pwd, totp)
        symbol = None
        if data['status'] == False:
            return {"success": False, "error": "data status is false"}
        else:
            if param.isSymbol:
                token, symbol, exch = getTokenFromAngelMaster(param.entity)
            else:
                token, symbol, exch = getTokenFromName(param.entity, threshold=int(threshold))
            
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
            order_response = make_api_call(smartApi, 'placeOrder', orderparams)
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
        return {"success": False, "error": str(e)}


@app.post('/buyStockSLM')
async def buy_stock_slm(param: BuyStockSLM):
    try:
        load_dotenv()
        api_key = os.environ.get('api_key')
        username = os.environ.get('username')
        pwd = os.environ.get('pwd')
        token = os.environ.get('token')
        threshold = os.environ.get('threshold')
        
        smartApi = SmartConnect(api_key)
        totp = pyotp.TOTP(token).now()
        data = make_api_call(smartApi, 'generateSession', username, pwd, totp)
        symbol = None
        if data['status'] == False:
            return {"success": False, "error": "data status is false"}
        else:
            if param.isSymbol:
                token, symbol, exch = getTokenFromAngelMaster(param.entity)
            else:
                token, symbol, exch = getTokenFromName(param.entity, threshold=int(threshold))
            
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
            
            order_response = make_api_call(smartApi, 'placeOrder', orderparams)

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
        return {"success": False, "error": str(e)}


@app.post('/sellStockSLL')
async def sell_stock_sll(param: SellStockSLL):
    try:
        load_dotenv()
        api_key = os.environ.get('api_key')
        username = os.environ.get('username')
        pwd = os.environ.get('pwd')
        token = os.environ.get('token')
        threshold = os.environ.get('threshold')
        
        smartApi = SmartConnect(api_key)
        totp = pyotp.TOTP(token).now()
        data = make_api_call(smartApi, 'generateSession', username, pwd, totp)
        symbol = None
        if data['status'] == False:
            return {"success": False, "error": "data status is false"}
        else:
            if param.isSymbol:
                token, symbol, exch = getTokenFromAngelMaster(param.entity)
            else:
                token, symbol, exch = getTokenFromName(param.entity, threshold=int(threshold))
            
            if not token or not exch:
                raise Exception("No such company registered in NSE or BSE")
            
            holdings = smartApi.allholding()
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
            
            order_response = make_api_call(smartApi, 'placeOrder', orderparams)

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
                error_msg = order_response.get('message', 'Order placement failed') if isinstance(order_response, dict) else str(order_response)
                return {"success": False, "error": error_msg}
            
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post('/sellStockSLM')
async def sell_stock_slm(param: SellStockSLM):
    try:
        load_dotenv()
        api_key = os.environ.get('api_key')
        username = os.environ.get('username')
        pwd = os.environ.get('pwd')
        token = os.environ.get('token')
        threshold = os.environ.get('threshold')
        
        smartApi = SmartConnect(api_key)
        totp = pyotp.TOTP(token).now()
        data = make_api_call(smartApi, 'generateSession', username, pwd, totp)
        symbol = None
        if data['status'] == False:
            return {"success": False, "error": "data status is false"}
        else:
            if param.isSymbol:
                token, symbol, exch = getTokenFromAngelMaster(param.entity)
            else:
                token, symbol, exch = getTokenFromName(param.entity, threshold=int(threshold))
            if not token or not exch:
                raise Exception("No such company registered in NSE or BSE")
            
            holdings = smartApi.allholding()
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
            
            order_response = make_api_call(smartApi, 'placeOrder', orderparams)

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
                error_msg = order_response.get('message', 'Order placement failed') if isinstance(order_response, dict) else str(order_response)
                return {"success": False, "error": error_msg}
            
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post('/targetSell')
async def target_sell(param: TargetSell):
    try:
        load_dotenv()
        api_key = os.environ.get('api_key')
        username = os.environ.get('username')
        pwd = os.environ.get('pwd')
        token = os.environ.get('token')
        threshold = os.environ.get('threshold')
        
        smartApi = SmartConnect(api_key)
        totp = pyotp.TOTP(token).now()
        data = make_api_call(smartApi, 'generateSession', username, pwd, totp)
        symbol = None
        if data['status'] == False:
            return {"success": False, "error": "data status is false"}
        else:
            if param.isSymbol:
                token, symbol, exch = getTokenFromAngelMaster(param.entity)
            else:
                token, symbol, exch = getTokenFromName(param.entity, threshold=int(threshold))
            
            if not token or not exch:
                raise Exception("No such company registered in NSE or BSE")
            
            holdings = smartApi.allholding()
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
            
            order_response = make_api_call(smartApi, 'placeOrder', orderparams)

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
                error_msg = order_response.get('message', 'Order placement failed') if isinstance(order_response, dict) else str(order_response)
                return {"success": False, "error": error_msg}
            
    except Exception as e:
        return {"success": False, "error": str(e)}
    
@app.post('/cancelOrder')
async def cancel_order(param: CancelOrder):
    try:
        load_dotenv()
        api_key = os.environ.get('api_key')
        username = os.environ.get('username')
        pwd = os.environ.get('pwd')
        token = os.environ.get('token')
        
        smartApi = SmartConnect(api_key)
        totp = pyotp.TOTP(token).now()
        data = make_api_call(smartApi, 'generateSession', username, pwd, totp)
        if data['status'] == False:
            return {"success": False, "error": "data status is false"}
        else:
            cancel_response = make_api_call(smartApi, 'cancelOrder', param.order_id, param.variety)
            
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
        return {"success": False, "error": str(e)}

@app.get('/pendingOrders')
async def get_pending_orders():
    try:
        load_dotenv()
        api_key = os.environ.get('api_key')
        username = os.environ.get('username')
        pwd = os.environ.get('pwd')
        token = os.environ.get('token')
        
        smartApi = SmartConnect(api_key)
        totp = pyotp.TOTP(token).now()
        data = make_api_call(smartApi, 'generateSession', username, pwd, totp)
        
        if data['status'] == False:
            return {"success": False, "error": "data status is false"}
        else:
            orders = smartApi.orderBook()
            
            if not orders.get('status'):
                return {"success": False, "error": "Failed to fetch orders"}
            
            orders_list = orders.get("data", [])
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
