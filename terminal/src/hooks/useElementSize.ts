import { useCallback, useRef, useState } from "react";

export interface ElementSize {
  width: number;
  height: number;
}

// Tracks an element's rendered content size with a ResizeObserver. Returns a
// callback ref (so it follows the element across mount / unmount, e.g. a plot
// that only renders once data arrives) and the latest size, or null before
// the first measurement.
export function useElementSize<T extends Element = HTMLDivElement>(): [
  (node: T | null) => void,
  ElementSize | null,
] {
  const [size, setSize] = useState<ElementSize | null>(null);
  const observer = useRef<ResizeObserver | null>(null);

  const ref = useCallback((node: T | null) => {
    observer.current?.disconnect();
    observer.current = null;
    if (!node) return;
    const ro = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      setSize((prev) =>
        prev && prev.width === width && prev.height === height ? prev : { width, height },
      );
    });
    ro.observe(node);
    observer.current = ro;
  }, []);

  return [ref, size];
}
