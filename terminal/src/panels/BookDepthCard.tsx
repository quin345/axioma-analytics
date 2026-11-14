import ChartCard, { emptyReason } from "./ChartCard";
import { ACCENT, ACCENT_SELL, GRID, LABEL, MONO, Plot } from "../charts/Axes";
import { useMarket } from "../context/MarketContext";
import { useBook } from "../hooks/useMarketData";
import { BOOK_PLOT, buildBookDepth } from "../lib/chart";
import { fmtPrice, fmtTimeUtc, fmtVolume } from "../lib/format";

// Book depth from the newest two-sided snapshot: cumulative resting size per
// level, bids growing left from the centre, asks growing right, nearest the
// touch on the top row. Real data - the feed's own `bids` / `asks` ladder.
export default function BookDepthCard() {
  const { symbolId, live } = useMarket();
  const { data, error } = useBook();
  const model = buildBookDepth(data, fmtVolume);
  const hasData = model.rowCount > 0;
  const centerX = (BOOK_PLOT.left + BOOK_PLOT.right) / 2;
  const rowH = hasData ? (BOOK_PLOT.bottom - BOOK_PLOT.top) / model.rowCount : 0;

  return (
    <ChartCard
      title="Book depth"
      caption={`Cumulative resting size · ${model.rowCount} levels${data && !live ? ` · as of ${fmtTimeUtc(data.timestamp)}` : ""}`}
      headline={`B ${fmtVolume(model.bidTotal)} / A ${fmtVolume(model.askTotal)}`}
      size="h-[248px] w-[509px]"
      plotSize="h-[166px] w-[481px]"
      dataNodeId="2007:7346"
      hasData={hasData}
      emptyReason={emptyReason(symbolId, error, data !== null, "No two-sided book on record.")}
    >
      <Plot box={BOOK_PLOT} label="Cumulative order book depth">
        <line x1={centerX} x2={centerX} y1={BOOK_PLOT.top} y2={BOOK_PLOT.bottom} stroke={GRID} />
        {model.bars.map((b) => (
          <g key={`${b.side}${b.price}`}>
            <rect
              x={b.x}
              y={b.y}
              width={Math.max(0.5, b.width)}
              height={b.height}
              fill={b.side === "bid" ? ACCENT : ACCENT_SELL}
              opacity={0.55}
            />
          </g>
        ))}
        {model.bars
          .filter((_, i) => i % 2 === 0)
          .map((b, i) => (
            <text
              key={`l${i}`}
              x={0}
              y={b.y + rowH / 2 + 3}
              fill={LABEL}
              fontSize={8}
              fontFamily={MONO}
            >
              {fmtPrice(b.price)}
            </text>
          ))}
        <text x={centerX - 4} y={BOOK_PLOT.bottom + 14} fill={ACCENT} fontSize={9} fontFamily={MONO} textAnchor="end">
          BID {fmtPrice(model.bestBid)}
        </text>
        <text x={centerX + 4} y={BOOK_PLOT.bottom + 14} fill={ACCENT_SELL} fontSize={9} fontFamily={MONO}>
          ASK {fmtPrice(model.bestAsk)}
        </text>
        <text x={BOOK_PLOT.right} y={BOOK_PLOT.bottom + 14} fill={LABEL} fontSize={9} fontFamily={MONO} textAnchor="end">
          max {fmtVolume(model.maxVolume)}
        </text>
      </Plot>
    </ChartCard>
  );
}
