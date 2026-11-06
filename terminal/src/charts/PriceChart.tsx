import { useMarket } from "../context/MarketContext";
import { buildPriceChart, PLOT } from "../lib/chart";
import { fmtPrice, fmtTimeUtc } from "../lib/format";

const GRID = "#202a34";
const LABEL = "#647587";

export default function PriceChart() {
  const { series } = useMarket();
  const model = buildPriceChart(series, fmtPrice, fmtTimeUtc);

  return (
    <svg
      viewBox={`0 0 ${PLOT.width} ${PLOT.height}`}
      width="100%"
      height={PLOT.height}
      role="img"
      aria-label="Mid price and depth events per bucket"
      style={{ display: "block", overflow: "hidden" }}
    >
      {model.yTicks.map((t) => (
        <g key={`y${t.pos}`}>
          <line x1={PLOT.left} x2={PLOT.right} y1={t.pos} y2={t.pos} stroke={GRID} />
          <text x={0} y={t.pos + 3} fill={LABEL} fontSize={9} fontFamily="IBM Plex Mono, monospace">
            {t.label}
          </text>
        </g>
      ))}
      {model.xTicks.map((t, i) => (
        <g key={`x${i}`}>
          <line x1={t.pos} x2={t.pos} y1={PLOT.top} y2={PLOT.bottom} stroke={GRID} />
          <text
            x={t.pos}
            y={215}
            fill={LABEL}
            fontSize={9}
            fontFamily="IBM Plex Mono, monospace"
            textAnchor="middle"
          >
            {t.label}
          </text>
        </g>
      ))}
      {model.bars.map((b, i) => (
        <rect
          key={i}
          x={b.x}
          y={b.y}
          width={b.width}
          height={b.height}
          fill="#163b47"
          opacity={0.65}
        />
      ))}
      <path d={model.linePath} fill="none" stroke="#79d9ee" strokeWidth={1.5} />
    </svg>
  );
}
