import type { PlotBox, Tick } from "../lib/chart";

// Shared SVG axes for the card plots: horizontal grid rules with value labels
// in the left gutter, and vertical rules with labels in the bottom strip.
// Colours and type follow the design (grid #202a34, label #647587, 9px mono).
export const GRID = "#202a34";
export const LABEL = "#647587";
export const ACCENT = "#79d9ee";
export const ACCENT_SELL = "#baa2ec";
export const POSITIVE = "#8ed6be";
export const NEGATIVE = "#f2a7a7";
export const BAR = "#163b47";
export const MONO = "IBM Plex Mono, monospace";

type Props = {
  box: PlotBox;
  yTicks?: Tick[];
  xTicks?: Tick[];
  // x tick label anchoring; bars read better left-aligned at the first label.
  xAnchor?: "middle" | "start";
};

export default function Axes({ box, yTicks = [], xTicks = [], xAnchor = "middle" }: Props) {
  return (
    <g>
      {yTicks.map((t, i) => (
        <g key={`y${i}`}>
          <line x1={box.left} x2={box.right} y1={t.pos} y2={t.pos} stroke={GRID} />
          <text x={0} y={t.pos + 3} fill={LABEL} fontSize={9} fontFamily={MONO}>
            {t.label}
          </text>
        </g>
      ))}
      {xTicks.map((t, i) => (
        <g key={`x${i}`}>
          <line x1={t.pos} x2={t.pos} y1={box.top} y2={box.bottom} stroke={GRID} />
          <text
            x={t.pos}
            y={box.bottom + 13}
            fill={LABEL}
            fontSize={9}
            fontFamily={MONO}
            textAnchor={
              xAnchor === "start" ? "start" : i === 0 ? "start" : i === xTicks.length - 1 ? "end" : "middle"
            }
          >
            {t.label}
          </text>
        </g>
      ))}
    </g>
  );
}

// An SVG root sized to its plot box.
export function Plot({
  box,
  label,
  children,
}: {
  box: PlotBox;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <svg
      viewBox={`0 0 ${box.width} ${box.height}`}
      width="100%"
      height="100%"
      role="img"
      aria-label={label}
      style={{ display: "block", overflow: "hidden" }}
    >
      {children}
    </svg>
  );
}
