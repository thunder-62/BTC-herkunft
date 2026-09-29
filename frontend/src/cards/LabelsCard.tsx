import {
  displayAddress,
} from "../privacy";
import type { AppState } from "../appState";
import { shortHex } from "../appShared";

export default function LabelsCard({ s }: { s: AppState }) {
  const { privacyMode, labels, packEntities, haltefristCount, evidenceGapCount } = s;
  return (
    <section className="collapsible">
      <details>
        <summary>
          <h2>Labels / Haltefrist</h2>
        </summary>
      <p className="muted">
        Evidence-Gap-Status z.&nbsp;B. „Kaufnachweis fehlt, Quelle nicht erreichbar“ —
        <strong> niemals</strong> stille Cost Basis 0. Haltefrist-Hinweis bei Inflows ≥ 365
        Tage (BMF 06.03.2025 / Anlage SO — nur Hinweis).
      </p>
      <p>
        Labels in Sitzung: <strong>{labels.length}</strong>
        {" · "}Flows mit Evidence-Gap: <strong>{evidenceGapCount}</strong>
        {" · "}Haltefrist-Hinweise: <strong>{haltefristCount}</strong>
      </p>
      {packEntities.length > 0 && (
        <p className="muted">
          Pack-Entitäten: {packEntities.join(", ")}
        </p>
      )}
      {labels.length > 0 && (
        <ul className="note-list">
          {labels.slice(0, 30).map((l, i) => (
            <li key={`${l.address}-${i}`}>
              <code className={privacyMode ? "privacy-mask" : undefined}>
                {displayAddress(privacyMode, l.address, shortHex, 12, 6)}
              </code>{" "}
              → <strong>{l.label}</strong>
              {" — "}
              <em>{l.status}</em>
            </li>
          ))}
        </ul>
      )}
      </details>
    </section>
  );
}
