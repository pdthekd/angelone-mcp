import pytest

from angel_one_mcp import server as s
from angel_one_mcp import type as t

NEW_TOOLS = {"get_positions", "get_rms_limit", "get_market_quote", "search_scrip", "estimate_charges"}


class FakeAPI:
    def __init__(self):
        self.seen = {}

    def position(self):
        return {"status": True, "data": None}

    def rmsLimit(self):
        return {"status": True, "data": {"availablecash": "100.0"}}

    def getMarketData(self, mode, tokens):
        self.seen["quote"] = (mode, tokens)
        return {"status": True, "data": {"fetched": [{"tradingSymbol": "SBIN-EQ", "ltp": 1.0}], "unfetched": []}}

    def _postRequest(self, route, params):
        self.seen[route] = params
        if route == "api.search.scrip":
            return {"status": True, "data": [{"tradingsymbol": "SBIN-EQ"}]}
        return {"status": True, "data": {"summary": {"total_charges": 1.5}}}


@pytest.fixture
def api(monkeypatch):
    a = FakeAPI()
    monkeypatch.setattr(s.session_manager, "get_api", lambda: a)
    monkeypatch.setattr(s, "getTokenFromAngelMaster", lambda e: ("3045", e, "NSE") if e != "NOPE" else (None, None, None))
    return a


@pytest.mark.asyncio
async def test_new_tools_registered():
    names = {x.name for x in await s.mcp.list_tools()}
    assert NEW_TOOLS <= names


@pytest.mark.asyncio
async def test_positions_empty_is_list(api):
    assert (await s.get_positions())["positions"] == []


@pytest.mark.asyncio
async def test_rms_limit(api):
    assert (await s.get_rms_limit())["funds"]["availablecash"] == "100.0"


@pytest.mark.asyncio
async def test_quote_groups_by_exchange_and_reports_unresolved(api):
    out = await s.get_market_quote(t.MarketQuoteRequest(entities=["SBIN-EQ", "NOPE"]))
    assert api.seen["quote"] == ("LTP", {"NSE": ["3045"]})
    assert out["quotes"][0]["ltp"] == 1.0
    assert out["unfetched"][0]["symbol"] == "NOPE"


@pytest.mark.asyncio
async def test_search_scrip(api):
    out = await s.search_scrip(t.SearchScripRequest(query="SBIN"))
    assert api.seen["api.search.scrip"] == {"exchange": "NSE", "searchscrip": "SBIN"}
    assert out["matches"][0]["tradingsymbol"] == "SBIN-EQ"


@pytest.mark.asyncio
async def test_estimate_charges_sends_string_fields_and_places_nothing(api):
    out = await s.estimate_charges(t.EstimateChargesRequest(
        orders=[t.ChargeOrder(entity="SBIN-EQ", transaction_type="BUY", quantity=10, price=800)]))
    sent = api.seen["api.estimateCharges"]["orders"][0]
    assert sent["quantity"] == "10" and sent["price"] == "800.0" and sent["token"] == "3045"
    assert out["charges"]["summary"]["total_charges"] == 1.5
