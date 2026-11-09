import { useEffect, useMemo, useState } from "react";
import TerminalHeader from "./sections/TerminalHeader";
import AnalysisControls from "./sections/AnalysisControls";
import HeadlineMetrics from "./sections/HeadlineMetrics";
import AnalyticsGrid from "./sections/AnalyticsGrid";
import DataAndCoverage from "./sections/DataAndCoverage";
import { MarketProvider, type Market, type MarketActions } from "./context/MarketContext";
import { useSeries, useSymbols } from "./hooks/useMarketData";
import { summarise } from "./lib/metrics";
import { WINDOWS } from "./lib/window";

export default function AxiomaMarketAnalyticsTerminal() {
  const [activeTab, setActiveTab] = useState("analytics");
  const [windowSize, setWindowSize] = useState("4h");
  const [assetClass, setAssetClass] = useState("FX");
  const [symbolId, setSymbolId] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  const symbols = useSymbols();
  const series = useSeries(symbolId, WINDOWS[windowSize].minutes);
  const summary = useMemo(() => (series.data ? summarise(series.data) : null), [series.data]);

  // Pick the first collected symbol once the list arrives.
  useEffect(() => {
    if (symbolId === null && symbols.data && symbols.data.length > 0) {
      setSymbolId(symbols.data[0]);
    }
  }, [symbolId, symbols.data]);

  // Actions the controls call directly. Each control has its own onClick.
  const actions: MarketActions = {
    setTab: setActiveTab,
    setWindow: setWindowSize,
    cycleAssetClass: () =>
      setAssetClass((current) =>
        current === "FX" ? "Equities" : current === "Equities" ? "Crypto" : "FX",
      ),
    cycleSymbol: () => {
      const list = symbols.data ?? [];
      if (list.length === 0) return;
      setSymbolId((current) => {
        const index = current === null ? -1 : list.indexOf(current);
        return list[(index + 1) % list.length];
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
    symbolLabel: symbolId ? `Symbol ${symbolId}` : "Loading…",
    symbolId,
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
