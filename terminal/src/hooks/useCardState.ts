import { useState } from "react";

// Collapse, expand and highlight state for an analytics card. The CSS in
// index.css reads the is-collapsed / is-expanded / is-highlighted classes.
export type CardState = ReturnType<typeof useCardState>;

export function useCardState() {
  const [collapsed, setCollapsed] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [highlighted, setHighlighted] = useState(false);

  const className = [
    collapsed && "is-collapsed",
    expanded && "is-expanded",
    highlighted && "is-highlighted",
  ]
    .filter(Boolean)
    .join(" ");

  return {
    className,
    // Expanded cards let their plot take the whole card (see ChartCard and
    // charts/geometry.plotBox) instead of keeping the design plot height.
    expanded,
    onToggleCollapse: () => setCollapsed((v) => !v),
    onToggleExpand: () => setExpanded((v) => !v),
    onToggleHighlight: () => setHighlighted((v) => !v),
  };
}
