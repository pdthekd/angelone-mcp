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
    rid = server_module._create_intent_and_audit("placeOrder", {"tradingsymbol": "X"}, {"type": "buy"})
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
