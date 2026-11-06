import { fetchSeries, fetchSymbols } from "../api/endpoints";
import type { SeriesPoint } from "../api/types";
import { BIN_SECONDS } from "../lib/window";
import { usePoll, type PollState } from "./usePoll";

const SYMBOL_REFRESH_MS = 60_000;
const SERIES_REFRESH_MS = 5_000;

export function useSymbols(): PollState<string[]> {
  return usePoll(fetchSymbols, [], SYMBOL_REFRESH_MS);
}

export function useSeries(symbolId: string | null, minutes: number): PollState<SeriesPoint[]> {
  const load = symbolId ? () => fetchSeries(symbolId, minutes, BIN_SECONDS) : null;
  return usePoll(load, [symbolId, minutes], SERIES_REFRESH_MS);
}
