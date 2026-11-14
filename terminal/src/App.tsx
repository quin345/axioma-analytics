import { useEffect, useMemo, useRef, useState } from "react";
import TerminalHeader from "./sections/TerminalHeader";
import AnalysisControls from "./sections/AnalysisControls";
import HeadlineMetrics from "./sections/HeadlineMetrics";
import AnalyticsGrid from "./sections/AnalyticsGrid";
import DataAndCoverage from "./sections/DataAndCoverage";
import { MarketProvider, type Market, type MarketActions } from "./context/MarketContext";
import { useAssetClasses, useSeries, useSymbols } from "./hooks/useMarketData";
import { refreshVolumeAtPrice } from "./api/endpoints";
import type { Instrument } from "./api/types";
import { summarise } from "./lib/metrics";
import { decimalsForMid, setPriceDecimals } from "./lib/format";
import { WINDOWS } from "./lib/window";

// A feed that has not ticked for this long is shown as closed, not live.
const LIVE_AFTER_MS = 5 * 60_000;

// An instrument's identity. A symbol id is shared across brokers (114 is
// BTCUSD on one account and EURCHF on another), so selection is keyed on the
// account as well, never on the bare id.
function keyOf(i: Instrument): string {
  return `${i.account ?? ""}:${i.symbolId}`;
}

export default function AxiomaMarketAnalyticsTerminal() {
  const [activeTab, setActiveTab] = useState("analytics");
  const [windowSize, setWindowSize] = useState("4h");
  // null = "all classes". The list is whatever the server reports (canonical
  // five, plus "Other"); it is not hardcoded here.
  const [assetClass, setAssetClass] = useState<string | null>(null);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [runError, setRunError] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());

  const symbols = useSymbols();
  const assetClasses = useAssetClasses();
  const instruments = useMemo(() => symbols.data ?? [], [symbols.data]);

  // The instruments in the selected class. A null class means "all".
  const visible = useMemo(
    () => (assetClass ? instruments.filter((i) => i.assetClass === assetClass) : instruments),
    [instruments, assetClass],
  );

  // The instrument behind the current selection.
  const selected = useMemo(
    () => instruments.find((i) => keyOf(i) === selectedKey) ?? null,
    [instruments, selectedKey],
  );

  // Pick the first instrument once the list arrives, and re-pick whenever the
  // current one is filtered out by a class change, so the selection always
  // points at something visible.
  useEffect(() => {
    if (visible.length === 0) return;
    if (selectedKey === null || !visible.some((i) => keyOf(i) === selectedKey)) {
      setSelectedKey(keyOf(visible[0]));
    }
  }, [selectedKey, visible]);

  // Re-evaluate "live" as time passes, not only when data arrives.
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 15_000);
    return () => window.clearInterval(t);
  }, []);

  const ref = useMemo(
    () => (selected ? { symbolId: selected.symbolId, account: selected.account } : null),
    [selected],
  );
  const series = useSeries(ref, WINDOWS[windowSize].minutes, refreshKey);
  const summary = useMemo(() => (series.data ? summarise(series.data) : null), [series.data]);

  // Display precision follows the instrument's price level. Set before the
  // children render so every card formats with it.
  const priceDecimals = decimalsForMid(summary?.lastMid ?? null);
  setPriceDecimals(priceDecimals);

  const lastSeen = selected?.lastSeen ?? summary?.lastBucket ?? null;
  const live = lastSeen !== null && now - Date.parse(lastSeen) < LIVE_AFTER_MS;

  // The Run button: re-derive the recorded volume-at-price profile from the
  // feed, then bump the refresh key so every panel re-fetches at once.
  const runRef = useRef(false);
  const run = () => {
    if (runRef.current || !ref) return;
    runRef.current = true;
    setRunning(true);
    setRunError(null);
    refreshVolumeAtPrice(ref)
      .catch((err: unknown) => setRunError(String(err)))
      .finally(() => {
        runRef.current = false;
        setRunning(false);
        setRefreshKey((k) => k + 1);
      });
  };

  // Actions the controls call directly. Each control has its own onClick.
  const actions: MarketActions = {
    setTab: setActiveTab,
    setWindow: setWindowSize,
    cycleAssetClass: () => {
      const order = assetClasses.data?.filter((c) => c.count > 0).map((c) => c.assetClass) ?? [];
      if (order.length === 0) return;
      setAssetClass((current) => {
        if (current === null) return order[0];
        const index = order.indexOf(current);
        // Past the last class, wrap back to "all".
        return index === -1 || index === order.length - 1 ? null : order[index + 1];
      });
    },
    cycleSymbol: () => {
      if (visible.length === 0) return;
      setSelectedKey((current) => {
        const index = current === null ? -1 : visible.findIndex((i) => keyOf(i) === current);
        return keyOf(visible[(index + 1) % visible.length]);
      });
    },
    run,
  };

  const error = series.error ?? runError;
  const market: Market = {
    actions,
    // The standardized name when we have the mapping, else the bare id.
    symbolLabel: selected
      ? selected.standardizedName ?? selected.name ?? `Symbol ${selected.symbolId}`
      : symbols.error
        ? "Unavailable"
        : instruments.length === 0
          ? "Loading…"
          : "—",
    symbolId: selected?.symbolId ?? null,
    broker: selected?.broker ?? null,
    account: selected?.account ?? null,
    lastSeen,
    live,
    refreshKey,
    priceDecimals,
    assetClass,
    assetClasses: assetClasses.data ?? [],
    windowMinutes: WINDOWS[windowSize].minutes,
    series: series.data ?? [],
    summary,
    ticks: summary ? summary.ticks : null,
    windowLabel: WINDOWS[windowSize].label,
    status: error ? `error: ${error}` : running ? "running" : live ? "live" : "market closed",
  };

  return (
    <MarketProvider value={market}>
      <div
        className="terminal-shell bg-[#0b0e12] content-stretch flex flex-col items-start relative size-full"
        data-active-tab={activeTab}
        data-active-window={windowSize}
        data-running={running}
        data-node-id="2007:7116"
        data-name="AXIOMA market analytics terminal"
      >
        <TerminalHeader />
        <div
          className="content-stretch flex flex-col gap-[20px] items-start overflow-clip p-[24px] relative shrink-0 w-full"
          data-node-id="2007:7135"
          data-name="Analysis workspace"
        >
          <AnalysisControls
            assetClass={assetClass}
            instrument={market.symbolLabel}
            running={running}
          />
          <HeadlineMetrics />
          <AnalyticsGrid />
          <DataAndCoverage />
        </div>
      </div>
    </MarketProvider>
  );
}
