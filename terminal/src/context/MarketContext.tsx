import { createContext, useContext } from "react";
import type { SeriesPoint } from "../api/types";
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
  symbolLabel: string;
  symbolId: string | null;
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
