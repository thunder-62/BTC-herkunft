"""Label service — Git label packs + never silent cost basis 0 + no session disk write."""

from __future__ import annotations

import json
from pathlib import Path

from btc_origin.label_service import (
    STATUS_PROOF_MISSING,
    STATUS_PROOF_MISSING_SOURCE_UNREACHABLE,
    LabelService,
    default_pack_json,
    resolve_label_packs_dir,
)
from btc_origin.tx_ingestor import Flow


def test_pack_contains_insolvency_clusters() -> None:
    svc = LabelService()
    for key in ("ftx", "mtgox", "celsius", "blockfi", "voyager"):
        assert svc.lookup_entity(key) is not None
    assert svc.lookup_entity("mtgox") == "Mt.Gox"


def test_loads_real_packs_from_repo_data_dir() -> None:
    packs = resolve_label_packs_dir()
    assert packs is not None
    assert (packs / "ftx.json").is_file()
    assert (packs / "mtgox.json").is_file()
    svc = LabelService()
    assert any(n.endswith(".json") for n in svc.packs_loaded)
    # Real Mt.Gox proof-of-solvency address
    a = svc.label_address("1eHhgW6vquBYhwMPhQ668HPjxTtpvZGPC")
    assert a is not None
    assert a.label == "Mt.Gox"
    assert a.cost_basis is None
    assert a.status == STATUS_PROOF_MISSING_SOURCE_UNREACHABLE
    # Real FTX.US source from TokenScope Nov 2022 outflow research
    ftx = svc.label_address("bc1q8umytnw6dh6f909c9r53ae4n08uqmtyw88v0ge")
    assert ftx is not None
    assert ftx.label == "FTX"
    assert ftx.cost_basis is None
    # Celsius — CoinDesk-cited company BTC donation wallet
    cel = svc.label_address("bc1q49aukmruj3edwtktsdgtkqgu87j9gsvaw5k2xc")
    assert cel is not None
    assert cel.label == "Celsius"
    assert cel.status == STATUS_PROOF_MISSING_SOURCE_UNREACHABLE
    assert cel.cost_basis is None
    # BlockFi — court-window withdrawal hot wallet
    bf = svc.label_address("3QSPK5fyNPwPtvh8s7YAGPHFeiTRJnJMD8")
    assert bf is not None
    assert bf.label == "BlockFi"
    assert bf.status == STATUS_PROOF_MISSING_SOURCE_UNREACHABLE
    assert bf.cost_basis is None


def test_load_packs_from_temp_fixture_dir(tmp_path: Path) -> None:
    fixture = {
        "id": "demoex",
        "name": "DemoEx",
        "status": STATUS_PROOF_MISSING_SOURCE_UNREACHABLE,
        "note": "fixture only",
        "source": "test://fixture",
        "updated": "2026-09-27",
        "addresses": ["bc1qfixturecluster000000000000000000001"],
    }
    (tmp_path / "demoex.json").write_text(
        json.dumps(fixture), encoding="utf-8"
    )
    (tmp_path / "index.json").write_text(
        json.dumps({"packs": [{"id": "demoex", "file": "demoex.json"}]}),
        encoding="utf-8",
    )
    svc = LabelService(autoload_packs=False, entity_pack={}, address_pack={})
    n = svc.load_packs_from_dir(tmp_path)
    assert n == 1
    assert svc.lookup_entity("demoex") == "DemoEx"
    hit = svc.label_address("bc1qfixturecluster000000000000000000001")
    assert hit is not None
    assert hit.label == "DemoEx"
    assert hit.status == STATUS_PROOF_MISSING_SOURCE_UNREACHABLE
    assert hit.cost_basis is None


def test_missing_packs_dir_falls_back_empty(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    svc = LabelService(autoload_packs=False, entity_pack={}, address_pack={})
    n = svc.load_packs_from_dir(missing)
    assert n == 0
    assert svc.label_address("bc1qanything") is None
    # Entities can still be assigned manually without inventing cost basis
    a = svc.assign("address", "x", label="FTX", cost_basis=None)
    assert a.cost_basis is None


def test_address_match_sets_evidence_gap_not_cost_basis_zero() -> None:
    svc = LabelService(autoload_packs=False)
    svc.load_pack_dict(
        {
            "id": "ftx",
            "name": "FTX",
            "status": STATUS_PROOF_MISSING_SOURCE_UNREACHABLE,
            "addresses": ["bc1qftxcluster00000000000000000000000001"],
        }
    )
    addr = "bc1qftxcluster00000000000000000000000001"
    a = svc.label_address(addr)
    assert a is not None
    assert a.label == "FTX"
    assert a.cost_basis is None
    assert "Kaufnachweis fehlt" in a.status
    assert a.status == STATUS_PROOF_MISSING_SOURCE_UNREACHABLE


def test_assign_rejects_silent_zero_cost_basis() -> None:
    svc = LabelService(autoload_packs=False)
    a = svc.assign("address", "x", label="FTX", cost_basis=0.0)
    assert a.cost_basis is None
    assert "Kaufnachweis fehlt" in a.status


def test_apply_to_flows() -> None:
    svc = LabelService(autoload_packs=False)
    svc.load_pack_dict(
        {
            "entities": {"celsius": "Celsius"},
            "addresses": {"bc1qcelsiuscluster000000000000000000001": "celsius"},
        }
    )
    flows = [
        Flow(
            txid="t1",
            address="bc1qcelsiuscluster000000000000000000001",
            direction="in",
            amount_sats=1,
        ),
        Flow(txid="t2", address="bc1qunknown000", direction="in", amount_sats=1),
    ]
    assignments = svc.apply_to_flows(flows)
    labels = {a.label for a in assignments}
    assert "Celsius" in labels
    assert all(a.cost_basis is None for a in assignments)


def test_load_pack_json_expandable() -> None:
    svc = LabelService(autoload_packs=False)
    svc.load_pack_json(
        '{"entities": {"newfail": "NewFail Exchange"}, '
        '"addresses": {"bc1qnewfail000": "newfail"}}'
    )
    assert svc.lookup_entity("newfail") == "NewFail Exchange"
    hit = svc.label_address("bc1qnewfail000")
    assert hit is not None and hit.label == "NewFail Exchange"


def test_default_pack_json_roundtrip() -> None:
    text = default_pack_json()
    assert "FTX" in text or "ftx" in text
    svc = LabelService(autoload_packs=False, entity_pack={}, address_pack={})
    svc.load_pack_json(text)
    assert svc.lookup_entity("ftx") == "FTX"


def test_status_constants() -> None:
    assert STATUS_PROOF_MISSING == "Kaufnachweis fehlt"
    assert "Quelle nicht erreichbar" in STATUS_PROOF_MISSING_SOURCE_UNREACHABLE


def test_label_service_does_not_write_session_to_disk(
    tmp_path: Path, monkeypatch
) -> None:
    """Public packs may be read from Git data/; session must not be persisted."""
    monkeypatch.chdir(tmp_path)
    svc = LabelService(autoload_packs=False)
    svc.load_pack_dict(
        {
            "id": "ftx",
            "name": "FTX",
            "addresses": ["bc1qsessiontest00000000000000000000001"],
        }
    )
    a = svc.label_address("bc1qsessiontest00000000000000000000001")
    assert a is not None
    # No new files under cwd from labeling
    written = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert written == []
