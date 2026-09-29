/**
 * BTC-Herkunft Localhost UI (M5).
 *
 * Hard Boundaries (session ephemeral):
 * - Paste-only xpubs (no USB / device auto-detect).
 * - No LocalStorage / IndexedDB / durable cache for wallet data.
 * - Closing the tab or stopping the process clears everything.
 * - CSV/PDF only via explicit Save / Print clicks — never automatic.
 * - Manufacturer-neutral (any BIP32/BIP84 xpub).
 */
import {
  FlowLotDetailPanel,
} from "./LotFlowViz";
import PPCheckCard from "./PPCheck";
import RegelwerkCard from "./cards/RegelwerkCard";
import { reloadLocalFiles } from "./api";
import SteuerReportCard from "./cards/SteuerReportCard";
import ProvenanceCard from "./ProvenanceCard";
import { useAppState } from "./appState";
import WalletsCard from "./cards/WalletsCard";
import SyncCard from "./cards/SyncCard";
import CloudSummaryCard from "./cards/CloudSummaryCard";
import FlowsCard from "./cards/FlowsCard";
import YearsCard from "./cards/YearsCard";
import LotFlowCard from "./cards/LotFlowCard";
import TraceCard from "./cards/TraceCard";
import LabelsCard from "./cards/LabelsCard";
import ExternalCard from "./cards/ExternalCard";
import ReportCard from "./cards/ReportCard";
import ExportCard from "./cards/ExportCard";

export default function App() {
  const s = useAppState();
  const { privacyMode, togglePrivacy, statusNote, errorNote, apiOk, cloudFlows, cloudLots, selectedFlow, setSelectedFlow, localInfo } = s;
  return (
    <main className={privacyMode ? "privacy-on" : undefined}>
      <div className="app-header">
        <h1>BTC-Herkunft</h1>
        <div className="privacy-controls">
          {privacyMode && (
            <span className="badge privacy-badge" title="Sensitive Werte als Sternchen">
              Demo · maskiert
            </span>
          )}
          <button
            type="button"
            className={privacyMode ? "privacy-toggle active" : "privacy-toggle"}
            onClick={togglePrivacy}
            aria-pressed={privacyMode}
            title="Wallet-Namen, xpubs, TxIDs und BTC-Beträge für Demos maskieren"
          >
            {privacyMode ? "Demo-Maskierung an" : "Privacy-Ansicht"}
          </button>
        </div>
      </div>
      <p className="muted">
        Localhost-only · Package <code>btc-origin</code> · Keine Seeds / Private Keys
        {apiOk === true && (
          <>
            {" "}
            · API <span className="ok">verbunden</span>
          </>
        )}
        {apiOk === false && (
          <>
            {" "}
            · API <span className="err">offline</span>
          </>
        )}
      </p>
      <p>
        <span className="badge">HW-agnostisch</span>
        <span className="badge">Unlimited xpubs</span>
        <span className="badge">Paste only</span>
        <span className="badge">Memory-only session</span>
        <span className="badge">Kein LocalStorage</span>
        <span className="badge">M5 UI</span>
      </p>
      <p className="ephemeral-banner">
        <strong>Flüchtige Sitzung:</strong> Alles liegt nur im Arbeitsspeicher.
        Tab schließen oder Prozess beenden = xpubs, Historie und Labels sind weg.
        Kein Cache, kein LocalStorage/IndexedDB, keine SQLite-Datei.
      </p>
      <p className="muted">
        Jede Hardware- oder Software-Wallet, die BIP32/BIP84-xpubs exportieren kann
        (z.&nbsp;B. Trezor, BitBox, Ledger, Coldcard, Foundation Devices, Sparrow, …).
        Kein USB/HID, keine automatische Geräte-Erkennung — nur manuelles Einfügen.
      </p>
      <p className="disclaimer">
        <strong>Keine Steuerberatung.</strong> Haltefrist-, Label- und Preisangaben sind
        technische Hinweise und Referenzwerte — keine Kostenbasis und keine Anlageberatung.
      </p>

      <WalletsCard s={s} />

      <SyncCard s={s} />


      <CloudSummaryCard s={s} />

      <FlowsCard s={s} />

      {selectedFlow && (
        <FlowLotDetailPanel
          flow={selectedFlow}
          lots={cloudLots}
          privacyMode={privacyMode}
          onClose={() => setSelectedFlow(null)}
        />
      )}

      <YearsCard s={s} />

      <LotFlowCard s={s} />

      <TraceCard s={s} />

      <LabelsCard s={s} />

      <ExternalCard s={s} />

      <ProvenanceCard lots={cloudLots} privacy={privacyMode} />

      <PPCheckCard privacy={privacyMode} flows={cloudFlows} local={localInfo} />

      <RegelwerkCard
        privacy={privacyMode}
        local={localInfo}
        onSaved={() =>
          reloadLocalFiles()
            .then((r) => s.setLocalInfo(r.local))
            .catch(() => undefined)
        }
      />

      <ReportCard s={s} />

      <SteuerReportCard s={s} />

      <ExportCard s={s} />

      {statusNote && (
        <p className="status" role="status">
          {statusNote}
        </p>
      )}
      {errorNote && (
        <p className="status error" role="alert">
          {errorNote}
        </p>
      )}
    </main>
  );
}

