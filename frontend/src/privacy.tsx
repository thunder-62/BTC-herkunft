/**
 * Privacy / Demo-Maskierung — reine Anzeige-Helfer für Demos.
 * Session-Daten bleiben unverändert im RAM; nur gerenderte Strings werden maskiert.
 * Keine Seeds/Keys; keine Disk-Persistenz (optional sessionStorage nur für Toggle-UX).
 */
import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";

/** Konsistente Sternchen — Spaltenbreite bleibt grob erhalten. */
export const MASK_NAME = "********";
export const MASK_TXID = "************";
export const MASK_BTC = "*.********";
export const MASK_SATS = "********";
export const MASK_HEX = "************";
export const MASK_XPUB = "************************";

const SESSION_KEY = "btc-origin-privacy-mode";

export function maskName(_s?: string | null): string {
  return MASK_NAME;
}

/** TxID / shortHex-Länge — feste Sternchen. */
export function maskTxid(_s?: string | null): string {
  return MASK_TXID;
}

/** BTC-Anzeige (bereits formatiert oder Rohstring) → *.******** */
export function maskBtc(_displayOrSats?: string | number | null): string {
  return MASK_BTC;
}

export function maskSats(_n?: number | null): string {
  return MASK_SATS;
}

/** Generische Hex-/Adress-Kurzform (falls gewünscht). */
export function maskHex(_s?: string | null): string {
  return MASK_HEX;
}

/** xpub/ypub/zpub-Kurzvorschau (Wallet-Liste). */
export function maskXpub(_s?: string | null): string {
  return MASK_XPUB;
}

/** Bitcoin-Adresse (bc1/1/3/tb1…) — volle Maske, kein Präfix sichtbar. */
export const MASK_ADDR = "************";

export function maskAddress(_s?: string | null): string {
  return MASK_ADDR;
}

type PrivacyCtx = {
  privacyMode: boolean;
  setPrivacyMode: (on: boolean) => void;
  togglePrivacy: () => void;
};

const PrivacyContext = createContext<PrivacyCtx | null>(null);

function readInitial(): boolean {
  try {
    return sessionStorage.getItem(SESSION_KEY) === "1";
  } catch {
    return false;
  }
}

export function PrivacyProvider({ children }: { children: ReactNode }) {
  const [privacyMode, setPrivacyModeState] = useState<boolean>(readInitial);

  const setPrivacyMode = useCallback((on: boolean) => {
    setPrivacyModeState(on);
    try {
      if (on) sessionStorage.setItem(SESSION_KEY, "1");
      else sessionStorage.removeItem(SESSION_KEY);
    } catch {
      /* sessionStorage optional — Demo-UX only */
    }
  }, []);

  const togglePrivacy = useCallback(() => {
    setPrivacyMode(!privacyMode);
  }, [privacyMode, setPrivacyMode]);

  const value = useMemo(
    () => ({ privacyMode, setPrivacyMode, togglePrivacy }),
    [privacyMode, setPrivacyMode, togglePrivacy],
  );

  return (
    <PrivacyContext.Provider value={value}>{children}</PrivacyContext.Provider>
  );
}

export function usePrivacy(): PrivacyCtx {
  const ctx = useContext(PrivacyContext);
  if (!ctx) {
    return {
      privacyMode: false,
      setPrivacyMode: () => {},
      togglePrivacy: () => {},
    };
  }
  return ctx;
}

/** Full Bitcoin address or a shortened one („bc1qabcd…wxyz“). */
function looksLikeAddress(s: string): boolean {
  const v = s.trim();
  return (
    v.includes("…") ||
    /^(bc1|tb1)[02-9ac-hj-np-z]{20,}$/i.test(v) ||
    /^[123mn][1-9A-HJ-NP-Za-km-z]{25,34}$/.test(v)
  );
}

/** Anzeige: Namen (Wallet, Gegenstelle, Börse) bleiben auch in der
 *  Privacy-Ansicht lesbar — nur ein „Name“, der eine Adresse ist, wird maskiert. */
export function displayName(
  privacy: boolean,
  name?: string | null,
  fallback = "—",
): string {
  if (!name) return fallback;
  return privacy && looksLikeAddress(name) ? maskName(name) : name;
}

/** Anzeige: shortHex oder maskiert. */
export function displayTxid(
  privacy: boolean,
  txid: string,
  shortFn: (s: string, head?: number, tail?: number) => string,
  head = 10,
  tail = 6,
): string {
  if (!txid) return txid;
  return privacy ? maskTxid(txid) : shortFn(txid, head, tail);
}

/** Anzeige: sats→BTC-String oder maskiert. */
export function displayBtc(
  privacy: boolean,
  sats: number,
  satsToBtcFn: (s: number) => string,
): string {
  return privacy ? maskBtc(sats) : satsToBtcFn(sats);
}

/** Anzeige: xpub/Adresse Kurzform oder maskiert. */
export function displayXpub(
  privacy: boolean,
  value: string,
  shortFn: (s: string, head?: number, tail?: number) => string,
  head = 16,
  tail = 8,
): string {
  if (!value) return value;
  return privacy ? maskXpub(value) : shortFn(value, head, tail);
}

/** Anzeige: Bitcoin-Adresse — bei Privacy volle Maske (kein bc1q…-Präfix). */
export function displayAddress(
  privacy: boolean,
  value: string,
  shortFn: (s: string, head?: number, tail?: number) => string,
  head = 12,
  tail = 6,
): string {
  if (!value) return value;
  return privacy ? maskAddress(value) : shortFn(value, head, tail);
}

/** Anzeige: EUR-Betrag oder maskiert / Kurs fehlt. */
export function displayEur(
  privacy: boolean,
  eur: number | null | undefined,
  formatFn?: (n: number) => string,
): string {
  if (eur == null || Number.isNaN(eur)) return "Kurs fehlt";
  if (privacy) return maskBtc(); // same star style as BTC amounts
  if (formatFn) return formatFn(eur);
  return eur.toLocaleString("de-DE", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

