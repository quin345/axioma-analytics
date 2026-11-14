// Shapes returned by the Flask API in webapp/app.py.
// Prices are already divided by PRICE_SCALE on the server.

// One collected instrument, from /api/symbols. The broker, account and symbol
// id are the feed's own triple - the same key the metadata catalogue is stored
// under - so they are always present. The catalogue fields (`name`,
// `standardizedName`, `assetClass`) are null when the catalogue has no row for
// that exact triple: a symbol id means a different instrument on each broker,
// so the app never guesses across brokers.
export interface Instrument {
  symbolId: string;
  broker: string | null;
  account: string | null;
  // The broker's own name for the instrument, e.g. "XAUUSD.pro" (catalogue).
  name: string | null;
  // `name` with the account-tier suffix stripped, e.g. "XAUUSD"; the display name.
  standardizedName: string | null;
  // A canonical class: FX | Equities | Index | Commodities | Crypto | Other.
  assetClass: string | null;
  // When the feed last ticked for this instrument (ISO, UTC). A closed market
  // stays listed, with its last session analysed.
  lastSeen: string | null;
}

// The instrument a request is scoped to. A symbol id is shared across brokers,
// so the trader account always travels with it.
export interface InstrumentRef {
  symbolId: string;
  account: string | null;
}

// One row of /api/asset-classes: a canonical class and how many collected
// instruments are in it.
export interface AssetClassCount {
  assetClass: string;
  count: number;
}

export interface SeriesPoint {
  bucket: string;
  best_bid: number | null;
  best_ask: number | null;
  rel_spread: number | null;
  rel_vwap_spread: number | null;
  imbalance_ratio?: number | null;
  events: number;
}

// /api/book, normalised by the endpoint into price/volume levels, nearest the
// touch first. Volume is the feed's resting size per slot.
export interface BookLevel {
  price: number;
  volume: number;
}

export interface BookDepth {
  timestamp: string;
  best_bid: number | null;
  best_ask: number | null;
  total_bid: number | null;
  total_ask: number | null;
  bids: BookLevel[];
  asks: BookLevel[];
}

// /api/tick-gaps: distribution of gaps between distinct event timestamps.
export interface TickGapBin {
  from_ms: number;
  to_ms: number | null;
  count: number;
}

export interface TickGaps {
  events: number;
  distinct_timestamps: number;
  shared_timestamp_share: number | null;
  median_gap_ms: number | null;
  bins: TickGapBin[];
}

export interface HourActivity {
  hour: number;
  events: number;
}

export interface DayActivity {
  day: string; // YYYY-MM-DD, UTC
  events: number;
}

export interface VolatilityPoint {
  bucket: string;
  vol_pct: number;
}

export interface Autocorrelation {
  n: number;
  band: number | null;
  lags: { lag: number; acf: number | null }[];
}

// /api/order-flow - the signed-flow proxy (tick-rule signed depth events).
export interface OrderFlow {
  proxy: string;
  series: { bucket: string; signed: number; cumulative: number }[];
  buy_events: number;
  sell_events: number;
  imbalance: number | null;
  book_imbalance: number | null;
}

// /api/impact - the large-trade-impact proxy: mid response after bursts.
export interface BurstImpact {
  proxy: string;
  threshold: number | null;
  bursts: number;
  headline_horizon_s?: number;
  curve: { horizon_s: number; mean_bps: number | null; n: number }[];
  events: {
    bucket: string;
    events: number;
    direction: 1 | -1;
    move_bps: number;
    impact_bps: number | null;
  }[];
}

// /api/ticks - newest raw book updates with an inferred side.
export interface RawTick {
  timestamp: string;
  seq: number | null;
  bid: number | null;
  ask: number | null;
  spread: number | null;
  bid_size: number | null;
  ask_size: number | null;
  side: "buy" | "sell" | null;
}

// One point of the bid-ask spread series: the absolute quoted spread at a
// bucket and the same spread as a fraction of that bucket's mid price.
export interface SpreadPoint {
  bucket: string;
  spread: number | null;
  rel_spread: number | null;
}

// `analytics.spread()`'s payload: the per-bucket series plus its summary stats.
export interface SpreadSummary {
  series: SpreadPoint[];
  mean: number | null;
  median: number | null;
  min: number | null;
  max: number | null;
}

// One rung of the volume-at-price ladder. `volume` is traded size when the feed
// carries it, otherwise the bucket's event count.
export interface VapBin {
  price: number;
  volume: number;
  ticks: number;
}

// A derived, locally recorded volume-at-price profile for one lookback window.
export interface VolumeAtPriceProfile {
  symbol_id: string;
  window_minutes: number;
  as_of: string;
  computed_at: string;
  poc: number | null;
  value_area: [number, number] | null;
  total_volume: number;
  high_volume_nodes: VapBin[];
  bins: VapBin[];
  // "local" when served from the record-keeping database, "kusto" when freshly
  // derived from the feed on this request.
  source: "local" | "kusto";
}
