"""Unit tests for HL asset class inference."""

from strategy.hyperliquid.asset_class import infer_hl_asset_class


def test_crypto_coin():
    assert infer_hl_asset_class("WLD") == "crypto"
    assert infer_hl_asset_class("BTC") == "crypto"


def test_tradfi_indices():
    assert infer_hl_asset_class("xyz:SP500") == "indices"


def test_tradfi_fx():
    assert infer_hl_asset_class("xyz:EUR") == "fx"
