import { useMarket } from "../context/MarketContext";
import { buildPriceChart, fitBox, PLOT } from "../lib/chart";
import { useElementSize } from "../hooks/useElementSize";
import { fmtPrice, fmtTimeUtc } from "../lib/format";

const GRID = "#202a34";
const LABEL = "#647587";

export default function PriceChart() {
  // Fill the plot area: measure it and stretch the design box to its width.
  const [plotRef, plotDims] = useElementSize();
  const box = fitBox(PLOT, plotDims);
  const { series } = useMarket();
  const model = buildPriceChart(series, fmtPrice, fmtTimeUtc, box);

  return (
    <div ref={plotRef} className="size-full">
      <svg
        viewBox={`0 0 ${box.width} ${box.height}`}
        width="100%"
        height="100%"
        role="img"
        aria-label="Mid price and depth events per bucket"
        style={{ display: "block", overflow: "hidden" }}
      >
        {model.yTicks.map((t) => (
          <g key={`y${t.pos}`}>
            <line x1={box.left} x2={box.right} y1={t.pos} y2={t.pos} stroke={GRID} />
            <text x={0} y={t.pos + 3} fill={LABEL} fontSize={9} fontFamily="IBM Plex Mono, monospace">
              {t.label}
            </text>
          </g>
        ))}
        {model.xTicks.map((t, i) => (
          <g key={`x${i}`}>
            <line x1={t.pos} x2={t.pos} y1={box.top} y2={box.bottom} stroke={GRID} />
            <text
              x={t.pos}
              y={box.bottom + 13}
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
    </div>
  );
}
