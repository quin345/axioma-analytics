// Chart geometry for the price plot. Pure functions, no React.
import type {
  BookDepth,
  SeriesPoint,
  SpreadPoint,
  VolumeAtPriceProfile,
  VapBin,
} from "../api/types";

// Matches the design's plot box: 996 x 230 px, with a left axis gutter.
export const PLOT = { width: 996, height: 230, left: 46, right: 982, top: 12, bottom: 202 };

const VOLUME_HEIGHT = 40; // bottom strip reserved for event-count bars

export interface Bar {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface Tick {
  pos: number;
  label: string;
}

export interface PriceChartModel {
  linePath: string;
  bars: Bar[];
  yTicks: Tick[];
  xTicks: Tick[];
}

export function buildPriceChart(
  points: SeriesPoint[],
  formatPrice: (n: number) => string,
  formatTime: (iso: string) => string,
  box: PlotBox = PLOT,
): PriceChartModel {
  const valid = points.filter((p) => p.best_bid !== null && p.best_ask !== null);
  if (points.length < 2 || valid.length < 2) {
    return { linePath: "", bars: [], yTicks: [], xTicks: [] };
  }

  const PRICE_BOTTOM = box.bottom - VOLUME_HEIGHT - 8;
  const n = points.length;
  const plotWidth = box.right - box.left;
  const xAt = (i: number) => box.left + (i / (n - 1)) * plotWidth;

  const mids = valid.map((p) => ((p.best_bid as number) + (p.best_ask as number)) / 2);
  const lo0 = Math.min(...mids);
  const hi0 = Math.max(...mids);
  const pad = (hi0 - lo0) * 0.1 || Math.abs(hi0) * 0.0005 || 1;
  const lo = lo0 - pad;
  const hi = hi0 + pad;
  const yAt = (v: number) => box.top + (1 - (v - lo) / (hi - lo)) * (PRICE_BOTTOM - box.top);

  // Price line, one vertex per bucket that has both sides of the book.
  const linePath = points
    .map((p, i) => {
      if (p.best_bid === null || p.best_ask === null) return null;
      const mid = (p.best_bid + p.best_ask) / 2;
      return `${xAt(i).toFixed(2)},${yAt(mid).toFixed(2)}`;
    })
    .filter((s): s is string => s !== null)
    .map((s, i) => `${i === 0 ? "M" : "L"}${s}`)
    .join(" ");

  const maxEvents = Math.max(1, ...points.map((p) => p.events));
  const barWidth = Math.max(1, plotWidth / n - 1);
  const bars: Bar[] = points.map((p, i) => {
    const height = (p.events / maxEvents) * VOLUME_HEIGHT;
    return { x: xAt(i) - barWidth / 2, y: box.bottom - height, width: barWidth, height };
  });

  const yTicks: Tick[] = [0, 1, 2, 3].map((k) => {
    const value = lo + ((hi - lo) * (3 - k)) / 3;
    return { pos: yAt(value), label: formatPrice(value) };
  });

  const xIndices = [0, Math.round((n - 1) / 3), Math.round((2 * (n - 1)) / 3), n - 1];
  const xTicks: Tick[] = xIndices.map((i) => ({
    pos: xAt(i),
    label: formatTime(points[i].bucket),
  }));

  return { linePath, bars, yTicks, xTicks };
}

// Bid-ask spread card: the same plot box as the other 510x248 liquidity cards,
// with a left axis gutter. One line: the relative quoted spread per bucket.
export const SPREAD_PLOT = { width: 482, height: 166, left: 46, right: 468, top: 12, bottom: 138 };

export interface SpreadChartModel {
  linePath: string;
  // Where the newest observation sits, for the card's marker dot.
  lastPoint: { x: number; y: number } | null;
  yTicks: Tick[];
  xTicks: Tick[];
  hasSeries: boolean;
}

// The relative spread series as an SVG polyline. Kept separate from
// buildPriceChart: one series, no volume bars, and it plots `rel_spread` (a
// fraction of mid) rather than a price. `formatValue` renders the y ticks, so
// the caller decides the unit (the card uses basis points).
export function buildSpreadChart(
  points: SpreadPoint[],
  formatValue: (n: number) => string,
  formatTime: (iso: string) => string,
  box: PlotBox = SPREAD_PLOT,
): SpreadChartModel {
  const empty: SpreadChartModel = {
    linePath: "",
    lastPoint: null,
    yTicks: [],
    xTicks: [],
    hasSeries: false,
  };

  const valid = points.filter((p) => p.rel_spread !== null) as (SpreadPoint & {
    rel_spread: number;
  })[];
  if (valid.length < 2) return empty;

  const n = points.length;
  const plotWidth = box.right - box.left;
  // x is positioned against the full series so gaps stay where they are.
  const xAt = (i: number) => box.left + (i / (n - 1)) * plotWidth;

  const values = valid.map((p) => p.rel_spread);
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  // A flat window has no range to scale: give it a symmetric band around the
  // value so the line lands mid-plot instead of dividing by zero.
  const span = hi - lo;
  const pad = span > 0 ? span * 0.1 : Math.abs(hi) * 0.1 || 1e-6;
  const yLo = lo - pad;
  const yHi = hi + pad;
  const yAt = (v: number) =>
    box.top + (1 - (v - yLo) / (yHi - yLo)) * (box.bottom - box.top);

  const linePath = points
    .map((p, i) => (p.rel_spread === null ? null : `${xAt(i).toFixed(2)},${yAt(p.rel_spread).toFixed(2)}`))
    .filter((s): s is string => s !== null)
    .map((s, i) => `${i === 0 ? "M" : "L"}${s}`)
    .join(" ");

  const lastIndex = points.reduce((acc, p, i) => (p.rel_spread === null ? acc : i), -1);
  const lastPoint =
    lastIndex < 0 ? null : { x: xAt(lastIndex), y: yAt(points[lastIndex].rel_spread as number) };

  const yTicks: Tick[] = [0, 1, 2, 3].map((k) => {
    const value = yLo + ((yHi - yLo) * (3 - k)) / 3;
    return { pos: yAt(value), label: formatValue(value) };
  });

  const xIndices = [0, Math.round((n - 1) / 3), Math.round((2 * (n - 1)) / 3), n - 1];
  const xTicks: Tick[] = xIndices.map((i) => ({
    pos: xAt(i),
    label: formatTime(points[i].bucket),
  }));

  return { linePath, lastPoint, yTicks, xTicks, hasSeries: true };
}
// Order-book depth card: one shared plot box, center = mid price. Bid bars
// extend left from center, ask bars extend right; bar width = cumulative
// volume at that level. Prices are already in display units.
export const BOOK_PLOT = { width: 481, height: 166, left: 46, right: 481, top: 12, bottom: 138 };

export interface BookBar {
  price: number;
  cumulative: number;
  x: number;
  y: number;
  width: number;
  height: number;
  side: "bid" | "ask";
}

export interface BookDepthModel {
  bestBid: number | null;
  bestAsk: number | null;
  mid: number | null;
  bidTotal: number;
  askTotal: number;
  maxVolume: number;
  bars: BookBar[];
  yTicks: { pos: number; label: string }[];
  rowCount: number;
}

export function buildBookDepth(
  depth: BookDepth | null,
  formatVolume: (n: number) => string,
  box: PlotBox = BOOK_PLOT,
): BookDepthModel {
  const empty: BookDepthModel = {
    bestBid: null,
    bestAsk: null,
    mid: null,
    bidTotal: 0,
    askTotal: 0,
    maxVolume: 0,
    bars: [],
    yTicks: [],
    rowCount: 0,
  };
  if (!depth) return empty;

  const bestBid = depth.best_bid ?? null;
  const bestAsk = depth.best_ask ?? null;
  const mid =
    bestBid !== null && bestAsk !== null ? (bestBid + bestAsk) / 2 : null;

  const plot = box;
  const centerX = (plot.left + plot.right) / 2;
  const maxBarWidth = ((plot.right - plot.left) / 2) * 0.85;

  const rows = Math.max(depth.bids.length, depth.asks.length);
  if (rows === 0) return empty;

  const rowHeight = (plot.bottom - plot.top) / rows;

  // Bids closest to mid first (highest price); cumulative volume grows outward.
  const bidLevels: { price: number; volume: number; cumulative: number }[] = [];
  let bidCum = 0;
  for (const lvl of depth.bids) {
    bidCum += lvl.volume;
    bidLevels.push({ price: lvl.price, volume: lvl.volume, cumulative: bidCum });
  }
  const bidTotal = bidCum;

  // Asks closest to mid first (lowest price); cumulative volume grows outward.
  const askLevels: { price: number; volume: number; cumulative: number }[] = [];
  let askCum = 0;
  for (const lvl of depth.asks) {
    askCum += lvl.volume;
    askLevels.push({ price: lvl.price, volume: lvl.volume, cumulative: askCum });
  }
  const askTotal = askCum;

  const maxVolume = Math.max(1, bidTotal, askTotal);

  const bars: BookBar[] = [];
  for (let i = 0; i < rows; i++) {
    const y = plot.top + i * rowHeight;
    const height = Math.max(1, rowHeight - 1);

    if (i < bidLevels.length) {
      const lvl = bidLevels[i];
      const width = (lvl.cumulative / maxVolume) * maxBarWidth;
      bars.push({
        price: lvl.price,
        cumulative: lvl.cumulative,
        x: centerX - width,
        y,
        width,
        height,
        side: "bid",
      });
    }
    if (i < askLevels.length) {
      const lvl = askLevels[i];
      const width = (lvl.cumulative / maxVolume) * maxBarWidth;
      bars.push({
        price: lvl.price,
        cumulative: lvl.cumulative,
        x: centerX,
        y,
        width,
        height,
        side: "ask",
      });
    }
  }

  const yTicks: { pos: number; label: string }[] = [0, 1].map((k) => {
    const value = maxVolume * k;
    const pos = plot.bottom - k * (plot.bottom - plot.top);
    return { pos, label: formatVolume(value) };
  });

  return {
    bestBid,
    bestAsk,
    mid,
    bidTotal,
    askTotal,
    maxVolume,
    bars,
    yTicks,
    rowCount: rows,
  };
}

// metadata strip and a bottom axis gutter. Horizontal bars, one per price bin,
// widest = busiest, with the point of control marked and the value area shaded.
export const VAP_PLOT = { width: 481, height: 166, left: 0, right: 481, top: 0, bottom: 166 };

export interface VapBar {
  price: number;
  y: number;
  width: number;
  height: number;
  volume: number;
  inValueArea: boolean;
  isPoc: boolean;
}

export interface VapLadderModel {
  bars: VapBar[];
  pocY: number | null;
  // The tooltip-style label the card shows over the ladder: POC and value area.
  pocLabel: string | null;
  valueAreaLabel: string | null;
  volumeLabel: string | null;
  rowCount: number;
}

// Ladders are returned ascending by price; the card draws the highest price at
// the top, so the model is inverted before laying the bars out.
export function buildVapLadder(
  profile: VolumeAtPriceProfile | null,
  formatPrice: (n: number) => string,
  formatVolume: (n: number) => string,
  box: PlotBox = VAP_PLOT,
): VapLadderModel {
  const empty: VapLadderModel = {
    bars: [],
    pocY: null,
    pocLabel: null,
    valueAreaLabel: null,
    volumeLabel: null,
    rowCount: 0,
  };
  if (!profile || profile.bins.length === 0) return empty;

  const bins = profile.bins;
  const top = box.top;
  const bottom = box.bottom;
  const height = (bottom - top) / bins.length;
  const maxVolume = Math.max(1, ...bins.map((b) => b.volume));
  const area = profile.value_area;
  const [areaLo, areaHi] = area ?? [Number.NaN, Number.NaN];

  // Draw descending price: the last (highest) bin goes on the top row.
  const ordered = [...bins].reverse() as VapBin[];
  const bars: VapBar[] = ordered.map((bin, i) => {
    const inValueArea =
      area !== null && bin.price >= areaLo && bin.price <= areaHi;
    return {
      price: bin.price,
      y: top + i * height,
      width: (bin.volume / maxVolume) * box.width,
      height: Math.max(1, height - 1),
      volume: bin.volume,
      inValueArea,
      isPoc: profile.poc !== null && Math.abs(bin.price - profile.poc) < 1e-9,
    };
  });

  const pocBar = bars.find((b) => b.isPoc);

  return {
    bars,
    pocY: pocBar ? pocBar.y + pocBar.height / 2 : null,
    pocLabel: profile.poc === null ? null : formatPrice(profile.poc),
    valueAreaLabel:
      area === null ? null : `${formatPrice(areaLo)} – ${formatPrice(areaHi)}`,
    volumeLabel: formatVolume(profile.total_volume),
    rowCount: bars.length,
  };
}

// --- Generic plot geometry ---------------------------------------------------
// One model for the line and bar cards added with the live data: a plot box
// with a left value gutter and a bottom label strip, linear scales, and tick
// positions. Each card picks its own box so it matches its Figma frame.

export interface PlotBox {
  width: number;
  height: number;
  left: number;
  right: number;
  top: number;
  bottom: number;
}

// Stretch a design plot box to the width its container actually renders at.
// The viewBox keeps the design height (so type and strokes stay at design
// size) and takes the container's aspect ratio, so the plot fills the width
// instead of letterboxing. Gutters and the right margin keep their design
// size. Before the first measurement (or while hidden) the design box is used.
export function fitBox<B extends PlotBox>(box: B, size: { width: number; height: number } | null): B {
  if (!size || size.width <= 0 || size.height <= 0) return box;
  const minWidth = box.left + (box.width - box.right) + 1;
  const width = Math.max(minWidth, (size.width * box.height) / size.height);
  return { ...box, width, right: width - (box.width - box.right) };
}

export interface Scale {
  lo: number;
  hi: number;
  at: (v: number) => number;
}

// A y scale over [lo, hi], padded so the extremes do not touch the frame. A
// flat range gets a symmetric band so nothing divides by zero.
export function yScale(values: number[], box: PlotBox, opts: { pad?: number; includeZero?: boolean } = {}): Scale {
  const finite = values.filter((v) => Number.isFinite(v));
  let lo = finite.length ? Math.min(...finite) : 0;
  let hi = finite.length ? Math.max(...finite) : 1;
  if (opts.includeZero) {
    lo = Math.min(lo, 0);
    hi = Math.max(hi, 0);
  }
  const span = hi - lo;
  const pad = span > 0 ? span * (opts.pad ?? 0.1) : Math.abs(hi) * 0.1 || 1;
  if (!(opts.includeZero && lo === 0)) lo -= pad;
  if (!(opts.includeZero && hi === 0)) hi += pad;
  const at = (v: number) => box.top + (1 - (v - lo) / (hi - lo)) * (box.bottom - box.top);
  return { lo, hi, at };
}

// `count` evenly spaced value ticks from hi down to lo.
export function valueTicks(scale: Scale, count: number, format: (n: number) => string): Tick[] {
  return Array.from({ length: count }, (_, k) => {
    const value = scale.hi - ((scale.hi - scale.lo) * k) / (count - 1);
    return { pos: scale.at(value), label: format(value) };
  });
}

// x position of item i of n across the box.
export function xAt(box: PlotBox, i: number, n: number): number {
  return box.left + (n <= 1 ? 0 : (i / (n - 1)) * (box.right - box.left));
}

// Four time ticks (first, thirds, last) for a bucketed series.
export function timeTicks(
  buckets: string[],
  box: PlotBox,
  format: (iso: string) => string,
): Tick[] {
  const n = buckets.length;
  if (n === 0) return [];
  const idx = [0, Math.round((n - 1) / 3), Math.round((2 * (n - 1)) / 3), n - 1];
  return idx.map((i) => ({ pos: xAt(box, i, n), label: format(buckets[i]) }));
}

// An SVG path through the points, skipping nulls (a gap starts a new run).
export function linePath(ys: (number | null)[], box: PlotBox, scale: Scale): string {
  const n = ys.length;
  let out = "";
  let pen = false;
  ys.forEach((v, i) => {
    if (v === null || !Number.isFinite(v)) {
      pen = false;
      return;
    }
    out += `${pen ? "L" : "M"}${xAt(box, i, n).toFixed(2)},${scale.at(v).toFixed(2)} `;
    pen = true;
  });
  return out.trim();
}
