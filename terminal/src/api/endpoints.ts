// Named API calls. Components never build URLs themselves.
import { getJson, postJson } from "./client";
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
} from "./types";

type Params = Record<string, string | number>;

// The instrument as query parameters. A symbol id is shared across brokers,
// so the trader account is always sent when known.
function scope(ref: InstrumentRef, extra: Params = {}): Params {
  const params: Params = { symbol: ref.symbolId, ...extra };
  if (ref.account) params.account = ref.account;
  return params;
}

// Every collected instrument, with its broker/account/class metadata.
export function fetchSymbols(): Promise<Instrument[]> {
  return getJson<Instrument[]>("/api/symbols");
}

// The canonical asset classes and their collected-instrument counts.
export function fetchAssetClasses(): Promise<AssetClassCount[]> {
  return getJson<AssetClassCount[]>("/api/asset-classes");
}

export function fetchSeries(
  ref: InstrumentRef,
  minutes: number,
  binSeconds: number,
): Promise<SeriesPoint[]> {
  return getJson<SeriesPoint[]>("/api/series", scope(ref, { minutes, bin_seconds: binSeconds }));
}

// Quoted bid-ask spread per bucket over the lookback window, plus its summary
// stats. Derived on the server from the order-book feed, nothing is stored.
export function fetchSpread(ref: InstrumentRef, minutes: number): Promise<SpreadSummary> {
  return getJson<SpreadSummary>("/api/spread", scope(ref, { minutes }));
}

// The newest two-sided book snapshot, reshaped into price/volume levels.
export async function fetchBook(ref: InstrumentRef): Promise<BookDepth | null> {
  const raw = await getJson<{
    timestamp: string;
    best_bid: number | null;
    best_ask: number | null;
    total_bid?: number | null;
    total_ask?: number | null;
    bids: [number, number][];
    asks: [number, number][];
  } | null>("/api/book", scope(ref));
  if (!raw) return null;
  const level = ([price, volume]: [number, number]) => ({ price, volume });
  return {
    timestamp: raw.timestamp,
    best_bid: raw.best_bid,
    best_ask: raw.best_ask,
    total_bid: raw.total_bid ?? null,
    total_ask: raw.total_ask ?? null,
    bids: raw.bids.map(level),
    asks: raw.asks.map(level),
  };
}

export function fetchVolatility(
  ref: InstrumentRef,
  minutes: number,
  window: number,
): Promise<VolatilityPoint[]> {
  return getJson<VolatilityPoint[]>("/api/volatility", scope(ref, { minutes, window }));
}

export function fetchAcf(ref: InstrumentRef, minutes: number, maxLag: number): Promise<Autocorrelation> {
  return getJson<Autocorrelation>("/api/acf", scope(ref, { minutes, max_lag: maxLag }));
}

export function fetchTickGaps(ref: InstrumentRef, minutes: number): Promise<TickGaps> {
  return getJson<TickGaps>("/api/tick-gaps", scope(ref, { minutes }));
}

export function fetchActivityByHour(ref: InstrumentRef, days: number): Promise<HourActivity[]> {
  return getJson<HourActivity[]>("/api/activity", scope(ref, { days }));
}

export function fetchActivityByDay(ref: InstrumentRef, days: number): Promise<DayActivity[]> {
  return getJson<DayActivity[]>("/api/activity-days", scope(ref, { days }));
}

// Trade-flow proxies. The feed has no trades; each payload names its proxy.
export function fetchOrderFlow(
  ref: InstrumentRef,
  minutes: number,
  binSeconds: number,
): Promise<OrderFlow> {
  return getJson<OrderFlow>("/api/order-flow", scope(ref, { minutes, bin_seconds: binSeconds }));
}

export function fetchImpact(ref: InstrumentRef, minutes: number): Promise<BurstImpact> {
  return getJson<BurstImpact>("/api/impact", scope(ref, { minutes }));
}

export function fetchTicks(ref: InstrumentRef, limit: number): Promise<RawTick[]> {
  return getJson<RawTick[]>("/api/ticks", scope(ref, { limit }));
}

// The volume-at-price ladder for one lookback window, from the server's local
// record (derived on first request).
export function fetchVolumeAtPrice(
  ref: InstrumentRef,
  minutes: number,
): Promise<VolumeAtPriceProfile | null> {
  return getJson<VolumeAtPriceProfile | null>("/api/volume-at-price", scope(ref, { window: minutes }));
}

// Re-derive every window's volume-at-price profile from the feed and record
// it. The control bar's Run button.
export function refreshVolumeAtPrice(ref: InstrumentRef): Promise<unknown> {
  return postJson("/api/volume-at-price/refresh", scope(ref));
}
