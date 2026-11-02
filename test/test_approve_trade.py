import pytest

from angel_one_mcp import server as server_module
from angel_one_mcp.utils import db as db_utils


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_utils.init_db(str(tmp_path / "t.db"))
    monkeypatch.delenv("ALLOWED_OPERATORS", raising=False)
    monkeypatch.delenv("APPROVAL_TTL_SECONDS", raising=False)


class FakeAPI:
    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    def placeOrder(self, params):
        self.calls += 1
        return self.reply


def approved_intent(api, monkeypatch):
    monkeypatch.setattr(server_module.session_manager, "get_api", lambda: api)
    rid = server_module._create_intent_and_audit("placeOrder", {"tradingsymbol": "X"}, {"type": "buy_stock_sll"})
    assert db_utils.mark_human_approved(rid)
    return rid


@pytest.mark.asyncio
async def test_rejected_order_is_not_recorded_as_executed(monkeypatch):
    api = FakeAPI({"status": False, "message": "Insufficient funds", "errorcode": "AB1004"})
    rid = approved_intent(api, monkeypatch)
    with pytest.raises(RuntimeError):
        await server_module.approve_trade(rid, "me")
    assert db_utils.get_trade_intent(rid)["status"] == "FAILED"


@pytest.mark.asyncio
async def test_second_approve_does_not_place_order_twice(monkeypatch):
    api = FakeAPI("ORDER123")
    rid = approved_intent(api, monkeypatch)
    assert (await server_module.approve_trade(rid, "me"))["order_id"] == "ORDER123"
    with pytest.raises(RuntimeError):
        await server_module.approve_trade(rid, "me")
    assert api.calls == 1


@pytest.mark.asyncio
async def test_expired_approval_is_refused_and_reset(monkeypatch):
    api = FakeAPI("ORDER123")
    rid = approved_intent(api, monkeypatch)
    monkeypatch.setenv("APPROVAL_TTL_SECONDS", "-1")
    with pytest.raises(RuntimeError, match="expired"):
        await server_module.approve_trade(rid, "me")
    assert api.calls == 0
    assert db_utils.get_trade_intent(rid)["status"] == "PENDING"


class ModifyAPI:
    def __init__(self, status="open"):
        self.status = status
        self.modified = None
        self.placed = 0

    def orderBook(self):
        return {"status": True, "data": [{
            "orderid": "O1", "status": self.status, "variety": "STOPLOSS", "ordertype": "STOPLOSS_LIMIT",
            "producttype": "DELIVERY", "duration": "DAY", "price": 100, "quantity": 5, "triggerprice": 99,
            "tradingsymbol": "X-EQ", "symboltoken": "1", "exchange": "NSE"}]}

    def modifyOrder(self, params):
        self.modified = params
        return {"status": True, "data": {"orderid": "O1"}}

    def placeOrder(self, params):
        self.placed += 1


@pytest.mark.asyncio
async def test_modify_order_fills_unchanged_fields_and_never_places(monkeypatch):
    from angel_one_mcp import type as t
    api = ModifyAPI()
    monkeypatch.setattr(server_module.session_manager, "get_api", lambda: api)
    msg = await server_module.modify_order(t.ModifyOrder(order_id="O1", price=101.5))
    rid = msg.split("ID:")[1].split(".")[0].strip()
    assert api.modified is None  # intent only
    intent = db_utils.get_trade_intent(rid)
    assert intent["order_params"]["price"] == 101.5
    assert intent["order_params"]["quantity"] == 5
    assert intent["order_params"]["triggerprice"] == 99
    db_utils.mark_human_approved(rid)
    await server_module.approve_trade(rid, "me")
    assert api.modified["orderid"] == "O1" and api.placed == 0


@pytest.mark.asyncio
async def test_modify_order_refuses_closed_order_and_empty_change(monkeypatch):
    from angel_one_mcp import type as t
    monkeypatch.setattr(server_module.session_manager, "get_api", lambda: ModifyAPI("complete"))
    with pytest.raises(RuntimeError, match="only open orders"):
        await server_module.modify_order(t.ModifyOrder(order_id="O1", price=1))
    with pytest.raises(RuntimeError, match="Nothing to change"):
        await server_module.modify_order(t.ModifyOrder(order_id="O1"))


@pytest.mark.asyncio
async def test_unknown_intent_type_is_refused_not_placed(monkeypatch):
    api = FakeAPI("ORDER123")
    monkeypatch.setattr(server_module.session_manager, "get_api", lambda: api)
    rid = server_module._create_intent_and_audit("mystery", {"x": 1}, {"type": "something_new"})
    db_utils.mark_human_approved(rid)
    with pytest.raises(RuntimeError, match="Unknown intent type"):
        await server_module.approve_trade(rid, "me")
    assert api.calls == 0
    assert db_utils.get_trade_intent(rid)["status"] == "FAILED"
