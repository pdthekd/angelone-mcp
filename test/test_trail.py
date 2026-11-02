import pytest

from angel_one_mcp import server as s
from angel_one_mcp import type as t
from angel_one_mcp.utils import db as db_utils


@pytest.fixture(autouse=True)
def isolated_db(tmp_path):
    db_utils.init_db(str(tmp_path / "t.db"))


P = t.TrailGTTRules()


def rule(trigger, price, **kw):
    return {"triggerprice": trigger, "price": price, **kw}


def test_stop_loss_trails_up_and_keeps_offset():
    out = s._plan_trail(rule(900, 895), 1000, P)
    assert out["kind"] == "stop_loss" and out["new_trigger"] == 920.0
    assert out["new_price"] == 915.0  # offset -5 preserved


def test_stop_loss_never_moves_down():
    assert "skip" in s._plan_trail(rule(950, 945), 1000, P)  # 920 < 950


def test_target_untouched_when_far():
    assert "skip" in s._plan_trail(rule(1200, 1205), 1000, P)


def test_target_raised_when_close():
    out = s._plan_trail(rule(1000, 1005), 990, P)  # within 2%
    assert out["kind"] == "target" and out["new_trigger"] == 1039.5 and out["new_price"] == 1044.5


class FakeAPI:
    def gttLists(self, status, page, count):
        return {"status": True, "data": [
            {"id": 1, "tradingsymbol": "A-EQ", "exchange": "NSE", "symboltoken": "11", "transactiontype": "SELL",
             "triggerprice": 900, "price": 895, "qty": 5, "disclosedqty": 0},
            {"id": 2, "tradingsymbol": "B-EQ", "exchange": "NSE", "symboltoken": "22", "transactiontype": "BUY",
             "triggerprice": 10, "price": 10, "qty": 1}]}

    def getMarketData(self, mode, tokens):
        return {"status": True, "data": {"fetched": [{"exchange": "NSE", "symbolToken": "11", "ltp": 1000}]}}

    def gttModifyRule(self, *a):
        raise AssertionError("must not execute")


@pytest.mark.asyncio
async def test_default_only_plans_no_intents(monkeypatch):
    monkeypatch.setattr(s.session_manager, "get_api", lambda: FakeAPI())
    out = await s.trail_gtt_rules(t.TrailGTTRules())
    assert [p["rule_id"] for p in out["proposals"]] == ["1"]  # BUY rule ignored
    assert "request_id" not in out["proposals"][0]
    assert db_utils.list_pending_intents() == []


@pytest.mark.asyncio
async def test_create_intents_then_no_duplicates(monkeypatch):
    monkeypatch.setattr(s.session_manager, "get_api", lambda: FakeAPI())
    out = await s.trail_gtt_rules(t.TrailGTTRules(create_intents=True))
    rid = out["proposals"][0]["request_id"]
    intent = db_utils.get_trade_intent(rid)
    assert intent["status"] == "PENDING" and intent["order_params"]["triggerprice"] == 920.0
    again = await s.trail_gtt_rules(t.TrailGTTRules(create_intents=True))
    assert again["proposals"] == [] and "already pending" in again["skipped"][0]["reason"]
