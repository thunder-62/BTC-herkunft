/**
 * Lot-Fluss / Satoshi-Verfolgung — interactive genealogy in-app.
 * 1) Flow detail panel (single flow + related lot paths)
 * 2) Aggregated arc diagram of all open lots (one arc per wallet pair)
 */
import { useEffect, useMemo, useRef, useState } from "react";
import type { CloudFlowRow, CloudLot, FlowRow, LotPathStep } from "./api";
import {
  displayAddress,
  displayName,
  displayTxid,
  displayBtc,
  maskSats,
} from "./privacy";

function satsToBtc(sats: number): string {
  return (sats / 1e8).toFixed(8);
}

function shortHex(s: string, head = 10, tail = 6): string {
  if (!s || s.length <= head + tail + 1) return s;
  return `${s.slice(0, head)}…${s.slice(-tail)}`;
}

function walletLabel(step: {
  wallet_name?: string | null;
  wallet_id?: number | null;
}): string {
  if (step.wallet_name) return step.wallet_name;
  if (step.wallet_id != null) return `Wallet ${step.wallet_id}`;
  return "?";
}

/** Lots whose origin or path hop references this flow's txid (or same lot_date+wallet). */
export function lotsForFlow(lots: CloudLot[], flow: FlowRow): CloudLot[] {
  const txid = flow.txid;
  const matched = lots.filter(
    (l) =>
      l.origin.txid === txid ||
      l.path.some((p) => p.txid === txid) ||
      (flow.lot_date != null &&
        l.lot_date === String(flow.lot_date).slice(0, 10) &&
        (flow.wallet_id == null ||
          l.current_wallet_id === flow.wallet_id ||
          l.origin.wallet_id === flow.wallet_id)),
  );
  // Deduplicate by lot_id + current wallet
  const seen = new Set<string>();
  return matched.filter((l) => {
    const key = `${l.lot_id}|${l.current_wallet_id}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export function PathBreadcrumb({
  path,
  privacyMode = false,
}: {
  path: LotPathStep[];
  privacyMode?: boolean;
}) {
  if (!path.length) return <span className="muted">—</span>;
  return (
    <ol className="lot-path">
      {path.map((step, i) => (
        <li key={`${step.txid ?? "x"}-${step.wallet_id}-${i}`}>
          <span
            className={
              privacyMode ? "lot-path-wallet privacy-mask" : "lot-path-wallet"
            }
          >
            {privacyMode ? displayName(true, walletLabel(step)) : walletLabel(step)}
          </span>
          {step.kind === "inflow" ? (
            <span className="badge-mini hint">Inflow</span>
          ) : (
            <span className="badge-mini">Umbuchung</span>
          )}
          {step.date && <span className="muted"> {step.date}</span>}
          {step.txid && (
            <code className={privacyMode ? "lot-path-txid privacy-mask" : "lot-path-txid"}>
              {" "}
              {displayTxid(privacyMode, step.txid, shortHex)}
            </code>
          )}
          {i < path.length - 1 && <span className="lot-path-arrow"> → </span>}
        </li>
      ))}
    </ol>
  );
}


/** Compact wallet-name breadcrumb: A → B → C (Privacy-aware). */
export function CompactPathBreadcrumb({
  path,
  pathLabels,
  privacyMode = false,
}: {
  path?: LotPathStep[];
  pathLabels?: string[];
  privacyMode?: boolean;
}) {
  const labels =
    pathLabels && pathLabels.length
      ? pathLabels
      : (path || []).map((s) => walletLabel(s));
  if (!labels.length) return <span className="muted">—</span>;
  return (
    <span className="cloud-path-compact" title={labels.join(" → ")}>
      {labels.map((lab, i) => (
        <span key={`${lab}-${i}`}>
          <span className={privacyMode ? "privacy-mask" : undefined}>
            {privacyMode ? displayName(true, lab) : lab}
          </span>
          {i < labels.length - 1 && <span className="lot-path-arrow"> → </span>}
        </span>
      ))}
    </span>
  );
}

export function FlowLotDetailPanel({
  flow,
  lots,
  onClose,
  privacyMode = false,
}: {
  flow: FlowRow;
  lots: CloudLot[];
  onClose: () => void;
  privacyMode?: boolean;
}) {
  const related = useMemo(() => lotsForFlow(lots, flow), [lots, flow]);

  return (
    <div className="lot-detail-overlay" role="dialog" aria-modal="true">
      <div className="lot-detail-panel">
        <div className="lot-detail-head">
          <h3>Flow-Detail &amp; Lot-Pfad</h3>
          <button type="button" className="secondary" onClick={onClose}>
            Schließen
          </button>
        </div>
        <dl className="lot-detail-meta">
          <div>
            <dt>Richtung</dt>
            <dd>
              {flow.direction}
              {flow.is_internal ? (
                <span className="badge-mini"> intern</span>
              ) : null}
            </dd>
          </div>
          <div>
            <dt>Betrag</dt>
            <dd className={privacyMode ? "num privacy-mask" : "num"}>
              {displayBtc(privacyMode, flow.amount_sats, satsToBtc)} BTC (
              {privacyMode
                ? maskSats(flow.amount_sats)
                : flow.amount_sats.toLocaleString("de-DE")}{" "}
              sats)
            </dd>
          </div>
          {flow.direction === "out" && !flow.is_internal ? (
            <div>
              <dt>Ziel (extern)</dt>
              <dd>
                {flow.address ? (
                  <code className={privacyMode ? "privacy-mask" : undefined}>
                    {displayAddress(privacyMode, flow.address, shortHex, 12, 6)}
                  </code>
                ) : (
                  <span className="muted">außerhalb Cloud</span>
                )}{" "}
                <span className="muted">— nicht deine Wallet</span>
              </dd>
            </div>
          ) : null}
          <div>
            <dt>{flow.direction === "out" ? "Aus Wallet" : "Wallet"}</dt>
            <dd className={privacyMode ? "privacy-mask" : undefined}>
              {privacyMode
                ? displayName(
                    true,
                    flow.wallet_name ||
                      (flow.wallet_id != null ? `ID ${flow.wallet_id}` : null),
                    "—",
                  )
                : flow.wallet_name ||
                  (flow.wallet_id != null ? `ID ${flow.wallet_id}` : "—")}
            </dd>
          </div>
          <div>
            <dt>TxID</dt>
            <dd>
              <code className={privacyMode ? "privacy-mask" : undefined}>
                {displayTxid(privacyMode, flow.txid, shortHex, 16, 10)}
              </code>
            </dd>
          </div>
          <div>
            <dt>Zeit</dt>
            <dd>{flow.block_time ? String(flow.block_time).slice(0, 19) : "—"}</dd>
          </div>
          <div>
            <dt>Lot-Datum</dt>
            <dd>{flow.lot_date ? String(flow.lot_date).slice(0, 10) : "—"}</dd>
          </div>
          <div>
            <dt>Haltefrist</dt>
            <dd>
              {flow.haltefrist_hint === true
                ? `erfüllt (${flow.haltefrist_days ?? "?"} Tage)`
                : flow.haltefrist_days != null
                  ? `offen (${flow.haltefrist_days} Tage)`
                  : "—"}
            </dd>
          </div>
        </dl>

        <h4>Zugehörige FIFO-Lots</h4>
        {related.length === 0 ? (
          <p className="muted">
            Kein offenes Lot mit diesem Flow verknüpft (vollständig verbraucht oder noch
            kein Sync/Enrichment).
          </p>
        ) : (
          <ul className="lot-card-list">
            {related.map((lot) => (
              <li key={`${lot.lot_id}-${lot.current_wallet_id}`} className="lot-card">
                <div className="lot-card-title">
                  <code className={privacyMode ? "privacy-mask" : undefined}>
                    {displayTxid(privacyMode, lot.lot_id, shortHex, 14, 8)}
                  </code>
                  {lot.qualifies_haltefrist ? (
                    <span className="badge-mini hint">Haltefrist erfüllt</span>
                  ) : (
                    <span className="badge-mini">Haltefrist offen</span>
                  )}
                </div>
                <p className="muted">
                  Anschaffung {lot.lot_date} · Rest{" "}
                  <span className={privacyMode ? "privacy-mask" : undefined}>
                    {displayBtc(privacyMode, lot.remaining_sats, satsToBtc)}
                  </span>{" "}
                  BTC
                  {" / "}Original{" "}
                  <span className={privacyMode ? "privacy-mask" : undefined}>
                    {displayBtc(privacyMode, lot.original_amount_sats, satsToBtc)}
                  </span>{" "}
                  BTC · {lot.days_held} Tage · jetzt bei{" "}
                  <strong className={privacyMode ? "privacy-mask" : undefined}>
                    {privacyMode
                      ? displayName(
                          true,
                          lot.current_wallet_name || `Wallet ${lot.current_wallet_id}`,
                        )
                      : lot.current_wallet_name || `Wallet ${lot.current_wallet_id}`}
                  </strong>
                </p>
                <PathBreadcrumb path={lot.path} privacyMode={privacyMode} />
              </li>
            ))}
          </ul>
        )}
        <p className="muted disclaimer-inline">
          Hinweis: FIFO-Genealogie aus der Sitzung — keine Steuerberatung.
        </p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Lot-Fluss: aggregated arc diagram (one arc per wallet pair, width = BTC)
// ---------------------------------------------------------------------------

const SOURCE_KEY = "__zufluss__";
const SINK_KEY = "__abfluss__";
const SOURCE_LABEL = "Zufluss (extern)";
const SINK_LABEL = "Abfluss (extern)";
const isExternal = (key: string) => key === SOURCE_KEY || key === SINK_KEY;

type FlowNode = {
  key: string;
  label: string;
  firstDate: string;
  holdingSats: number;
  holdingLots: number;
};

type FlowEdge = {
  key: string;
  from: string;
  to: string;
  kind: "entry" | "transfer" | "exit";
  sats: number;
  lots: Set<string>;
  txids: Set<string>;
  firstDate: string | null;
  lastDate: string | null;
};

type FlowTip =
  | { x: number; y: number; edge: FlowEdge; node?: undefined }
  | { x: number; y: number; node: FlowNode; edge?: undefined };

function nodeKeyOf(wid: number | null | undefined, name?: string | null): string {
  if (wid != null) return `w:${wid}`;
  return `n:${name ?? "?"}`;
}

function fmtDay(d: string | null): string {
  if (!d) return "—";
  const [y, m, day] = d.slice(0, 10).split("-");
  return day && m && y ? `${day}.${m}.${y}` : d;
}

function truncate(label: string, max = 18): string {
  return label.length > max ? `${label.slice(0, max - 1)}…` : label;
}

/** Whole history as wallet nodes + one edge per (from → to) pair:
 *  entries (Cloud-Eintritt, full amount) · internal hops (FIFO share that moved
 *  along that route — later spent or still held) · exits (Cloud-Austritt). */
export function buildLotFlowModel(
  lots: CloudLot[],
  flows: CloudFlowRow[] = [],
): { nodes: FlowNode[]; edges: FlowEdge[] } {
  const nodes = new Map<string, FlowNode>();
  const edges = new Map<string, FlowEdge>();
  const blank = (key: string, label: string, firstDate: string): FlowNode => ({
    key,
    label,
    firstDate,
    holdingSats: 0,
    holdingLots: 0,
  });
  nodes.set(SOURCE_KEY, blank(SOURCE_KEY, SOURCE_LABEL, ""));

  const touchNode = (key: string, label: string, date?: string | null) => {
    let n = nodes.get(key);
    const d = date ? date.slice(0, 10) : "9999";
    if (!n) {
      n = blank(key, label, d);
      nodes.set(key, n);
    } else if (d < n.firstDate) {
      n.firstDate = d;
    }
    return n;
  };

  const addEdge = (
    from: string,
    to: string,
    sats: number,
    lotId: string | null | undefined,
    txid?: string | null,
    date?: string | null,
  ) => {
    if (sats <= 0 || from === to) return;
    const key = `${from}>${to}`;
    let e = edges.get(key);
    if (!e) {
      e = {
        key,
        from,
        to,
        kind: from === SOURCE_KEY ? "entry" : to === SINK_KEY ? "exit" : "transfer",
        sats: 0,
        lots: new Set(),
        txids: new Set(),
        firstDate: null,
        lastDate: null,
      };
      edges.set(key, e);
    }
    e.sats += sats;
    if (lotId) e.lots.add(lotId);
    if (txid) e.txids.add(txid);
    const d = date ? date.slice(0, 10) : null;
    if (d && (!e.firstDate || d < e.firstDate)) e.firstDate = d;
    if (d && (!e.lastDate || d > e.lastDate)) e.lastDate = d;
  };

  /** Internal hops along a genealogy path (skips the entry step itself). */
  const walkPath = (path: LotPathStep[], sats: number, lotId: string) => {
    let prev: string | null = null;
    for (const step of path) {
      const key = nodeKeyOf(step.wallet_id, step.wallet_name);
      touchNode(key, walletLabel(step), step.date);
      if (prev !== null && key !== prev) addEdge(prev, key, sats, lotId, step.txid, step.date);
      prev = key;
    }
    return prev;
  };

  const hasFlows = flows.length > 0;
  for (const f of flows) {
    if (f.direction === "in") {
      // Entry: full amount into the wallet where the coins arrived.
      const first = f.path?.[0];
      const key = nodeKeyOf(first?.wallet_id ?? f.wallet_id, first?.wallet_name ?? f.wallet_name);
      touchNode(key, walletLabel(first ?? f), f.time);
      addEdge(SOURCE_KEY, key, f.amount_sats, f.lot_id, f.txid, f.time);
    } else if (f.direction === "out" && f.kind !== "fee") {
      const last = walkPath(f.path ?? [], f.amount_sats, f.lot_id ?? f.txid);
      const key = nodeKeyOf(f.wallet_id, f.wallet_name) ?? last;
      touchNode(key, walletLabel(f), f.time);
      if (last && last !== key) addEdge(last, key, f.amount_sats, f.lot_id, f.txid, f.time);
      nodes.set(SINK_KEY, nodes.get(SINK_KEY) ?? blank(SINK_KEY, SINK_LABEL, "zzzz"));
      addEdge(key, SINK_KEY, f.amount_sats, f.lot_id, f.txid, f.time);
    }
  }

  for (const lot of lots) {
    if (lot.remaining_sats <= 0) continue;
    const path: LotPathStep[] = lot.path.length
      ? lot.path
      : [{ wallet_id: lot.origin.wallet_id, wallet_name: lot.origin.wallet_name, date: lot.lot_date }];
    if (!hasFlows) {
      const k0 = nodeKeyOf(path[0].wallet_id, path[0].wallet_name);
      touchNode(k0, walletLabel(path[0]), path[0].date);
      addEdge(SOURCE_KEY, k0, lot.remaining_sats, lot.lot_id, lot.origin.txid, lot.lot_date);
    }
    const last = walkPath(path, lot.remaining_sats, lot.lot_id);
    const cur = nodeKeyOf(lot.current_wallet_id, lot.current_wallet_name);
    const node = touchNode(
      cur,
      walletLabel({ wallet_id: lot.current_wallet_id, wallet_name: lot.current_wallet_name }),
    );
    node.holdingSats += lot.remaining_sats;
    node.holdingLots += 1;
    if (last && last !== cur) addEdge(last, cur, lot.remaining_sats, lot.lot_id);
  }

  const rank = (n: FlowNode) => (n.key === SOURCE_KEY ? 0 : n.key === SINK_KEY ? 2 : 1);
  const ordered = [...nodes.values()].sort(
    (a, b) =>
      rank(a) - rank(b) ||
      a.firstDate.localeCompare(b.firstDate) ||
      a.label.localeCompare(b.label),
  );
  return { nodes: ordered, edges: [...edges.values()].sort((a, b) => b.sats - a.sats) };
}

const NET_HEIGHT = 520;
const NET_MARGIN_X = 70;
const NET_MARGIN_Y = 56;
const NET_MIN_WIDTH = 640;

type Pos = { x: number; y: number };

/** Spread the wallets in the plane (Fruchterman–Reingold): nodes repel each
 *  other, edges pull their wallets together (stronger for more BTC). Inflow
 *  and outflow sit fixed at the left/right edge. Deterministic — same data,
 *  same picture. */
function layoutNetwork(
  nodes: FlowNode[],
  edges: FlowEdge[],
  width: number,
  height: number,
): Map<string, Pos> {
  const pos = new Map<string, Pos>();
  const inner = nodes.filter((n) => !isExternal(n.key));
  const cx = width / 2;
  const cy = height / 2;
  pos.set(SOURCE_KEY, { x: NET_MARGIN_X, y: cy });
  pos.set(SINK_KEY, { x: width - NET_MARGIN_X, y: cy });
  inner.forEach((n, i) => {
    const a = (2 * Math.PI * i) / Math.max(1, inner.length) - Math.PI / 2;
    pos.set(n.key, {
      x: cx + (width * 0.28) * Math.cos(a),
      y: cy + (height * 0.3) * Math.sin(a),
    });
  });
  if (inner.length <= 1) return pos;
  const k = 1.1 * Math.sqrt(((width - 2 * NET_MARGIN_X) * (height - 2 * NET_MARGIN_Y)) / (inner.length + 2));
  const MIN_GAP = 110; // keeps circles and their labels apart
  const maxSats = Math.max(1, ...edges.map((e) => e.sats));
  let temp = width / 8;
  for (let it = 0; it < 350; it++) {
    const disp = new Map<string, Pos>(inner.map((n) => [n.key, { x: 0, y: 0 }]));
    const all = [...pos.keys()];
    for (const a of inner) {
      const pa = pos.get(a.key)!;
      const da = disp.get(a.key)!;
      for (const bKey of all) {
        if (bKey === a.key) continue;
        const pb = pos.get(bKey)!;
        let dx = pa.x - pb.x;
        let dy = pa.y - pb.y;
        let d = Math.hypot(dx, dy);
        if (d < 0.01) {
          dx = 0.01;
          dy = 0;
          d = 0.01;
        }
        const f = (k * k) / d;
        da.x += (dx / d) * f;
        da.y += (dy / d) * f;
      }
    }
    for (const e of edges) {
      const pa = pos.get(e.from);
      const pb = pos.get(e.to);
      if (!pa || !pb) continue;
      const dx = pa.x - pb.x;
      const dy = pa.y - pb.y;
      const d = Math.max(0.01, Math.hypot(dx, dy));
      // external edges pull less, so wallets do not pile up at the sides
      const w = (isExternal(e.from) || isExternal(e.to) ? 0.25 : 0.6) * (0.5 + Math.sqrt(e.sats / maxSats));
      const f = ((d * d) / k) * w;
      const ua = disp.get(e.from);
      const ub = disp.get(e.to);
      if (ua) {
        ua.x -= (dx / d) * f;
        ua.y -= (dy / d) * f;
      }
      if (ub) {
        ub.x += (dx / d) * f;
        ub.y += (dy / d) * f;
      }
    }
    for (const n of inner) {
      const p = pos.get(n.key)!;
      const dv = disp.get(n.key)!;
      // mild pull to the centre keeps unconnected wallets on screen
      dv.x += (cx - p.x) * 0.01 * k;
      dv.y += (cy - p.y) * 0.01 * k;
      const d = Math.max(0.01, Math.hypot(dv.x, dv.y));
      p.x += (dv.x / d) * Math.min(d, temp);
      p.y += (dv.y / d) * Math.min(d, temp);
      p.x = Math.min(width - NET_MARGIN_X - 60, Math.max(NET_MARGIN_X + 60, p.x));
      p.y = Math.min(height - NET_MARGIN_Y, Math.max(NET_MARGIN_Y, p.y));
    }
    // collision: push wallets that are too close apart
    for (let i = 0; i < inner.length; i++) {
      for (let j = i + 1; j < inner.length; j++) {
        const pa = pos.get(inner[i].key)!;
        const pb = pos.get(inner[j].key)!;
        const dx = pb.x - pa.x;
        const dy = pb.y - pa.y;
        const d = Math.max(0.01, Math.hypot(dx, dy));
        if (d < MIN_GAP) {
          const push = (MIN_GAP - d) / 2;
          pa.x -= (dx / d) * push;
          pa.y -= (dy / d) * push;
          pb.x += (dx / d) * push;
          pb.y += (dy / d) * push;
        }
      }
    }
    temp *= 0.985;
  }
  for (const n of inner) {
    const p = pos.get(n.key)!;
    p.x = Math.min(width - NET_MARGIN_X - 60, Math.max(NET_MARGIN_X + 60, p.x));
    p.y = Math.min(height - NET_MARGIN_Y, Math.max(NET_MARGIN_Y, p.y));
  }
  return pos;
}

/** Curved edge a→b (bends to the right of its direction, so A→B and B→A
 *  stay apart); returns path + midpoint + tangent for the arrow. */
function curve(a: Pos, b: Pos, ra: number, rb: number) {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const d = Math.max(1, Math.hypot(dx, dy));
  const nx = -dy / d;
  const ny = dx / d;
  const bend = Math.min(80, d * 0.2);
  const c = { x: (a.x + b.x) / 2 + nx * bend, y: (a.y + b.y) / 2 + ny * bend };
  // start/end on the circle rims, aimed at the control point
  const trim = (p: Pos, r: number) => {
    const vx = c.x - p.x;
    const vy = c.y - p.y;
    const l = Math.max(1, Math.hypot(vx, vy));
    return { x: p.x + (vx / l) * r, y: p.y + (vy / l) * r };
  };
  const s = trim(a, ra);
  const t = trim(b, rb + 4);
  const mid = { x: 0.25 * s.x + 0.5 * c.x + 0.25 * t.x, y: 0.25 * s.y + 0.5 * c.y + 0.25 * t.y };
  const tl = Math.max(1, Math.hypot(t.x - s.x, t.y - s.y));
  return {
    d: `M ${s.x} ${s.y} Q ${c.x} ${c.y} ${t.x} ${t.y}`,
    mid,
    tan: { x: (t.x - s.x) / tl, y: (t.y - s.y) / tl },
  };
}

export function LotFlowDiagram({
  lots,
  flows = [],
  privacyMode = false,
}: {
  lots: CloudLot[];
  flows?: CloudFlowRow[];
  privacyMode?: boolean;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [tip, setTip] = useState<FlowTip | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const [dragged, setDragged] = useState<Map<string, Pos>>(new Map());
  const dragKey = useRef<string | null>(null);

  const model = useMemo(() => buildLotFlowModel(lots, flows), [lots, flows]);
  const [boxWidth, setBoxWidth] = useState(0);
  useEffect(() => {
    const el = wrapRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => setBoxWidth(el.clientWidth));
    ro.observe(el);
    setBoxWidth(el.clientWidth);
    return () => ro.disconnect();
    // Re-attach once the chart appears (empty state renders no wrapper).
  }, [model.edges.length > 0]);

  const width = Math.max(NET_MIN_WIDTH, boxWidth);
  const height = NET_HEIGHT;
  const auto = useMemo(
    () => layoutNetwork(model.nodes, model.edges, width, height),
    [model, width, height],
  );
  useEffect(() => setDragged(new Map()), [model]);

  const layout = useMemo(() => {
    const pos = (key: string): Pos => dragged.get(key) ?? auto.get(key) ?? { x: 0, y: 0 };
    const maxSats = Math.max(1, ...model.edges.map((e) => e.sats));
    const maxHold = Math.max(1, ...model.nodes.map((nd) => nd.holdingSats));
    const radius = (nd: FlowNode) =>
      isExternal(nd.key) ? 16 : 10 + 26 * Math.sqrt(nd.holdingSats / maxHold);
    const rOf = new Map(model.nodes.map((nd) => [nd.key, radius(nd)]));
    const arcs = model.edges.map((e) => {
      const c = curve(pos(e.from), pos(e.to), rOf.get(e.from) ?? 10, rOf.get(e.to) ?? 10);
      return { edge: e, ...c, width: 1.5 + 14 * Math.sqrt(e.sats / maxSats) };
    });
    return { pos, radius, arcs };
  }, [model, auto, dragged]);

  const showTip = (clientX: number, clientY: number, t: Omit<FlowTip, "x" | "y">) => {
    const el = wrapRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    setTip({ ...(t as FlowTip), x: clientX - r.left + el.scrollLeft, y: clientY - r.top });
  };

  const nodeLabel = (key: string) =>
    model.nodes.find((nd) => nd.key === key)?.label ?? "?";
  const name = (label: string) =>
    label === SOURCE_LABEL || label === SINK_LABEL
      ? label
      : displayName(privacyMode, label) ?? label;
  const btc = (sats: number) => displayBtc(privacyMode, sats, satsToBtc);

  if (model.edges.length === 0) {
    return <p className="muted">Keine offenen Lots — nach Sync erscheint hier der Fluss.</p>;
  }

  const active = (e: FlowEdge) => !hover || hover === e.key || hover === e.from || hover === e.to;

  return (
    <div className="lotflow">
      <div className="lotflow-legend" aria-hidden="true">
        <span>
          <svg width="28" height="10">
            <line x1="1" y1="5" x2="27" y2="5" className="lotflow-key entry" />
          </svg>{" "}
          Zufluss (gestrichelt)
        </span>
        <span>
          <svg width="28" height="10">
            <line x1="1" y1="5" x2="27" y2="5" className="lotflow-key transfer" />
          </svg>{" "}
          intern
        </span>
        <span>
          <svg width="28" height="10">
            <line x1="1" y1="5" x2="27" y2="5" className="lotflow-key exit" />
          </svg>{" "}
          Abfluss
        </span>
        <span className="muted">
          Linienstärke = BTC-Menge · Kreisgröße = heutiger Bestand · Pfeil = Richtung ·
          Wallets lassen sich mit der Maus verschieben
        </span>
      </div>
      <div
        className="lotflow-wrap"
        ref={wrapRef}
        onMouseLeave={() => {
          setTip(null);
          setHover(null);
        }}
      >
        <svg
          className="lotflow-svg"
          width={width}
          height={height}
          viewBox={`0 0 ${width} ${height}`}
          role="img"
          aria-label="Lot-Fluss: Wallets im Raum, Pfeile zwischen ihnen, Stärke nach BTC-Menge"
          onPointerMove={(ev) => {
            const key = dragKey.current;
            if (!key) return;
            const r = (ev.currentTarget as SVGSVGElement).getBoundingClientRect();
            const x = Math.min(width - 20, Math.max(20, ev.clientX - r.left));
            const y = Math.min(height - 20, Math.max(20, ev.clientY - r.top));
            setDragged((m) => new Map(m).set(key, { x, y }));
          }}
          onPointerUp={() => {
            dragKey.current = null;
          }}
          onPointerLeave={() => {
            dragKey.current = null;
          }}
        >
          {layout.arcs.map((a) => (
            <g key={a.edge.key} className={active(a.edge) ? "" : "lotflow-dim"}>
              <path d={a.d} className={`lotflow-arc ${a.edge.kind}`} strokeWidth={a.width} />
              <path
                d={`M ${a.mid.x - a.tan.x * 6 - a.tan.y * 5} ${a.mid.y - a.tan.y * 6 + a.tan.x * 5} L ${a.mid.x + a.tan.x * 4} ${a.mid.y + a.tan.y * 4} L ${a.mid.x - a.tan.x * 6 + a.tan.y * 5} ${a.mid.y - a.tan.y * 6 - a.tan.x * 5}`}
                className="lotflow-chevron"
              />
              <path
                d={a.d}
                className="lotflow-hit"
                strokeWidth={a.width + 12}
                tabIndex={0}
                aria-label={`${nodeLabel(a.edge.from)} nach ${nodeLabel(a.edge.to)}`}
                onMouseMove={(ev) => {
                  if (dragKey.current) return;
                  setHover(a.edge.key);
                  showTip(ev.clientX, ev.clientY, { edge: a.edge });
                }}
                onFocus={() => {
                  setHover(a.edge.key);
                  setTip({ x: a.mid.x, y: a.mid.y, edge: a.edge });
                }}
                onBlur={() => {
                  setHover(null);
                  setTip(null);
                }}
              />
            </g>
          ))}
          {model.nodes.map((nd) => {
            const { x: cx, y: cy } = layout.pos(nd.key);
            const r = layout.radius(nd);
            const external = isExternal(nd.key);
            return (
              <g
                key={nd.key}
                className={`lotflow-node${external ? "" : " draggable"}`}
                tabIndex={0}
                onPointerDown={(ev) => {
                  if (external) return;
                  dragKey.current = nd.key;
                  setTip(null);
                  (ev.currentTarget as SVGGElement).ownerSVGElement?.setPointerCapture?.(ev.pointerId);
                }}
                onMouseMove={(ev) => {
                  if (dragKey.current) return;
                  setHover(nd.key);
                  showTip(ev.clientX, ev.clientY, { node: nd });
                }}
                onFocus={() => {
                  setHover(nd.key);
                  setTip({ x: cx, y: cy, node: nd });
                }}
                onBlur={() => {
                  setHover(null);
                  setTip(null);
                }}
              >
                <circle
                  cx={cx}
                  cy={cy}
                  r={r}
                  className={
                    nd.key === SOURCE_KEY
                      ? "lotflow-circle source"
                      : nd.key === SINK_KEY
                        ? "lotflow-circle sink"
                        : "lotflow-circle"
                  }
                />
                <text x={cx} y={cy + r + 15} className="lotflow-label">
                  {truncate(name(nd.label))}
                </text>
                {nd.holdingSats > 0 ? (
                  <text x={cx} y={cy + r + 29} className="lotflow-sub">
                    {btc(nd.holdingSats)}
                  </text>
                ) : null}
              </g>
            );
          })}
        </svg>
        {tip ? (
          <div
            className="lotflow-tip"
            role="status"
            style={{ left: tip.x + 14, top: Math.max(4, tip.y - 10) }}
          >
            {tip.edge ? (
              <>
                <div className="lotflow-tip-value">{btc(tip.edge.sats)} BTC</div>
                <div>
                  {name(nodeLabel(tip.edge.from))} → {name(nodeLabel(tip.edge.to))}
                </div>
                <div className="muted">
                  {tip.edge.lots.size} Lot{tip.edge.lots.size === 1 ? "" : "s"}
                  {tip.edge.txids.size > 0
                    ? ` aus ${tip.edge.txids.size} Transaktion${tip.edge.txids.size === 1 ? "" : "en"}`
                    : ""}
                </div>
                <div className="muted">
                  {tip.edge.firstDate === tip.edge.lastDate
                    ? fmtDay(tip.edge.firstDate)
                    : `${fmtDay(tip.edge.firstDate)} – ${fmtDay(tip.edge.lastDate)}`}
                </div>
              </>
            ) : tip.node ? (
              <>
                <div className="lotflow-tip-value">
                  {tip.node.key === SOURCE_KEY
                    ? "Quelle aller Zuflüsse"
                    : tip.node.key === SINK_KEY
                      ? "Ziel aller Abflüsse"
                      : `${btc(tip.node.holdingSats)} BTC`}
                </div>
                <div>{name(tip.node.label)}</div>
                {!isExternal(tip.node.key) ? (
                  <div className="muted">
                    heutiger Bestand · {tip.node.holdingLots} Lot
                    {tip.node.holdingLots === 1 ? "" : "s"}
                  </div>
                ) : null}
              </>
            ) : null}
          </div>
        ) : null}
      </div>
      <details className="lotflow-table">
        <summary>Als Tabelle anzeigen</summary>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Von</th>
                <th>Nach</th>
                <th>BTC</th>
                <th>Lots</th>
                <th>Transaktionen</th>
                <th>Zeitraum</th>
              </tr>
            </thead>
            <tbody>
              {model.edges.map((e) => (
                <tr key={e.key}>
                  <td className={privacyMode ? "privacy-mask" : undefined}>
                    {name(nodeLabel(e.from))}
                  </td>
                  <td className={privacyMode ? "privacy-mask" : undefined}>
                    {name(nodeLabel(e.to))}
                  </td>
                  <td className={privacyMode ? "num privacy-mask" : "num"}>{btc(e.sats)}</td>
                  <td className="num">{e.lots.size}</td>
                  <td className="num">{e.txids.size || "—"}</td>
                  <td className="nowrap">
                    {e.firstDate === e.lastDate
                      ? fmtDay(e.firstDate)
                      : `${fmtDay(e.firstDate)} – ${fmtDay(e.lastDate)}`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}
