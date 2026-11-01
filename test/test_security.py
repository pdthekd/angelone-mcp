# test/test_security.py
import os
import pytest
import asyncio
import json

from src.angel_one_mcp import server as server_module
from src.angel_one_mcp.utils import db as db_utils
from src.angel_one_mcp.utils import sanitizer
from src.angel_one_mcp import type as types_module
from src.angel_one_mcp.utils import getTokens

@pytest.fixture(autouse=True)
def isolate_env(tmp_path, monkeypatch):
    db_path = str(tmp_path / "trades_test.db")
    monkeypatch.setenv("TRADE_DB_PATH", db_path)
    monkeypatch.setenv("HUMAN_APPROVAL_PIN", "9999")
    # do not enforce allowed operators by default
    monkeypatch.delenv("ALLOWED_OPERATORS", raising=False)
    db_utils.init_db(db_path)
    yield

@pytest.mark.asyncio
async def test_direct_execution_disabled(monkeypatch):
    class DummyAPI:
        def placeOrder(self, params):
            raise RuntimeError("should not be called during intent creation")

    monkeypatch.setattr(server_module.session_manager, "get_api", lambda: DummyAPI())

    param = types_module.BuyStockSLL(
        entity="SBIN-EQ",
        quantity=1,
        trigger_price=100.0,
        limit_price=101.0,
        product_type=types_module.ProductType.DELIVERY,
        isSymbol=True,
    )
    res = await server_module.buy_stock_sll(param)
    assert isinstance(res, str)
    assert "TRADE INTENT CREATED" in res
    request_id = res.split("ID:")[1].split(".")[0].strip()
    row = db_utils.get_trade_intent(request_id)
    assert row is not None
    assert row["status"] == "PENDING"

def test_fuzzy_resolution_safety(monkeypatch):
    def fake_fuzzy_nse(normalized_name, original_name, limit=10):
        return [{"symbol": "FAKE", "name": "Fake Corp", "score": 91}]
    def fake_fuzzy_bse(normalized_name, original_name, limit=10):
        return []
    monkeypatch.setattr(getTokens, "_fuzzy_candidates_nse", fake_fuzzy_nse)
    monkeypatch.setattr(getTokens, "_fuzzy_candidates_bse", fake_fuzzy_bse)
    with pytest.raises(ValueError):
        getTokens.getTokenFromName("Ambiguous Name", threshold=98)

def test_data_redaction():
    data = {
        "user": {
            "clientCode": "ABC123",
            "name": "Alice",
            "tokens": {
                "refreshToken": "abcd" * 10,
                "jwtToken": "xyz" * 10
            }
        },
        "list": [
            {"pin": "123456"},
            {"normal": "value"}
        ],
        "api_key": "AKIA" + "X" * 30
    }
    redacted = sanitizer.redact_sensitive_keys(data)
    assert redacted["user"]["clientCode"] == "<REDACTED>" or redacted["user"].get("clientcode") == "<REDACTED>"
    assert redacted["user"]["tokens"]["refreshToken"] == "<REDACTED>" or redacted["user"]["tokens"].get("refreshtoken") == "<REDACTED>"
    assert redacted["list"][0]["pin"] == "<REDACTED>"
    assert redacted["api_key"] == "<REDACTED>"