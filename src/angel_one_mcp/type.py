# src/angel_one_mcp/type.py
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
    entity: str = Field(..., description="Stock name or symbol to get data for", json_schema_extra={"example": "SBIN-EQ"})
    interval: Interval = Field(..., description="Interval for candlestick data", json_schema_extra={"example": "FIFTEEN_MINUTE"})
    fromdate: datetime = Field(..., description="Start date and time for data retrieval", json_schema_extra={"example": "2021-02-08 09:00"})
    todate: datetime = Field(..., description="End date and time for data retrieval", json_schema_extra={"example": "2021-02-08 09:16"})
    isSymbol: bool = Field(..., description="If True, 'entity' is treated as symbol; otherwise as name", json_schema_extra={"example": True})

class BuyStockSLL(BaseModel):
    entity: str = Field(..., description="Stock name or symbol to buy", json_schema_extra={"example": "SBIN-EQ"})
    quantity: int = Field(..., gt=0, description="Quantity of stocks to buy", json_schema_extra={"example": 10})
    trigger_price: float = Field(..., gt=0, description="Trigger price for the stop-loss limit order", json_schema_extra={"example": 500.0})
    limit_price: float = Field(..., gt=0, description="Limit price for the stop-loss limit order", json_schema_extra={"example": 505.0})
    product_type: ProductType = Field(default=ProductType.DELIVERY, description="Product type for the order, e.g., INTRADAY or DELIVERY")
    isSymbol: bool = Field(default=True, description="If True, 'entity' is treated as symbol; otherwise as name")

class BuyStockSLM(BaseModel):
    entity: str = Field(..., description="Stock name or symbol to buy", json_schema_extra={"example": "SBIN-EQ"})
    quantity: int = Field(..., gt=0, description="Quantity of stocks to buy", json_schema_extra={"example": 10})
    trigger_price: float = Field(..., gt=0, description="Trigger price for the stop-loss market order", json_schema_extra={"example": 500.0})
    product_type: ProductType = Field(default=ProductType.DELIVERY, description="Product type for the order, e.g., INTRADAY or DELIVERY")
    isSymbol: bool = Field(default=True, description="If True, 'entity' is treated as symbol; otherwise as name")

class SellStockSLL(BaseModel):
    entity: str = Field(..., description="Stock name or symbol to sell", json_schema_extra={"example": "SBIN-EQ"})
    quantity: int = Field(..., gt=0, description="Quantity of stocks to sell", json_schema_extra={"example": 10})
    trigger_price: float = Field(..., gt=0, description="Trigger price for the stop-loss limit order", json_schema_extra={"example": 480.0})
    limit_price: float = Field(..., gt=0, description="Limit price for the stop-loss limit order", json_schema_extra={"example": 475.0})
    isSymbol: bool = Field(default=True, description="If True, 'entity' is treated as symbol; otherwise as name")
    sell_all: bool = Field(default=False, description="If True, sells all available quantity ignoring 'quantity' otherwise use quantity")

class SellStockSLM(BaseModel):
    entity: str = Field(..., description="Stock name or symbol to sell", json_schema_extra={"example": "SBIN-EQ"})
    quantity: int = Field(..., gt=0, description="Quantity of stocks to sell", json_schema_extra={"example": 10})
    trigger_price: float = Field(..., gt=0, description="Trigger price for the stop-loss market order", json_schema_extra={"example": 480.0})
    isSymbol: bool = Field(default=True, description="If True, 'entity' is treated as symbol; otherwise as name")
    sell_all: bool = Field(default=False, description="If True, sells all available quantity ignoring 'quantity' otherwise use quantity")

class TargetSell(BaseModel):
    entity: str = Field(..., description="Stock name or symbol to sell", json_schema_extra={"example": "SBIN-EQ"})
    quantity: int = Field(..., gt=0, description="Quantity of stocks to sell", json_schema_extra={"example": 10})
    target_price: float = Field(..., gt=0, description="Target price to sell the stock", json_schema_extra={"example": 550.0})
    isSymbol: bool = Field(default=True, description="If True, 'entity' is treated as symbol; otherwise as name")
    sell_all: bool = Field(default=False, description="If True, sells all available quantity ignoring 'quantity' otherwise use quantity")

class CancelOrder(BaseModel):
    order_id: str = Field(..., description="Order ID of the order to be cancelled", json_schema_extra={"example": "241008000012345"})
    variety: str = Field(default="NORMAL", description="Variety of the order, e.g., NORMAL, AMO", json_schema_extra={"example": "NORMAL"})

# ApproveTrade model kept for compatibility (not required by server tool which accepts primitive args)
class ApproveTrade(BaseModel):
    request_id: str = Field(..., description="ID of pending trade intent to execute")
    approval_code: str = Field(..., description="Operator approval token: TOTP or HMAC signature")
