// Named API calls. Components never build URLs themselves.
import { getJson } from "./client";
import type { SeriesPoint, SpreadSummary, VolumeAtPriceProfile } from "./types";

export function fetchSymbols(): Promise<string[]> {
  return getJson<string[]>("/api/symbols");
}

export function fetchSeries(
  symbolId: string,
  minutes: number,
  binSeconds: number,
): Promise<SeriesPoint[]> {
  return getJson<SeriesPoint[]>("/api/series", {
    symbol: symbolId,
    minutes,
    bin_seconds: binSeconds,
  });
}

// Quoted bid-ask spread per bucket over the lookback window, plus its summary
// stats. Derived on the server from the order-book feed, nothing is stored.
export function fetchSpread(symbolId: string, minutes: number): Promise<SpreadSummary> {
  return getJson<SpreadSummary>("/api/spread", { symbol: symbolId, minutes });
}

// The volume-at-price ladder for one lookback window. The server reads its
// local record first; `refresh` asks it to re-derive from the feed and record
// the result, which is also what the control bar's Run button does.
export function fetchVolumeAtPrice(
  symbolId: string,
  minutes: number,
  refresh = false,
): Promise<VolumeAtPriceProfile | null> {
  const params: Record<string, string | number> = { symbol: symbolId, window: minutes };
  if (refresh) params.refresh = 1;
  return getJson<VolumeAtPriceProfile | null>("/api/volume-at-price", params);
}
