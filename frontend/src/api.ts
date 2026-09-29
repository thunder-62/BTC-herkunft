/**
 * BTC-Herkunft API client — relative /api paths (Vite proxy → FastAPI).
 * Session data lives only in backend RAM + React state. Never LocalStorage.
 */

export type ApiWallet = {
  id: number;
  name: string;
  kind: string;
  xpub: string | null;
  address: string | null;
};

export type FlowRow = {
  id?: number;
  txid: string;
  address: string;
  direction: string;
  amount_sats: number;
  wallet_id?: number | null;
  is_internal?: number | boolean | null;
  block_time?: string | null;
  lot_date?: string | null;
  entity_label?: string;
  evidence_gap_status?: string;
  haltefrist_hint?: boolean;
  haltefrist_days?: number;
  btc_price_eur?: number | null;
  btc_price_usd?: number | null;
  btc_price_note?: string | null;
  wallet_name?: string | null;
  address_label?: string | null;
};

export type CloudSummary = {
  inflow_sats: number;
  bestand_sats: number;
  outflow_sats: number;
  internal_fees_sats: number;
  external_fees_sats?: number;
  flow_count?: number;
  /** Unspent own coins from the raw ledger — must equal bestand_sats. */
  chain_balance_sats?: number;
  /** False → Bestand ≠ Blockchain or negative: numbers must not be used. */
  consistent?: boolean;
  notes?: string[];
  /** Historischer EUR der externen Zuflüsse (Kurs am Cloud-Entry-/Lot-Tag). */
  inflow_eur?: number | null;
  /** Bestand × aktueller EUR-Spot. */
  bestand_eur?: number | null;
  /** Historischer EUR der externen Abflüsse. */
  outflow_eur?: number | null;
  /** Interne Gebühren × Kurs am Fee-/Tx-Tag. */
  fees_eur?: number | null;
  internal_fees_eur?: number | null;
  external_fees_eur?: number | null;
  spot_eur?: number | null;
  eur_note?: string | null;
  eur_partial?: boolean;
  labels?: Record<string, string>;
  note?: string;
  ephemeral?: boolean;
};

export type LotPathStep = {
  wallet_id: number | null;
  wallet_name?: string | null;
  txid?: string | null;
  date?: string | null;
  kind?: string;
  address?: string | null;
};

export type CloudLot = {
  lot_id: string;
  lot_date: string;
  original_amount_sats: number;
  remaining_sats: number;
  origin: {
    wallet_id: number | null;
    wallet_name?: string | null;
    txid: string;
    address: string;
  };
  current_wallet_id: number | null;
  current_wallet_name?: string | null;
  current_address?: string | null;
  path: LotPathStep[];
  qualifies_haltefrist: boolean;
  days_held: number;
  /** „Kauf auf X lt. Export“ — leer = kein Kaufbeleg (nur Blockchain). */
  acq_source?: string | null;
  acq_price_eur?: number | null;
};

export type CloudLotsResponse = {
  prices_pending?: boolean;
  lots: CloudLot[];
  count: number;
  cloud?: CloudSummary;
  ephemeral?: boolean;
  note?: string;
};


export type CloudFlowRow = {
  direction: "in" | "out" | string;
  kind?: string;
  amount_sats: number;
  original_amount_sats?: number | null;
  address: string;
  address_display?: string | null;
  wallet_id?: number | null;
  wallet_name?: string | null;
  time: string;
  txid: string;
  lot_id?: string;
  lot_date?: string | null;
  /** „Kauf auf X lt. Export“ — leer = Anschaffung laut Blockchain (unbelegt). */
  acq_source?: string | null;
  path: LotPathStep[];
  path_labels?: string[];
  haltefrist_hint?: boolean | null;
  haltefrist_days?: number;
  btc_price_eur?: number | null;
  btc_price_usd?: number | null;
  btc_price_note?: string | null;
  amount_basis?: string;
  note?: string;
  /** OUT rows: ext-NNN or imported name of the external destination. */
  external_name?: string | null;
  /** IN rows: sender addresses of the entry tx (foreign). */
  source_addresses?: string[];
  /** IN rows: ext-NNN / imported name of the (bundled) sender. */
  source_name?: string | null;
  source_complete?: boolean;
  /** IN rows: newly mined coins (coinbase). */
  source_coinbase?: boolean;
  /** IN rows: sats of this entry still in the cloud. */
  remaining_sats?: number;
  /** IN rows: vorhanden | teilweise | abgeflossen */
  status?: string;
  status_de?: string;
  /** IN rows: where the remaining sats sit now. */
  current_locations?: {
    wallet_id?: number | null;
    wallet_name?: string | null;
    address: string;
    remaining_sats: number;
  }[];
  origin_txid?: string | null;
  address_at_disposal?: string | null;
};

export type CloudFlowsResponse = {
  flows: CloudFlowRow[];
  count: number;
  cloud?: CloudSummary;
  ephemeral?: boolean;
  semantics?: string;
  amount_basis_in?: string;
  note?: string;
  /** Prices still loading in the background → re-poll. */
  prices_pending?: boolean;
  price_status?: PriceStatus;
};

export type PriceStatus = {
  days_loaded: number;
  days_failed: number;
  spot_available: boolean;
  bulk_points?: number;
  /** Last error per source, e.g. {"Binance": "HTTP 451"}. */
  errors?: Record<string, string>;
};

export type ExternalEntry = {
  address: string;
  /** All addresses of this counterparty (bundled senders). */
  addresses?: string[];
  name: string;
  auto_named: boolean;
  in_count?: number;
  in_sats?: number;
  /** Public label-pack hit, e.g. "Binance-Wallet". */
  label?: string | null;
  /** Exchange-like behaviour: "Sammelauszahlung", "Hot-Wallet". */
  exchange_hints?: string[];
  out_count: number;
  total_sats: number;
  first_time?: string | null;
  last_time?: string | null;
  /** Name from local/zuordnung.csv (date rule) — taxpayer's own statement. */
  declared?: string | null;
};


export type SyncProgress = {
  running: boolean;
  phase: string;
  tx_done: number;
  tx_total: number;
  addresses_done: number;
  addresses_total: number;
  message: string;
  updated_at?: string | null;
};

export type SyncSummary = {
  status: string;
  wallets: number;
  addresses_derived: number;
  addresses_with_history: number;
  txids: number;
  flows: number;
  inflows: number;
  outflows: number;
  internal_count: number;
  labels_applied: number;
  haltefrist_qualified: number;
  enrichment?: Record<string, unknown> | null;
  ownership?: OwnershipReport | null;
  errors?: string[];
  notes?: string[];
  electrum?: Record<string, unknown> | null;
  ephemeral?: boolean;
};

/** Heuristische Eigentums-Ableitung (Session-RAM only). */
export type OwnershipReport = {
  seed_count: number;
  inferred_count: number;
  own_count?: number;
  rounds?: number;
  inferred_addresses_sample?: string[];
  inferred_addresses?: string[];
  inferred_by_sample?: Record<string, string>;
  notes?: string[];
  heuristic?: boolean;
  label_de?: string;
  warning_de?: string;
  ephemeral?: boolean;
  source?: string;
};

export type TraceResult = {
  root_txid: string;
  max_depth: number;
  depth_reached: number;
  ambiguous: boolean;
  truncated: boolean;
  notes: string[];
  steps: Array<{
    txid: string;
    depth: number;
    parents: string[];
    ambiguous: boolean;
    truncated: boolean;
    note?: string | null;
    /** Funding prevout addresses (who funded this hop). */
    input_addresses?: string[];
    /** Outputs of this tx (secondary). */
    output_addresses?: string[];
    /** address → pack entity or session wallet name. */
    address_labels?: Record<string, string>;
  }>;
  nodes: Array<{ txid: string; depth: number; parents: string[] }>;
  ephemeral?: boolean;
  note?: string;
  status?: string;
  electrum?: { host?: string | null; port?: number | null; ssl?: boolean | null };
};

export type LabelRow = {
  address: string;
  label: string;
  status: string;
  source?: string | null;
};

async function parseJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`HTTP ${res.status}: ${text.slice(0, 200) || res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export async function getHealth(): Promise<Record<string, unknown>> {
  const res = await fetch("/api/health");
  return parseJson(res);
}

export async function listWallets(): Promise<{
  wallets: ApiWallet[];
  count: number;
  warning?: string | null;
}> {
  const res = await fetch("/api/wallets");
  return parseJson(res);
}

export async function addWalletsBatch(
  items: Array<{ name: string; xpub?: string; address?: string }>,
): Promise<{
  ok: boolean;
  imported: number;
  total: number;
  results: Array<{ name: string; id?: number; kind?: string; warning?: string; error?: string }>;
}> {
  const res = await fetch("/api/wallets/batch", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ items }),
  });
  return parseJson(res);
}

export async function syncWallets(): Promise<SyncSummary> {
  const res = await fetch("/api/sync", { method: "POST" });
  return parseJson(res);
}

export async function getSyncProgress(): Promise<SyncProgress> {
  const res = await fetch("/api/sync/progress");
  return parseJson(res);
}

export async function getFlows(): Promise<{
  flows: FlowRow[];
  count: number;
  cloud?: CloudSummary | null;
  last_sync?: SyncSummary | null;
  last_enrichment?: Record<string, unknown> | null;
  prices_pending?: boolean;
}> {
  const res = await fetch("/api/flows");
  return parseJson(res);
}

export async function getCloudSummary(): Promise<CloudSummary> {
  const res = await fetch("/api/cloud");
  return parseJson(res);
}

export async function getCloudLots(): Promise<CloudLotsResponse> {
  const res = await fetch("/api/cloud/lots");
  return parseJson(res);
}

export type YearBucket = {
  btc_sats: number;
  proceeds_eur: number;
  cost_eur: number;
  /** Werbungskosten: Netzwerkgebühr der Veräußerung (BMF 06.03.2025, Rz. 59). */
  fees_eur: number;
  gain_eur: number;
  priced_sats: number;
  missing_price: number;
};

export type YearSummary = {
  year: number;
  disposals: number;
  short: YearBucket;
  long: YearBucket;
  freigrenze_eur: number;
  over_freigrenze: boolean | null;
  complete: boolean;
  details: {
    exit_date: string;
    acquisition_date: string | null;
    days_held: number | null;
    short_term: boolean;
    btc_sats: number;
    gain_eur: number | null;
    counterparty: string;
  }[];
};

/** Jahres-Resümee (hypothetisch): exits valued as sale on the exit day. */
export async function getCloudYears(): Promise<{
  years: YearSummary[];
  assumption?: string;
  note?: string;
  prices_pending?: boolean;
}> {
  const res = await fetch("/api/cloud/years");
  return parseJson(res);
}

export async function getCloudFlows(): Promise<CloudFlowsResponse> {
  const res = await fetch("/api/cloud/flows");
  return parseJson(res);
}

export async function getOwnership(privacy = false): Promise<OwnershipReport> {
  const q = privacy ? "?privacy=true" : "";
  const res = await fetch(`/api/cloud/ownership${q}`);
  return parseJson(res);
}

export async function getLabels(): Promise<{
  labels: LabelRow[];
  count: number;
  pack_entities: string[];
  note?: string;
}> {
  const res = await fetch("/api/labels");
  return parseJson(res);
}

export async function runTrace(
  txid: string,
  maxDepth?: number,
): Promise<TraceResult> {
  const body: Record<string, unknown> = { txid };
  if (maxDepth != null) body.max_depth = maxDepth;
  const res = await fetch("/api/trace", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return parseJson(res);
}

export async function clearSession(): Promise<{ ok: boolean; note?: string }> {
  const res = await fetch("/api/session", { method: "DELETE" });
  return parseJson(res);
}

export type LocalFilesInfo = {
  directory: string;
  names_file?: string | null;
  names_count: number;
  date_rules?: { name: string; bis: string }[];
  exchange_files: { file: string; name: string; txids: number; amount_rows?: number }[];
  errors: string[];
};

export async function getExternal(): Promise<{
  external: ExternalEntry[];
  count: number;
  local?: LocalFilesInfo;
}> {
  const res = await fetch("/api/external");
  return parseJson(res);
}

/** Re-read local/ (names CSV + exchange exports). The app never writes there. */
export async function reloadLocalFiles(): Promise<{ local: LocalFilesInfo }> {
  const res = await fetch("/api/local/reload", { method: "POST" });
  return parseJson(res);
}

/** Import names for external addresses (CSV text read in the browser). */
export async function importExternalCsv(csv: string): Promise<{
  imported: number;
  updated: number;
  errors: string[];
}> {
  const res = await fetch("/api/external/import", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ csv }),
  });
  return parseJson(res);
}

/** Explicit Save — external addresses as CSV (name,adresse). */
export async function downloadExternalCsv(): Promise<void> {
  const res = await fetch("/api/external/csv", { method: "POST" });
  if (!res.ok) {
    throw new Error(`Export fehlgeschlagen (HTTP ${res.status})`);
  }
  triggerBlobDownload(await res.blob(), "externe-adressen.csv");
}

/** Explicit Save — download blob. Never auto-called. */
export async function downloadReportCsv(): Promise<void> {
  const res = await fetch("/api/report/csv", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  if (!res.ok) {
    throw new Error(`CSV-Export fehlgeschlagen (HTTP ${res.status})`);
  }
  const blob = await res.blob();
  triggerBlobDownload(blob, "btc-herkunft-report.csv");
}

/** Explicit Save — download PDF blob. Never auto-called. */
export async function downloadReportPdf(): Promise<void> {
  const res = await fetch("/api/report/pdf", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  if (!res.ok) {
    throw new Error(`PDF-Export fehlgeschlagen (HTTP ${res.status})`);
  }
  const blob = await res.blob();
  triggerBlobDownload(blob, "btc-herkunft-report.pdf");
}

/** Default Stichtag: 31.12. of the current calendar year (local date). */
export function defaultFaStichtag(now: Date = new Date()): string {
  const y = now.getFullYear();
  return `${y}-12-31`;
}

/** Explicit Save — FA-Inbound PDF from session lots. Never auto-called. */
export async function downloadFaInboundPdf(
  stichtag: string,
  opts?: { privacy?: boolean },
): Promise<void> {
  const iso = (stichtag || defaultFaStichtag()).trim().slice(0, 10);
  const privacy = Boolean(opts?.privacy);
  const res = await fetch("/api/report/pdf/fa-inbound", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ stichtag: iso, privacy }),
  });
  if (!res.ok) {
    let detail = "";
    try {
      const j = await res.json();
      detail = typeof j?.detail === "string" ? `: ${j.detail}` : "";
    } catch {
      /* ignore */
    }
    throw new Error(`FA-Inbound-PDF fehlgeschlagen (HTTP ${res.status})${detail}`);
  }
  const blob = await res.blob();
  triggerBlobDownload(
    blob,
    privacy
      ? "btc-herkunft-fa-inbound-privacy.pdf"
      : "btc-herkunft-fa-inbound.pdf",
  );
}

function triggerBlobDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/** Classify paste line as xpub vs single address (heuristic). */
export function classifyPublicInput(raw: string): "xpub" | "address" | "reject" {
  const v = raw.trim();
  if (!v) return "reject";
  const lower = v.toLowerCase();
  if (
    lower.includes("seed") ||
    lower.startsWith("xprv") ||
    lower.startsWith("yprv") ||
    lower.startsWith("zprv") ||
    lower.startsWith("tprv")
  ) {
    return "reject";
  }
  const words = v.split(/\s+/);
  if (
    words.length >= 12 &&
    words.length <= 24 &&
    words.every((w) => /^[a-zA-Z]+$/.test(w))
  ) {
    return "reject";
  }
  if (/^[xyzuvt]pub[1-9A-HJ-NP-Za-km-z]+$/i.test(v)) return "xpub";
  if (/^(bc1|tb1|bcrt1|[13]|[mn2])[a-zA-HJ-NP-Z0-9]+$/i.test(v)) return "address";
  // Soft: treat long base58-ish as xpub; otherwise address-ish
  if (/^[xyzuvt]pub/i.test(v)) return "xpub";
  if (v.length >= 26) return "address";
  return "reject";
}

export type PPRow = {
  line: number;
  day: string;
  direction: "in" | "out";
  type: string;
  sats: number;
  eur: number | null;
  fee_eur: number | null;
  note: string;
};

export type PPChainMove = {
  txid: string;
  day: string;
  direction: "in" | "out";
  sats: number;
  fee_sats: number;
  wallet: string;
  counterparty: string;
};

export type PPAction = {
  id: string;
  no: number;
  kind: "amount" | "missing" | "only_pp" | "fees";
  day: string;
  year: number;
  title: string;
  suggest: { code: string; sats?: number; target_sats?: number; text?: string; day?: string };
  effect_sats: number;
  pp: PPRow[];
  chain: PPChainMove[];
};

export type PPCheck = {
  exists: boolean;
  file: string;
  synced?: boolean;
  errors?: string[];
  securities?: Record<string, number>;
  ignored_securities?: Record<string, number>;
  bestand: {
    pp_sats: number;
    chain_sats: number;
    delta_sats: number;
    after_sats: number;
    rest_sats: number;
  };
  ok: { exact: number; date: number; grouped: number; fees: number; corrected?: number; total: number };
  skipped_transfers?: number;
  actions: PPAction[];
  years: { year: number; open: number; effect_sats: number }[];
  counts: { pp: number; chain: number; matched: number };
  tolerance?: { days: number; agg_days: number; sats: number; ratio: number };
};

/** Korrigierte CSV für den PP-Import herunterladen (nur auf Klick). */
export async function downloadPPImportCsv(keepLines: number[]): Promise<string> {
  const res = await fetch("/api/check/pp/import.csv", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ keep_lines: keepLines }),
  });
  if (!res.ok) throw new Error(`PP-Import-CSV: ${res.status} ${await res.text()}`);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "pp-import-bitcoin.csv";
  a.click();
  URL.revokeObjectURL(url);
  return res.headers.get("X-PP-Import-Stats") ?? "";
}

/** Diagnose zum PP-Abgleich ohne Bestände (Zähler, Jahre, Anteile in %). */
export async function getPPDiagnose(): Promise<string> {
  const res = await fetch("/api/check/pp/diagnose");
  const data = await parseJson<{ text: string }>(res);
  return data.text;
}

/** Abgleich local/check-pp.csv (Portfolio Performance) — nur Browser. */
export async function getPPCheck(): Promise<PPCheck> {
  const res = await fetch("/api/check/pp");
  return parseJson(res);
}

export type TrailResult = {
  name: string;
  direction: "out" | "in" | "both";
  guess: string | null;
  confidence: "hoch" | "mittel" | "niedrig" | "keine";
  reason: string;
  steps: { hop: number; address: string; txid: string | null; time: string | null; text: string }[];
  evidence: { kind: string; hop: number; text: string; name?: string | null }[];
  checked: { addresses: number; transactions: number };
  errors: string[];
  /** Adressen der Gegenstelle (vollständig). */
  addresses?: string[];
};

/** Spurensuche für eine Gegenstelle (Electrum, heuristisch). */
export async function traceCounterparty(name: string): Promise<TrailResult> {
  const res = await fetch("/api/external/trail", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  return parseJson(res);
}

/** Suchliste ungeklärter Gegenstellen als CSV (nur auf Klick). */
export async function downloadSearchList(): Promise<void> {
  const res = await fetch("/api/external/suchliste.csv");
  if (!res.ok) throw new Error(`Suchliste: ${res.status}`);
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = "suchliste-gegenstellen.csv";
  a.click();
  URL.revokeObjectURL(url);
}

// ---------------------------------------------------------------------------
// Regelwerk / Kategorien (btc-regeln/, local/kategorien.yaml)
// ---------------------------------------------------------------------------

export type Kategorie = { name: string; anzeigename: string; behandlung: string; beschreibung: string };
export type ExportArt = {
  exchange: string;
  art: string;
  count: number;
  richtung: string;
  /** passende Zuordnungen zur Richtung, Kauf bzw. Verkauf zuerst */
  passend: string[];
  /** Zuordnung gilt in der Sitzung, ist aber nicht in local/kategorien.yaml gespeichert */
  ungespeichert: boolean;
  zuordnung: string;
  status: "ok" | "nicht_unterstuetzt";
  grund: string;
  jahre: Record<string, number>;
  erste_je_jahr: Record<string, string>;
  erste: string;
};
export type Zufluss = {
  tnr: string;
  day: string;
  wallet: string;
  source: string;
  sats: number;
  belegt: boolean;
};
export type ManuelleZuordnung = { kategorie: string; datum: string; erlaeuterung: string };
export type KategorienInfo = {
  kategorien: Kategorie[];
  technisch: string[];
  arten: ExportArt[];
  /** Zuordnungen der Sitzung (nur im Arbeitsspeicher) */
  sitzung: { aktiv: boolean; ungespeichert: boolean };
  aenderungen: { zeilen: number; wirkt_auf_werte: number };
  manuell: Record<string, ManuelleZuordnung>;
  zufluesse: Zufluss[];
  local_file: string;
  local_exists: boolean;
};
export type KategorienAenderung = {
  export?: Record<string, Record<string, string>>;
  manuell?: Record<string, ManuelleZuordnung | null>;
};

export async function getKategorien(): Promise<KategorienInfo> {
  return parseJson(await fetch("/api/regelwerk/kategorien"));
}

export async function previewKategorien(change: KategorienAenderung): Promise<{ yaml: string; file: string }> {
  return parseJson(
    await fetch("/api/regelwerk/kategorien/vorschau", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(change),
    }),
  );
}

/** Änderungen für die Sitzung übernehmen — nur im Arbeitsspeicher, nichts wird geschrieben. */
export async function applyKategorien(change: KategorienAenderung): Promise<{ ok: boolean }> {
  return parseJson(
    await fetch("/api/regelwerk/kategorien/anwenden", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(change),
    }),
  );
}

/** Änderungen der Sitzung verwerfen — es gilt wieder die gespeicherte Datei. */
export async function discardKategorien(): Promise<{ ok: boolean }> {
  return parseJson(await fetch("/api/regelwerk/kategorien/verwerfen", { method: "POST" }));
}

/** Schreibt local/kategorien.yaml — nur nach Bestätigung im Dialog aufrufen. */
export async function saveKategorien(yamlText: string): Promise<{ ok: boolean; file: string; backup: string | null }> {
  return parseJson(
    await fetch("/api/regelwerk/kategorien/speichern", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ yaml: yamlText, bestaetigt: true }),
    }),
  );
}

export function downloadText(text: string, filename: string): void {
  triggerBlobDownload(new Blob([text], { type: "text/yaml;charset=utf-8" }), filename);
}

export type PdfVergleich = { gleich: boolean; zeilen: number; abschnitte: { abschnitt: string; zeilen: number }[] };

async function fileToBase64(file: File): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(bin);
}

export async function comparePdfs(alt: File, neu: File): Promise<PdfVergleich> {
  const body = JSON.stringify({ alt: await fileToBase64(alt), neu: await fileToBase64(neu) });
  return parseJson(
    await fetch("/api/pdf-vergleich", { method: "POST", headers: { "Content-Type": "application/json" }, body }),
  );
}

// ---------------------------------------------------------------------------
// Jahressteuerreport
// ---------------------------------------------------------------------------

export type SteuerJahr = { jahr: number; regelwerk: boolean; vermerk: string };
export type SteuerJahre = {
  jahre: SteuerJahr[];
  vorgabe: number | null;
  /** in dieser Sitzung erzeugte Herkunftsnachweise mit Stichtag 31.12. (Jahr → Stand, Build) */
  herkunft: Record<string, { stand: string; stichtag: string; build: string }>;
};

export async function getSteuerJahre(): Promise<SteuerJahre> {
  return parseJson(await fetch("/api/report/steuer/jahre"));
}

export function steuerReportUrl(
  jahr: number,
  fassung: "intern" | "finanzamt",
  opts: { privacy: boolean; name: string; steuerId: string; anschlussJahr: number },
): string {
  const q = new URLSearchParams({ jahr: String(jahr), fassung, anschluss_jahr: String(opts.anschlussJahr) });
  if (opts.privacy) q.set("privacy", "true");
  if (opts.name.trim()) q.set("name", opts.name.trim());
  if (opts.steuerId.trim()) q.set("steuer_id", opts.steuerId.trim());
  return `/api/report/steuer.pdf?${q.toString()}`;
}

export type SteuerPruefung = { ergebnis: "bestanden" | "warnung" | "fehler"; fehler: string[]; warnungen: number };

/** Ergebnis des Prüfprotokolls (erzeugt und prüft beide Fassungen) — für die Anzeige am Button. */
export async function getSteuerPruefung(url: string): Promise<SteuerPruefung> {
  return parseJson(await fetch(url));
}

/** Herkunftsnachweis mit Stichtag 31.12. des Jahres (nur Vorgänge bis dahin) — für den Anschluss. */
export function herkunftBisUrl(
  jahr: number,
  fassung: "intern" | "finanzamt",
  opts: { privacy: boolean; name: string; steuerId: string },
): string {
  const tag = `${jahr}-12-31`;
  const q = new URLSearchParams({ stichtag: tag, bis: tag, fassung });
  if (opts.privacy) q.set("privacy", "true");
  if (opts.name.trim()) q.set("name", opts.name.trim());
  if (opts.steuerId.trim()) q.set("steuer_id", opts.steuerId.trim());
  return `/api/report/herkunft.pdf?${q.toString()}`;
}
