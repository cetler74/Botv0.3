from strategy.hyperliquid.consensus import (
    calculate_hyperliquid_consensus,
    select_recommended_strategy,
)


def test_consensus_long_majority():
    results = {
        "a": {"signal": "long", "confidence": 0.8, "strength": 0.7},
        "b": {"signal": "long", "confidence": 0.7, "strength": 0.6},
        "c": {"signal": "hold", "confidence": 0.0, "strength": 0.0},
    }
    c = calculate_hyperliquid_consensus(results, min_agreement=50)
    assert c["signal"] == "long"
    assert c["long_votes"] == 2


def test_consensus_short_majority():
    results = {
        "a": {"signal": "short", "confidence": 0.75, "strength": 0.7},
        "b": {"signal": "short", "confidence": 0.7, "strength": 0.65},
        "c": {"signal": "hold", "confidence": 0.0, "strength": 0.0},
    }
    c = calculate_hyperliquid_consensus(results, min_agreement=50)
    assert c["signal"] == "short"


def test_recommended_strategy():
    results = {
        "macd_momentum": {"signal": "long", "confidence": 0.9, "strength": 0.8, "state": {"indicators": {}}},
        "other": {"signal": "long", "confidence": 0.5, "strength": 0.4, "state": {"indicators": {}}},
    }
    rec = select_recommended_strategy(results, "long")
    assert rec["strategy"] == "macd_momentum"
    assert rec["side"] == "long"


def test_legacy_spot_terms_are_normalized_but_not_emitted():
    results = {
        "a": {"signal": "sell", "confidence": 0.75, "strength": 0.7},
        "b": {"signal": "sell", "confidence": 0.7, "strength": 0.65},
    }
    c = calculate_hyperliquid_consensus(results, min_agreement=50)
    assert c["signal"] == "short"
