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
