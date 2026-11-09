import { fetchSeries, fetchSpread, fetchSymbols, fetchVolumeAtPrice } from "../api/endpoints";
import type { SeriesPoint, SpreadSummary, VolumeAtPriceProfile } from "../api/types";
import { BIN_SECONDS } from "../lib/window";
import { usePoll, type PollState } from "./usePoll";

const SYMBOL_REFRESH_MS = 60_000;
const SERIES_REFRESH_MS = 5_000;
// The ladder is a recorded profile, rebuilt on the 5-minute server grid, so
// polling it faster than that only repeats the same row.
const VAP_REFRESH_MS = 300_000;

export function useSymbols(): PollState<string[]> {
  return usePoll(fetchSymbols, [], SYMBOL_REFRESH_MS);
}

export function useSeries(symbolId: string | null, minutes: number): PollState<SeriesPoint[]> {
  const load = symbolId ? () => fetchSeries(symbolId, minutes, BIN_SECONDS) : null;
  return usePoll(load, [symbolId, minutes], SERIES_REFRESH_MS);
}

// The spread series is derived from the same feed the price chart polls, so it
// rides the same cadence.
export function useSpread(symbolId: string | null, minutes: number): PollState<SpreadSummary> {
  const load = symbolId ? () => fetchSpread(symbolId, minutes) : null;
  return usePoll(load, [symbolId, minutes], SERIES_REFRESH_MS);
}

export function useVolumeAtPrice(
  symbolId: string | null,
  minutes: number,
): PollState<VolumeAtPriceProfile | null> {
  const load = symbolId ? () => fetchVolumeAtPrice(symbolId, minutes) : null;
  return usePoll(load, [symbolId, minutes], VAP_REFRESH_MS);
}
