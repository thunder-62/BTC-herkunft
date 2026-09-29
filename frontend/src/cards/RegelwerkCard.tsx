import { useEffect, useRef, useState } from "react";
import {
  applyKategorien,
  comparePdfs,
  discardKategorien,
  downloadText,
  getKategorien,
  previewKategorien,
  saveKategorien,
  type KategorienAenderung,
  type KategorienInfo,
  type ManuelleZuordnung,
  type PdfVergleich,
} from "../api";
import { displayBtc } from "../privacy";

/** Regelwerk & Kategorien: Zeilenarten der Börsen-Exporte zuordnen, einzelne Zuflüsse
 *  einordnen (z. B. Empfehlungsprämie), local/kategorien.yaml als Vorschau, Download oder —
 *  nur nach Bestätigung — speichern; zwei Berichts-PDFs vergleichen. */

const SUGGEST: Record<string, string> = {
  Kauf: "kauf",
  Verkauf: "verkauf",
  Auszahlung: "auszahlung",
  Einzahlung: "einzahlung",
};
const BEHANDLUNG: Record<string, string> = {
  anschaffung: "Anschaffung",
  einkunft_22_3: "Einkunft § 22 Nr. 3",
  nicht_unterstuetzt: "nicht unterstützt",
};
const TECHNISCH: Record<string, string> = {
  verkauf: "Verkauf (Veräußerung)",
  auszahlung: "Auszahlung (Umbuchung in eine Wallet)",
  einzahlung: "Einzahlung (Umbuchung auf die Börse)",
};
const key = (exchange: string, art: string) => `${exchange}\u0000${art}`;

function satsToBtc(sats: number): string {
  return (sats / 1e8).toFixed(8);
}

export default function RegelwerkCard({
  privacy,
  local,
  onSaved,
}: {
  privacy: boolean;
  local: unknown;
  /** nach dem Speichern: lokale Daten neu melden (Steuer-Report prüft neu) */
  onSaved?: () => void;
}) {
  const [data, setData] = useState<KategorienInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [arten, setArten] = useState<Record<string, string>>({});
  const [manuell, setManuell] = useState<Record<string, ManuelleZuordnung | null>>({});
  const [preview, setPreview] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [pdfAlt, setPdfAlt] = useState<File | null>(null);
  const [pdfNeu, setPdfNeu] = useState<File | null>(null);
  const [vergleich, setVergleich] = useState<PdfVergleich | null>(null);
  const [jahr, setJahr] = useState<string>("");

  const load = () =>
    getKategorien()
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  useEffect(() => {
    void load();
  }, [local]);

  // Änderungen gelten sofort für die Sitzung (nur im Arbeitsspeicher); Speichern ist optional.
  const pending = useRef("");
  useEffect(() => {
    const snap = JSON.stringify({ arten, manuell });
    pending.current = snap;
    if (Object.keys(arten).length === 0 && Object.keys(manuell).length === 0) return;
    const timer = window.setTimeout(() => {
      const exp: Record<string, Record<string, string>> = {};
      for (const [k, v] of Object.entries(arten)) {
        const [exchange, art] = k.split("\u0000");
        (exp[exchange] ??= {})[art] = v;
      }
      applyKategorien({ export: exp, manuell })
        .then(() => load())
        .then(() => {
          if (pending.current === snap) {
            setArten({});
            setManuell({});
          }
          setNote(null);
          onSaved?.();
        })
        .catch((e: Error) => setNote(`Nicht übernommen: ${e.message}`));
    }, 400);
    return () => window.clearTimeout(timer);
  }, [arten, manuell]);

  if (!data) {
    return error ? (
      <section className="collapsible">
        <p className="status error">Regelwerk: {error}</p>
      </section>
    ) : null;
  }

  const offen = data.arten.filter((a) => a.status !== "ok").length;
  // Auswahl nach Jahr, Sortierung nach dem ersten Vorkommen (im gewählten Jahr)
  const jahre = [
    ...new Set([...data.arten.flatMap((a) => Object.keys(a.jahre)), ...data.zufluesse.map((z) => z.day.slice(0, 4))]),
  ]
    .filter(Boolean)
    .sort();
  const firstIn = (a: (typeof data.arten)[number]) => (jahr ? a.erste_je_jahr[jahr] : a.erste) ?? "";
  const artenShown = data.arten
    .filter((a) => !jahr || a.jahre[jahr])
    .sort((x, y) => firstIn(x).localeCompare(firstIn(y)) || x.exchange.localeCompare(y.exchange));
  const kat = Object.fromEntries(data.kategorien.map((k) => [k.name, k]));
  const label = (n: string) => {
    const k = kat[n];
    if (!k) return TECHNISCH[n] ?? n;
    return k.behandlung === "nicht_unterstuetzt"
      ? k.anzeigename
      : `${k.anzeigename} (${BEHANDLUNG[k.behandlung] ?? k.behandlung})`;
  };
  const current = (exchange: string, art: string, fallback: string) => arten[key(exchange, art)] ?? fallback;
  const change = (): KategorienAenderung => {
    const exp: Record<string, Record<string, string>> = {};
    for (const [k, v] of Object.entries(arten)) {
      const [exchange, art] = k.split("\u0000");
      (exp[exchange] ??= {})[art] = v;
    }
    return { export: exp, manuell };
  };
  const dirty =
    Object.keys(arten).length > 0 || Object.keys(manuell).length > 0 || data.sitzung.ungespeichert;

  const suggest = () => {
    const next = { ...arten };
    for (const a of data.arten) {
      const s = SUGGEST[a.richtung];
      if (a.status !== "ok" && !a.zuordnung && s && next[key(a.exchange, a.art)] === undefined) {
        next[key(a.exchange, a.art)] = s;
      }
    }
    setArten(next);
  };

  const run = async (what: "preview" | "download" | "save") => {
    setNote(null);
    try {
      const p = await previewKategorien(change());
      if (what === "preview") {
        setPreview(p.yaml);
      } else if (what === "download") {
        downloadText(p.yaml, "kategorien.yaml");
        setNote("Heruntergeladen — Datei als local/kategorien.yaml ablegen und „Lokale Dateien neu laden“.");
      } else {
        const ok = window.confirm(
          `Die App schreibt jetzt die Datei\n${p.file}\n\n` +
            "Die bisherige Fassung bleibt als kategorien.yaml.bak erhalten. Sonst wird nichts geschrieben.\n\nSpeichern?",
        );
        if (!ok) return;
        const r = await saveKategorien(p.yaml);
        setArten({});
        setManuell({});
        setPreview(null);
        setNote(`Gespeichert: ${r.file}${r.backup ? ` (Sicherung: ${r.backup})` : ""}. Export-Dateien neu eingelesen.`);
        void load();
        onSaved?.();
      }
    } catch (e) {
      setNote(`Nicht übernommen: ${(e as Error).message}`);
    }
  };

  const zufluesse = data.zufluesse
    .filter((z) => !z.belegt || data.manuell[z.tnr] || manuell[z.tnr])
    .filter((z) => !jahr || z.day.startsWith(jahr))
    .sort((x, y) => x.day.localeCompare(y.day) || x.tnr.localeCompare(y.tnr));
  const inflowOptions = data.kategorien.filter((k) => k.behandlung !== "anschaffung");

  return (
    <section className="collapsible regelwerk">
      <details>
        <summary>
          <h2>
            Regelwerk &amp; Kategorien{" "}
            <span className="muted count">({offen === 0 ? "alles zugeordnet" : `${offen} nicht unterstützt`})</span>
          </h2>
        </summary>
        <p className="muted">
          Jede Zeilenart eines Börsen-Exports braucht eine Kategorie (<code>btc-regeln/kategorien.yaml</code>,
          ergänzt durch <code>local/kategorien.yaml</code>). Nicht zugeordnete oder nicht unterstützte Zeilen
          werden nicht bewertet und im Bericht in Abschnitt 8.3 ausgewiesen. Änderungen gelten erst nach dem
          Speichern — die App schreibt nur nach deiner Bestätigung.
        </p>

        <p>
          <label>
            Jahr{" "}
            <select value={jahr} onChange={(e) => setJahr(e.target.value)}>
              <option value="">alle Jahre</option>
              {jahre.map((y) => (
                <option key={y} value={y}>
                  {y}
                </option>
              ))}
            </select>
          </label>{" "}
          <span className="muted">
            Sortiert nach dem ersten Vorkommen{jahr ? ` im Jahr ${jahr}` : ""}. Eine Zuordnung gilt für alle Jahre.
          </span>
        </p>

        <h3>Zeilenarten der Börsen-Exporte</h3>
        <p className={data.aenderungen.wirkt_auf_werte ? "warn" : "muted"}>
          Gegenüber der bisherigen Auswertung anders behandelt: {data.aenderungen.zeilen} Zeile(n)
          {data.aenderungen.zeilen > 0 && <>, davon {data.aenderungen.wirkt_auf_werte} mit Wirkung auf die Werte</>}
          {" · "}
          <a href="/api/regelwerk/aenderungen.csv" download="kategorien-aenderungen.csv">
            Liste als CSV (Börse, Art, Datum, Menge)
          </a>
        </p>
        {data.arten.length === 0 ? (
          <p className="muted">Keine Börsen-Exporte in local/boersen/.</p>
        ) : (
          <>
            <p>
              <button type="button" className="linkish" onClick={suggest}>
                Vorschläge für nicht zugeordnete Arten einsetzen
              </button>{" "}
              <span className="muted">(aus der Richtung laut Export — bitte prüfen)</span>
            </p>
            <table className="regelwerk-table">
              <thead>
                <tr>
                  <th>erstes Vorkommen</th>
                  <th>Börse</th>
                  <th>Art laut Export</th>
                  <th>Zeilen</th>
                  <th>Richtung</th>
                  <th>Kategorie</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {artenShown.map((a) => {
                  const value = current(a.exchange, a.art, a.zuordnung);
                  const changed = arten[key(a.exchange, a.art)] !== undefined;
                  return (
                    <tr key={key(a.exchange, a.art)} className={a.status !== "ok" && !changed ? "warn" : undefined}>
                      <td>{firstIn(a)}</td>
                      <td>{a.exchange}</td>
                      <td>
                        <code>{a.art || "(leer)"}</code>
                      </td>
                      <td className="num">{jahr ? a.jahre[jahr] : a.count}</td>
                      <td>{a.richtung}</td>
                      <td>
                        <select
                          value={value}
                          onChange={(e) => setArten({ ...arten, [key(a.exchange, a.art)]: e.target.value })}
                        >
                          <option value="">— nicht zugeordnet —</option>
                          {value && !a.passend.includes(value) && (
                            <option value={value}>⚠ {label(value)} — passt nicht zur Richtung</option>
                          )}
                          <optgroup label={`passt zu: ${a.richtung}`}>
                            {a.passend
                              .filter((n) => kat[n]?.behandlung !== "nicht_unterstuetzt")
                              .map((n) => (
                                <option key={n} value={n}>
                                  {label(n)}
                                </option>
                              ))}
                          </optgroup>
                          <optgroup label="nicht unterstützt (nur Kennzeichnung)">
                            {a.passend
                              .filter((n) => kat[n]?.behandlung === "nicht_unterstuetzt")
                              .map((n) => (
                                <option key={n} value={n}>
                                  {label(n)}
                                </option>
                              ))}
                          </optgroup>
                        </select>
                      </td>
                      <td>
                        {changed
                          ? "wird übernommen …"
                          : (a.status === "ok" ? "✓" : a.grund) +
                            (a.ungespeichert ? " — gilt in dieser Sitzung, nicht gespeichert" : "")}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </>
        )}

        <h3>Einzelne Zuflüsse einordnen</h3>
        <p className="muted">
          Zuflüsse ohne Kaufbeleg — z. B. eine Empfehlungsprämie, die nicht im Export steht. Gespeichert wird
          per Transaktions-Hash (stabil) mit Datum zur Kontrolle; die T-Nummer dient nur der Anzeige.
        </p>
        {zufluesse.length === 0 ? (
          <p className="muted">Keine Zuflüsse ohne Kaufbeleg.</p>
        ) : (
          <table className="regelwerk-table">
            <thead>
              <tr>
                <th>T-Nr.</th>
                <th>Datum</th>
                <th>Wallet</th>
                <th>Absender</th>
                <th>Menge (BTC)</th>
                <th>Kategorie</th>
                <th>Erläuterung</th>
              </tr>
            </thead>
            <tbody>
              {zufluesse.map((z) => {
                const m = manuell[z.tnr] !== undefined ? manuell[z.tnr] : data.manuell[z.tnr] ?? null;
                const set = (patch: Partial<ManuelleZuordnung>) => {
                  const next = { kategorie: m?.kategorie ?? "", datum: z.day, erlaeuterung: m?.erlaeuterung ?? "", ...patch };
                  setManuell({ ...manuell, [z.tnr]: next.kategorie ? next : null });
                };
                return (
                  <tr key={z.tnr}>
                    <td>{z.tnr}</td>
                    <td>{z.day}</td>
                    <td>{z.wallet}</td>
                    <td>{z.source}</td>
                    <td className="num">{displayBtc(privacy, z.sats, satsToBtc)}</td>
                    <td>
                      <select value={m?.kategorie ?? ""} onChange={(e) => set({ kategorie: e.target.value })}>
                        <option value="">— Kauf ohne Beleg (Tageskurs) —</option>
                        {inflowOptions.map((k) => (
                          <option key={k.name} value={k.name}>
                            {k.anzeigename} ({BEHANDLUNG[k.behandlung] ?? k.behandlung})
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <input
                        type="text"
                        value={m?.erlaeuterung ?? ""}
                        disabled={!m?.kategorie}
                        placeholder="optional"
                        maxLength={200}
                        onChange={(e) => set({ erlaeuterung: e.target.value })}
                      />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}

        <p className="muted">
          Änderungen gelten sofort für diese Sitzung (nur im Arbeitsspeicher) — Berichte und Prüfprotokoll
          rechnen damit. Speichern in local/ ist optional; ohne Speichern gehen sie beim Beenden verloren.
          {data.sitzung.ungespeichert && <strong> Es gibt nicht gespeicherte Zuordnungen.</strong>}
        </p>
        <p className="regelwerk-actions">
          <button type="button" disabled={!dirty} onClick={() => run("preview")}>
            Vorschau
          </button>{" "}
          <button type="button" disabled={!dirty} onClick={() => run("download")}>
            kategorien.yaml herunterladen
          </button>{" "}
          <button type="button" disabled={!dirty} onClick={() => run("save")}>
            In local/ speichern … (optional)
          </button>{" "}
          <button
            type="button"
            className="linkish"
            disabled={!dirty}
            onClick={() => {
              setArten({});
              setManuell({});
              setPreview(null);
              discardKategorien()
                .then(() => load())
                .then(() => {
                  setNote("Änderungen verworfen — es gilt wieder die gespeicherte Datei.");
                  onSaved?.();
                })
                .catch((e: Error) => setNote(`Nicht verworfen: ${e.message}`));
            }}
          >
            Änderungen verwerfen
          </button>
        </p>
        {note && <p className="muted">{note}</p>}
        {preview && <pre className="pp-diagnose">{preview}</pre>}

        <h3>Zwei Berichte vergleichen</h3>
        <p className="muted">
          Z. B. vor und nach einer Änderung am Regelwerk. Gezeigt werden nur Abschnitte und die Anzahl geänderter
          Zeilen — keine Werte. Nichts wird gespeichert.
        </p>
        <p>
          <label>
            alt{" "}
            <input type="file" accept="application/pdf" onChange={(e) => setPdfAlt(e.target.files?.[0] ?? null)} />
          </label>{" "}
          <label>
            neu{" "}
            <input type="file" accept="application/pdf" onChange={(e) => setPdfNeu(e.target.files?.[0] ?? null)} />
          </label>{" "}
          <button
            type="button"
            disabled={!pdfAlt || !pdfNeu}
            onClick={() => {
              if (pdfAlt && pdfNeu) {
                comparePdfs(pdfAlt, pdfNeu)
                  .then(setVergleich)
                  .catch((e: Error) => setNote(`Vergleich fehlgeschlagen: ${e.message}`));
              }
            }}
          >
            Vergleichen
          </button>
        </p>
        {vergleich &&
          (vergleich.gleich ? (
            <p>✓ gleich ({vergleich.zeilen} Zeilen verglichen)</p>
          ) : (
            <ul>
              {vergleich.abschnitte.map((a) => (
                <li key={a.abschnitt}>
                  {a.abschnitt}: {a.zeilen} Zeilen geändert
                </li>
              ))}
            </ul>
          ))}
      </details>
    </section>
  );
}
