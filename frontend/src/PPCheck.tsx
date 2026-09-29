import { Fragment, useEffect, useState } from "react";
import { downloadPPImportCsv, getPPCheck, getPPDiagnose, type PPAction, type PPCheck } from "./api";
import { displayBtc, displayName, displayTxid } from "./privacy";

/** Abgleich local/check-pp.csv (Portfolio Performance) ↔ Blockchain.
 *  Nur im Browser — Korrekturliste zur Bereinigung des PP-Depots, nie im
 *  Bericht. Erscheint nur, wenn die Datei existiert. */

const DONE_KEY = "btc-origin.pp-done";
const KEEP_KEY = "btc-origin.pp-keep";

function satsToBtc(sats: number): string {
  return (sats / 1e8).toFixed(8);
}

function signedBtc(sats: number): string {
  return (sats > 0 ? "+" : "") + satsToBtc(sats);
}

function shortHex(s: string, head = 10, tail = 6): string {
  if (!s || s.length <= head + tail + 1) return s;
  return `${s.slice(0, head)}…${s.slice(-tail)}`;
}

function loadSet(key: string): Set<string> {
  try {
    return new Set(JSON.parse(window.localStorage.getItem(key) || "[]") as string[]);
  } catch {
    return new Set();
  }
}

function saveSet(key: string, set: Set<string>): void {
  try {
    window.localStorage.setItem(key, JSON.stringify([...set]));
  } catch {
    /* ohne Speicher nur für diese Ansicht */
  }
}

export default function PPCheckCard({
  privacy,
  flows,
  local,
}: {
  privacy: boolean;
  /** Neu laden nach Sync bzw. „Lokale Dateien neu laden“. */
  flows: unknown;
  local: unknown;
}) {
  const [data, setData] = useState<PPCheck | null>(null);
  const [year, setYear] = useState<number | null>(null);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [done, setDone] = useState<Set<string>>(() => loadSet(DONE_KEY));
  const [keep, setKeep] = useState<Set<string>>(() => loadSet(KEEP_KEY));
  const [exportNote, setExportNote] = useState<string | null>(null);
  const [diag, setDiag] = useState<string | null>(null);
  const [hideDone, setHideDone] = useState(true);

  const load = () => {
    getPPCheck()
      .then(setData)
      .catch(() => setData(null));
  };
  useEffect(load, [flows, local]);

  if (!data || !data.exists) return null;

  const btc = (s: number) => displayBtc(privacy, s, satsToBtc);
  const dBtc = (s: number) => (s === 0 ? "0" : displayBtc(privacy, s, signedBtc));
  const b = data.bestand;
  const actions = data.actions ?? [];
  const openCount = actions.filter((a) => !done.has(a.id)).length;
  const shown = actions.filter(
    (a) => (year === null || a.year === year) && !(hideDone && done.has(a.id)),
  );

  // PP-Zeilen ohne Gegenstück werden in PP gelöscht, außer „behalten“ ist angehakt.
  const keptOnlyPP = actions
    .filter((a) => a.kind === "only_pp")
    .flatMap((a) => a.pp)
    .filter((p) => keep.has(String(p.line)))
    .reduce((sum, p) => sum + (p.direction === "in" ? p.sats : -p.sats), 0);
  const afterImport = b.after_sats + keptOnlyPP;
  const onlyPP = actions.filter((a) => a.kind === "only_pp").flatMap((a) => a.pp);

  const exportCsv = () => {
    downloadPPImportCsv([...keep].map(Number))
      .then((stats) => {
        const m = Object.fromEntries(stats.split(",").map((x) => x.split("=")));
        const delta = Number(m.delta_sats);
        const toDelete = Number(m.to_delete);
        const after = b.pp_sats + delta - Number(m.delete_sats);
        setExportNote(
          `Heruntergeladen — ${m.added ?? 0} neue Zeilen (davon ${m.corrections ?? 0} Mengenkorrekturen), ` +
            `Wirkung auf den PP-Bestand ${dBtc(delta)}.` +
            (toDelete
              ? ` Danach ${toDelete} PP-Zeile${toDelete === 1 ? "" : "n"} ohne Gegenstück von Hand löschen (Liste unten, „entfällt“).`
              : "") +
            ` PP-Bestand dann ${btc(after)} ` +
            (after === b.chain_sats ? "= Blockchain ✓." : `≠ Blockchain (Δ ${dBtc(after - b.chain_sats)}).`) +
            (Number(m.skipped_transfers) ? ` ${m.skipped_transfers} Umbuchungen nicht berücksichtigt.` : "") +
            (Number(m.missing_price) ? ` ${m.missing_price} Zeilen ohne Kurs (Wert leer).` : ""),
        );
      })
      .catch((e) => setExportNote(e instanceof Error ? e.message : String(e)));
  };

  const diagnose = () => {
    getPPDiagnose()
      .then((text) => {
        setDiag(text);
        navigator.clipboard?.writeText(text).catch(() => undefined);
      })
      .catch((e) => setDiag(e instanceof Error ? e.message : String(e)));
  };

  const toggle = (set: Set<string>, id: string) => {
    const next = new Set(set);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    return next;
  };

  const suggestion = (a: PPAction): string => {
    const s = a.suggest;
    switch (s.code) {
      case "in_fee":
        return `Auszahlungsgebühr ${btc(s.sats ?? 0)} als Auslieferung buchen — oder Stück auf ${btc(s.target_sats ?? 0)} ändern`;
      case "in_more":
        return `Stück auf ${btc(s.target_sats ?? 0)} erhöhen`;
      case "out_fee":
        return `Netzwerkgebühr ${btc(s.sats ?? 0)} als Auslieferung buchen`;
      case "out_set":
        return `Abgang auf ${btc(s.target_sats ?? 0)} ändern (inkl. Netzwerkgebühr)`;
      case "book":
        return `${s.text} in PP buchen (Abgänge inkl. Netzwerkgebühr)`;
      case "loose":
        return `Stück auf ${btc(s.target_sats ?? 0)} setzen (Blockchain ${s.day}), Wert im selben Verhältnis`;
      case "book_fees":
        return `${s.text} — Summe als eine Auslieferung buchen`;
      default:
        return "in PP löschen — oder „behalten“ anhaken, falls die Bitcoin noch auf einer Börse liegen";
    }
  };

  return (
    <section className="collapsible pp-check">
      <details>
        <summary>
          <h2>
            Abgleich Portfolio Performance{" "}
            <span className="muted count">
              ({openCount === 0 ? "stimmt" : `${openCount} offen`})
            </span>
          </h2>
        </summary>
        <p className="muted">
          Nur zur Bereinigung von PP, erscheint nicht im Bericht. Quelle{" "}
          <code>local/{data.file}</code>.{" "}
          <button type="button" className="linkish" onClick={load}>
            neu laden
          </button>
          {" · "}
          <button type="button" className="linkish" onClick={diagnose}>
            Diagnose kopieren (ohne Bestände)
          </button>
          {" · "}
          <a href="/api/check/pp/ist.csv" download="ist-stand-blockchain.csv">
            Ist-Stand (Blockchain) als CSV
          </a>
        </p>
        {diag && <pre className="pp-diagnose">{diag}</pre>}
        {data.errors && data.errors.length > 0 && (
          <p className="warn">{data.errors.slice(0, 3).join(" · ")}</p>
        )}
        {!data.synced && <p className="warn">Noch kein Sync — Blockchain-Seite leer.</p>}
        {data.securities && (
          <p className="muted" style={{ fontSize: "0.85em" }}>
            Gezählt als Bitcoin:{" "}
            {Object.entries(data.securities)
              .map(([n, c]) => `${n} (${c} Zeilen)`)
              .join(", ") || "—"}
            {data.ignored_securities && Object.keys(data.ignored_securities).length > 0 && (
              <>
                {" · "}
                <span className="warn">
                  nicht gezählt (Fonds/ETP o. ä., Stück ≠ BTC):{" "}
                  {Object.entries(data.ignored_securities)
                    .map(([n, c]) => `${n} (${c})`)
                    .join(", ")}
                </span>
              </>
            )}
          </p>
        )}

        <div className="pp-summary">
          <div>
            <span className="muted">PP-Bestand</span>
            <strong>{btc(b.pp_sats)}</strong>
          </div>
          <div>
            <span className="muted">Blockchain</span>
            <strong>{btc(b.chain_sats)}</strong>
          </div>
          <div>
            <span className="muted">Δ PP − Blockchain</span>
            <strong className={b.delta_sats === 0 ? "ok" : "warn"}>{dBtc(b.delta_sats)}</strong>
          </div>
          <div>
            <span className="muted">PP-Bestand nach Delta-Import</span>
            <strong className={afterImport === b.chain_sats ? "ok" : "warn"}>
              {btc(afterImport)}{" "}
              <small>
                {afterImport === b.chain_sats ? "= Blockchain" : `Δ ${dBtc(afterImport - b.chain_sats)}`}
              </small>
            </strong>
          </div>
        </div>
        <p className="muted" style={{ fontSize: "0.85em" }}>
          ✓ {data.ok.total} Buchungen stimmen (exakt {data.ok.exact}, Datum verschoben{" "}
          {data.ok.date}, Sammelbuchung {data.ok.grouped}, Gebühr separat gebucht{" "}
          {data.ok.fees}, per Delta-Import korrigiert {data.ok.corrected ?? 0}) und werden nicht
          angezeigt.
          {keptOnlyPP !== 0 &&
            ` Δ nach Import = behaltene PP-Zeilen ohne Gegenstück (${dBtc(keptOnlyPP)}).`}
          {afterImport - keptOnlyPP !== b.chain_sats &&
            " Differenz ohne behaltene Zeilen — Bewegungen außerhalb der Zu- und Abflüsse, bitte melden."}
        </p>

        <div className="pp-export">
          <div>
            <strong>Delta importieren:</strong> Die Datei enthält nur, was in PP fehlt — fehlende
            Bewegungen, Gebühren und Mengenkorrekturen (als Differenz), jeweils als
            Ein-/Auslieferung zum Tageskurs und ohne Verrechnungskonto. Deine bestehenden
            Buchungen bleiben unverändert, samt Konten und Währungen.
            <ol>
              <li>PP-Datei sichern.</li>
              <li>
                PP: Datei → Importieren → CSV-Dateien → <em>Depotumsätze</em>, Depot wählen.
              </li>
              <li>
                PP-Zeilen ohne Blockchain-Gegenstück (Liste unten) von Hand löschen — außer du
                hakst „behalten“ an (z. B. Bitcoin noch auf einer Börse).
              </li>
              <li>Neu exportieren als <code>local/check-pp.csv</code> → hier „neu laden“.</li>
            </ol>
            <span className="muted">
              Umbuchungen zwischen PP-Depots sind nicht enthalten — alles kommt in ein Depot.
            </span>
          </div>
          <div className="pp-export-action">
            <button type="button" onClick={exportCsv} disabled={!data.synced}>
              Delta-CSV für PP
            </button>
          </div>
        </div>
        {exportNote && <p className="muted">{exportNote}</p>}

        {onlyPP.length > 0 && (
          <div className="pp-delete">
            <strong>
              In PP von Hand löschen ({onlyPP.filter((p) => !keep.has(String(p.line))).length} von{" "}
              {onlyPP.length}):
            </strong>{" "}
            <span className="muted">
              PP-Buchungen ohne Blockchain-Gegenstück — ein Import kann sie nicht entfernen. Liegen
              die Bitcoin noch auf einer Börse, „behalten“ anhaken.
            </span>
            <div className="table-wrap">
              <table className="pp-actions">
                <thead>
                  <tr>
                    <th>Datum</th>
                    <th>Typ</th>
                    <th className="num">Stück</th>
                    <th>Notiz</th>
                    <th>behalten</th>
                  </tr>
                </thead>
                <tbody>
                  {onlyPP.map((p) => (
                    <tr key={p.line} className={keep.has(String(p.line)) ? "pp-done" : undefined}>
                      <td className="nowrap">{p.day}</td>
                      <td>{p.type}</td>
                      <td className="num">{btc(p.sats)}</td>
                      <td>{p.note ? displayName(privacy, p.note) : ""}</td>
                      <td>
                        <input
                          type="checkbox"
                          checked={keep.has(String(p.line))}
                          onChange={() => {
                            const next = toggle(keep, String(p.line));
                            setKeep(next);
                            saveSet(KEEP_KEY, next);
                          }}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        <div className="pp-years">
          <button
            type="button"
            className={year === null ? "chip active" : "chip"}
            onClick={() => setYear(null)}
          >
            alle · {actions.length}
          </button>
          {data.years.map((y) => (
            <button
              key={y.year}
              type="button"
              className={`chip${year === y.year ? " active" : ""}${y.open === 0 ? " done" : ""}`}
              onClick={() => setYear(year === y.year ? null : y.year)}
              disabled={y.open === 0}
              title={y.open ? `Wirkung auf PP-Bestand ${dBtc(y.effect_sats)}` : "stimmt"}
            >
              {y.year} · {y.open === 0 ? "✓" : y.open}
            </button>
          ))}
          <label className="pp-hide">
            <input
              type="checkbox"
              checked={hideDone}
              onChange={(e) => setHideDone(e.target.checked)}
            />{" "}
            erledigte ausblenden
          </label>
        </div>

        {shown.length === 0 ? (
          <p className="muted">Nichts zu korrigieren.</p>
        ) : (
          <div className="table-wrap">
            <table className="pp-actions">
              <thead>
                <tr>
                  <th title="erledigt">✓</th>
                  <th>Datum</th>
                  <th>Korrektur in PP</th>
                  <th title="Wirkung auf den PP-Bestand, wenn umgesetzt">Wirkung</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((a) => (
                  <Fragment key={a.id}>
                    <tr className={done.has(a.id) ? "pp-done" : undefined}>
                      <td>
                        <input
                          type="checkbox"
                          checked={done.has(a.id)}
                          onChange={() => {
                            const next = toggle(done, a.id);
                            setDone(next);
                            saveSet(DONE_KEY, next);
                          }}
                        />
                      </td>
                      <td className="nowrap">{a.day}</td>
                      <td>
                        <button
                          type="button"
                          className="linkish pp-title"
                          onClick={() => setOpen(toggle(open, a.id))}
                        >
                          {open.has(a.id) ? "▾" : "▸"} {displayName(privacy, a.title)}
                        </button>
                        <div className="muted">{suggestion(a)}</div>
                      </td>
                      <td className="num">{dBtc(a.effect_sats)}</td>
                    </tr>
                    {open.has(a.id) && (
                      <tr className="pp-detail">
                        <td />
                        <td colSpan={3}>
                          <ul>
                            {a.pp.map((p) => (
                              <li key={`pp${p.line}`}>
                                PP Zeile {p.line} · {p.day} · {p.type} · {btc(p.sats)}
                                {p.note ? ` · ${p.note}` : ""}
                                {a.kind === "only_pp" && (
                                  <label className="pp-drop">
                                    <input
                                      type="checkbox"
                                      checked={keep.has(String(p.line))}
                                      onChange={() => {
                                        const next = toggle(keep, String(p.line));
                                        setKeep(next);
                                        saveSet(KEEP_KEY, next);
                                      }}
                                    />{" "}
                                    behalten (noch auf der Börse)
                                  </label>
                                )}
                              </li>
                            ))}
                            {a.chain.map((c) => (
                              <li key={`c${c.direction}${c.txid}`}>
                                Blockchain · {c.day} ·{" "}
                                {c.direction === "in" ? "Einlieferung" : "Auslieferung"} ·{" "}
                                {btc(c.sats)}
                                {c.fee_sats ? ` + Gebühr ${btc(c.fee_sats)}` : ""} ·{" "}
                                {displayName(privacy, c.wallet)} ·{" "}
                                <code>{displayTxid(privacy, c.txid, shortHex)}</code>
                              </li>
                            ))}
                          </ul>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="muted" style={{ fontSize: "0.8em" }}>
          Zuordnung: gleiche Richtung, ±{data.tolerance?.days} Tage, Menge ± max(0,001 BTC, 3 %);
          mehrere PP-Käufe bis {data.tolerance?.agg_days} Tage vor einer Auszahlung zählen als
          Sammelbuchung. Wirkung = Änderung des PP-Bestands, wenn die Korrektur umgesetzt wird.
          Abhaken merkt sich dieser Browser.
        </p>
      </details>
    </section>
  );
}
