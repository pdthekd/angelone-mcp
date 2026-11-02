import pytest

from angel_one_mcp import server as s
from angel_one_mcp import type as t
from angel_one_mcp.utils import db as db_utils


@pytest.fixture(autouse=True)
def isolated_db(tmp_path):
    db_utils.init_db(str(tmp_path / "t.db"))


class FakeAPI:
    def __init__(self, held=9, live=()):
        self.held = held
        self.live = list(live)

    def allholding(self):
        return {"status": True, "data": {"holdings": [
            {"tradingsymbol": "NEOGEN-EQ", "exchange": "NSE", "quantity": self.held}]}}

    def gttLists(self, status, page, count):
        return {"status": True, "data": self.live}


def rule(qty):
    return {"id": "1", "tradingsymbol": "NEOGEN-EQ", "exchange": "NSE", "transactiontype": "SELL",
            "status": "NEW", "qty": qty, "price": 1, "triggerprice": 1}


@pytest.fixture
def setup(monkeypatch):
    def _go(api):
        monkeypatch.setattr(s.session_manager, "get_api", lambda: api)
        monkeypatch.setattr(s, "getTokenFromAngelMaster", lambda e: ("1", e, "NSE"))
    return _go


def sell(qty, **kw):
    return t.CreateGTTRule(entity="NEOGEN-EQ", transaction_type="SELL", quantity=qty, price=100, trigger_price=101, **kw)


@pytest.mark.asyncio
async def test_sell_more_than_held_is_blocked_even_with_allow_duplicate(setup):
    setup(FakeAPI(held=9))
    with pytest.raises(RuntimeError, match="only 9 held"):
        await s.create_gtt_rule(sell(10, allow_duplicate=True))


@pytest.mark.asyncio
async def test_sell_already_covered_by_live_rule_is_blocked(setup):
    setup(FakeAPI(held=9, live=[rule(9)]))
    with pytest.raises(RuntimeError, match="already covered"):
        await s.create_gtt_rule(sell(9, allow_duplicate=False))


@pytest.mark.asyncio
async def test_overlap_allowed_on_purpose_within_holdings(setup):
    setup(FakeAPI(held=9, live=[rule(9)]))
    assert "INTENT CREATED" in await s.create_gtt_rule(sell(9, allow_duplicate=True))


@pytest.mark.asyncio
async def test_sell_within_free_quantity_is_allowed(setup):
    setup(FakeAPI(held=9))
    assert "INTENT CREATED" in await s.create_gtt_rule(sell(9))
