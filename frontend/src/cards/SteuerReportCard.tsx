import { useEffect, useState } from "react";
import {
  getSteuerJahre,
  getSteuerPruefung,
  herkunftBisUrl,
  steuerReportUrl,
  type SteuerJahre,
  type SteuerPruefung,
} from "../api";
import type { AppState } from "../appState";

/** Jahressteuerreport: ein Kalenderjahr, interne oder Finanzamt-Fassung. Name, Steuer-ID und
 *  „maskiert“ sind mit der Karte Herkunftsanalyse gekoppelt; nichts wird gespeichert. */
export default function SteuerReportCard({ s }: { s: AppState }) {
  const { reportName, setReportName, reportTaxId, setReportTaxId, reportMasked, setReportMasked, canExport, localInfo, cloudFlows } =
    s;
  const [data, setData] = useState<SteuerJahre | null>(null);
  const [jahr, setJahr] = useState<number | null>(null);
  const [note, setNote] = useState<string | null>(null);
  // Anschluss: Herkunftsnachweis mit Stichtag 31.12. dieses Jahres (Vorgabe: Vorjahr; 0 = keiner)
  const [anschlussWahl, setAnschlussWahl] = useState<number | null>(null);
  const [pruefung, setPruefung] = useState<SteuerPruefung | "läuft" | null>(null);

  const load = () => {
    getSteuerJahre()
      .then((d) => {
        setData(d);
        setJahr((j) => j ?? d.vorgabe);
      })
      .catch((e: Error) => setNote(`Jahre nicht geladen: ${e.message}`));
  };
  useEffect(load, [localInfo, cloudFlows]);
  // Herkunftsnachweis in einem neuen Tab erzeugt → beim Zurückkehren Anschluss neu laden
  useEffect(() => {
    window.addEventListener("focus", load);
    return () => window.removeEventListener("focus", load);
  }, []);

  // Anschluss (Abschnitt 1.1): -1 = Herkunftsnachweis auf Anforderung (Standard), 0 = ausdrücklich
  // keiner (Warnung), Jahr = in dieser Sitzung erzeugter Herkunftsnachweis mit Stichtag 31.12.
  const anschlussJahr =
    anschlussWahl !== null && (anschlussWahl <= 0 || data?.herkunft[String(anschlussWahl)]) ? anschlussWahl : -1;
  const opts = { privacy: reportMasked, name: reportName, steuerId: reportTaxId, anschlussJahr };
  // Stichtage bis zum Vorjahr des gewählten Jahres, ab dem ersten Jahr mit Daten
  const firstYear = data?.jahre[0]?.jahr;
  const anschlussJahre =
    jahr !== null && firstYear !== undefined
      ? Array.from({ length: Math.max(0, jahr - firstYear) }, (_, i) => jahr - 1 - i)
      : [];
  const selected = data?.jahre.find((j) => j.jahr === jahr);
  const ready = canExport && jahr !== null && !!selected?.regelwerk;
  const protokollUrl =
    ready && jahr !== null ? steuerReportUrl(jahr, "intern", opts).replace("/steuer.pdf", "/steuer-pruefprotokoll.pdf") : "";
  const pruefUrl = protokollUrl.replace("/steuer-pruefprotokoll.pdf", "/steuer-pruefung");
  const erzeugt = anschlussJahr > 0 ? data?.herkunft[String(anschlussJahr)] : undefined;
  const oeffnenJahr = anschlussJahr > 0 ? anschlussJahr : jahr !== null ? jahr - 1 : 0;
  const herkunftKey = JSON.stringify(data?.herkunft ?? {});

  // Prüfergebnis im Hintergrund, sobald Jahr/Anschluss/Daten feststehen — für die Anzeige am Button
  useEffect(() => {
    if (!pruefUrl) {
      setPruefung(null);
      return;
    }
    let cancelled = false;
    setPruefung("läuft");
    // kurz warten — Name/Steuer-ID werden getippt
    const timer = window.setTimeout(() => {
      getSteuerPruefung(pruefUrl)
        .then((r) => {
          if (!cancelled) setPruefung(r);
        })
        .catch(() => {
          if (!cancelled) setPruefung(null);
        });
    }, 600);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [pruefUrl, localInfo, cloudFlows, herkunftKey]);

  const badge =
    pruefung === "läuft"
      ? { text: "… wird geprüft", cls: "" }
      : pruefung?.ergebnis === "bestanden"
        ? { text: "✓ bestanden", cls: " pruef-ok" }
        : pruefung?.ergebnis === "warnung"
          ? { text: `⚠ bestanden mit ${pruefung.warnungen} Warnung(en)`, cls: " pruef-warn" }
          : pruefung?.ergebnis === "fehler"
            ? { text: `✗ Fehler (${pruefung.fehler.length})`, cls: " pruef-fehler" }
            : null;
  return (
    <section className="collapsible report-box">
      <details>
        <summary>
          <h2>Finanzamt: Jahressteuerreport Bitcoin</h2>
        </summary>
      <p className="muted">
        Ein Kalenderjahr (01.01.–31.12., UTC): Seite 1 mit privaten Veräußerungsgeschäften (§ 23 EStG) und
        Einkünften nach § 22 Nr. 3 EStG, danach Mengenabstimmung, Veräußerungen mit Herkunft der Teilbestände,
        Anschaffungen, sonstige Bewegungen, offene Punkte und Methodik. Werte wie im Herkunftsnachweis; Name,
        Steuer-ID und „maskiert“ gelten auch für die Herkunftsanalyse. Öffnet im Browser.
      </p>
      <div className="report-form">
        <label>
          <span>Jahr</span>
          <select value={jahr ?? ""} onChange={(e) => {
              setJahr(e.target.value ? Number(e.target.value) : null);
              setAnschlussWahl(null); // Vorgabe: Herkunftsnachweis auf Anforderung
            }}>
            {!data?.jahre.length && <option value="">— keine Transaktionsdaten —</option>}
            {data?.jahre.map((j) => (
              <option key={j.jahr} value={j.jahr} disabled={!j.regelwerk} title={j.vermerk}>
                {j.jahr}
                {j.regelwerk ? "" : " — Regelwerk fehlt"}
              </option>
            ))}
          </select>
        </label>
        <label title="Abschnitt 1.1. Standard: Herkunftsnachweis auf Anforderung. Einen bestimmten Herkunftsnachweis nur wählen, wenn er bereits vorgelegt wurde — er muss in dieser Sitzung erzeugt sein (Stand, Stichtag, Build des Dokuments).">
          <span>Anschluss an Herkunftsnachweis</span>
          <select value={anschlussJahr} onChange={(e) => setAnschlussWahl(Number(e.target.value))} disabled={jahr === null}>
            <option value={-1}>auf Anforderung (Standard)</option>
            {anschlussJahre.map((y) => (
              <option key={y} value={y} disabled={!data?.herkunft[String(y)]}>
                mit Stichtag 31.12.{y}
                {data?.herkunft[String(y)] ? ` — erzeugt ${data.herkunft[String(y)].stand}` : " — in dieser Sitzung nicht erzeugt"}
              </option>
            ))}
            <option value={0}>kein Herkunftsnachweis (Warnung)</option>
          </select>
        </label>
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
        <label className="report-check">
          <input type="checkbox" checked={reportMasked} onChange={(e) => setReportMasked(e.target.checked)} />
          <span>maskiert (Beträge, Steuer-ID und Hashes verdeckt, Namen lesbar)</span>
        </label>
        <a
          className={`button-link${!ready ? " disabled" : ""}`}
          href={ready && jahr !== null ? steuerReportUrl(jahr, "intern", opts) : undefined}
          target="_blank"
          rel="noopener"
          aria-disabled={!ready}
        >
          Interne Fassung öffnen
        </a>
        <a
          className={`button-link${!ready ? " disabled" : ""}`}
          href={ready && jahr !== null ? steuerReportUrl(jahr, "finanzamt", opts) : undefined}
          target="_blank"
          rel="noopener"
          aria-disabled={!ready}
        >
          Finanzamt-Fassung öffnen
        </a>
        <a
          className={`button-link${!ready ? " disabled" : ""}${badge?.cls ?? ""}`}
          href={protokollUrl || undefined}
          target="_blank"
          rel="noopener"
          aria-disabled={!ready}
          title={
            pruefung && pruefung !== "läuft" && pruefung.fehler.length
              ? `Nicht bestanden: ${pruefung.fehler.join("; ")}`
              : "Beide Fassungen werden erzeugt und geprüft (Abnahmeprüfungen 1–7, Regelwerk, Hashes)"
          }
        >
          Prüfprotokoll öffnen{badge ? ` — ${badge.text}` : ""}
        </a>
      </div>
      {jahr !== null && oeffnenJahr > 0 && (
        <p className="muted">
          {erzeugt
            ? `Anschluss: Herkunftsnachweis Stand ${erzeugt.stand}, Stichtag ${erzeugt.stichtag}, Build ${erzeugt.build}. `
            : "Wurde ein bestimmter Herkunftsnachweis bereits vorgelegt, ihn hier erzeugen und oben wählen. "}
          Herkunftsnachweis mit Stichtag 31.12.{oeffnenJahr} öffnen:{" "}
          <a href={herkunftBisUrl(oeffnenJahr, "intern", opts)} target="_blank" rel="noopener">
            interne Fassung
          </a>
          {" · "}
          <a href={herkunftBisUrl(oeffnenJahr, "finanzamt", opts)} target="_blank" rel="noopener">
            Finanzamt-Fassung
          </a>
        </p>
      )}
      {selected && !selected.regelwerk && <p className="warn">{selected.vermerk}</p>}
      {note && <p className="muted">{note}</p>}
      </details>
    </section>
  );
}
