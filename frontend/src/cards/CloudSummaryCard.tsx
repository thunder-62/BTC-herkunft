import {
  displayBtc,
  displayEur,
} from "../privacy";
import type { AppState } from "../appState";
import { satsToBtc, formatEur } from "../appShared";

export default function CloudSummaryCard({ s }: { s: AppState }) {
  const { privacyMode, cloudSummary } = s;
  return (
    <section className="cloud-summary">
      <h2>Wallet-Cloud — Übersicht</h2>
      {cloudSummary && cloudSummary.consistent === false && (
        <p className="banner warn" role="alert">
          <strong>Plausibilitätsprüfung fehlgeschlagen:</strong> Bestand{" "}
          {(cloudSummary.bestand_sats / 1e8).toFixed(8)} BTC ≠ unverbrauchte
          Einzelbeträge (UTXO) laut Blockchain{" "}
          {((cloudSummary.chain_balance_sats ?? 0) / 1e8).toFixed(8)} BTC. Zu-
          und Abflüsse bitte nicht verwenden.
        </p>
      )}
      <p className="muted">
        Alle Wallets dieser Sitzung bilden eine Cloud (alle gepasteten xpubs /
        Adressen). Interne Transfers (auch zwischen Wallets) sind netto null;
        Gebühren erscheinen als interne Transaktionskosten. Eigen sind nur die
        Adressen der eingefügten xpubs.
      </p>
      <div className="cloud-grid">
        <div className="cloud-card">
          <div className="cloud-label">Inflow</div>
          <div className="cloud-value">
            <span className={privacyMode && cloudSummary ? "privacy-mask" : undefined}>
              {cloudSummary ? displayBtc(privacyMode, cloudSummary.inflow_sats, satsToBtc) : "—"}
            </span>{" "}
            BTC
          </div>
          <div className="cloud-eur muted">
            <span className={privacyMode && cloudSummary ? "privacy-mask" : undefined}>
              {cloudSummary
                ? displayEur(privacyMode, cloudSummary.inflow_eur, formatEur)
                : "—"}
            </span>
          </div>
        </div>
        <div className="cloud-card highlight">
          <div className="cloud-label">Bestand</div>
          <div className="cloud-value">
            <span className={privacyMode && cloudSummary ? "privacy-mask" : undefined}>
              {cloudSummary ? displayBtc(privacyMode, cloudSummary.bestand_sats, satsToBtc) : "—"}
            </span>{" "}
            BTC
          </div>
          <div className="cloud-eur muted">
            <span className={privacyMode && cloudSummary ? "privacy-mask" : undefined}>
              {cloudSummary
                ? displayEur(privacyMode, cloudSummary.bestand_eur, formatEur)
                : "—"}
            </span>
          </div>
        </div>
        <div className="cloud-card">
          <div className="cloud-label">Outflow</div>
          <div className="cloud-value">
            <span className={privacyMode && cloudSummary ? "privacy-mask" : undefined}>
              {cloudSummary ? displayBtc(privacyMode, cloudSummary.outflow_sats, satsToBtc) : "—"}
            </span>{" "}
            BTC
          </div>
          <div className="cloud-eur muted">
            <span className={privacyMode && cloudSummary ? "privacy-mask" : undefined}>
              {cloudSummary
                ? displayEur(privacyMode, cloudSummary.outflow_eur, formatEur)
                : "—"}
            </span>
          </div>
        </div>
        <div className="cloud-card">
          <div className="cloud-label">Interne Transaktionskosten</div>
          <div className="cloud-value">
            <span className={privacyMode && cloudSummary ? "privacy-mask" : undefined}>
              {cloudSummary
                ? displayBtc(privacyMode, cloudSummary.internal_fees_sats, satsToBtc)
                : "—"}
            </span>{" "}
            BTC
          </div>
          <div className="cloud-eur muted">
            <span className={privacyMode && cloudSummary ? "privacy-mask" : undefined}>
              {cloudSummary
                ? displayEur(
                    privacyMode,
                    cloudSummary.fees_eur ?? cloudSummary.internal_fees_eur,
                    formatEur,
                  )
                : "—"}
            </span>
          </div>
        </div>
      </div>
      {cloudSummary?.eur_note && (
        <p className="muted" style={{ marginTop: "0.5rem", fontSize: "0.85rem" }}>
          {cloudSummary.eur_note}
        </p>
      )}
    </section>
  );
}
