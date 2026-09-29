import {
  LotFlowDiagram,
} from "../LotFlowViz";
import type { AppState } from "../appState";

export default function LotFlowCard({ s }: { s: AppState }) {
  const { privacyMode, cloudFlows, cloudLots } = s;
  return (
    <section className="lot-fluss collapsible">
      <details>
      <summary>
        <h2>Lot-Fluss / Satoshi-Verfolgung</h2>
      </summary>
      <p className="muted">
        Gesamter Fluss durch die Wallet-Cloud: Die Wallets liegen im Raum verteilt — stark
        verbundene nah beieinander, Kreisgröße = heutiger Bestand. Links kommen Zuflüsse
        herein (grün gestrichelt), zwischen den Wallets laufen Umbuchungen (weiß), rechts
        gehen Abflüsse hinaus (rot). Je Richtung ein Pfeil, Stärke = BTC-Menge. Maus über
        einen Pfeil oder eine Wallet zeigt Details; Wallets lassen sich verschieben. Daten
        aus <code>GET /api/cloud/lots</code> (Sitzungs-RAM).
      </p>
      <LotFlowDiagram lots={cloudLots} flows={cloudFlows} privacyMode={privacyMode} />
      </details>
    </section>
  );
}
