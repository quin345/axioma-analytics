// Named API calls. Components never build URLs themselves.
import { getJson } from "./client";
import type { SeriesPoint } from "./types";

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
