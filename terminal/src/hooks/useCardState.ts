import { useState } from "react";

// Collapse, expand and highlight state for an analytics card. The CSS in
// index.css reads the is-collapsed / is-expanded / is-highlighted classes.
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
    onToggleCollapse: () => setCollapsed((v) => !v),
    onToggleExpand: () => setExpanded((v) => !v),
    onToggleHighlight: () => setHighlighted((v) => !v),
  };
}
