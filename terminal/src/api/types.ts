// Shapes returned by the Flask API in webapp/app.py.
// Prices are already divided by PRICE_SCALE on the server.

export interface SeriesPoint {
  bucket: string;
  best_bid: number | null;
  best_ask: number | null;
  rel_spread: number | null;
  rel_vwap_spread: number | null;
  events: number;
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
