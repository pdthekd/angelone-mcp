# tests/test_security.py
import os
import pytest
import asyncio
import json
import hmac
import hashlib

from datetime import datetime

# ensure tests import from correct package path
from src.angel_one_mcp import server as server_module
from src.angel_one_mcp.utils import db as db_utils
from src.angel_one_mcp.utils import sanitizer
from src.angel_one_mcp import type as types_module
from src.angel_one_mcp.utils import getTokens

# fixtures


@pytest.fixture(autouse=True)
def isolate_env(tmp_path, monkeypatch):
    # set a temporary DB path per test
    db_path = str(tmp_path / "trades_test.db")
    monkeypatch.setenv("TRADE_DB_PATH", db_path)
    # set HUMAN_APPROVAL_PIN default
    monkeypatch.setenv("HUMAN_APPROVAL_PIN", "9999")
    # ensure DB initialized
    db_utils.init_db(db_path)
    yield
    # cleanup handled by tmp_path


@pytest.mark.asyncio
async def test_direct_execution_disabled(monkeypatch):
    """
    Verify buy tool creates a PENDING intent and does not call placeOrder directly.
    """
    # monkeypatch session_manager.get_api to avoid network
    class DummyAPI:
        def placeOrder(self, params):
            raise RuntimeError("should not be called during intent creation")

    monkeypatch.setattr(server_module.session_manager, "get_api", lambda: DummyAPI())
    # Create a param instance
    param = types_module.BuyStockSLL(
        entity="SBIN-EQ",
        quantity=1,
        trigger_price=100.0,
        limit_price=101.0,
        product_type=types_module.ProductType.DELIVERY,
        isSymbol=True,
    )
    # Call buy tool
    res = await server_module.buy_stock_sll(param)
    assert isinstance(res, str)
    assert "TRADE INTENT CREATED" in res
    request_id = res.split("ID:")[1].split(".")[0].strip()
    row = db_utils.get_trade_intent(request_id)
    assert row is not None
    assert row["status"] == "PENDING"


@pytest.mark.asyncio
async def test_operator_auth(monkeypatch):
    """
    Verify approve_trade fails with bad PIN and succeeds with the right PIN.
    """
    # Prepare dummy API to return successful order
    class DummyAPI:
        def placeOrder(self, params):
            return {"status": True, "data": {"orderid": "ORD-123"}}

    monkeypatch.setattr(server_module.session_manager, "get_api", lambda: DummyAPI())

    # create intent directly in DB
    request_id = "req12345"
    order_params = {"tradingsymbol": "SBIN-EQ", "quantity": 1}
    db_utils.insert_trade_intent(request_id, order_params, {"type": "buy_stock_sll"})
    db_utils.log_audit_event("test_setup", {"request_id": request_id})

    # Wrong PIN
    with pytest.raises(Exception):
        await server_module.approve_trade(request_id, "0000")

    # Correct PIN
    result = await server_module.approve_trade(request_id, "9999")
    assert isinstance(result, dict)
    assert result.get("success", True) is True or "order_id" in result
    # verify DB updated
    row = db_utils.get_trade_intent(request_id)
    assert row["status"] == "EXECUTED"
    assert row["execution_result"] is not None


def test_fuzzy_resolution_safety(monkeypatch):
    """
    Verify getTokenFromName does not auto-resolve low-confidence matches.
    We'll monkeypatch the fuzzy candidate functions to produce low top scores.
    """
    # monkeypatch candidate fetchers to return low scores
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
    # check redaction of known keys
    assert redacted["user"]["clientCode"] == "<REDACTED>" or redacted["user"].get("clientcode") == "<REDACTED>"
    assert redacted["user"]["tokens"]["refreshToken"] == "<REDACTED>" or redacted["user"]["tokens"].get("refreshtoken") == "<REDACTED>"
    assert redacted["list"][0]["pin"] == "<REDACTED>"
    assert redacted["api_key"] == "<REDACTED>"
