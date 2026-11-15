// Plot-box sizing shared by the card panels. Pure functions, no React.
import { fitBox, type PlotBox } from "../lib/chart";

// Map a design plot box onto the pixels its container actually has, in both
// directions. Gutters and margins keep their design size (so 9px labels stay
// 9px), and the plot area takes the rest. Used for expanded cards, where the
// plot is far taller than the design and `fitBox` (which keeps the design
// height and only stretches the width) would scale the type up with it.
export function fillBox<B extends PlotBox>(box: B, size: { width: number; height: number } | null): B {
  if (!size || size.width <= 0 || size.height <= 0) return box;
  const rightMargin = box.width - box.right;
  const bottomMargin = box.height - box.bottom;
  const width = Math.max(box.left + rightMargin + 1, size.width);
  const height = Math.max(box.top + bottomMargin + 1, size.height);
  return { ...box, width, height, right: width - rightMargin, bottom: height - bottomMargin };
}

// The box a card should draw in: the design box stretched to the plot's width
// in the normal grid (unchanged behaviour), or the full plot area when the
// card is expanded.
export function plotBox<B extends PlotBox>(
  box: B,
  size: { width: number; height: number } | null,
  expanded: boolean,
): B {
  return expanded ? fillBox(box, size) : fitBox(box, size);
}
