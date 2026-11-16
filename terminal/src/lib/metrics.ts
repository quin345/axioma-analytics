// Turns a series of 10-second buckets into the numbers the KPI tiles show.
// Pure functions only, so they can be unit-tested without the browser.
import type { SeriesPoint } from "../api/types";
import { BIN_SECONDS } from "./window";

const SECONDS_PER_YEAR = 365 * 24 * 3600;

export interface Summary {
  ticks: number;
  lastMid: number | null;
  changeAbs: number | null;
  changePct: number | null;
  high: number | null;
  low: number | null;
  avgSpread: number | null;
  // Mean spread as a fraction of mid.
  avgRelSpread: number | null;
  realisedVolPct: number | null;
  // Depth events per minute over the window's span.
  ticksPerMin: number | null;
  // Signed-flow proxy in [-1, +1]: events signed by the tick rule (mid up =
  // buy, mid down = sell, unchanged inherits). Same rule as /api/order-flow.
  flowImbalance: number | null;
  // Newest bucket in the window - the session the numbers describe.
  lastBucket: string | null;
  firstBucket: string | null;
}

function mean(xs: number[]): number {
  return xs.reduce((a, b) => a + b, 0) / xs.length;
}

function stdev(xs: number[]): number {
  const m = mean(xs);
  return Math.sqrt(xs.reduce((a, x) => a + (x - m) ** 2, 0) / (xs.length - 1));
}

export function summarise(points: SeriesPoint[]): Summary {
  let ticks = 0;
  const mids: number[] = [];
  const spreads: number[] = [];
  const relSpreads: number[] = [];
  let buy = 0;
  let sell = 0;
  let sign = 0;

  for (const p of points) {
    ticks += p.events;
    if (p.best_bid !== null && p.best_ask !== null) {
      const mid = (p.best_bid + p.best_ask) / 2;
      const prev = mids.length ? mids[mids.length - 1] : null;
      if (prev !== null && mid !== prev) sign = mid > prev ? 1 : -1;
      if (sign > 0) buy += p.events;
      else if (sign < 0) sell += p.events;
      mids.push(mid);
      spreads.push(p.best_ask - p.best_bid);
      if (mid > 0) relSpreads.push((p.best_ask - p.best_bid) / mid);
    }
  }

  const firstBucket = points.length ? points[0].bucket : null;
  const lastBucket = points.length ? points[points.length - 1].bucket : null;
  const spanMin =
    firstBucket && lastBucket
      ? (Date.parse(lastBucket) - Date.parse(firstBucket)) / 60_000 + BIN_SECONDS / 60
      : 0;
  const ticksPerMin = spanMin > 0 ? ticks / spanMin : null;
  const flowImbalance = buy + sell > 0 ? (buy - sell) / (buy + sell) : null;

  if (mids.length === 0) {
    return {
      ticks,
      lastMid: null,
      changeAbs: null,
      changePct: null,
      high: null,
      low: null,
      avgSpread: null,
      avgRelSpread: null,
      realisedVolPct: null,
      ticksPerMin,
      flowImbalance,
      lastBucket,
      firstBucket,
    };
  }

  const first = mids[0];
  const last = mids[mids.length - 1];
  const logReturns = mids.slice(1).map((m, i) => Math.log(m / mids[i]));
  // Annualised from 10-second buckets; returns are in percent.
  const realisedVolPct =
    logReturns.length > 1
      ? stdev(logReturns) * Math.sqrt(SECONDS_PER_YEAR / BIN_SECONDS) * 100
      : null;

  return {
    ticks,
    lastMid: last,
    changeAbs: last - first,
    changePct: first !== 0 ? ((last - first) / first) * 100 : null,
    high: Math.max(...mids),
    low: Math.min(...mids),
    avgSpread: mean(spreads),
    avgRelSpread: relSpreads.length ? mean(relSpreads) : null,
    realisedVolPct,
    ticksPerMin,
    flowImbalance,
    lastBucket,
    firstBucket,
  };
}
