import {
  CompactPathBreadcrumb,
} from "../LotFlowViz";
import {
  displayName,
  displayTxid,
  displayBtc,
  displayAddress,
} from "../privacy";
import type { AppState } from "../appState";
import { satsToBtc, AltNeuBadge, shortHex } from "../appShared";

export default function FlowsCard({ s }: { s: AppState }) {
  const { privacyMode, cloudFlows, setSelectedFlow, setTraceTxid, pricesPending, priceStatus } = s;
  return (
    <section className="collapsible">
      <details>
      <summary>
        <h2>
          Flows <span className="muted count">({cloudFlows.length})</span>
        </h2>
      </summary>
      {pricesPending && (
        <p className="muted" role="status">
          BTC-Kurse werden im Hintergrund geladen … (werden automatisch ergänzt)
        </p>
      )}
      {!pricesPending && priceStatus && priceStatus.days_failed > 0 && (
        <p className="warn" role="status">
          Für {priceStatus.days_failed} Tag(e) kein BTC-Kurs
          {priceStatus.errors && Object.keys(priceStatus.errors).length > 0
            ? ` — ${Object.entries(priceStatus.errors)
                .map(([src, msg]) => `${src}: ${msg}`)
                .join(" · ")}`
            : ""}
          . Neuer Versuch automatisch nach ca. 2 Minuten (Seite neu laden).
        </p>
      )}
      <p className="muted">
        Wallet-Cloud-Flows: <strong>in</strong> = jeder Cloud-Eintritt (externer Zufluss),
        auch wenn die Bitcoin später wieder abgeflossen sind; <strong>out</strong> =
        Cloud-Austritt an eine fremde Adresse. Interne Umbuchungen erscheinen nicht als
        eigene Zeilen — sie stehen in der <em>Pfad</em>-Kette. Betrag bei IN = Betrag beim
        Eintritt, darunter wie viel davon noch da ist und wo („jetzt: …“). Zeit = Eintritt
        bzw. Austritt.
      </p>
      {cloudFlows.length === 0 ? (
        <p className="muted">Noch keine Cloud-Flows — Sync ausführen.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Richtung</th>
                <th>Betrag (BTC)</th>
                <th>BTC-Kurs</th>
                <th>TxID</th>
                <th title="Woher die Sats kamen: fremder Absender (in) bzw. eigene Wallet (out)">
                  Ursprung
                </th>
                <th title="Wohin: eigene Wallet beim Eintritt (in) bzw. fremdes Ziel (out)">
                  Ziel
                </th>
                <th>Pfad</th>
                <th>Zeit</th>
                <th>Flags</th>
              </tr>
            </thead>
            <tbody>
              {cloudFlows.slice(0, 200).map((f, i) => (
                <tr
                  key={`${f.direction}-${f.txid}-${f.lot_id ?? i}-${i}`}
                  className="flow-row-clickable"
                  title="Klicken für Detail & Lot-Pfad"
                  onClick={() => {
                    // Bridge to existing lot detail: synthetic FlowRow for matching.
                    setSelectedFlow({
                      txid: f.txid,
                      address: f.address || "",
                      direction: f.direction,
                      amount_sats: f.amount_sats,
                      wallet_id: f.wallet_id,
                      wallet_name: f.wallet_name,
                      address_label: f.address_display,
                      block_time: f.time,
                      lot_date: f.lot_date,
                      haltefrist_hint: f.haltefrist_hint ?? undefined,
                      haltefrist_days: f.haltefrist_days,
                      btc_price_eur: f.btc_price_eur,
                      btc_price_usd: f.btc_price_usd,
                      btc_price_note: f.btc_price_note,
                      is_internal: false,
                    });
                  }}
                >
                  <td>
                    <span
                      className={
                        f.direction === "in"
                          ? "badge-mini hint"
                          : "badge-mini gap"
                      }
                      title={
                        f.direction === "in"
                          ? "Cloud-Eintritt"
                          : "Cloud-Austritt"
                      }
                    >
                      {f.direction}
                    </span>
                  </td>
                  <td className={privacyMode ? "num privacy-mask" : "num"}>
                    {displayBtc(privacyMode, f.amount_sats, satsToBtc)}
                    {f.direction === "in" && f.remaining_sats != null ? (
                      <div className="muted" style={{ fontSize: "0.75em" }}>
                        {f.remaining_sats <= 0
                          ? "vollständig abgeflossen"
                          : f.remaining_sats >= f.amount_sats
                            ? "vollständig vorhanden"
                            : (
                              <>
                                davon noch{" "}
                                {displayBtc(
                                  privacyMode,
                                  f.remaining_sats,
                                  satsToBtc,
                                )}
                              </>
                            )}
                      </div>
                    ) : null}
                  </td>
                  <td
                    className="num"
                    title={
                      f.btc_price_note ||
                      "Anschaffungs-Referenz — keine Kostenbasis"
                    }
                  >
                    {f.btc_price_eur != null
                      ? `${Number(f.btc_price_eur).toLocaleString("de-DE", {
                          maximumFractionDigits: 0,
                        })} €`
                      : f.btc_price_usd != null
                        ? `${Number(f.btc_price_usd).toLocaleString("de-DE", {
                            maximumFractionDigits: 0,
                          })} $`
                        : f.btc_price_note
                          ? f.btc_price_note
                          : "—"}
                  </td>
                  <td>
                    <button
                      type="button"
                      className="linkish"
                      title="In Trace-Feld übernehmen"
                      onClick={(e) => {
                        e.stopPropagation();
                        setTraceTxid(f.txid);
                      }}
                    >
                      <code className={privacyMode ? "privacy-mask" : undefined}>
                        {displayTxid(privacyMode, f.txid, shortHex)}
                      </code>
                    </button>
                  </td>
                  <td>
                    {f.direction === "in" ? (
                      // Cloud-Eintritt: foreign sender(s) of the entry tx.
                      f.source_coinbase ? (
                        <span>
                          ← <strong>neu gemint</strong>{" "}
                          <span className="badge-mini hint" title="Coinbase-Transaktion: neu geschürfte Bitcoin (nicht die Börse Coinbase)">Mining</span>
                        </span>
                      ) : (f.source_addresses?.length ?? 0) > 0 ? (
                        <>
                          ←{" "}
                          {f.source_name ? (
                            <strong
                              className={privacyMode ? "privacy-mask" : undefined}
                            >
                              {displayName(privacyMode, f.source_name)}
                            </strong>
                          ) : null}{" "}
                          <code
                            className={privacyMode ? "privacy-mask" : undefined}
                            title={
                              privacyMode
                                ? undefined
                                : f.source_addresses!.join("\n")
                            }
                          >
                            {displayAddress(
                              privacyMode,
                              f.source_addresses![0],
                              shortHex,
                              8,
                              4,
                            )}
                          </code>{" "}
                          <span className="badge-mini gap">extern</span>
                          {f.source_addresses!.length > 1 ? (
                            <div className="muted" style={{ fontSize: "0.75em" }}>
                              +{f.source_addresses!.length - 1} weitere Absender-Adresse
                              {f.source_addresses!.length > 2 ? "n" : ""}
                            </div>
                          ) : null}
                        </>
                      ) : (
                        <span className="muted" title="Absender nicht ermittelt — erneut synchronisieren">
                          ← unbekannt
                        </span>
                      )
                    ) : f.wallet_name ? (
                      // Cloud-Austritt: from which own wallet.
                      <>
                        <strong className={privacyMode ? "privacy-mask" : undefined}>
                          {displayName(privacyMode, f.wallet_name)}
                        </strong>{" "}
                        {f.address_at_disposal ? (
                          <code className={privacyMode ? "privacy-mask" : undefined}>
                            ({displayAddress(
                              privacyMode,
                              f.address_at_disposal,
                              shortHex,
                              8,
                              4,
                            )})
                          </code>
                        ) : null}{" "}
                        <span className="badge-mini hint">eigen</span>
                      </>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                  <td
                    title={
                      privacyMode
                        ? undefined
                        : f.address_display || f.address || undefined
                    }
                  >
                    {f.direction === "out" ? (
                      // Cloud-Austritt: the address is the EXTERNAL target,
                      // never one of the user's wallets. Source shown apart.
                      <>
                        {!f.address ||
                        (f.address_display || "").includes("außerhalb") ? (
                          <span className="muted">→ außerhalb Cloud</span>
                        ) : (
                          <>
                            →{" "}
                            {f.external_name ? (
                              <strong
                                className={privacyMode ? "privacy-mask" : undefined}
                              >
                                {displayName(privacyMode, f.external_name)}
                              </strong>
                            ) : null}{" "}
                            <code
                              className={privacyMode ? "privacy-mask" : undefined}
                            >
                              {displayAddress(
                                privacyMode,
                                f.address,
                                shortHex,
                                8,
                                4,
                              )}
                            </code>{" "}
                            <span className="badge-mini gap">extern</span>
                          </>
                        )}
                      </>
                    ) : f.wallet_name && f.address ? (
                      <>
                        <strong
                          className={privacyMode ? "privacy-mask" : undefined}
                        >
                          {displayName(privacyMode, f.wallet_name)}
                        </strong>{" "}
                        <code
                          className={privacyMode ? "privacy-mask" : undefined}
                        >
                          (
                          {displayAddress(
                            privacyMode,
                            f.address,
                            shortHex,
                            8,
                            4,
                          )}
                          )
                        </code>
                      </>
                    ) : f.address ? (
                      <code
                        className={privacyMode ? "privacy-mask" : undefined}
                      >
                        {displayAddress(
                          privacyMode,
                          f.address,
                          shortHex,
                          12,
                          6,
                        )}
                      </code>
                    ) : (
                      <span className="muted">
                        {f.address_display || "—"}
                      </span>
                    )}
                    {f.direction === "in" &&
                    (f.current_locations?.length ?? 0) > 0 ? (
                      <div className="muted" style={{ fontSize: "0.75em" }}>
                        jetzt:{" "}
                        {f.current_locations!.map((loc, k) => (
                          <span key={`${loc.address}-${k}`}>
                            {k > 0 ? ", " : ""}
                            <span
                              className={privacyMode ? "privacy-mask" : undefined}
                            >
                              {loc.wallet_name
                                ? displayName(privacyMode, loc.wallet_name)
                                : ""}{" "}
                              ({displayAddress(privacyMode, loc.address, shortHex, 8, 4)})
                            </span>
                          </span>
                        ))}
                      </div>
                    ) : null}
                  </td>
                  <td>
                    <CompactPathBreadcrumb
                      path={f.path}
                      pathLabels={f.path_labels}
                      privacyMode={privacyMode}
                    />
                  </td>
                  <td className="nowrap">{f.time ? String(f.time).slice(0, 10) : "—"}</td>
                  <td>
                    {f.haltefrist_hint === true ? (
                      <span
                        className="badge-mini hint"
                        title={
                          f.haltefrist_days != null
                            ? `${f.haltefrist_days} Tage`
                            : undefined
                        }
                      >
                        Haltefrist erfüllt
                      </span>
                    ) : f.direction === "in" && f.status === "abgeflossen" ? (
                      <span className="badge-mini" title="Siehe out-Zeilen">
                        abgeflossen
                      </span>
                    ) : f.direction === "in" ? (
                      <span
                        className="badge-mini"
                        title={
                          f.haltefrist_days != null
                            ? `${f.haltefrist_days} Tage`
                            : undefined
                        }
                      >
                        Haltefrist offen
                      </span>
                    ) : f.haltefrist_days != null ? (
                      <span
                        className="badge-mini"
                        title={`${f.haltefrist_days} Tage bei Verkauf`}
                      >
                        {f.haltefrist_days} T
                      </span>
                    ) : null}
                    {f.direction === "in" && f.status !== "abgeflossen" && f.lot_date ? (
                      <AltNeuBadge lotDate={f.lot_date} belegt={Boolean(f.acq_source)} />
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {cloudFlows.length > 200 && (
            <p className="muted">
              Anzeige auf 200 Zeilen begrenzt ({cloudFlows.length} gesamt).
            </p>
          )}
        </div>
      )}
      <p className="muted">
        Zeile anklicken öffnet Detail mit dem Weg des Teilbestands (Inflow → Umbuchungen →
        aktueller Wallet). Roh-Ledger weiterhin unter{" "}
        <code>GET /api/flows</code>; Cloud-Flows unter{" "}
        <code>GET /api/cloud/flows</code>.
      </p>
      </details>
    </section>
  );
}
