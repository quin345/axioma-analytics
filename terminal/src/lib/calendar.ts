// Month-grid geometry for the activity calendar. Pure functions, no React.

export const WEEKDAYS = ["M", "T", "W", "T", "F", "S", "S"];
export const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

export interface MonthCursor {
  year: number;
  month: number; // 0-11
}

export function isoDay(year: number, month: number, day: number): string {
  return `${year}-${String(month + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

// Monday-first weeks of day numbers, null-padded at both ends.
export function monthWeeks({ year, month }: MonthCursor): (number | null)[][] {
  const lead = (new Date(Date.UTC(year, month, 1)).getUTCDay() + 6) % 7;
  const days = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
  const cells: (number | null)[] = [
    ...Array<null>(lead).fill(null),
    ...Array.from({ length: days }, (_, i) => i + 1),
  ];
  while (cells.length % 7) cells.push(null);
  return Array.from({ length: cells.length / 7 }, (_, w) => cells.slice(w * 7, w * 7 + 7));
}

export function shiftMonth({ year, month }: MonthCursor, delta: number): MonthCursor {
  const m = month + delta;
  return { year: year + Math.floor(m / 12), month: ((m % 12) + 12) % 12 };
}

// Heat colour for a day's share of the busiest day: four steps of the accent.
export function heat(share: number): string {
  if (share <= 0) return "#11161d";
  if (share < 0.25) return "#132a33";
  if (share < 0.5) return "#163b47";
  if (share < 0.75) return "#1f5666";
  return "#2b7a8e";
}
