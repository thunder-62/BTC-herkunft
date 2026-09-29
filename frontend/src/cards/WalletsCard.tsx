import {
  displayName,
  displayXpub,
} from "../privacy";
import type { AppState } from "../appState";
import { shortHex } from "../appShared";

export default function WalletsCard({ s }: { s: AppState }) {
  const { privacyMode, name, setName, xpubPaste, setXpubPaste, wallets, busy, softWarn, registerPaste, handleClearSession } = s;
  return (
    <section>
      <h2>Wallets — xpub einfügen (Paste)</h2>
      <p className="muted">
        Beliebig viele xpubs — kein Hard-Limit. Eine Zeile pro Eintrag; optional{" "}
        <code>Name⇥xpub…</code> (Tab zwischen Name und Schlüssel). Nur Cut-and-Paste im Browser.
      </p>
      <label htmlFor="wname">Anzeigename (optional, gilt für alle neuen Zeilen)</label>
      <input
        id="wname"
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="z. B. Sparkonto"
        autoComplete="off"
      />
      <label htmlFor="xpub">Öffentliche Extended Keys oder Adressen</label>
      <textarea
        id="xpub"
        rows={5}
        value={xpubPaste}
        onChange={(e) => setXpubPaste(e.target.value)}
        placeholder={
          "xpub… / ypub… / zpub… oder bc1… hier einfügen\n" +
          "optional: Name<Tab>zpub…  · mehrere Zeilen (nie Seed/Mnemonic)"
        }
        autoComplete="off"
        spellCheck={false}
      />
      <div className="actions">
        <button type="button" onClick={registerPaste} disabled={busy !== null}>
          {busy === "import" ? "Importiere…" : "Zur Sitzung hinzufügen"}
        </button>
        <button
          type="button"
          className="secondary"
          onClick={handleClearSession}
          disabled={busy !== null}
        >
          Sitzung leeren
        </button>
      </div>
      {softWarn && <p className="warn">{softWarn}</p>}
      {wallets.length > 0 && (
        <ul className="wallet-list">
          {wallets.map((w) => (
            <li key={w.id}>
              <strong className={privacyMode ? "privacy-mask" : undefined}>
                {displayName(privacyMode, w.name)}
              </strong>{" "}
              <span className="badge-mini">{w.kind}</span>{" "}
              <code className={privacyMode ? "xpub-preview privacy-mask" : "xpub-preview"}>
                {displayXpub(privacyMode, w.xpub || w.address || "", shortHex)}
              </code>
            </li>
          ))}
        </ul>
      )}
      <p className="muted">
        Registriert über FastAPI <code>POST /api/wallets/batch</code> (ebenfalls nur RAM).
      </p>
    </section>
  );
}
