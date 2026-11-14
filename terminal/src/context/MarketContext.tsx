import { createContext, useContext } from "react";
import type { AssetClassCount, SeriesPoint } from "../api/types";
import type { Summary } from "../lib/metrics";

export interface MarketActions {
  setTab: (tab: string) => void;
  setWindow: (windowKey: string) => void;
  cycleAssetClass: () => void;
  cycleSymbol: () => void;
  run: () => void;
}

export interface Market {
  actions: MarketActions;
  // The standardized instrument name, from the broker/symbol mapping.
  symbolLabel: string;
  symbolId: string | null;
  // Where the selected instrument came from, when the mapping knows. The
  // account also scopes every query: a symbol id is shared across brokers.
  broker: string | null;
  account: string | null;
  // When the feed last ticked for the selection, and whether that is recent
  // enough to call the market live (a closed market shows its last session).
  lastSeen: string | null;
  live: boolean;
  // Bumped by the Run button; every data hook re-fetches when it changes.
  refreshKey: number;
  // Display decimals for the selected instrument's prices.
  priceDecimals: number;
  // The selected canonical class, or null for "all".
  assetClass: string | null;
  // Every canonical class and its collected-instrument count, server-ordered.
  assetClasses: AssetClassCount[];
  windowMinutes: number;
  series: SeriesPoint[];
  summary: Summary | null;
  ticks: number | null;
  windowLabel: string;
  status: string;
}

const noop = () => {};

const defaultMarket: Market = {
  actions: { setTab: noop, setWindow: noop, cycleAssetClass: noop, cycleSymbol: noop, run: noop },
  symbolLabel: "—",
  symbolId: null,
  broker: null,
  account: null,
  lastSeen: null,
  live: false,
  refreshKey: 0,
  priceDecimals: 2,
  assetClass: null,
  assetClasses: [],
  windowMinutes: 240,
  series: [],
  summary: null,
  ticks: null,
  windowLabel: "",
  status: "",
};

const MarketContext = createContext<Market>(defaultMarket);

export const MarketProvider = MarketContext.Provider;

export function useMarket(): Market {
  return useContext(MarketContext);
}
