import { useMemo, useState } from "react";
import type { CloudLot } from "./api";
import { displayAddress, displayBtc, displayName } from "./privacy";

/** Herkunftsnachweis je Wallet (heutiger Bestand): innerer Ring mit/ohne
 *  Kaufbeleg, äußerer Ring die Wallets darin. Zweiter Tab: Überlegungen, wie
 *  unbelegte Coins noch 2026 einen Nachweis bekommen. Keine Steuerberatung. */

const SURFACE = "#1a1e27"; // Kartenhintergrund = 2px-Lücken zwischen Segmenten
const GOOD = "#0ca30c"; // Status „mit Nachweis“
const WARN = "#fab219"; // Status „ohne Nachweis“
const REFORM_CUTOFF = "2026-12-31";

type Seg = { key: string; label: string; sats: number; proven: boolean; coins: number };

function shortHex(s: string, head = 12, tail = 6): string {
  if (!s || s.length <= head + tail + 1) return s;
  return `${s.slice(0, head)}…${s.slice(-tail)}`;
}

function satsToBtc(sats: number): string {
  return (sats / 1e8).toFixed(8);
}

/** Tag, ab dem ein Verkauf steuerfrei ist (Tag nach dem Jahrestag). */
function taxFreeFrom(iso: string): string {
  const d = new Date(`${iso.slice(0, 10)}T00:00:00Z`);
  const y = d.getUTCFullYear() + 1;
  const m = d.getUTCMonth();
  let day = d.getUTCDate();
  if (m === 1 && day === 29) day = 28;
  const a = new Date(Date.UTC(y, m, day));
  a.setUTCDate(a.getUTCDate() + 1);
  return a.toISOString().slice(0, 10);
}

function arc(cx: number, cy: number, r0: number, r1: number, a0: number, a1: number): string {
  // Kreisring-Segment, Winkel im Uhrzeigersinn ab 12 Uhr
  const p = (r: number, a: number) => [cx + r * Math.sin(a), cy - r * Math.cos(a)];
  const large = a1 - a0 > Math.PI ? 1 : 0;
  if (a1 - a0 >= 2 * Math.PI - 1e-9) {
    // Vollkreis: zwei Halbbögen
    const [x0, y0] = p(r1, 0);
    const [x1, y1] = p(r1, Math.PI);
    const [x2, y2] = p(r0, 0);
    const [x3, y3] = p(r0, Math.PI);
    return `M${x0},${y0}A${r1},${r1} 0 1 1 ${x1},${y1}A${r1},${r1} 0 1 1 ${x0},${y0}Z M${x2},${y2}A${r0},${r0} 0 1 0 ${x3},${y3}A${r0},${r0} 0 1 0 ${x2},${y2}Z`;
  }
  const [ax, ay] = p(r1, a0);
  const [bx, by] = p(r1, a1);
  const [cx2, cy2] = p(r0, a1);
  const [dx, dy] = p(r0, a0);
  return `M${ax},${ay}A${r1},${r1} 0 ${large} 1 ${bx},${by}L${cx2},${cy2}A${r0},${r0} 0 ${large} 0 ${dx},${dy}Z`;
}

export default function ProvenanceCard({ lots, privacy }: { lots: CloudLot[]; privacy: boolean }) {
  const [tab, setTab] = useState<"chart" | "strategy">("chart");
  const [hover, setHover] = useState<string | null>(null);
  const btc = (s: number) => displayBtc(privacy, s, satsToBtc);

  const held = useMemo(() => lots.filter((l) => l.remaining_sats > 0), [lots]);
  const total = held.reduce((s, l) => s + l.remaining_sats, 0);

  // Segmente: erst „mit Nachweis“, dann „ohne“; darin Wallets nach Menge
  const { inner, outer, wallets } = useMemo(() => {
    const by = new Map<string, Seg>();
    for (const l of held) {
      const proven = Boolean(l.acq_source);
      const w = l.current_wallet_name || `Wallet ${l.current_wallet_id ?? "?"}`;
      const key = `${proven ? "1" : "0"}|${w}`;
      const seg = by.get(key) ?? { key, label: w, sats: 0, proven, coins: 0 };
      seg.sats += l.remaining_sats;
      seg.coins += 1;
      by.set(key, seg);
    }
    const segs = [...by.values()];
    const outerSegs = [
      ...segs.filter((s) => s.proven).sort((a, b) => b.sats - a.sats),
      ...segs.filter((s) => !s.proven).sort((a, b) => b.sats - a.sats),
    ];
    const sum = (p: boolean) => segs.filter((s) => s.proven === p).reduce((a, s) => a + s.sats, 0);
    const count = (p: boolean) => segs.filter((s) => s.proven === p).reduce((a, s) => a + s.coins, 0);
    const innerSegs: Seg[] = [
      { key: "proven", label: "mit Nachweis", sats: sum(true), proven: true, coins: count(true) },
      { key: "unproven", label: "ohne Nachweis", sats: sum(false), proven: false, coins: count(false) },
    ].filter((s) => s.sats > 0);
    const names = [...new Set(segs.map((s) => s.label))].sort();
    const table = names.map((n) => {
      const p = segs.find((s) => s.label === n && s.proven);
      const u = segs.find((s) => s.label === n && !s.proven);
      return { name: n, proven: p?.sats ?? 0, unproven: u?.sats ?? 0, pc: p?.coins ?? 0, uc: u?.coins ?? 0 };
    });
    return { inner: innerSegs, outer: outerSegs, wallets: table };
  }, [held]);

  // Strategie: unbelegte Coins nach „steuerfrei ab“ gruppiert
  const today = new Date().toISOString().slice(0, 10);
  const groups = useMemo(() => {
    const unproven = held.filter((l) => !l.acq_source);
    const g = { now: [] as CloudLot[], in2026: [] as CloudLot[], later: [] as CloudLot[] };
    for (const l of unproven) {
      const free = taxFreeFrom(l.lot_date);
      if (free <= today) g.now.push(l);
      else if (free <= REFORM_CUTOFF) g.in2026.push(l);
      else g.later.push(l);
    }
    return g;
  }, [held, today]);

  if (total <= 0) return null;

  const pct = (s: number) => `${((100 * s) / total).toLocaleString("de-DE", { maximumFractionDigits: 1 })} %`;
  const size = 300;
  const c = size / 2;
  const layout = (segs: Seg[]) => {
    let a = 0;
    return segs.map((s) => {
      const a0 = a;
      a += (2 * Math.PI * s.sats) / total;
      return { s, a0, a1: a };
    });
  };
  const innerL = layout(inner);
  const outerL = layout(outer);
  const hovered = [...inner, ...outer].find((s) => s.key === hover) ?? null;
  const sumSats = (ls: CloudLot[]) => ls.reduce((s, l) => s + l.remaining_sats, 0);
  const plusDays = (iso: string, n: number) => {
    const d = new Date(`${iso}T00:00:00Z`);
    d.setUTCDate(d.getUTCDate() + n);
    return d.toISOString().slice(0, 10);
  };
  // Coins, die bis 31.12.2026 steuerfrei umgeschichtet werden können — mit
  // einem Tag Puffer (Zeitzone des Handels) und der Adresse für Coin-Control.
  const eligible = [...groups.now, ...groups.in2026]
    .map((l) => ({ l, from: plusDays(taxFreeFrom(l.lot_date), 1) }))
    .map((x) => ({ ...x, from: x.from < today ? today : x.from }))
    .sort((a, b) => a.from.localeCompare(b.from));
  const span = (ls: CloudLot[]) => {
    if (!ls.length) return "—";
    const d = ls.map((l) => taxFreeFrom(l.lot_date)).sort();
    return d[0] === d[d.length - 1] ? d[0] : `${d[0]} – ${d[d.length - 1]}`;
  };

  return (
    <section className="collapsible provenance">
      <details>
        <summary>
          <h2>
            Herkunftsnachweis je Wallet{" "}
            <span className="muted count">
              ({pct(inner.find((s) => s.proven)?.sats ?? 0)} mit Nachweis)
            </span>
          </h2>
        </summary>
        <div className="tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={tab === "chart"}
            className={tab === "chart" ? "tab active" : "tab"}
            onClick={() => setTab("chart")}
          >
            Verteilung
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "strategy"}
            className={tab === "strategy" ? "tab active" : "tab"}
            onClick={() => setTab("strategy")}
          >
            Strategie 2026
          </button>
        </div>

        {tab === "chart" ? (
          <div className="prov-chart">
            <div className="prov-svg">
              <svg
                viewBox={`0 0 ${size} ${size}`}
                width={size}
                height={size}
                role="img"
                aria-label="Bestand nach Herkunftsnachweis und Wallet"
              >
                {innerL.map(({ s, a0, a1 }) => (
                  <path
                    key={s.key}
                    d={arc(c, c, 62, 96, a0, a1)}
                    fill={s.proven ? GOOD : WARN}
                    fillOpacity={hover && hover !== s.key ? 0.35 : 0.6}
                    stroke={SURFACE}
                    strokeWidth={2}
                    onMouseEnter={() => setHover(s.key)}
                    onMouseLeave={() => setHover(null)}
                  />
                ))}
                {outerL.map(({ s, a0, a1 }) => (
                  <path
                    key={s.key}
                    d={arc(c, c, 100, 146, a0, a1)}
                    fill={s.proven ? GOOD : WARN}
                    fillOpacity={hover && hover !== s.key ? 0.4 : 1}
                    stroke={SURFACE}
                    strokeWidth={2}
                    onMouseEnter={() => setHover(s.key)}
                    onMouseLeave={() => setHover(null)}
                  >
                    <title>{`${displayName(privacy, s.label)} · ${s.proven ? "mit" : "ohne"} Nachweis · ${btc(s.sats)} BTC · ${pct(s.sats)}`}</title>
                  </path>
                ))}
                <text x={c} y={c - 6} textAnchor="middle" className="prov-center-big">
                  {hovered ? pct(hovered.sats) : btc(total)}
                </text>
                <text x={c} y={c + 14} textAnchor="middle" className="prov-center-small">
                  {hovered
                    ? `${displayName(privacy, hovered.label)}${hovered.key.includes("|") ? (hovered.proven ? " ✓" : " !") : ""}`
                    : "BTC gesamt"}
                </text>
              </svg>
              <div className="prov-legend">
                <span>
                  <i style={{ background: GOOD }} /> ✓ mit Nachweis (Börsen-Export){" "}
                  <strong>{pct(inner.find((s) => s.proven)?.sats ?? 0)}</strong>
                </span>
                <span>
                  <i style={{ background: WARN }} /> ! ohne Nachweis (nur Blockchain){" "}
                  <strong>{pct(inner.find((s) => !s.proven)?.sats ?? 0)}</strong>
                </span>
                <span className="muted">Innen: Nachweis · außen: Wallets</span>
              </div>
            </div>
            <div className="table-wrap prov-table">
              <table>
                <thead>
                  <tr>
                    <th>Wallet</th>
                    <th className="num">✓ mit Nachweis (BTC)</th>
                    <th className="num">! ohne Nachweis (BTC)</th>
                    <th className="num">Anteil am Bestand</th>
                  </tr>
                </thead>
                <tbody>
                  {wallets.map((w) => (
                    <tr
                      key={w.name}
                      onMouseEnter={() => setHover(`${w.unproven ? "0" : "1"}|${w.name}`)}
                      onMouseLeave={() => setHover(null)}
                    >
                      <td>{displayName(privacy, w.name)}</td>
                      <td className="num">
                        {w.proven ? btc(w.proven) : "—"}
                        {w.pc ? <span className="muted"> · aus {w.pc} {w.pc === 1 ? "Zufluss" : "Zuflüssen"}</span> : null}
                      </td>
                      <td className="num">
                        {w.unproven ? btc(w.unproven) : "—"}
                        {w.uc ? <span className="muted"> · aus {w.uc} {w.uc === 1 ? "Zufluss" : "Zuflüssen"}</span> : null}
                      </td>
                      <td className="num">{pct(w.proven + w.unproven)}</td>
                    </tr>
                  ))}
                  <tr className="prov-total">
                    <td>Summe</td>
                    <td className="num">{btc(inner.find((s) => s.proven)?.sats ?? 0)}</td>
                    <td className="num">{btc(inner.find((s) => !s.proven)?.sats ?? 0)}</td>
                    <td className="num">100 %</td>
                  </tr>
                </tbody>
              </table>
              <p className="muted" style={{ fontSize: "0.8em" }}>
                Zufluss = ein Eingang in deine Wallets (z. B. eine Auszahlung von der Börse), von
                dem heute noch etwas vorhanden ist. Wurde ein Zufluss auf mehrere Wallets
                verteilt, zählt er in jeder davon.
              </p>
            </div>
          </div>
        ) : (
          <div className="prov-strategy">
            <p className="warn">
              Überlegungen, keine Steuerberatung — vor der Umsetzung mit einer Steuerberaterin/einem
              Steuerberater klären. Die Kryptosteuer-Reform ist ein Referentenentwurf (September
              2026), kein geltendes Recht.
            </p>
            <p>
              <strong>Ziel:</strong> Bitcoin ohne Kaufbeleg bis 31.12.2026 in einen{" "}
              <strong>belegten Kauf</strong> tauschen. Nach dem Entwurf bleibt alles, was bis
              31.12.2026 angeschafft wird, Altbestand mit Haltefrist — mit Beleg lassen sich
              Anschaffungszeitpunkt und -kosten dann eindeutig nachweisen (ohne Nachweis droht laut
              Entwurf eine Pauschale von 50 % des Erlöses).
            </p>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Bitcoin ohne Kaufbeleg</th>
                    <th className="num" title="Zuflüsse in die Wallets, von denen heute noch etwas vorhanden ist">Zuflüsse</th>
                    <th className="num">BTC</th>
                    <th>steuerfrei ab</th>
                    <th>Empfehlung</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>Haltefrist schon erfüllt</td>
                    <td className="num">{groups.now.length}</td>
                    <td className="num">{btc(sumSats(groups.now))}</td>
                    <td>{groups.now.length ? "bereits" : "—"}</td>
                    <td>jetzt verkaufen und sofort zurückkaufen — Verkauf steuerfrei</td>
                  </tr>
                  <tr>
                    <td>Haltefrist endet noch 2026</td>
                    <td className="num">{groups.in2026.length}</td>
                    <td className="num">{btc(sumSats(groups.in2026))}</td>
                    <td>{span(groups.in2026)}</td>
                    <td>ab dem Datum verkaufen und zurückkaufen, spätestens Mitte Dezember</td>
                  </tr>
                  <tr>
                    <td>Haltefrist endet erst 2027</td>
                    <td className="num">{groups.later.length}</td>
                    <td className="num">{btc(sumSats(groups.later))}</td>
                    <td>{span(groups.later)}</td>
                    <td>Verkauf 2026 wäre steuerpflichtig — abwägen (siehe 3.)</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <h3>Haltefrist nicht verletzen — Checkliste</h3>
            <ul className="prov-check">
              <li>
                <strong>Nur Bitcoin mit abgelaufener Haltefrist verkaufen.</strong> Verkaufstag
                frühestens am Tag <em>nach</em> „steuerfrei ab“ (ein Tag Puffer, weil Börsen in
                UTC oder anderer Zeitzone buchen). Maßgeblich ist der Handelstag laut Export.
              </li>
              <li>
                <strong>Coin-Control beim Einzahlen.</strong> In der Wallet (z. B. Sparrow,
                BitBox, Ledger Live „Coin Control“) nur die unten gelisteten Adressen als
                Inputs wählen. Sonst mischt die Wallet jüngere Bitcoin in die Einzahlung, und die
                wären beim Verkauf steuerpflichtig. Wechselgeld bleibt eigen und behält sein
                Anschaffungsdatum.
              </li>
              <li>
                <strong>Leeres Börsenkonto.</strong> Auf der Börse gilt für die Reihenfolge FiFo
                (BMF 06.03.2025, Rz. 61, 62). Liegen dort schon andere BTC, entweder vorher
                auszahlen oder genau die eingezahlte Menge verkaufen und prüfen, dass keine
                jüngeren BTC älter gebucht sind. Am sichersten: ein Konto bzw. Unterkonto nur
                für diesen Zweck.
              </li>
              <li>
                <strong>Erst verkaufen, dann zurückkaufen</strong> — als zwei getrennte Orders zum
                Marktkurs. Die Einzahlung auf das eigene Börsenkonto ist keine Veräußerung
                (Umbuchung zwischen eigenen Wallets, Rz. 54).
              </li>
              <li>
                <strong>Die Börse kennt das Anschaffungsdatum nicht.</strong> Ihr Steuerreport
                zeigt die eingezahlten Bitcoin womöglich als „kurz gehalten“. Maßgeblich ist der
                eigene Nachweis: Herkunftsanalyse bzw. „Nachweis Altbestand“ mit Zufluss-Tx und
                Einzahlungs-Tx aufbewahren.
              </li>
              <li>
                <strong>Rückgekaufte Bitcoin getrennt halten.</strong> Für sie beginnt eine neue
                Haltefrist (steuerfrei erst ein Jahr nach dem Rückkauf). Am besten in eine eigene,
                neue Wallet auszahlen, damit sie nicht mit alten Beständen vermischt werden.
              </li>
              <li>
                <strong>Bitcoin mit Haltefrist erst 2027</strong> nicht umschichten — der Verkauf
                wäre steuerpflichtig (außer der Jahresgewinn bleibt unter der Freigrenze von
                1.000 €).
              </li>
            </ul>

            <h3>
              Steuerfrei umschichtbar bis 31.12.2026{" "}
              <span className="muted count">
                ({eligible.length} {eligible.length === 1 ? "Zufluss" : "Zuflüsse"})
              </span>
            </h3>
            {eligible.length === 0 ? (
              <p className="muted">Keine Bitcoin ohne Kaufbeleg, deren Haltefrist 2026 abläuft.</p>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Verkauf frühestens</th>
                      <th>Zufluss (Anschaffung)</th>
                      <th>Wallet</th>
                      <th>Adresse (Coin-Control)</th>
                      <th className="num">BTC</th>
                    </tr>
                  </thead>
                  <tbody>
                    {eligible.map(({ l, from }) => (
                      <tr key={l.lot_id}>
                        <td className="nowrap">{from === today ? "sofort" : from}</td>
                        <td className="nowrap">{l.lot_date.slice(0, 10)}</td>
                        <td>{displayName(privacy, l.current_wallet_name || "")}</td>
                        <td>
                          <code>{displayAddress(privacy, l.current_address || "", shortHex)}</code>
                        </td>
                        <td className="num">{btc(l.remaining_sats)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            <ol className="prov-steps">
              <li>
                <strong>Verkaufen und zurückkaufen (Haltefrist erfüllt).</strong> Die Bitcoin auf eine
                Börse mit vollständigem Konto-Export einzahlen, dort verkaufen und im selben Zug
                zurückkaufen, danach wieder in die eigene Wallet auszahlen. Der Verkauf ist nach
                mehr als einem Jahr steuerfrei (§ 23 Abs. 1 Satz 1 Nr. 2 EStG) — die Haltedauer
                belegt die Blockchain (Zufluss = spätester Anschaffungstag, siehe
                Herkunftsanalyse). Der Rückkauf ist ein belegter Kauf 2026 mit Datum und Preis. Die
                Haltefrist beginnt dafür neu: steuerfrei wieder ein Jahr nach dem Rückkauf. Kosten:
                Handelsgebühr und Spread (beides zweimal), Netzwerkgebühren für Ein- und Auszahlung.
              </li>
              <li>
                <strong>Haltefrist endet noch 2026.</strong> Erst nach dem „steuerfrei ab“-Datum
                verkaufen, sonst ist der Gewinn steuerpflichtig. Zeit für Einzahlung,
                Bestätigungen und Auszahlung einplanen — nicht auf die letzten Tage des Jahres
                legen; maßgeblich ist der Handelstag laut Export.
              </li>
              <li>
                <strong>Haltefrist endet erst 2027.</strong> Ein Verkauf in 2026 wäre ein privates
                Veräußerungsgeschäft: Gewinn = Erlös − Anschaffungskosten (Tageskurs am Zufluss).
                Unter 1.000 € Gesamtgewinn aller privaten Veräußerungsgeschäfte des Jahres bleibt er
                steuerfrei (Freigrenze, § 23 Abs. 3 Satz 5 EStG). Sonst abwägen: Steuer jetzt gegen
                das Risiko, später ohne Kaufbeleg dazustehen. Solche Bitcoin bleiben auch ohne
                Rückkauf Altbestand — die Blockchain belegt den Besitz vor dem 31.12.2026.
              </li>
              <li>
                <strong>Ohne Verkauf Belege beschaffen.</strong> Konto-Exporte der Börse nachträglich
                anfordern (Auskunft nach Art. 15 DSGVO), Bankauszüge der Überweisungen an die Börse,
                Kauf-E-Mails; bei insolventen Plattformen (FTX, Mt. Gox, Celsius …) die Daten aus
                dem Gläubigerportal. Ein neuer Export in <code>local/boersen/</code> macht Bitcoin
                ohne Handel zu „mit Nachweis“.
              </li>
              <li>
                <strong>Gestaltungsmissbrauch (§ 42 AO).</strong> Der BFH hat entschieden: Wer
                Wertpapiere verkauft und am selben Tag gleichartige Papiere zu einem anderen Kurs
                zurückkauft, missbraucht keine Gestaltung — Verkauf und Rückkauf sind eigenständige
                Geschäfte; entscheidend war das eingegangene Kursrisiko (Urteil vom 25.08.2009,
                IX R 60/07, dort zur Verlustrealisierung). Hier ist die Lage eher günstiger: Der
                Verkauf nach Ablauf der Haltefrist ist vom Gesetz gewollt steuerfrei, ein
                gesetzlich nicht vorgesehener Steuervorteil ist nicht erkennbar. Zu Kryptowerten
                gibt es aber (soweit bekannt) keine Entscheidung und keine Aussage im BMF-Schreiben
                vom 06.03.2025. Deshalb: nur echte Börsengeschäfte zum Marktkurs, keine
                Absprachen oder Geschäfte mit sich selbst (ein Finanzgericht sah beim Verkauf und
                Wiederkauf von Bezugsrechten ohne echtes Risiko einen Missbrauch); Kauf und Verkauf
                als getrennte Orders, der Beleg zeigt beide.
              </li>
              <li>
                <strong>Risiken der Reform.</strong> Der Entwurf ist nicht beschlossen; der
                Gesetzgeber kann den Stichtag oder Übergangsregeln noch ändern (bei der
                Abgeltungsteuer galt für manche Zertifikate ein früherer Stichtag). Laut Berichten
                soll der Bestandsschutz für Zuflüsse nach dem 31.12.2026 aus Altgeschäften nicht
                uneingeschränkt gelten — Rückkauf <em>und</em> Auszahlung in die Wallet daher
                deutlich vor Jahresende abschließen. Wird die Reform nicht beschlossen, bleibt vom
                Umschichten vor allem der saubere Beleg — und die neu beginnende Haltefrist.
              </li>
              <li>
                <strong>Worauf achten.</strong> Nur so viel umschichten, wie nötig; alles über eine
                Börse mit sauberem CSV-Export. Dokumente aufbewahren und den „Nachweis
                Altbestand“ nach dem Umschichten neu erzeugen.
              </li>
            </ol>
          </div>
        )}
      </details>
    </section>
  );
}
