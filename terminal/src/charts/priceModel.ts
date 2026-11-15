// Geometry for the price + volume panel's optional views: candlesticks and the
// volume-profile overlay. Pure functions, no React.
//
// The default view (line, no overlay) is still drawn by `buildPriceChart` in
// lib/chart. This model reproduces that layout - same index-based x axis,
// same 10% padded price scale, same 40px event strip at the bottom - so
// switching views does not move the axes, and adds:
//
// * candles: OHLC of the bucket mids, grouped into fixed time periods. The
//   API's /api/series row is the *last* quote in each 10s bucket, so a
//   candle's high/low are the extremes of those bucket-close mids, not of
//   every tick inside the period.
// * the volume profile: the recorded volume-at-price ladder for one lookback
//   (/api/volume-at-price/windows), drawn as horizontal bars from the right
//   edge, with the POC and value-area high/low as horizontal rules. The price
//   scale is widened to include POC / VAH / VAL so those levels are always on
//   screen; ladder bins outside the scale are clipped.
import type { SeriesPoint, VolumeAtPriceProfile } from "../api/types";
import type { Bar, PlotBox, Tick } from "../lib/chart";

// Must match lib/chart buildPriceChart so the views line up.
const VOLUME_HEIGHT = 40;
const VOLUME_GAP = 8;
// Candle periods to choose from, seconds.
const CANDLE_PERIODS = [30, 60, 120, 300, 600, 900, 1800, 3600];
// Aim for about this many candles across the window, whatever the plot width,
// so the period the card's caption names is the one drawn.
export const CANDLE_TARGET = 120;
// Widest profile bar, as a share of the plot width.
const PROFILE_MAX_SHARE = 0.22;

export type PriceMode = "line" | "candles";

export interface CandleGeom {
  start: string;
  open: number;
  high: number;
  low: number;
  close: number;
  x: number; // centre
  width: number;
  yOpen: number;
  yClose: number;
  yHigh: number;
  yLow: number;
  up: boolean;
}

export interface ProfileBarGeom {
  price: number;
  x: number;
  y: number;
  width: number;
  height: number;
  inValueArea: boolean;
  isPoc: boolean;
}

export interface ProfileLevelGeom {
  kind: "POC" | "VAH" | "VAL";
  price: number;
  y: number;
}

export interface PriceModel {
  // The price area (above the event strip); the profile is clipped to it.
  priceTop: number;
  priceBottom: number;
  linePath: string;
  candles: CandleGeom[];
  candleSeconds: number | null;
  bars: Bar[];
  yTicks: Tick[];
  xTicks: Tick[];
  profileBars: ProfileBarGeom[];
  profileLevels: ProfileLevelGeom[];
}

interface Group {
  start: string;
  i0: number;
  i1: number;
  mids: number[];
  events: number;
}

function midOf(p: SeriesPoint): number | null {
  return p.best_bid === null || p.best_ask === null ? null : (p.best_bid + p.best_ask) / 2;
}

// The candle period for a series: the shortest that keeps the count at or
// under `target`.
export function candlePeriod(points: SeriesPoint[], binSeconds: number, target: number): number {
  const t0 = Date.parse(points[0]?.bucket ?? "");
  const t1 = Date.parse(points[points.length - 1]?.bucket ?? "");
  const span = Number.isFinite(t0) && Number.isFinite(t1) ? (t1 - t0) / 1000 : 0;
  const options = CANDLE_PERIODS.filter((p) => p > binSeconds);
  return options.find((p) => span / p <= target) ?? options[options.length - 1];
}

// Group consecutive buckets by period (aligned to the UTC epoch).
function groupBuckets(points: SeriesPoint[], periodSeconds: number): Group[] {
  const groups: Group[] = [];
  let key: number | null = null;
  points.forEach((p, i) => {
    const t = Date.parse(p.bucket);
    const k = Math.floor(t / 1000 / periodSeconds);
    if (key === null || k !== key || groups.length === 0) {
      key = k;
      groups.push({
        start: new Date(k * periodSeconds * 1000).toISOString(),
        i0: i,
        i1: i,
        mids: [],
        events: 0,
      });
    }
    const g = groups[groups.length - 1];
    g.i1 = i;
    g.events += p.events;
    const m = midOf(p);
    if (m !== null) g.mids.push(m);
  });
  return groups;
}

export function buildPriceModel(
  points: SeriesPoint[],
  opts: {
    mode: PriceMode;
    profile: VolumeAtPriceProfile | null;
    binSeconds: number;
    formatPrice: (n: number) => string;
    formatTime: (iso: string) => string;
  },
  box: PlotBox,
): PriceModel {
  const priceTop = box.top;
  const priceBottom = box.bottom - VOLUME_HEIGHT - VOLUME_GAP;
  const empty: PriceModel = {
    priceTop,
    priceBottom,
    linePath: "",
    candles: [],
    candleSeconds: null,
    bars: [],
    yTicks: [],
    xTicks: [],
    profileBars: [],
    profileLevels: [],
  };
  const mids = points.map(midOf);
  const valid = mids.filter((m): m is number => m !== null);
  if (points.length < 2 || valid.length < 2) return empty;

  const n = points.length;
  const plotWidth = box.right - box.left;
  const step = plotWidth / (n - 1);
  const xAt = (i: number) => box.left + i * step;

  // Price domain: the mids (candle highs/lows are drawn from the same mids),
  // widened to the profile's key levels when the overlay is on.
  const profile = opts.profile && opts.profile.bins.length > 0 ? opts.profile : null;
  const levels: { kind: ProfileLevelGeom["kind"]; price: number }[] = [];
  if (profile) {
    if (profile.value_area) {
      levels.push({ kind: "VAH", price: profile.value_area[1] });
      levels.push({ kind: "VAL", price: profile.value_area[0] });
    }
    if (profile.poc !== null) levels.push({ kind: "POC", price: profile.poc });
  }
  const domain = [...valid, ...levels.map((l) => l.price)];
  const lo0 = Math.min(...domain);
  const hi0 = Math.max(...domain);
  const pad = (hi0 - lo0) * 0.1 || Math.abs(hi0) * 0.0005 || 1;
  const lo = lo0 - pad;
  const hi = hi0 + pad;
  const yAt = (v: number) => priceTop + (1 - (v - lo) / (hi - lo)) * (priceBottom - priceTop);

  let linePath = "";
  let candles: CandleGeom[] = [];
  let candleSeconds: number | null = null;
  let bars: Bar[];

  if (opts.mode === "candles") {
    candleSeconds = candlePeriod(points, opts.binSeconds, CANDLE_TARGET);
    const groups = groupBuckets(points, candleSeconds);
    const maxEvents = Math.max(1, ...groups.map((g) => g.events));
    const geom = groups.map((g) => {
      const x = (xAt(g.i0) + xAt(g.i1)) / 2;
      const width = Math.max(1, 0.7 * (xAt(g.i1) - xAt(g.i0) + step));
      return { g, x, width };
    });
    candles = geom
      .filter(({ g }) => g.mids.length > 0)
      .map(({ g, x, width }) => {
        const open = g.mids[0];
        const close = g.mids[g.mids.length - 1];
        const high = Math.max(...g.mids);
        const low = Math.min(...g.mids);
        return {
          start: g.start,
          open,
          high,
          low,
          close,
          x,
          width,
          yOpen: yAt(open),
          yClose: yAt(close),
          yHigh: yAt(high),
          yLow: yAt(low),
          up: close >= open,
        };
      });
    bars = geom.map(({ g, x, width }) => {
      const height = (g.events / maxEvents) * VOLUME_HEIGHT;
      return { x: x - width / 2, y: box.bottom - height, width, height };
    });
  } else {
    linePath = mids
      .map((m, i) => (m === null ? null : `${xAt(i).toFixed(2)},${yAt(m).toFixed(2)}`))
      .filter((s): s is string => s !== null)
      .map((s, i) => `${i === 0 ? "M" : "L"}${s}`)
      .join(" ");
    const maxEvents = Math.max(1, ...points.map((p) => p.events));
    const barWidth = Math.max(1, plotWidth / n - 1);
    bars = points.map((p, i) => {
      const height = (p.events / maxEvents) * VOLUME_HEIGHT;
      return { x: xAt(i) - barWidth / 2, y: box.bottom - height, width: barWidth, height };
    });
  }

  // Volume profile: one bar per recorded bin, centred on its price. Bins are
  // equal-width, so the bin height is the distance between neighbours.
  let profileBars: ProfileBarGeom[] = [];
  if (profile) {
    const bins = [...profile.bins].sort((a, b) => a.price - b.price);
    const binStep =
      bins.length > 1 ? (bins[bins.length - 1].price - bins[0].price) / (bins.length - 1) : (hi - lo) / 60;
    const maxVolume = Math.max(1, ...bins.map((b) => b.volume));
    const maxWidth = plotWidth * PROFILE_MAX_SHARE;
    const [vaLo, vaHi] = profile.value_area ?? [Number.NaN, Number.NaN];
    profileBars = bins
      .map((b) => {
        const yTop = yAt(b.price + binStep / 2);
        const yBottom = yAt(b.price - binStep / 2);
        const width = (b.volume / maxVolume) * maxWidth;
        return {
          price: b.price,
          x: box.right - width,
          y: yTop,
          width,
          height: Math.max(1, yBottom - yTop - 0.5),
          inValueArea: b.price >= vaLo - 1e-9 && b.price <= vaHi + 1e-9,
          isPoc: profile.poc !== null && Math.abs(b.price - profile.poc) < 1e-9,
        };
      })
      .filter((b) => b.width > 0 && b.y + b.height >= priceTop && b.y <= priceBottom);
  }
  const profileLevels: ProfileLevelGeom[] = levels.map((l) => ({ ...l, y: yAt(l.price) }));

  const yTicks: Tick[] = [0, 1, 2, 3].map((k) => {
    const value = lo + ((hi - lo) * (3 - k)) / 3;
    return { pos: yAt(value), label: opts.formatPrice(value) };
  });
  const xIndices = [0, Math.round((n - 1) / 3), Math.round((2 * (n - 1)) / 3), n - 1];
  const xTicks: Tick[] = xIndices.map((i) => ({ pos: xAt(i), label: opts.formatTime(points[i].bucket) }));

  return {
    priceTop,
    priceBottom,
    linePath,
    candles,
    candleSeconds,
    bars,
    yTicks,
    xTicks,
    profileBars,
    profileLevels,
  };
}
