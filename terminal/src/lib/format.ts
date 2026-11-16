// Display formatting. Keep every number-to-text rule in this file.
import type { Summary } from "./metrics";

// Default decimal places for prices. The live value is per instrument - see
// setPriceDecimals / decimalsForMid - because a 2-dp gold quote and a 5-dp FX
// quote cannot share one rule. The catalogue carries no precision, so it is
// derived from the price level.
export const PRICE_DECIMALS = 2;
let priceDecimals = PRICE_DECIMALS;

// Decimals that make one tick of a price at this level legible: FX majors near
// 1 get 5, JPY crosses near 150 get 3, gold and indices 2, crypto 2.
export function decimalsForMid(mid: number | null): number {
  if (mid === null || !Number.isFinite(mid) || mid <= 0) return PRICE_DECIMALS;
  if (mid < 10) return 5;
  if (mid < 1000) return 3;
  return 2;
}

// Set once per render from App, before any card formats a price.
export function setPriceDecimals(decimals: number): void {
  priceDecimals = decimals;
}

const intFormat = new Intl.NumberFormat("en-US");

export function fmtInt(n: number | null): string {
  return n === null ? "—" : intFormat.format(Math.round(n));
}

export function fmtTimeUtc(iso: string): string {
  return new Date(iso).toISOString().slice(11, 16);
}

// HH:MM:SS.mmm for raw tick rows.
export function fmtClockMs(iso: string): string {
  return new Date(iso).toISOString().slice(11, 23);
}

// "14 NOV 2026"
export function fmtDateUtc(iso: string): string {
  const d = new Date(iso);
  const month = d.toLocaleString("en-US", { month: "short", timeZone: "UTC" }).toUpperCase();
  return `${String(d.getUTCDate()).padStart(2, "0")} ${month} ${d.getUTCFullYear()}`;
}

export function fmtPrice(n: number | null): string {
  return n === null ? "—" : n.toFixed(priceDecimals);
}

// Compact count ("54.8k", "1.2m") for axis labels and peaks.
export function fmtCompact(n: number | null): string {
  if (n === null) return "—";
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}m`;
  if (abs >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return n.toFixed(0);
}

// A signed number with an explicit "+" and a true minus sign.
export function fmtSigned(n: number | null, digits: number, suffix = ""): string {
  if (n === null || !Number.isFinite(n)) return "—";
  const text = Math.abs(n).toFixed(digits);
  return `${n >= 0 ? "+" : "−"}${text}${suffix}`;
}

// Basis points straight (already in bps), signed.
export function fmtBpsSigned(bps: number | null): string {
  return fmtSigned(bps, 2);
}

// Relative spread (fraction of mid) as basis points: 0.00005 -> "0.50". The
// spread card's axis and summary read in bps, which is how a desk quotes it.
export function fmtBps(rel: number | null): string {
  return rel === null ? "—" : (rel * 10_000).toFixed(2);
}

// Compact volume text for the ladder ("18.4m", "942k", "0"). Matches the units
// the design's Book-depth card prints.
export function fmtVolume(n: number | null): string {
  if (n === null) return "—";
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}m`;
  if (abs >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return n.toFixed(0);
}

export interface KpiText {
  ticks: string;
  last: string;
  change: string;
  changeNote: string;
  changePositive: boolean | null;
  highLow: string;
  high: string;
  low: string;
  vol: string;
  spread: string;
  spreadNote: string;
  ticksPerMin: string;
  flow: string;
  flowPositive: boolean | null;
}

export function formatKpis(s: Summary | null): KpiText {
  if (!s) {
    return {
      ticks: "—",
      last: "—",
      change: "—",
      changeNote: "waiting for data",
      changePositive: null,
      highLow: "—",
      high: "—",
      low: "—",
      vol: "—",
      spread: "—",
      spreadNote: "",
      ticksPerMin: "—",
      flow: "—",
      flowPositive: null,
    };
  }
  return {
    ticks: fmtInt(s.ticks),
    last: fmtPrice(s.lastMid),
    change: fmtSigned(s.changePct, 2, "%"),
    changeNote:
      s.changeAbs === null ? "" : `${fmtSigned(s.changeAbs, priceDecimals)} vs window start`,
    changePositive: s.changeAbs === null ? null : s.changeAbs >= 0,
    highLow: s.high === null || s.low === null ? "—" : `${fmtPrice(s.high)} / ${fmtPrice(s.low)}`,
    high: fmtPrice(s.high),
    low: fmtPrice(s.low),
    vol: s.realisedVolPct === null ? "—" : `${s.realisedVolPct.toFixed(2)}%`,
    spread: fmtPrice(s.avgSpread),
    spreadNote: s.avgRelSpread === null ? "" : `${fmtBps(s.avgRelSpread)} bps of mid`,
    ticksPerMin: s.ticksPerMin === null ? "—" : fmtInt(s.ticksPerMin),
    flow: fmtSigned(s.flowImbalance, 2),
    flowPositive: s.flowImbalance === null ? null : s.flowImbalance >= 0,
  };
}
