
import type { AppState } from "../appState";

export default function ExportCard({ s }: { s: AppState }) {
  const { privacyMode, busy, canExport, externals, extFileRef, handleSaveCsv, handleSaveExternal, handleImportExternal, handleSavePdf, handlePrint } = s;
  return (
    <section className="collapsible">
      <details>
        <summary>
          <h2>Export — nur auf ausdrücklichen Klick</h2>
        </summary>
      <p className="muted">
        Kein automatischer Export. Spotkurs = <strong>REFERENZWERT</strong> (kein
        Kaufpreis). Historischer Kurs je Zufluss ={" "}
        <strong>Anschaffungs-Referenz</strong> (keine Kostenbasis). Keine
        Steuerberatung.
      </p>
      <div className="actions">
        <button
          type="button"
          onClick={handleSaveCsv}
          disabled={!canExport || busy !== null}
          title={!canExport ? "Zuerst Sync — dann gibt es etwas zu exportieren" : undefined}
        >
          {busy === "csv" ? "CSV…" : "Save CSV"}
        </button>
        <button
          type="button"
          onClick={handleSaveExternal}
          disabled={externals.length === 0 || busy !== null}
          title="Fremde Zieladressen (ext-NNN / eigene Namen) als CSV name,adresse speichern"
        >
          {busy === "ext" ? "…" : "Externe Adressen speichern"}
        </button>
        <button
          type="button"
          className="secondary"
          onClick={() => extFileRef.current?.click()}
          disabled={busy !== null}
          title="CSV name,adresse laden — benennt externe Adressen (nur in dieser Sitzung)"
        >
          Externe Adressen importieren
        </button>
        <input
          ref={extFileRef}
          type="file"
          accept=".csv,text/csv,text/plain"
          style={{ display: "none" }}
          onChange={(e) => void handleImportExternal(e.target.files?.[0])}
        />
        <button
          type="button"
          onClick={handleSavePdf}
          disabled={!canExport || busy !== null}
          title={!canExport ? "Zuerst Sync — dann gibt es etwas zu exportieren" : undefined}
        >
          {busy === "pdf" ? "PDF…" : "Save PDF"}
        </button>
        <button
          type="button"
          className="secondary"
          onClick={handlePrint}
          disabled={!canExport}
        >
          Print
        </button>
      </div>
      {!canExport && (
        <p className="muted">Export erst verfügbar, wenn Flows in der Sitzung liegen.</p>
      )}
      {privacyMode && (
        <p className="muted privacy-export-note">
          Hinweis: Privacy-Ansicht maskiert die Bildschirmanzeige. Für einen maskierten
          Bericht „maskiert“ bei der Herkunftsanalyse anhaken. CSV/allgemeines PDF
          enthalten weiterhin die echten Sitzungsdaten (explizites Save).
        </p>
      )}
      </details>
    </section>
  );
}
