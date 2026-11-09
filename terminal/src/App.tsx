import { useEffect, useMemo, useState } from "react";
import TerminalHeader from "./sections/TerminalHeader";
import AnalysisControls from "./sections/AnalysisControls";
import HeadlineMetrics from "./sections/HeadlineMetrics";
import AnalyticsGrid from "./sections/AnalyticsGrid";
import DataAndCoverage from "./sections/DataAndCoverage";
import { MarketProvider, type Market, type MarketActions } from "./context/MarketContext";
import { useAssetClasses, useSeries, useSymbols } from "./hooks/useMarketData";
import { summarise } from "./lib/metrics";
import { WINDOWS } from "./lib/window";

export default function AxiomaMarketAnalyticsTerminal() {
  const [activeTab, setActiveTab] = useState("analytics");
  const [windowSize, setWindowSize] = useState("4h");
  // null = "all classes". The list is whatever the server reports (canonical
  // five, plus "Other"); it is not hardcoded here.
  const [assetClass, setAssetClass] = useState<string | null>(null);
  const [symbolId, setSymbolId] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  const symbols = useSymbols();
  const assetClasses = useAssetClasses();
  const series = useSeries(symbolId, WINDOWS[windowSize].minutes);
  const summary = useMemo(() => (series.data ? summarise(series.data) : null), [series.data]);

  const instruments = useMemo(() => symbols.data ?? [], [symbols.data]);

  // The instruments in the selected class. A null class means "all".
  const visible = useMemo(
    () => (assetClass ? instruments.filter((i) => i.assetClass === assetClass) : instruments),
    [instruments, assetClass],
  );

  // The instrument behind the current selection, for the header label.
  const selected = useMemo(
    () => instruments.find((i) => i.symbolId === symbolId) ?? null,
    [instruments, symbolId],
  );

  // Pick the first instrument once the list arrives, and re-pick whenever the
  // current one is filtered out by a class change, so the selection always
  // points at something visible.
  useEffect(() => {
    if (visible.length === 0) return;
    if (symbolId === null || !visible.some((i) => i.symbolId === symbolId)) {
      setSymbolId(visible[0].symbolId);
    }
  }, [symbolId, visible]);

  // Actions the controls call directly. Each control has its own onClick.
  const actions: MarketActions = {
    setTab: setActiveTab,
    setWindow: setWindowSize,
    cycleAssetClass: () => {
      const order = assetClasses.data?.map((c) => c.assetClass) ?? [];
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
      setSymbolId((current) => {
        const index = current === null ? -1 : visible.findIndex((i) => i.symbolId === current);
        return visible[(index + 1) % visible.length].symbolId;
      });
    },
    run: () => {
      if (running) return;
      setRunning(true);
      window.setTimeout(() => setRunning(false), 900);
    },
  };

  const market: Market = {
    actions,
    // The standardized name when we have the mapping, else the bare id.
    symbolLabel: selected
      ? selected.standardizedName ?? selected.name ?? `Symbol ${selected.symbolId}`
      : instruments.length === 0
        ? "Loading…"
        : "—",
    symbolId,
    broker: selected?.broker ?? null,
    account: selected?.account ?? null,
    assetClass,
    assetClasses: assetClasses.data ?? [],
    windowMinutes: WINDOWS[windowSize].minutes,
    series: series.data ?? [],
    summary,
    ticks: summary ? summary.ticks : null,
    windowLabel: WINDOWS[windowSize].label,
    status: series.error ? `error: ${series.error}` : "live",
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
