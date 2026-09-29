"""Parse a real-ish P2WPKH tx hex into inflow flows."""

from __future__ import annotations

from embit import bip32
from embit.networks import NETWORKS
from embit.script import p2wpkh
from embit.transaction import Transaction, TransactionInput, TransactionOutput

from btc_origin.hd_deriver import normalize_extended_public_key
from btc_origin.tx_ingestor import TxIngestor, _tx_hex_to_flows

BIP84_ZPUB = (
    "zpub6rFR7y4Q2AijBEqTUquhVz398htDFrtymD9xYYfG1m4wAcvPhXNfE3EfH1r1A"
    "DqtfSdVCToUG868RvUUkgDKf31mGDtKsAYz2oz2AGutZYs"
)


def _build_pay_to_address_tx(address: str, amount: int = 50_000) -> tuple[str, str]:
    """Build a minimal unsigned-looking tx with one output to address."""
    from embit.script import address_to_scriptpubkey

    spk = address_to_scriptpubkey(address)
    # dummy prevout
    vin = [
        TransactionInput(
            txid=bytes.fromhex("11" * 32),
            vout=0,
        )
    ]
    vout = [TransactionOutput(amount, spk)]
    tx = Transaction(vin=vin, vout=vout)
    txid = tx.txid().hex()
    return tx.to_string(), txid


def test_parse_inflow_from_tx_hex() -> None:
    norm = normalize_extended_public_key(BIP84_ZPUB)
    hd = bip32.HDKey.from_string(norm.normalized)
    addr = p2wpkh(hd.derive([0, 0])).address(NETWORKS["main"])
    assert addr.startswith("bc1q")

    tx_hex, txid = _build_pay_to_address_tx(addr, 12345)
    owned: dict = {}
    flows = _tx_hex_to_flows(
        tx_hex,
        address=addr,
        wallet_id=1,
        height=700000,
        block_time="2024-01-15T12:00:00Z",
        owned_outpoints=owned,
    )
    assert len(flows) == 1
    assert flows[0].direction == "in"
    assert flows[0].amount_sats == 12345
    assert flows[0].txid == txid
    assert flows[0].block_time == "2024-01-15T12:00:00Z"
    assert f"{txid}:0" in owned


def test_ingestor_ingest_tx_dict() -> None:
    ing = TxIngestor()
    flows = ing.ingest_tx(
        {
            "txid": "ab" * 32,
            "direction": "in",
            "amount_sats": 100,
            "address": "bc1qtest",
            "block_time": "2024-06-01T00:00:00Z",
        },
        wallet_id=2,
    )
    assert flows[0].wallet_id == 2
    assert flows[0].amount_sats == 100


def test_outpoint_not_discarded_for_other_owned_address() -> None:
    """Outpoint belonging to another owned address must stay until that address parses."""
    norm = normalize_extended_public_key(BIP84_ZPUB)
    hd = bip32.HDKey.from_string(norm.normalized)
    addr_a = p2wpkh(hd.derive([0, 0])).address(NETWORKS["main"])
    addr_b = p2wpkh(hd.derive([0, 1])).address(NETWORKS["main"])

    # First: create an output to addr_a (records owned outpoint).
    tx_hex_in, txid_in = _build_pay_to_address_tx(addr_a, 50_000)
    owned: dict = {}
    flows_a_in = _tx_hex_to_flows(
        tx_hex_in,
        address=addr_a,
        wallet_id=1,
        height=1,
        block_time="2024-01-01T00:00:00Z",
        owned_outpoints=owned,
    )
    assert len(flows_a_in) == 1
    key = f"{txid_in}:0"
    assert key in owned

    # Build a spend of that outpoint paying addr_b (same session, other address).
    from embit.script import address_to_scriptpubkey

    spk_b = address_to_scriptpubkey(addr_b)
    vin = [
        TransactionInput(
            txid=bytes.fromhex(txid_in),
            vout=0,
        )
    ]
    vout = [TransactionOutput(49_000, spk_b)]
    spend = Transaction(vin=vin, vout=vout)
    spend_hex = spend.to_string()

    # Parse as addr_b first (wrong address for the outpoint owner).
    flows_b = _tx_hex_to_flows(
        spend_hex,
        address=addr_b,
        wallet_id=1,
        height=2,
        block_time="2024-01-02T00:00:00Z",
        owned_outpoints=owned,
    )
    # Inflow to B recorded; outpoint for A must NOT be discarded.
    assert any(f.direction == "in" for f in flows_b)
    assert key in owned

    # Now parse as addr_a — out flow must appear.
    flows_a_out = _tx_hex_to_flows(
        spend_hex,
        address=addr_a,
        wallet_id=1,
        height=2,
        block_time="2024-01-02T00:00:00Z",
        owned_outpoints=owned,
    )
    assert any(f.direction == "out" and f.amount_sats == 50_000 for f in flows_a_out)
    assert key not in owned
    # Fee helper: total outputs stamped on flows
    assert flows_a_out[0].tx_total_output_sats == 49_000
