/** Zustand und Aktionen der Oberfläche (nur im Arbeitsspeicher) — genutzt von App und Karten. */
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  type ApiWallet,
  type CloudFlowRow,
  type ExternalEntry,
  type YearSummary,
  type LocalFilesInfo,
  type PriceStatus,
  type CloudLot,
  type CloudSummary,
  type FlowRow,
  type LabelRow,
  type OwnershipReport,
  type SyncProgress,
  type SyncSummary,
  type TraceResult,
  addWalletsBatch,
  classifyPublicInput,
  clearSession,
  downloadReportCsv,
  downloadReportPdf,
  defaultFaStichtag,
  getCloudFlows,
  getExternal,
  getCloudYears,
  importExternalCsv,
  downloadExternalCsv,
  getCloudLots,
  getFlows,
  getHealth,
  getLabels,
  getOwnership,
  getSyncProgress,
  listWallets,
  runTrace,
  syncWallets,
} from "./api";
import {
  usePrivacy,
} from "./privacy";
import {
  useTrail,
} from "./TrailCell";
import { SOFT_WARN_COUNT, shortHex } from "./appShared";

export function useAppState() {
  const { privacyMode, togglePrivacy } = usePrivacy();
  // Pure React state = session RAM. Intentionally NOT written to
  // localStorage / IndexedDB for wallet data. Privacy-Toggle may use sessionStorage.
  const [name, setName] = useState("");
  const [xpubPaste, setXpubPaste] = useState("");
  const [wallets, setWallets] = useState<ApiWallet[]>([]);
  const [walletWarning, setWalletWarning] = useState<string | null>(null);
  const [statusNote, setStatusNote] = useState<string | null>(null);
  const [errorNote, setErrorNote] = useState<string | null>(null);

  const [apiOk, setApiOk] = useState<boolean | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [faStichtag, setFaStichtag] = useState<string>(() => defaultFaStichtag());
  const [reportName, setReportName] = useState("");
  const [reportTaxId, setReportTaxId] = useState("");
  const [reportMasked, setReportMasked] = useState(false);
  // --bis: nur Vorgänge bis zum Stichtag (beide Fassungen)
  const [reportUntil, setReportUntil] = useState(true);

  const [syncSummary, setSyncSummary] = useState<SyncSummary | null>(null);
  const [ownership, setOwnership] = useState<OwnershipReport | null>(null);
  const [showInferred, setShowInferred] = useState(false);
  const [syncProgress, setSyncProgress] = useState<SyncProgress | null>(null);
  const [flows, setFlows] = useState<FlowRow[]>([]);
  const [cloudFlows, setCloudFlows] = useState<CloudFlowRow[]>([]);
  const [labels, setLabels] = useState<LabelRow[]>([]);
  const [packEntities, setPackEntities] = useState<string[]>([]);
  const [cloudSummary, setCloudSummary] = useState<CloudSummary | null>(null);
  const [cloudLots, setCloudLots] = useState<CloudLot[]>([]);
  const [selectedFlow, setSelectedFlow] = useState<FlowRow | null>(null);

  const [traceTxid, setTraceTxid] = useState("");
  const [traceDepth, setTraceDepth] = useState(5);
  const [traceResult, setTraceResult] = useState<TraceResult | null>(null);

  const softWarn = useMemo(() => {
    if (walletWarning) return walletWarning;
    if (wallets.length >= SOFT_WARN_COUNT) {
      return `${wallets.length} Wallets in dieser Sitzung — Sync kann länger dauern (kein Hard-Limit).`;
    }
    return null;
  }, [wallets.length, walletWarning]);

  const canExport = flows.length > 0;

  const refreshWallets = useCallback(async () => {
    const data = await listWallets();
    setWallets(data.wallets);
    setWalletWarning(data.warning ?? null);
  }, []);

  // Prices load in the background after sync; re-poll until they are in.
  const [pricesPending, setPricesPending] = useState(false);
  const [priceStatus, setPriceStatus] = useState<PriceStatus | null>(null);
  const [externals, setExternals] = useState<ExternalEntry[]>([]);
  const trail = useTrail();
  const [years, setYears] = useState<YearSummary[]>([]);
  const [yearsAssumption, setYearsAssumption] = useState<string | null>(null);
  const [localInfo, setLocalInfo] = useState<LocalFilesInfo | null>(null);
  const extFileRef = useRef<HTMLInputElement | null>(null);
  const pricePollRef = useRef<number | null>(null);

  const refreshFlowsAndLabels = useCallback(async (): Promise<void> => {
    if (pricePollRef.current !== null) {
      window.clearTimeout(pricePollRef.current);
      pricePollRef.current = null;
    }
    const [f, l, lots, cf, ext, yrs] = await Promise.all([
      getFlows(),
      getLabels(),
      getCloudLots(),
      getCloudFlows(),
      getExternal().catch(() => ({ external: [] as ExternalEntry[], count: 0 })),
      getCloudYears().catch(() => ({
        years: [] as YearSummary[],
        assumption: undefined,
        prices_pending: false,
      })),
    ]);
    setYears(yrs.years ?? []);
    setYearsAssumption(yrs.assumption ?? null);
    setExternals(ext.external ?? []);
    setLocalInfo(("local" in ext ? ext.local : null) ?? null);
    setPriceStatus(cf.price_status ?? null);
    setFlows(f.flows);
    setCloudFlows(cf.flows ?? []);
    if (f.last_sync) setSyncSummary(f.last_sync as SyncSummary);
    if (cf.cloud) setCloudSummary(cf.cloud);
    else if (f.cloud) setCloudSummary(f.cloud);
    else if (lots.cloud) setCloudSummary(lots.cloud);
    setCloudLots(lots.lots ?? []);
    setLabels(l.labels);
    setPackEntities(l.pack_entities ?? []);
    const pending = Boolean(
      f.prices_pending || lots.prices_pending || cf.prices_pending || yrs.prices_pending,
    );
    setPricesPending(pending);
    if (pending) {
      pricePollRef.current = window.setTimeout(() => {
        pricePollRef.current = null;
        void refreshFlowsAndLabels().catch(() => {
          /* next sync / reload retries */
        });
      }, 2000);
    }
  }, []);

  useEffect(
    () => () => {
      if (pricePollRef.current !== null) {
        window.clearTimeout(pricePollRef.current);
      }
    },
    [],
  );

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const h = await getHealth();
        if (cancelled) return;
        setApiOk(h.status === "ok");
        await refreshWallets();
        await refreshFlowsAndLabels();
      } catch {
        if (!cancelled) {
          setApiOk(false);
          setErrorNote(
            "Backend nicht erreichbar. Bitte FastAPI starten (btc-origin / Port 8000) und npm run dev mit Proxy nutzen.",
          );
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [refreshWallets, refreshFlowsAndLabels]);

  async function registerPaste() {
    setErrorNote(null);
    const lines = xpubPaste
      .split(/\r?\n/)
      .map((l) => l.trim())
      .filter(Boolean);
    if (lines.length === 0) {
      setStatusNote("Bitte xpub / ypub / zpub oder Adresse einfügen (Paste).");
      return;
    }

    const items: Array<{ name: string; xpub?: string; address?: string }> = [];
    const rejects: string[] = [];
    const baseName = name.trim();

    lines.forEach((line, idx) => {
      let label = baseName;
      let value = line;
      // Optional "Name\\tXPub" (literal TAB; first TAB separates name from key)
      const tabIdx = line.indexOf("\t");
      if (tabIdx >= 0) {
        const namePart = line.slice(0, tabIdx).trim();
        const keyPart = line.slice(tabIdx + 1).trim();
        if (keyPart && classifyPublicInput(keyPart) !== "reject") {
          label = namePart || label;
          value = keyPart;
        }
      }
      const kind = classifyPublicInput(value);
      if (kind === "reject") {
        rejects.push(shortHex(value, 20, 8));
        return;
      }
      const entryName =
        label ||
        (lines.length === 1 ? `Wallet ${wallets.length + 1}` : `Wallet ${wallets.length + idx + 1}`);
      if (kind === "xpub") items.push({ name: entryName, xpub: value });
      else items.push({ name: entryName, address: value });
    });

    if (rejects.length) {
      setErrorNote(
        `Seeds / Private Keys / ungültige Zeilen abgelehnt (${rejects.length}): ${rejects.slice(0, 3).join(", ")}`,
      );
    }
    if (items.length === 0) {
      setStatusNote("Nichts Importierbares gefunden. Nur öffentliche xpubs/Adressen.");
      return;
    }

    setBusy("import");
    try {
      const result = await addWalletsBatch(items);
      const errs = result.results.filter((r) => r.error);
      await refreshWallets();
      setXpubPaste("");
      setName("");
      setStatusNote(
        `${result.imported} Einträge in Sitzungs-RAM übernommen (gesamt ${result.total}). ` +
          "Tab schließen löscht alles — nichts wird gespeichert.",
      );
      if (errs.length) {
        setErrorNote(errs.map((e) => `${e.name}: ${e.error}`).join("; "));
      }
    } catch (e) {
      setErrorNote(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function handleClearSession() {
    setBusy("clear");
    setErrorNote(null);
    try {
      await clearSession();
      setWallets([]);
      setFlows([]);
      setCloudFlows([]);
      setLabels([]);
      setSyncSummary(null);
      setSyncProgress(null);
      setOwnership(null);
      setShowInferred(false);
      setCloudSummary(null);
      setCloudLots([]);
      setSelectedFlow(null);
      setTraceResult(null);
      setTraceTxid("");
      setXpubPaste("");
      setName("");
      setWalletWarning(null);
      setStatusNote("Sitzung geleert (RAM). Es gab nie eine Datei / LocalStorage.");
    } catch (e) {
      setErrorNote(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function handleSync() {
    setBusy("sync");
    setErrorNote(null);
    setSyncProgress({
      running: true,
      phase: "connecting",
      tx_done: 0,
      tx_total: 0,
      addresses_done: 0,
      addresses_total: 0,
      message: "Electrum …",
    });
    setStatusNote("Sync läuft (Electrum → RAM)…");

    const pollId = window.setInterval(() => {
      void getSyncProgress()
        .then((p) => setSyncProgress(p))
        .catch(() => {
          /* ignore transient poll errors while POST holds the worker */
        });
    }, 500);

    try {
      const summary = await syncWallets();
      setSyncSummary(summary);
      if (summary.ownership) {
        setOwnership(summary.ownership);
      } else {
        try {
          setOwnership(await getOwnership(privacyMode));
        } catch {
          setOwnership(null);
        }
      }
      try {
        const finalProg = await getSyncProgress();
        setSyncProgress(finalProg);
      } catch {
        /* keep last polled progress */
      }
      await refreshFlowsAndLabels();
      const errPart =
        summary.errors && summary.errors.length
          ? ` · Fehler: ${summary.errors.slice(0, 2).join("; ")}`
          : "";
      setStatusNote(
        `Sync ${summary.status}: ${summary.flows} Flows, ${summary.inflows} In, ` +
          `${summary.outflows} Out, intern ${summary.internal_count}, ` +
          `Labels ${summary.labels_applied}, Haltefrist ${summary.haltefrist_qualified}.${errPart}`,
      );
      if (summary.errors?.length) {
        setErrorNote(summary.errors.join("\n"));
      }
    } catch (e) {
      setErrorNote(e instanceof Error ? e.message : String(e));
      setStatusNote(null);
    } finally {
      window.clearInterval(pollId);
      setBusy(null);
    }
  }

  async function handleTrace() {
    const txid = traceTxid.trim();
    if (!txid) {
      setStatusNote("Bitte eine TxID für den Trace eingeben.");
      return;
    }
    setBusy("trace");
    setErrorNote(null);
    try {
      const result = await runTrace(txid, traceDepth);
      setTraceResult(result);
      setStatusNote(
        `Trace fertig: Tiefe ${result.depth_reached}/${result.max_depth}` +
          (result.ambiguous ? " · mehrdeutig" : "") +
          (result.truncated ? " · abgeschnitten" : "") +
          ". Heuristik — keine Gewissheit, keine Steuerberatung.",
      );
    } catch (e) {
      setErrorNote(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function handleSaveCsv() {
    if (!canExport) return;
    setBusy("csv");
    setErrorNote(null);
    try {
      await downloadReportCsv();
      setStatusNote(
        "CSV lokal heruntergeladen (explizites Save). Kein automatischer Export, keine Server-Datei.",
      );
    } catch (e) {
      setErrorNote(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function handleSaveExternal() {
    setBusy("ext");
    setErrorNote(null);
    try {
      await downloadExternalCsv();
      setStatusNote("Externe Adressen als CSV heruntergeladen (name,adresse).");
    } catch (e) {
      setErrorNote(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function handleImportExternal(file: File | undefined) {
    if (!file) return;
    setBusy("ext");
    setErrorNote(null);
    try {
      const res = await importExternalCsv(await file.text());
      await refreshFlowsAndLabels();
      const errs = res.errors?.length
        ? ` · ${res.errors.length} übersprungen: ${res.errors.slice(0, 3).join("; ")}`
        : "";
      setStatusNote(
        `Externe Namen importiert: ${res.imported} neu, ${res.updated} geändert${errs}`,
      );
    } catch (e) {
      setErrorNote(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
      if (extFileRef.current) extFileRef.current.value = "";
    }
  }

  async function handleSavePdf() {
    if (!canExport) return;
    setBusy("pdf");
    setErrorNote(null);
    try {
      await downloadReportPdf();
      setStatusNote(
        "PDF lokal heruntergeladen (explizites Save). Kein automatischer Export, keine Server-Datei.",
      );
    } catch (e) {
      setErrorNote(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  function handlePrint() {
    window.print();
    setStatusNote("Druckdialog geöffnet (explizites Print). Kein Hintergrund-Report.");
  }

  // Relevant years for a Nacherklärung (§ 153 / § 371 AO): gain < 1 Jahr
  // reaches the Freigrenze. Only these are offered.
  const overFreigrenze = years.filter((y) => y.over_freigrenze === true);
  const taxYears = overFreigrenze;
  const [nachYears, setNachYears] = useState<number[] | null>(null);
  const chosenYears = (nachYears ?? overFreigrenze.map((y) => y.year)).filter((y) =>
    overFreigrenze.some((o) => o.year === y),
  );
  const nachUrl = (() => {
    const q = new URLSearchParams({
      stichtag: (faStichtag || defaultFaStichtag()).slice(0, 10),
      jahre: chosenYears.join(","),
    });
    if (reportMasked) q.set("privacy", "true");
    if (reportName.trim()) q.set("name", reportName.trim());
    if (reportTaxId.trim()) q.set("steuer_id", reportTaxId.trim());
    return `/api/report/nacherklaerung.pdf?${q.toString()}`;
  })();

  const altUrl = (() => {
    const q = new URLSearchParams();
    if (reportMasked) q.set("privacy", "true");
    if (reportName.trim()) q.set("name", reportName.trim());
    if (reportTaxId.trim()) q.set("steuer_id", reportTaxId.trim());
    return `/api/report/altbestand.pdf?${q.toString()}`;
  })();

  const reportUrl = (() => {
    const q = new URLSearchParams({
      stichtag: (faStichtag || defaultFaStichtag()).slice(0, 10),
    });
    if (reportMasked) q.set("privacy", "true");
    if (reportName.trim()) q.set("name", reportName.trim());
    if (reportTaxId.trim()) q.set("steuer_id", reportTaxId.trim());
    if (reportUntil) q.set("bis", (faStichtag || defaultFaStichtag()).slice(0, 10));
    return `/api/report/herkunft.pdf?${q.toString()}`;
  })();

  // Finanzamt-Fassung: gleiche Parameter, ohne Bestände/xpubs/Adressen/fremde TxIDs;
  // Kontrolldatei = Prüfprotokolle beider Fassungen + Freigabeliste der TxIDs
  const faReportUrl = `${reportUrl}&fassung=finanzamt`;
  const faControlUrl = reportUrl.replace("/herkunft.pdf?", "/herkunft-kontrolle.txt?");

  const haltefristCount = flows.filter((f) => f.haltefrist_hint).length;
  const evidenceGapCount = flows.filter((f) => f.evidence_gap_status).length;

  return {
    privacyMode,
    togglePrivacy,
    name,
    setName,
    xpubPaste,
    setXpubPaste,
    wallets,
    setWallets,
    walletWarning,
    setWalletWarning,
    statusNote,
    setStatusNote,
    errorNote,
    setErrorNote,
    apiOk,
    setApiOk,
    busy,
    setBusy,
    faStichtag,
    setFaStichtag,
    reportName,
    setReportName,
    reportTaxId,
    setReportTaxId,
    reportMasked,
    setReportMasked,
    reportUntil,
    setReportUntil,
    syncSummary,
    setSyncSummary,
    ownership,
    setOwnership,
    showInferred,
    setShowInferred,
    syncProgress,
    setSyncProgress,
    flows,
    setFlows,
    cloudFlows,
    setCloudFlows,
    labels,
    setLabels,
    packEntities,
    setPackEntities,
    cloudSummary,
    setCloudSummary,
    cloudLots,
    setCloudLots,
    selectedFlow,
    setSelectedFlow,
    traceTxid,
    setTraceTxid,
    traceDepth,
    setTraceDepth,
    traceResult,
    setTraceResult,
    softWarn,
    canExport,
    refreshWallets,
    pricesPending,
    setPricesPending,
    priceStatus,
    setPriceStatus,
    externals,
    setExternals,
    trail,
    years,
    setYears,
    yearsAssumption,
    setYearsAssumption,
    localInfo,
    setLocalInfo,
    extFileRef,
    pricePollRef,
    refreshFlowsAndLabels,
    registerPaste,
    handleClearSession,
    handleSync,
    handleTrace,
    handleSaveCsv,
    handleSaveExternal,
    handleImportExternal,
    handleSavePdf,
    handlePrint,
    overFreigrenze,
    taxYears,
    nachYears,
    setNachYears,
    chosenYears,
    nachUrl,
    altUrl,
    reportUrl,
    faReportUrl,
    faControlUrl,
    haltefristCount,
    evidenceGapCount,
  };
}

export type AppState = ReturnType<typeof useAppState>;
