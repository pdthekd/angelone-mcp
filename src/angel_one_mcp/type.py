from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field

class Exchange(str, Enum):
    NSE = "NSE"
    BSE = "BSE"

class Interval(str, Enum):
    ONE_MINUTE = "ONE_MINUTE"
    THREE_MINUTE = "THREE_MINUTE"
    FIVE_MINUTE = "FIVE_MINUTE"
    TEN_MINUTE = "TEN_MINUTE"
    FIFTEEN_MINUTE = "FIFTEEN_MINUTE"
    THIRTY_MINUTE = "THIRTY_MINUTE"
    ONE_HOUR = "ONE_HOUR"
    ONE_DAY = "ONE_DAY"

class ProductType(str, Enum):
    INTRADAY = "INTRADAY"
    DELIVERY = "DELIVERY"

class StockInput(BaseModel):
    entity: str = Field(..., example="SBIN-EQ", description="Stock name or symbol to get data for")
    interval: Interval = Field(..., example="FIFTEEN_MINUTE", description="Interval for candlestick data")
    fromdate: datetime = Field(..., example="2021-02-08 09:00", description="Start date and time for data retrieval")
    todate: datetime = Field(..., example="2021-02-08 09:16", description="End date and time for data retrieval")
    isSymbol: bool = Field(..., example=True, description="If True, 'entity' is treated as symbol; otherwise as name")

class BuyStockSLL(BaseModel):
    entity: str = Field(..., example="SBIN-EQ", description="Stock name or symbol to buy")
    quantity: int = Field(..., gt=0, example=10, description="Quantity of stocks to buy")
    trigger_price: float = Field(..., gt=0, example=500.0, description="Trigger price for the stop-loss limit order")
    limit_price: float = Field(..., gt=0, example=505.0, description="Limit price for the stop-loss limit order")
    product_type: ProductType = Field(default=ProductType.DELIVERY, description="Product type for the order, e.g., INTRADAY or DELIVERY")
    isSymbol: bool = Field(default=True, description="If True, 'entity' is treated as symbol; otherwise as name")

class BuyStockSLM(BaseModel):
    entity: str = Field(..., example="SBIN-EQ", description="Stock name or symbol to buy")
    quantity: int = Field(..., gt=0, example=10, description="Quantity of stocks to buy")
    trigger_price: float = Field(..., gt=0, example=500.0, description="Trigger price for the stop-loss market order")
    product_type: ProductType = Field(default=ProductType.DELIVERY, description="Product type for the order, e.g., INTRADAY or DELIVERY")
    isSymbol: bool = Field(default=True, description="If True, 'entity' is treated as symbol; otherwise as name")

class SellStockSLL(BaseModel):
    entity: str = Field(..., example="SBIN-EQ", description="Stock name or symbol to sell")
    quantity: int = Field(..., gt=0, example=10, description="Quantity of stocks to sell")
    trigger_price: float = Field(..., gt=0, example=480.0, description="Trigger price for the stop-loss limit order")
    limit_price: float = Field(..., gt=0, example=475.0, description="Limit price for the stop-loss limit order")
    isSymbol: bool = Field(default=True, description="If True, 'entity' is treated as symbol; otherwise as name")
    sell_all: bool = Field(default=False, description="If True, sells all available quantity ignoring 'quantity' otherwise use quantity")

class SellStockSLM(BaseModel):
    entity: str = Field(..., example="SBIN-EQ", description="Stock name or symbol to sell")
    quantity: int = Field(..., gt=0, example=10, description="Quantity of stocks to sell")
    trigger_price: float = Field(..., gt=0, example=480.0, description="Trigger price for the stop-loss market order")
    isSymbol: bool = Field(default=True, description="If True, 'entity' is treated as symbol; otherwise as name")
    sell_all: bool = Field(default=False, description="If True, sells all available quantity ignoring 'quantity' otherwise use quantity")

class TargetSell(BaseModel):
    entity: str = Field(..., example="SBIN-EQ", description="Stock name or symbol to sell")
    quantity: int = Field(..., gt=0, example=10, description="Quantity of stocks to sell")
    target_price: float = Field(..., gt=0, example=550.0, description="Target price to sell the stock")
    isSymbol: bool = Field(default=True, description="If True, 'entity' is treated as symbol; otherwise as name")
    sell_all: bool = Field(default=False, description="If True, sells all available quantity ignoring 'quantity' otherwise use quantity")

class CancelOrder(BaseModel):
    order_id: str = Field(..., example="241008000012345", description="Order ID of the order to be cancelled")
    variety: str = Field(default="NORMAL", example="NORMAL", description="Variety of the order, e.g., NORMAL, AMO")

# New model used by approve_trade tool:
class ApproveTrade(BaseModel):
    request_id: str = Field(..., description="ID of pending trade intent to execute")
    approval_code: str = Field(..., description="Operator approval token: TOTP or HMAC signature")
