import {
  defaultFaStichtag,
} from "../api";
import type { AppState } from "../appState";

export default function ReportCard({ s }: { s: AppState }) {
  const { faStichtag, setFaStichtag, reportName, setReportName, reportTaxId, setReportTaxId, reportMasked, setReportMasked, reportUntil, setReportUntil, canExport, overFreigrenze, taxYears, setNachYears, chosenYears, nachUrl, altUrl, reportUrl, faReportUrl, faControlUrl } = s;
  return (
    <section className="collapsible report-box">
      <details>
        <summary>
          <h2>Finanzamt: Herkunftsanalyse Bitcoin</h2>
        </summary>
      <p className="muted">
        Bericht mit Stand (Datum/Uhrzeit), betrachteten Wallets, Zuflüssen und ihrem
        Verbleib, möglichen Gewinnen/Verlusten je Jahr und Detailliste der heutigen
        Bestände. Öffnet im Browser — speichern oder drucken dort. Name und Steuer-ID
        werden nur in den Bericht geschrieben, nirgends gespeichert.
      </p>
      <div className="report-form">
        <label>
          <span>Name</span>
          <input
            type="text"
            value={reportName}
            onChange={(e) => setReportName(e.target.value)}
            placeholder="optional"
            autoComplete="name"
            maxLength={120}
          />
        </label>
        <label>
          <span>Steuer-ID</span>
          <input
            type="text"
            value={reportTaxId}
            onChange={(e) => setReportTaxId(e.target.value)}
            placeholder="optional"
            maxLength={40}
          />
        </label>
        <label title="Stichtag für die Haltefrist-Bewertung (Standard: 31.12. des laufenden Jahres)">
          <span>Stichtag</span>
          <input
            type="date"
            value={faStichtag}
            onChange={(e) => setFaStichtag(e.target.value || defaultFaStichtag())}
          />
        </label>
        <label className="report-check">
          <input
            type="checkbox"
            checked={reportMasked}
            onChange={(e) => setReportMasked(e.target.checked)}
          />
          <span>maskiert (xpubs/Adressen/Beträge verdeckt, Namen lesbar)</span>
        </label>
        <label
          className="report-check"
          title="Beide Fassungen enthalten nur Vorgänge bis zum Stichtag (Tagesende UTC); Abstimmung und Überleitungen zu diesem Tag."
        >
          <input
            type="checkbox"
            checked={reportUntil}
            onChange={(e) => setReportUntil(e.target.checked)}
          />
          <span>nur Vorgänge bis zum Stichtag</span>
        </label>
        <a
          className={`button-link${!canExport ? " disabled" : ""}`}
          href={canExport ? reportUrl : undefined}
          target="_blank"
          rel="noopener"
          aria-disabled={!canExport}
        >
          Interne Fassung (Full-Detail) öffnen
        </a>
        <a
          className={`button-link${!canExport ? " disabled" : ""}`}
          href={canExport ? faReportUrl : undefined}
          target="_blank"
          rel="noopener"
          aria-disabled={!canExport}
          title="Für das Finanzamt: alle Angaben zu Veräußerungen, aber ohne Bestände, xpubs, Adressen und Transaktions-IDs ohne Bezug zu einer Veräußerung."
        >
          Finanzamt-Fassung öffnen
        </a>
        <a
          className={`button-link secondary${!canExport ? " disabled" : ""}`}
          href={canExport ? faControlUrl : undefined}
          download
          aria-disabled={!canExport}
          title="Prüfprotokolle beider Fassungen und Liste der in der Finanzamt-Fassung vollständig abgedruckten Transaktions-IDs — für deine Unterlagen, nicht zum Einreichen."
        >
          Kontrolldatei (.txt)
        </a>
        <a
          className={`button-link${!canExport ? " disabled" : ""}`}
          href={canExport ? altUrl : undefined}
          target="_blank"
          rel="noopener"
          aria-disabled={!canExport}
          title="Nachweis der bis 31.12.2026 angeschafften Bitcoin (Bestandsschutz laut Referentenentwurf zur Kryptosteuer-Reform)"
        >
          Nachweis Altbestand öffnen
        </a>
        <a
          className={`button-link secondary${!canExport ? " disabled" : ""}`}
          href={
            canExport
              ? `/api/report/matching-diagnose.md?stichtag=${(faStichtag || defaultFaStichtag()).slice(0, 10)}${
                  reportMasked ? "&privacy=true" : ""
                }`
              : undefined
          }
          download
          aria-disabled={!canExport}
          title="Nicht fürs Finanzamt: Prüfstatus des Berichts und Beinahe-Treffer der Börsen-Zuordnung. Maskiert = Beträge als •••, zum Teilen geeignet; sonst echte Werte, bleibt auf deinem Rechner."
        >
          Matching-Diagnose{reportMasked ? " maskiert" : ""} (.md)
        </a>
      </div>
      {overFreigrenze.length > 0 ? (
        <div className="nach-box" role="note">
          <p>
            <strong>Möglicher steuerpflichtiger Gewinn:</strong> In{" "}
            {overFreigrenze.map((y) => y.year).join(", ")} erreicht der Gewinn aus
            Veräußerungen innerhalb eines Jahres die Freigrenze (laut den Annahmen der
            Analyse). Waren diese Gewinne nicht erklärt, kann eine Berichtigung nach § 153 AO
            oder eine Selbstanzeige nach § 371 AO nötig sein. Das Tool erstellt dafür einen{" "}
            <strong>Entwurf</strong> (Anschreiben mit Platzhaltern, Werte je Jahr,
            Einzelaufstellung, Checkliste) — <strong>bitte vor dem Einreichen mit einer
            Steuerberaterin/einem Steuerberater klären.</strong> Keine Rechtsberatung.
          </p>
          <div className="nach-years">
            <span>Jahre:</span>
            {taxYears.map((y) => (
              <label key={y.year} className="nach-check">
                <input
                  type="checkbox"
                  checked={chosenYears.includes(y.year)}
                  onChange={(e) =>
                    setNachYears(
                      e.target.checked
                        ? [...chosenYears, y.year].sort()
                        : chosenYears.filter((x) => x !== y.year),
                    )
                  }
                />
                <span>{y.year}</span>
              </label>
            ))}
            <a
              className={`button-link${chosenYears.length === 0 ? " disabled" : ""}`}
              href={chosenYears.length ? nachUrl : undefined}
              target="_blank"
              rel="noopener"
              aria-disabled={chosenYears.length === 0}
            >
              Nacherklärung (Entwurf) öffnen
            </a>
          </div>
        </div>
      ) : null}
      </details>
    </section>
  );
}
