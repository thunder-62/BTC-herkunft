import {
  Fragment,
} from "react";
import {
  reloadLocalFiles,
  downloadSearchList,
} from "../api";
import {
  displayName,
  displayBtc,
  displayAddress,
} from "../privacy";
import {
  TrailButton,
  TrailRow,
} from "../TrailCell";
import type { AppState } from "../appState";
import { satsToBtc, shortHex } from "../appShared";

export default function ExternalCard({ s }: { s: AppState }) {
  const { privacyMode, setStatusNote, setErrorNote, busy, externals, trail, localInfo, setLocalInfo, refreshFlowsAndLabels } = s;
  return (
    <section className="collapsible">
      <details>
      <summary>
        <h2>
          Externe Adressen <span className="muted count">({externals.length})</span>
        </h2>
      </summary>
      <p className="muted">
        Fremde Gegenstellen: Absender von Cloud-Eintritten und Ziele von Cloud-Austritten.
        Gebündelt (heuristisch) werden Absender-Adressen, die gemeinsam in einer Transaktion
        vorkommen, und Einzahlungsadressen, die später gemeinsam eingesammelt wurden.
        „vermutlich Börse“ = Sammelauszahlung (≥5 Empfänger) oder wiederholt genutzte
        Hot-Wallet. Name: eigener Name (CSV-Import unter Export) &gt; öffentliches Label &gt;{" "}
        <code>ext-NNN</code>.
      </p>
      <p className="muted" style={{ fontSize: "0.85em" }}>
        Lokaler Ordner{" "}
        <code className={privacyMode ? "privacy-mask" : undefined}>
          {localInfo?.directory || "local/"}
        </code>{" "}
        (nicht im Git, wird nur gelesen):{" "}
        {localInfo?.names_file
          ? `externe-adressen.csv mit ${localInfo.names_count} Namen`
          : "keine externe-adressen.csv"}
        {localInfo?.date_rules && localInfo.date_rules.length > 0
          ? ` · zuordnung.csv: ${localInfo.date_rules
              .map((r) => `${r.name} bis ${r.bis}`)
              .join(", ")} (Angabe, im Bericht mit ³)`
          : ""}
        {" · "}
        {localInfo && localInfo.exchange_files.length > 0
          ? `Börsen-Exporte: ${localInfo.exchange_files
              .map(
                (f) =>
                  `${f.name} (${f.txids} TxIDs${
                    f.amount_rows ? `, ${f.amount_rows} Zeilen Datum/Betrag` : ""
                  })`,
              )
              .join(", ")}`
          : "keine Börsen-Exporte in boersen/"}
        {localInfo && localInfo.errors.length > 0
          ? ` · Fehler: ${localInfo.errors.slice(0, 3).join("; ")}`
          : ""}{" "}
        <button
          type="button"
          className="linkish"
          disabled={busy !== null}
          onClick={() => {
            void (async () => {
              try {
                const r = await reloadLocalFiles();
                setLocalInfo(r.local);
                await refreshFlowsAndLabels();
                setStatusNote("Lokale Dateien neu geladen.");
              } catch (e) {
                setErrorNote(e instanceof Error ? e.message : String(e));
              }
            })();
          }}
        >
          Lokale Dateien neu laden
        </button>{" "}
        ·{" "}
        <button
          type="button"
          className="linkish"
          title="Ungeklärte Gegenstellen mit Datum, Menge und Wert — zum Abgleich mit Kontoauszügen und E-Mails"
          onClick={() => {
            downloadSearchList().catch((e) =>
              setErrorNote(e instanceof Error ? e.message : String(e)),
            );
          }}
        >
          Suchliste herunterladen
        </button>{" "}
        ·{" "}
        <button
          type="button"
          className="linkish"
          disabled={trail.running}
          title="Spurensuche für alle ungeklärten Gegenstellen nacheinander (kann einige Minuten dauern)"
          onClick={() =>
            void trail.runAll(externals.filter((x) => x.auto_named || x.declared).map((x) => x.name))
          }
        >
          {trail.running ? "prüfe alle…" : "Alle ungeklärten prüfen"}
        </button>
      </p>
      {externals.length === 0 ? (
        <p className="muted">Keine Cloud-Austritte an bekannte Adressen.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Adresse(n)</th>
                <th>Zuflüsse von hier</th>
                <th>Abflüsse dorthin</th>
                <th>Zeitraum</th>
                <th title="Spurensuche: wohin bzw. woher die Bitcoin dieser Gegenstelle flossen">Spur</th>
              </tr>
            </thead>
            <tbody>
              {externals.map((x) => (
                <Fragment key={x.address}>
                <tr>
                  <td className={privacyMode ? "privacy-mask" : undefined}>
                    {x.auto_named ? x.name : <strong>{displayName(privacyMode, x.name)}</strong>}
                    {x.declared ? (
                      <span className="badge-mini gap" title={x.declared}>
                        {" "}Angabe
                      </span>
                    ) : null}
                    {x.label ? (
                      <span
                        className="badge-mini hint"
                        title="Adresse steht in einem öffentlichen Label-Pack. Zeigt, wem die Wallet gehört — nicht zwingend, wo du gekauft hast (Dienste zahlen teils über Börsen-Wallets aus)."
                      >
                        {" "}Label
                      </span>
                    ) : null}
                    {(x.exchange_hints?.length ?? 0) > 0 ? (
                      <span
                        className="badge-mini"
                        title={`Hinweis (heuristisch): ${x.exchange_hints!.join(", ")}`}
                      >
                        vermutlich Börse
                      </span>
                    ) : null}
                  </td>
                  <td
                    title={
                      privacyMode ? undefined : (x.addresses ?? [x.address]).join("\n")
                    }
                  >
                    <code className={privacyMode ? "privacy-mask" : undefined}>
                      {displayAddress(privacyMode, x.address, shortHex, 12, 6)}
                    </code>
                    {(x.addresses?.length ?? 1) > 1 ? (
                      <span className="muted" style={{ fontSize: "0.75em" }}>
                        {" "}
                        +{(x.addresses?.length ?? 1) - 1} gebündelt
                      </span>
                    ) : null}
                    {privacyMode ? null : (
                      <>
                        {" "}
                        <button
                          type="button"
                          className="linkish"
                          style={{ fontSize: "0.75em" }}
                          title="Alle Adressen dieser Gegenstelle als Zeilen „Name,Adresse“ kopieren — Namen ändern und in local/externe-adressen.csv einfügen"
                          onClick={() => {
                            const lines = (x.addresses ?? [x.address]).map((a) => `${x.name},${a}`).join("\n");
                            navigator.clipboard?.writeText(lines).then(
                              () => setStatusNote(`${x.name}: ${(x.addresses ?? [x.address]).length} Adresse(n) kopiert (Name,Adresse).`),
                              () => setErrorNote("Kopieren nicht möglich — bitte „Externe Adressen speichern“ nutzen."),
                            );
                          }}
                        >
                          kopieren
                        </button>
                      </>
                    )}
                  </td>
                  <td className={privacyMode ? "num privacy-mask" : "num"}>
                    {x.in_count
                      ? `${x.in_count}× · ${displayBtc(privacyMode, x.in_sats ?? 0, satsToBtc)}`
                      : "—"}
                  </td>
                  <td className={privacyMode ? "num privacy-mask" : "num"}>
                    {x.out_count
                      ? `${x.out_count}× · ${displayBtc(privacyMode, x.total_sats, satsToBtc)}`
                      : "—"}
                  </td>
                  <td className="nowrap">
                    {x.first_time
                      ? `${String(x.first_time).slice(0, 10)}${
                          x.last_time && x.last_time !== x.first_time
                            ? ` – ${String(x.last_time).slice(0, 10)}`
                            : ""
                        }`
                      : "—"}
                  </td>
                  <td className="nowrap">
                    <TrailButton entry={x} state={trail.results[x.name]} onRun={() => trail.run(x.name)} />
                  </td>
                </tr>
                <TrailRow
                  entry={x}
                  state={trail.results[x.name]}
                  privacy={privacyMode}
                  colSpan={6}
                  onAdopted={() => void refreshFlowsAndLabels()}
                />
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
      </details>
    </section>
  );
}
