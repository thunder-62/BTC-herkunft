/** Kleine Helfer, die App und Karten gemeinsam nutzen. */


export const SOFT_WARN_COUNT = 50;

export function satsToBtc(sats: number): string {
  return (sats / 1e8).toFixed(8);
}

export function formatEur(n: number): string {
  return (
    n.toLocaleString("de-DE", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }) + " EUR"
  );
}

// Kryptosteuer-Reform (Referentenentwurf BMF, Sept. 2026 — kein geltendes Recht):
// Anschaffung bis 31.12.2026 = Altbestand (Haltefrist bleibt), danach Neubestand.
export const REFORM_CUTOFF = "2026-12-31";

export function AltNeuBadge({ lotDate, belegt }: { lotDate: string; belegt: boolean }) {
  const alt = lotDate.slice(0, 10) <= REFORM_CUTOFF;
  const title = alt
    ? `Altbestand: angeschafft bis ${REFORM_CUTOFF} — nach dem Referentenentwurf gilt weiter die Haltefrist.` +
      (belegt
        ? " Kauf belegt (Börsen-Export)."
        : " Kein Kaufbeleg: Blockchain belegt nur den Besitz ab dem Zufluss; Anschaffungskosten fehlen.")
    : "Neubestand: angeschafft nach dem 31.12.2026 — nach dem Referentenentwurf 25 % Abgeltungsteuer ohne Haltefrist.";
  return (
    <span
      className={`badge-mini${alt && !belegt ? " gap" : ""}`}
      title={title}
      style={{ marginLeft: 4 }}
    >
      {alt ? (belegt ? "Altbestand" : "Altbestand · unbelegt") : "Neubestand"}
    </span>
  );
}

export function shortHex(s: string, head = 10, tail = 6): string {
  if (!s || s.length <= head + tail + 1) return s;
  return `${s.slice(0, head)}…${s.slice(-tail)}`;
}
