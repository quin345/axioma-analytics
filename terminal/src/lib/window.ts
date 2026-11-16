// Rolling-window options shown in the control bar.
// Every series bucket is this many seconds wide.
export const BIN_SECONDS = 10;

export const WINDOWS: Record<string, { minutes: number; label: string }> = {
  "1h": { minutes: 60, label: "1-hour" },
  "2h": { minutes: 120, label: "2-hour" },
  "3h": { minutes: 180, label: "3-hour" },
  "4h": { minutes: 240, label: "4-hour" },
};
