# Label Packs — Public Exchange / Insolvency Clusters

**DE / EN** — Öffentliche Cluster-Adresslisten dürfen im Git landen; Nutzer-Session-Daten nicht.

Verwandt: [`DATA_MODEL.md`](DATA_MODEL.md) · [`WALLET_CLOUD.md`](WALLET_CLOUD.md) · [`API.md`](API.md) · [`../THREAT_MODEL.md`](../THREAT_MODEL.md)

## Boundary

| May live in Git / on disk | Must stay RAM / SQLite `:memory:` only |
|---------------------------|----------------------------------------|
| Public exchange & insolvency **cluster address lists** (`data/label_packs/`) | User xpubs, derived addresses |
| Pack metadata (`id`, `name`, `status`, `source`, `note`) | Transaction history / flows |
| | Personal / manual labels |
| | Session settings, report caches |
| | Seeds / private keys (**never accepted**) |

Frontend: **no LocalStorage / IndexedDB** for wallet data. Process/tab end = session gone.

Labels from packs set a status (e.g. **„Kaufnachweis fehlt, Quelle nicht erreichbar“**) and **never** invent cost basis `0`. Pack names also serve as names of external counterparties (see `WALLET_CLOUD.md`).

## Layout

```
data/label_packs/
  index.json          # optional catalog
  binance.json
  ftx.json
  mtgox.json
  celsius.json
  blockfi.json
  voyager.json
  …
```

Path resolution (`label_service.resolve_label_packs_dir`):

1. Explicit argument / `LabelService(pack_dir=…)`
2. Env `BTC_ORIGIN_LABEL_PACKS`
3. Walk parents of the package file for `data/label_packs` (editable checkout)
4. `<package>/data/label_packs` if bundled
5. Missing → empty/minimal in-memory entities only (no crash)

After load, matching is **in-memory** only. The service does not write session data back into these files.

## Cluster JSON schema (`btc-origin.label_pack.v1`)

```json
{
  "id": "ftx",
  "name": "FTX",
  "status": "Kaufnachweis fehlt, Quelle nicht erreichbar",
  "note": "How to expand; attribution caveats…",
  "source": "https://… or well-known public dataset name",
  "updated": "YYYY-MM-DD",
  "addresses": ["bc1…", "1…", "3…"]
}
```

| Field | Required | Notes |
|-------|----------|-------|
| `id` | yes | Stable key (`ftx`, `mtgox`, …) |
| `name` | yes | Display label |
| `status` | recommended | Default evidence-gap wording |
| `note` | recommended | Expansion / caveat text |
| `source` | **yes for real addresses** | URL or public dataset name — never invent addresses |
| `updated` | recommended | ISO date |
| `addresses` | yes (may be `[]`) | Bitcoin addresses only; modest verified sets OK |

Legacy in-memory shape still accepted by `load_pack_dict` / `load_pack_json`:

```json
{ "entities": { "ftx": "FTX" }, "addresses": { "bc1…": "ftx" } }
```

## Contribution

1. Prefer **primary public sources**: bankruptcy schedules, exchange PoR, peer-reviewed / reputable forensic write-ups (WizSec, TokenScope, Elliptic, Arkham entity pages with spelled-out addresses), Bitcoin Wiki.
2. **Never invent** addresses or claim unverified strings are real. Empty `addresses: []` with a clear `note` is better than fiction.
3. Cite `source` per pack (URL or dataset name). Document thief/hack destinations separately if needed — do not label attacker sinks as the exchange.
4. Keep packs modest; large dumps belong in follow-up PRs with review.
5. Run `pytest` after adding packs; ensure a known address → expected `name` + evidence-gap status + `cost_basis is None`.
6. Do **not** commit user session exports, xpubs, or personal labels into this tree.

## Current packs

| Pack | Addresses | Attribution approach |
|------|-----------|----------------------|
| `binance` | 1 | Binance hot wallet from Binance's own Proof-of-Reserves publication (shown as „Binance-Wallet“) |
| `ftx` | 53 | FTX.US sources of the 2022-11-12 unauthorized BTC outflow (TokenScope); reconstructed from funding txs |
| `mtgox` | 12 | Proof-of-solvency Block 132749 + WizSec hot-wallet tip |
| `celsius` | 15 | CoinDesk-cited Celsius Ukraine-donation BTC wallets + OXT.me CELSIUS NETWORK internal/withdrawal labels |
| `blockfi` | 2 | Court-supervised Wallet withdrawal hot wallet + replenisher (community on-chain analysis, Aug–Dec 2023) |
| `voyager` | 0 | Still empty — SDNY/Arkham/CoinDesk coverage checked; no fully spelled native-BTC strings found |

See each file’s `note` / `source` for details.
