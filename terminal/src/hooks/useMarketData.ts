import {
  fetchAcf,
  fetchActivityByDay,
  fetchActivityByHour,
  fetchAssetClasses,
  fetchBook,
  fetchImpact,
  fetchOrderFlow,
  fetchSeries,
  fetchSpread,
  fetchSymbols,
  fetchTickGaps,
  fetchTicks,
  fetchVolatility,
  fetchVolumeAtPrice,
} from "../api/endpoints";
import type {
  AssetClassCount,
  Autocorrelation,
  BookDepth,
  BurstImpact,
  DayActivity,
  HourActivity,
  Instrument,
  InstrumentRef,
  OrderFlow,
  RawTick,
  SeriesPoint,
  SpreadSummary,
  TickGaps,
  VolatilityPoint,
  VolumeAtPriceProfile,
} from "../api/types";
import { useMarket } from "../context/MarketContext";
import { BIN_SECONDS } from "../lib/window";
import { usePoll, type PollState } from "./usePoll";

const SYMBOL_REFRESH_MS = 60_000;
const SERIES_REFRESH_MS = 5_000;
// Derived panels whose shape changes slowly: no need to rescan every 5s.
const DERIVED_REFRESH_MS = 30_000;
// Day/hour activity moves on an hours-long scale.
const ACTIVITY_REFRESH_MS = 300_000;
// The ladder is a recorded profile, rebuilt on the 5-minute server grid, so
// polling it faster than that only repeats the same row.
const VAP_REFRESH_MS = 300_000;

// The rolling-volatility card: 30-minute window of 10-second returns.
export const VOL_WINDOW_BUCKETS = 180;
export const ACF_MAX_LAG = 30;
// The order-flow card buckets its signed flow per minute so a 4 h window is
// 240 bars, readable at card width.
export const FLOW_BIN_SECONDS = 60;
export const RAW_TICK_ROWS = 12;
export const CALENDAR_DAYS = 42;
export const ACTIVITY_HOUR_DAYS = 7;

export function useSymbols(): PollState<Instrument[]> {
  return usePoll(fetchSymbols, [], SYMBOL_REFRESH_MS);
}

// The canonical asset classes and their counts. Rides the symbol cadence - it is
// derived from the same collected set and the same mapping snapshot.
export function useAssetClasses(): PollState<AssetClassCount[]> {
  return usePoll(fetchAssetClasses, [], SYMBOL_REFRESH_MS);
}

// The selected instrument and the Run counter, from context. Every per-panel
// hook keys on all three, so a selection change or a Run re-fetches at once.
function useScope(): { ref: InstrumentRef | null; key: string; minutes: number } {
  const { symbolId, account, windowMinutes, refreshKey } = useMarket();
  const ref = symbolId ? { symbolId, account } : null;
  return { ref, key: `${account}:${symbolId}:${refreshKey}`, minutes: windowMinutes };
}

// The series drives the headline numbers and lives in App, above the context
// provider, so it takes its scope explicitly.
export function useSeries(
  ref: InstrumentRef | null,
  minutes: number,
  refreshKey: number,
): PollState<SeriesPoint[]> {
  const load = ref ? () => fetchSeries(ref, minutes, BIN_SECONDS) : null;
  return usePoll(load, [ref?.symbolId, ref?.account, minutes, refreshKey], SERIES_REFRESH_MS);
}

// The spread series is derived from the same feed the price chart polls, so it
// rides the same cadence.
export function useSpread(): PollState<SpreadSummary> {
  const { ref, key, minutes } = useScope();
  const load = ref ? () => fetchSpread(ref, minutes) : null;
  return usePoll(load, [key, minutes], SERIES_REFRESH_MS);
}

export function useBook(): PollState<BookDepth | null> {
  const { ref, key } = useScope();
  const load = ref ? () => fetchBook(ref) : null;
  return usePoll(load, [key], SERIES_REFRESH_MS);
}

export function useVolatility(): PollState<VolatilityPoint[]> {
  const { ref, key, minutes } = useScope();
  const load = ref ? () => fetchVolatility(ref, minutes, VOL_WINDOW_BUCKETS) : null;
  return usePoll(load, [key, minutes], DERIVED_REFRESH_MS);
}

export function useAcf(): PollState<Autocorrelation> {
  const { ref, key, minutes } = useScope();
  const load = ref ? () => fetchAcf(ref, minutes, ACF_MAX_LAG) : null;
  return usePoll(load, [key, minutes], DERIVED_REFRESH_MS);
}

export function useTickGaps(): PollState<TickGaps> {
  const { ref, key, minutes } = useScope();
  const load = ref ? () => fetchTickGaps(ref, minutes) : null;
  return usePoll(load, [key, minutes], DERIVED_REFRESH_MS);
}

export function useActivityByHour(): PollState<HourActivity[]> {
  const { ref, key } = useScope();
  const load = ref ? () => fetchActivityByHour(ref, ACTIVITY_HOUR_DAYS) : null;
  return usePoll(load, [key], ACTIVITY_REFRESH_MS);
}

export function useActivityByDay(): PollState<DayActivity[]> {
  const { ref, key } = useScope();
  const load = ref ? () => fetchActivityByDay(ref, CALENDAR_DAYS) : null;
  return usePoll(load, [key], ACTIVITY_REFRESH_MS);
}

export function useOrderFlow(): PollState<OrderFlow> {
  const { ref, key, minutes } = useScope();
  const load = ref ? () => fetchOrderFlow(ref, minutes, FLOW_BIN_SECONDS) : null;
  return usePoll(load, [key, minutes], DERIVED_REFRESH_MS);
}

export function useImpact(): PollState<BurstImpact> {
  const { ref, key, minutes } = useScope();
  const load = ref ? () => fetchImpact(ref, minutes) : null;
  return usePoll(load, [key, minutes], DERIVED_REFRESH_MS);
}

export function useTicks(): PollState<RawTick[]> {
  const { ref, key } = useScope();
  const load = ref ? () => fetchTicks(ref, RAW_TICK_ROWS) : null;
  return usePoll(load, [key], SERIES_REFRESH_MS);
}

export function useVolumeAtPrice(): PollState<VolumeAtPriceProfile | null> {
  const { ref, key, minutes } = useScope();
  const load = ref ? () => fetchVolumeAtPrice(ref, minutes) : null;
  return usePoll(load, [key, minutes], VAP_REFRESH_MS);
}
