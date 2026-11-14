import { useEffect, useState } from "react";

export interface PollState<T> {
  data: T | null;
  error: string | null;
}

// Calls `load` now and then every `intervalMs`. Pass load = null to skip fetching.
export function usePoll<T>(
  load: (() => Promise<T>) | null,
  deps: unknown[],
  intervalMs: number,
): PollState<T> {
  const [state, setState] = useState<PollState<T>>({ data: null, error: null });

  useEffect(() => {
    // A new key (instrument, window) must not keep showing the old one's data.
    setState({ data: null, error: null });
    if (!load) return;
    let active = true;
    const run = () =>
      load().then(
        (data) => active && setState({ data, error: null }),
        (err: unknown) => active && setState((prev) => ({ ...prev, error: String(err) })),
      );
    run();
    const timer = window.setInterval(run, intervalMs);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
    // deps come from the caller; load is recreated each render on purpose
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return state;
}
