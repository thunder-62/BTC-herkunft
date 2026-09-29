"""Ensure all package modules import cleanly."""

from __future__ import annotations


def test_import_all_modules() -> None:
    import btc_origin
    import btc_origin.config
    import btc_origin.db
    import btc_origin.wallet_registry
    import btc_origin.cloud_summary
    import btc_origin.hd_deriver
    import btc_origin.electrum_client
    import btc_origin.tx_ingestor
    import btc_origin.sync_pipeline
    import btc_origin.merger
    import btc_origin.internal_transfer_tagger
    import btc_origin.trace_engine
    import btc_origin.label_service
    import btc_origin.holding_clock
    import btc_origin.price_oracle
    import btc_origin.report_builder
    import btc_origin.api.app

    assert btc_origin.__version__ == "0.1.0"
    assert btc_origin.db.is_memory_only() is True
