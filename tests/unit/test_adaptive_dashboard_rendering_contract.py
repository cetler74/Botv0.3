from pathlib import Path


JS_PATH = Path("services/web-dashboard-service/static/js/portfolio-dashboard.js")
HTML_PATH = Path("services/web-dashboard-service/templates/portfolio_dashboard.html")


def test_dashboard_renderer_surfaces_history_and_apply_status():
    js = JS_PATH.read_text()

    assert "adaptiveAuditRows" in js
    assert "applyStatus" in js
    assert "applied live" in js
    assert "pending cycle" in js
    assert "pending reload" in js
    assert "apply failed" in js
    assert "releaseReason" in js
    assert "intendedEffect" in js
    assert "control.error" in js


def test_dashboard_template_has_reload_status_target():
    html = HTML_PATH.read_text()

    assert 'id="adaptive-reload-status"' in html
