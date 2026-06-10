from pathlib import Path


def test_adaptive_apply_script_verifies_all_live_surfaces():
    script = Path("scripts/apply_adaptive_pnl_config.sh")

    assert script.exists()
    text = script.read_text()
    assert "./scripts/apply_config.sh" in text
    assert "/api/v1/config/trading" in text
    assert "/api/v1/perps/adaptive-pnl-control" in text
    assert "/api/v1/dashboard/portfolio-intelligence" in text
    assert "adaptive_pnl_control" in text
    assert "WEB_DASHBOARD_JSON" not in text
    assert "urllib.request.urlopen" in text
    assert "pending reload" in text
    assert "applied live" in text
    assert "apply failed" in text
