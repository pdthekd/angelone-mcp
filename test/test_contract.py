"""Compatibility guard for existing consumers.

Scheduled tasks (Wealth OS daily/weekly/monthly refresh) call the `current_holdings`
tool and parse its payload. Other sessions call the tools listed below by name.
New work may ADD tools; it must not remove or rename these, or change the
current_holdings payload shape.
"""
import pytest

from angel_one_mcp import server as server_module

FROZEN_TOOLS = {
    "approve_trade", "buy_stock_sll", "buy_stock_slm", "cancel_gtt_rule", "cancel_order",
    "create_gtt_rule", "current_holdings", "discard_trade_intent", "get_audit_history",
    "get_exchanges", "get_gtt_rule_details", "get_gtt_rules", "get_ltp", "get_order_history",
    "get_order_status", "get_pending_orders", "get_portfolio_analysis", "get_stock_details",
    "get_trade_book", "get_trade_intent_status", "modify_gtt_rule",
    "request_batch_trade_approval", "request_trade_approval", "sell_stock_sll",
    "sell_stock_slm", "target_sell",
}


@pytest.mark.asyncio
async def test_existing_tools_still_registered():
    names = {t.name for t in await server_module.mcp.list_tools()}
    assert FROZEN_TOOLS <= names, f"missing: {sorted(FROZEN_TOOLS - names)}"


@pytest.mark.asyncio
async def test_current_holdings_payload_shape(monkeypatch):
    class FakeAPI:
        def allholding(self):
            return {"status": True, "data": {
                "holdings": [{"tradingsymbol": "ALPHA-EQ", "exchange": "NSE", "quantity": 10,
                              "averageprice": 100.0, "ltp": 150.0, "profitandloss": 500.0,
                              "pnlpercentage": 50.0, "product": "DELIVERY"}],
                "totalholding": {"totalholdingvalue": 1500, "totalinvvalue": 1000,
                                 "totalprofitandloss": 500, "totalpnlpercentage": 50},
            }}

    monkeypatch.setattr(server_module.session_manager, "get_api", lambda: FakeAPI())
    out = await server_module.current_holdings()
    assert out["success"] is True
    assert set(out["holdings"][0]) == {
        "symbol", "exchange", "quantity", "avg_price", "ltp", "pnl", "pnl_percentage", "product"}
    assert set(out["portfolio_summary"]) == {
        "total_value", "invested_value", "total_pnl", "total_pnl_percentage"}
