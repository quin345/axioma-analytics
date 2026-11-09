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
  // Where the selected instrument came from, when the mapping knows.
  broker: string | null;
  account: string | null;
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
