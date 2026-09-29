import { useState } from "react";
import { importExternalCsv, traceCounterparty, type ExternalEntry, type TrailResult } from "./api";
import { displayAddress } from "./privacy";

/** Spurensuche je Gegenstelle: Knopf in der Tabelle, Ergebnis als eigene Zeile. */

function shortHex(s: string, head = 10, tail = 6): string {
  if (!s || s.length <= head + tail + 1) return s;
  return `${s.slice(0, head)}…${s.slice(-tail)}`;
}

const CONF_CLASS: Record<string, string> = {
  hoch: "hint",
  mittel: "",
  niedrig: "gap",
  keine: "gap",
};

export function useTrail() {
  const [results, setResults] = useState<Record<string, TrailResult | "busy" | string>>({});
  const [running, setRunning] = useState(false);
  const run = (name: string): Promise<void> => {
    setResults((r) => ({ ...r, [name]: "busy" }));
    return traceCounterparty(name)
      .then((res) => setResults((r) => ({ ...r, [name]: res })))
      .catch((e) => setResults((r) => ({ ...r, [name]: e instanceof Error ? e.message : String(e) })));
  };
  /** Nacheinander (schont den Electrum-Server). */
  const runAll = async (names: string[]) => {
    setRunning(true);
    try {
      for (const n of names) await run(n);
    } finally {
      setRunning(false);
    }
  };
  return { results, run, runAll, running };
}

export function TrailButton({
  entry,
  state,
  onRun,
}: {
  entry: ExternalEntry;
  state: TrailResult | "busy" | string | undefined;
  onRun: () => void;
}) {
  if (!entry.auto_named && !entry.declared) return <span className="muted">—</span>;
  return (
    <button type="button" className="linkish" disabled={state === "busy"} onClick={() => void onRun()}>
      {state === "busy" ? "prüfe…" : state ? "erneut prüfen" : "Spur prüfen"}
    </button>
  );
}

export function TrailRow({
  entry,
  state,
  privacy,
  colSpan,
  onAdopted,
}: {
  entry: ExternalEntry;
  state: TrailResult | "busy" | string | undefined;
  privacy: boolean;
  colSpan: number;
  onAdopted: () => void;
}) {
  const [note, setNote] = useState<string | null>(null);
  if (!state || state === "busy") return null;
  if (typeof state === "string") {
    return (
      <tr className="trail-row">
        <td colSpan={colSpan} className="warn">
          Spurensuche fehlgeschlagen: {state}
        </td>
      </tr>
    );
  }
  const r = state;
  const adoptable = r.evidence.some((e) => e.kind === "label") && r.guess;
  const adopt = () => {
    const name = `${r.guess} (vermutet)`;
    const csv =
      "name,adresse\n" + (entry.addresses ?? [entry.address]).map((a) => `${name},${a}`).join("\n");
    importExternalCsv(csv)
      .then(() => {
        setNote(
          `Als „${name}“ übernommen (nur in dieser Sitzung). Dauerhaft: unten „Externe Adressen speichern“ und als local/externe-adressen.csv ablegen.`,
        );
        onAdopted();
      })
      .catch((e) => setNote(e instanceof Error ? e.message : String(e)));
  };
  return (
    <tr className="trail-row">
      <td colSpan={colSpan}>
        <div className="trail-head">
          <span className={`badge-mini ${CONF_CLASS[r.confidence] ?? ""}`}>
            {r.guess ? `vermutlich: ${r.guess}` : "keine Zuordnung"}
          </span>{" "}
          <span className="muted">
            Sicherheit: {r.confidence} · {r.direction === "in" ? "rückwärts: woher kamen die Bitcoin?" : r.direction === "out" ? "vorwärts: wohin gingen sie?" : "vor- und rückwärts"} ·{" "}
            {r.checked.addresses} Adressen, {r.checked.transactions} Transaktionen geprüft
          </span>
          {adoptable && (
            <button type="button" className="linkish" onClick={adopt} style={{ marginLeft: 8 }}>
              Name übernehmen
            </button>
          )}
        </div>
        <div className="muted">{r.reason}</div>
        {r.direction !== "out" && r.guess && r.confidence !== "keine" && (
          <div>
            <strong>Wo vermutlich gekauft:</strong> {r.guess} — dort nach Kontoauszügen bzw. einem
            Transaktions-Export für den Zeitraum fragen; die Daten der Zuflüsse stehen in der
            Suchliste.
          </div>
        )}
        <div className="trail-addrs">
          <span className="muted">Adresse{(r.addresses ?? [entry.address]).length > 1 ? "n" : ""} der Gegenstelle:</span>
          {(r.addresses ?? entry.addresses ?? [entry.address]).map((a) => (
            <span key={a} className="trail-addr">
              <code>{privacy ? displayAddress(true, a, shortHex) : a}</code>
              {!privacy && (
                <>
                  <button
                    type="button"
                    className="linkish"
                    title="Adresse in die Zwischenablage kopieren"
                    onClick={() => void navigator.clipboard?.writeText(a)}
                  >
                    kopieren
                  </button>
                  <a
                    href={`https://mempool.space/address/${a}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    title="Öffnet mempool.space — die Adresse wird dabei an diesen Dienst übertragen"
                  >
                    im Explorer ↗
                  </a>
                </>
              )}
            </span>
          ))}
        </div>
        {r.steps.length > 0 && (
          <ol className="trail-steps">
            {r.steps.slice(0, 12).map((s, i) => (
              <li key={i}>
                Schritt {s.hop}
                {s.time ? ` · ${s.time.slice(0, 10)}` : ""} ·{" "}
                <code>{displayAddress(privacy, s.address, shortHex, 10, 6)}</code>: {privacy ? s.text.replace(/\b(bc1|[13])[a-zA-HJ-NP-Z0-9]{20,}\b/g, "•••") : s.text}
                {s.txid && !privacy && (
                  <>
                    {" "}
                    <a
                      href={`https://mempool.space/tx/${s.txid}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      title="Transaktion bei mempool.space öffnen (TxID wird an den Dienst übertragen)"
                    >
                      Tx ↗
                    </a>
                  </>
                )}
              </li>
            ))}
          </ol>
        )}
        {r.errors.length > 0 && <div className="warn">Unvollständig: {r.errors.join("; ")}</div>}
        {note && <div className="muted">{note}</div>}
        <div className="muted" style={{ fontSize: "0.8em" }}>
          Heuristischer Hinweis, kein Beleg — im Bericht gilt eine Übernahme als eigene Angabe.
        </div>
      </td>
    </tr>
  );
}
