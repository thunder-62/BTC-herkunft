import {
  displayBtc,
} from "../privacy";
import type { AppState } from "../appState";
import { satsToBtc } from "../appShared";

export default function YearsCard({ s }: { s: AppState }) {
  const { privacyMode, years, yearsAssumption } = s;
  return (
    <section className="collapsible">
      <details>
        <summary>
          <h2>Jahres-Resümee — hypothetischer Gewinn/Verlust</h2>
        </summary>
      <p className="muted">
        Je Kalenderjahr: Was wäre an Gewinn/Verlust entstanden, <strong>falls</strong> alles,
        was in dem Jahr die Cloud verlassen hat (Börsen, fremde Adressen), am selben Tag zum
        Tageskurs verkauft wurde? Getrennt nach Haltedauer: <strong>unter 1 Jahr</strong>{" "}
        (privates Veräußerungsgeschäft) und <strong>ab 1 Jahr</strong> (Haltefrist erfüllt).
        Referenzkurse; Netzwerkgebühren der Veräußerung als Werbungskosten. Liegt unter{" "}
        <code>local/boersen/</code> ein Export mit Verkäufen vor, gelten für Einzahlungen auf
        diese Börse Verkaufstag und Erlös laut Export. <strong>Keine Steuerberatung.</strong>
      </p>
      {yearsAssumption ? (
        <p className="muted" style={{ fontSize: "0.85em" }}>
          Annahme: {yearsAssumption}
        </p>
      ) : null}
      {years.length === 0 ? (
        <p className="muted">Keine Cloud-Austritte.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th rowSpan={2}>Jahr</th>
                <th rowSpan={2}>Austritte</th>
                <th colSpan={5} style={{ textAlign: "center" }}>
                  unter 1 Jahr gehalten (steuerlich relevant, falls verkauft)
                </th>
                <th colSpan={2} style={{ textAlign: "center" }}>
                  ab 1 Jahr gehalten (Haltefrist erfüllt)
                </th>
                <th rowSpan={2}>Freigrenze (Hinweis)</th>
              </tr>
              <tr>
                <th>BTC</th>
                <th>Veräußerungswert</th>
                <th>Anschaffungswert</th>
                <th title="Netzwerkgebühr der Veräußerung (BMF-Schreiben 06.03.2025, Rz. 59)">
                  Werbungskosten
                </th>
                <th>Gewinn/Verlust</th>
                <th>BTC</th>
                <th>Gewinn/Verlust</th>
              </tr>
            </thead>
            <tbody>
              {years.map((y) => {
                const eur = (v: number) =>
                  privacyMode
                    ? "*** €"
                    : `${v.toLocaleString("de-DE", { maximumFractionDigits: 0 })} €`;
                const gainCls = (v: number) =>
                  privacyMode ? "num" : v > 0 ? "num gain-pos" : v < 0 ? "num gain-neg" : "num";
                const missing = y.short.missing_price + y.long.missing_price;
                return (
                  <tr key={y.year}>
                    <td>
                      <strong>{y.year}</strong>
                      {!y.complete ? (
                        <div className="muted" style={{ fontSize: "0.75em" }}>
                          {missing} ohne Kurs
                        </div>
                      ) : null}
                    </td>
                    <td className="num">{y.disposals}</td>
                    <td className={privacyMode ? "num privacy-mask" : "num"}>
                      {y.short.btc_sats ? displayBtc(privacyMode, y.short.btc_sats, satsToBtc) : "—"}
                    </td>
                    <td className="num">{y.short.btc_sats ? eur(y.short.proceeds_eur) : "—"}</td>
                    <td className="num">{y.short.btc_sats ? eur(y.short.cost_eur) : "—"}</td>
                    <td className="num">{y.short.btc_sats ? eur(y.short.fees_eur ?? 0) : "—"}</td>
                    <td className={gainCls(y.short.gain_eur)}>
                      {y.short.btc_sats ? <strong>{eur(y.short.gain_eur)}</strong> : "—"}
                    </td>
                    <td className={privacyMode ? "num privacy-mask" : "num"}>
                      {y.long.btc_sats ? displayBtc(privacyMode, y.long.btc_sats, satsToBtc) : "—"}
                    </td>
                    <td className={gainCls(y.long.gain_eur)}>
                      {y.long.btc_sats ? eur(y.long.gain_eur) : "—"}
                    </td>
                    <td>
                      {y.freigrenze_eur.toLocaleString("de-DE")} €{" "}
                      {y.over_freigrenze === true ? (
                        <span
                          className="badge-mini gap"
                          title="Gewinn aus < 1 Jahr liegt über der Freigrenze — falls tatsächlich verkauft. Die Freigrenze gilt für alle privaten Veräußerungsgeschäfte des Jahres zusammen."
                        >
                          überschritten
                        </span>
                      ) : y.over_freigrenze === false && y.short.btc_sats ? (
                        <span className="badge-mini hint">darunter</span>
                      ) : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
            <tfoot>
              <tr>
                <td>
                  <strong>Summe</strong>
                </td>
                <td className="num">{years.reduce((a, y) => a + y.disposals, 0)}</td>
                <td className={privacyMode ? "num privacy-mask" : "num"}>
                  {displayBtc(
                    privacyMode,
                    years.reduce((a, y) => a + y.short.btc_sats, 0),
                    satsToBtc,
                  )}
                </td>
                <td />
                <td />
                <td />
                <td className="num">
                  <strong>
                    {privacyMode
                      ? "*** €"
                      : `${years
                          .reduce((a, y) => a + y.short.gain_eur, 0)
                          .toLocaleString("de-DE", { maximumFractionDigits: 0 })} €`}
                  </strong>
                </td>
                <td className={privacyMode ? "num privacy-mask" : "num"}>
                  {displayBtc(
                    privacyMode,
                    years.reduce((a, y) => a + y.long.btc_sats, 0),
                    satsToBtc,
                  )}
                </td>
                <td className="num">
                  {privacyMode
                    ? "*** €"
                    : `${years
                        .reduce((a, y) => a + y.long.gain_eur, 0)
                        .toLocaleString("de-DE", { maximumFractionDigits: 0 })} €`}
                </td>
                <td />
              </tr>
            </tfoot>
          </table>
        </div>
      )}
      </details>
    </section>
  );
}
