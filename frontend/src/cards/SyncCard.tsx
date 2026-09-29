import {
  displayAddress,
} from "../privacy";
import type { AppState } from "../appState";
import { shortHex } from "../appShared";

export default function SyncCard({ s }: { s: AppState }) {
  const { privacyMode, wallets, busy, syncSummary, ownership, showInferred, setShowInferred, syncProgress, handleSync } = s;
  return (
    <section>
      <h2>Sync-Status</h2>
      <p className="muted">
        Electrum-Sync schreibt nur in die Sitzung — nie auf Disk. Öffentliche Server
        können unerreichbar sein; Fehler erscheinen hier.
      </p>
      <div className="actions">
        <button
          type="button"
          onClick={handleSync}
          disabled={busy !== null || wallets.length === 0}
        >
          {busy === "sync" ? "Sync läuft…" : "Sync starten"}
        </button>
      </div>
      {(busy === "sync" || syncProgress) && (
        <p className="sync-progress" aria-live="polite">
          <strong>
            {syncProgress?.message ||
              (syncProgress &&
              (syncProgress.tx_total > 0 || syncProgress.tx_done > 0)
                ? `${syncProgress.tx_done} / ${syncProgress.tx_total} Tx verarbeitet`
                : busy === "sync"
                  ? "Electrum …"
                  : "")}
          </strong>
          {syncProgress?.phase &&
            syncProgress.phase !== "idle" &&
            syncProgress.phase !== "done" && (
              <span className="muted">
                {" "}
                ·{" "}
                {syncProgress.phase === "connecting"
                  ? "Verbinden"
                  : syncProgress.phase === "deriving"
                    ? "Adressen"
                    : syncProgress.phase === "history"
                      ? "Adressen"
                      : syncProgress.phase === "ingest"
                        ? "Transaktionen"
                        : syncProgress.phase === "enrich"
                          ? "Anreichern"
                          : syncProgress.phase === "error"
                            ? "Fehler"
                            : syncProgress.phase}
              </span>
            )}
        </p>
      )}
      <p>
        Status:{" "}
        <strong>{syncSummary?.status ?? "idle"}</strong>
        {" · "}Wallets in RAM: {wallets.length}
        {syncSummary && (
          <>
            {" · "}Flows: {syncSummary.flows}
            {" · "}Adressen: {syncSummary.addresses_derived}
            {" · "}Tx: {syncSummary.txids}
          </>
        )}
      </p>
      {syncSummary?.electrum &&
        typeof syncSummary.electrum === "object" &&
        syncSummary.electrum.host != null && (
          <p className="muted">
            Electrum:{" "}
            <code>
              {String(syncSummary.electrum.host)}:
              {String(syncSummary.electrum.port ?? "?")}
              {syncSummary.electrum.ssl ? " (SSL)" : ""}
            </code>
            {Array.isArray(syncSummary.electrum.version) &&
              syncSummary.electrum.version.length > 0 && (
                <> · Version: {String(syncSummary.electrum.version[0])}</>
              )}
          </p>
        )}
      {syncSummary?.errors && syncSummary.errors.length > 0 && (
        <ul className="note-list">
          {syncSummary.errors.map((n, i) => (
            <li key={`err-${i}`}>Fehler: {n}</li>
          ))}
        </ul>
      )}
      {syncSummary?.notes && syncSummary.notes.length > 0 && (
        <ul className="note-list">
          {syncSummary.notes.map((n, i) => (
            <li key={i}>{n}</li>
          ))}
        </ul>
      )}
      {(ownership || syncSummary?.ownership) && (
        <div className="ownership-box" style={{ marginTop: "0.75rem" }}>
          <p>
            <strong>Eigene Adressen:</strong>{" "}
            {(ownership ?? syncSummary?.ownership)!.seed_count} aus
            eingefügten xpubs/Adressen
            {(ownership ?? syncSummary?.ownership)!.inferred_count > 0 ? (
              <>
                , {(ownership ?? syncSummary?.ownership)!.inferred_count}{" "}
                heuristisch aus Co-Spends
                <span className="muted">
                  {" "}
                  (abgeleitet / heuristisch — keine Steuerberatung, keine
                  zertifizierte Sicherheit)
                </span>
              </>
            ) : (
              <span className="muted"> (keine heuristische Ableitung)</span>
            )}
          </p>
          {(ownership ?? syncSummary?.ownership)!.inferred_count > 0 && (
            <p>
              <button
                type="button"
                className="linkish"
                onClick={() => setShowInferred((v) => !v)}
              >
                {showInferred
                  ? "Liste ausblenden"
                  : "Heuristisch abgeleitete Adressen anzeigen"}
              </button>
            </p>
          )}
          {showInferred && (
            <ul className="note-list mono">
              {(
                (ownership ?? syncSummary?.ownership)!.inferred_addresses ??
                (ownership ?? syncSummary?.ownership)!.inferred_addresses_sample ??
                []
              ).map((a, i) => (
                <li key={`inf-${i}`}>
                  <code>
                    {displayAddress(privacyMode, a, shortHex, 12, 6)}
                  </code>
                  {(ownership ?? syncSummary?.ownership)!.inferred_by_sample?.[
                    a
                  ] && (
                    <span className="muted">
                      {" "}
                      ·{" "}
                      {
                        (ownership ?? syncSummary?.ownership)!
                          .inferred_by_sample![a]
                      }
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
