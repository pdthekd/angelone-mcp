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
    entity: str
    interval: Interval
    fromdate: datetime = Field(..., example="2021-02-08 09:00")
    todate: datetime = Field(..., example="2021-02-08 09:16")
    isSymbol: bool = Field(..., example=True)


class BuyStockSLL(BaseModel):
    entity: str = Field(..., example="SBIN-EQ")
    quantity: int = Field(..., gt=0, example=10)
    trigger_price: float = Field(..., gt=0, example=500.0)
    limit_price: float = Field(..., gt=0, example=505.0)
    product_type: ProductType = Field(default=ProductType.DELIVERY)
    isSymbol: bool = Field(default=True)

class BuyStockSLM(BaseModel):
    entity: str = Field(..., example="SBIN-EQ")
    quantity: int = Field(..., gt=0, example=10)
    trigger_price: float = Field(..., gt=0, example=500.0)
    product_type: ProductType = Field(default=ProductType.DELIVERY)
    isSymbol: bool = Field(default=True)

class SellStockSLL(BaseModel):
    entity: str = Field(..., example="SBIN-EQ")
    quantity: int = Field(..., gt=0, example=10)
    trigger_price: float = Field(..., gt=0, example=480.0)
    limit_price: float = Field(..., gt=0, example=475.0)
    isSymbol: bool = Field(default=True)
    sell_all: bool = Field(default=False, description="If True, sells all available quantity")

class SellStockSLM(BaseModel):
    entity: str = Field(..., example="SBIN-EQ")
    quantity: int = Field(..., gt=0, example=10)
    trigger_price: float = Field(..., gt=0, example=480.0)
    isSymbol: bool = Field(default=True)
    sell_all: bool = Field(default=False, description="If True, sells all available quantity")

class TargetSell(BaseModel):
    entity: str = Field(..., example="SBIN-EQ")
    quantity: int = Field(..., gt=0, example=10)
    target_price: float = Field(..., gt=0, example=550.0)
    isSymbol: bool = Field(default=True)
    sell_all: bool = Field(default=False, description="If True, sells all available quantity")

class CancelOrder(BaseModel):
    order_id: str = Field(..., example="241008000012345")
    variety: str = Field(default="NORMAL", example="NORMAL")