import {
  displayTxid,
  displayAddress,
} from "../privacy";
import type { AppState } from "../appState";
import { shortHex } from "../appShared";

export default function TraceCard({ s }: { s: AppState }) {
  const { privacyMode, busy, traceTxid, setTraceTxid, traceDepth, setTraceDepth, traceResult, handleTrace } = s;
  return (
    <section className="collapsible">
      <details>
        <summary>
          <h2>Trace — Herkunft (heuristisch)</h2>
        </summary>
      <p className="muted">
        Reverse-BFS über TxIDs (Vin-Eltern), tiefenbegrenzt. Pro Hop: Finanzierungs-Adressen
        aus den Prevouts, sofern decodierbar. Mehrdeutigkeit wird angezeigt — keine
        zertifizierte Gewissheit. Keine Steuerberatung.
      </p>
      <label htmlFor="txid">TxID</label>
      <input
        id="txid"
        value={traceTxid}
        onChange={(e) => setTraceTxid(e.target.value)}
        placeholder="Hex-TxID"
        autoComplete="off"
        spellCheck={false}
      />
      <label htmlFor="depth">Max. Tiefe</label>
      <input
        id="depth"
        type="number"
        min={1}
        max={20}
        value={traceDepth}
        onChange={(e) => setTraceDepth(Number(e.target.value) || 5)}
      />
      <div className="actions">
        <button type="button" onClick={handleTrace} disabled={busy !== null}>
          {busy === "trace" ? "Trace läuft…" : "Trace starten"}
        </button>
      </div>
      {traceResult && (
        <div className="trace-box">
          <p>
            Wurzel{" "}
            <code className={privacyMode ? "privacy-mask" : undefined}>
              {displayTxid(privacyMode, traceResult.root_txid, shortHex, 16, 8)}
            </code>
            {" · "}Tiefe {traceResult.depth_reached}/{traceResult.max_depth}
            {traceResult.ambiguous && (
              <span className="badge-mini gap"> mehrdeutig</span>
            )}
            {traceResult.truncated && (
              <span className="badge-mini"> abgeschnitten</span>
            )}
          </p>
          {traceResult.steps.length > 0 ? (
            <ul className="note-list">
              {traceResult.steps.map((s, i) => (
                <li key={`${s.txid}-${i}`}>
                  d{s.depth}:{" "}
                  <code className={privacyMode ? "privacy-mask" : undefined}>
                    {displayTxid(privacyMode, s.txid, shortHex)}
                  </code>
                  {s.parents.length > 0 && (
                    <>
                      {" "}
                      ←{" "}
                      <span className={privacyMode ? "privacy-mask" : undefined}>
                        {s.parents
                          .map((p) => displayTxid(privacyMode, p, shortHex))
                          .join(", ")}
                      </span>
                    </>
                  )}
                  {(s.input_addresses?.length ?? 0) > 0 && (
                    <>
                      {" · "}Adressen:{" "}
                      {privacyMode ? (
                        <span className="privacy-mask">
                          <code>{displayAddress(true, "addr", shortHex)}</code>
                          {s.input_addresses!.length > 1
                            ? ` ×${s.input_addresses!.length}`
                            : ""}
                        </span>
                      ) : (
                        <span>
                          {s.input_addresses!.map((a, j) => {
                            const lab = s.address_labels?.[a];
                            const shown = displayAddress(false, a, shortHex, 12, 6);
                            return (
                              <span key={`${a}-${j}`}>
                                {j > 0 ? ", " : ""}
                                <code>{shown}</code>
                                {lab ? ` (${lab})` : ""}
                              </span>
                            );
                          })}
                        </span>
                      )}
                    </>
                  )}
                  {s.ambiguous && " (mehrdeutig)"}
                  {s.note ? ` — ${s.note}` : ""}
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">Keine Schritte (keine Eltern / offline).</p>
          )}
          {traceResult.depth_reached === 0 &&
            traceResult.ambiguous &&
            (traceResult.notes?.length ?? 0) > 0 && (
              <ul className="note-list trace-warn">
                {traceResult.notes.map((n, i) => (
                  <li key={`note-${i}`}>{n}</li>
                ))}
              </ul>
            )}
          {traceResult.status === "electrum_unreachable" && (
            <p className="error-inline">
              Electrum unerreichbar — Vin-Eltern konnten nicht aufgelöst werden.
            </p>
          )}
          {traceResult.note && <p className="muted">{traceResult.note}</p>}
        </div>
      )}
      </details>
    </section>
  );
}
