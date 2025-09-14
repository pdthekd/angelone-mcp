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

class StockInput(BaseModel):
    symbol: str
    interval: Interval
    fromdate: datetime = Field(..., example="2021-02-08 09:00")
    todate: datetime = Field(..., example="2021-02-08 09:16")