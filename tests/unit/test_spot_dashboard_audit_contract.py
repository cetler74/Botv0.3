from pathlib import Path


SPOT_JS = Path("services/web-dashboard-service/static/js/spot-dashboard.js")
SPOT_HTML = Path("services/web-dashboard-service/templates/spot_dashboard.html")
MAIN_PY = Path("services/web-dashboard-service/main.py")
PORTFOLIO_JS = Path("services/web-dashboard-service/static/js/portfolio-dashboard.js")
PORTFOLIO_HTML = Path("services/web-dashboard-service/templates/portfolio_dashboard.html")


def test_spot_dashboard_filters_audit_by_spot_venues():
    main_py = MAIN_PY.read_text()
    assert "_fetch_spot_strategy_audit" in main_py
    assert 'params={"hours": hours, "limit": limit_per_venue, "venue": venue}' in main_py
    assert "get_spot_intelligence" in main_py
    assert "_fetch_hl_strategy_audit" in main_py


def test_spot_dashboard_shows_exchange_column_for_ema50():
    js = SPOT_JS.read_text()
    html = SPOT_HTML.read_text()

    assert "formatAuditVenue" in js
    assert "auditExchangeCell" in js
    assert "spot exchanges only" in html
    assert "<th>Exchange</th>" in html


def test_portfolio_ema50_audit_shows_entry_block_column():
    js = PORTFOLIO_JS.read_text()
    html = PORTFOLIO_HTML.read_text()
    main_py = MAIN_PY.read_text()

    assert "enrich_ema50_audit_entry_blocks" in main_py
    assert "ema50EntryBlockLabel" in js
    assert "entry_block_reason" in js
    assert "<th>Entry block</th>" in html
