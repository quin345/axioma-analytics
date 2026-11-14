import ChartCard, { emptyReason } from "./ChartCard";
import { useElementSize } from "../hooks/useElementSize";
import Axes, { ACCENT, ACCENT_SELL, LABEL, MONO, Plot } from "../charts/Axes";
import { useMarket } from "../context/MarketContext";
import { FLOW_BIN_SECONDS, useOrderFlow } from "../hooks/useMarketData";
import { linePath, timeTicks, valueTicks, xAt, yScale, fitBox, type PlotBox } from "../lib/chart";
import { fmtCompact, fmtInt, fmtSigned, fmtTimeUtc } from "../lib/format";

const BOX: PlotBox = { width: 481, height: 166, left: 40, right: 476, top: 8, bottom: 146 };

// Order-flow imbalance, proxied. The feed has no trades and its ladder sizes
// are fixed (the book's own imbalance_ratio is ~0), so flow is approximated by
// the tick rule: each minute's depth events are signed by the mid's direction.
// Bars are the per-minute signed flow; the line is its running sum.
export default function OrderFlowImbalanceCard() {
  const [plotRef, plotDims] = useElementSize();
  const box = fitBox(BOX, plotDims);
  const { symbolId, windowMinutes } = useMarket();
  const { data, error } = useOrderFlow();
  const points = data?.series ?? [];
  const hasData = points.length > 1 && (data?.buy_events ?? 0) + (data?.sell_events ?? 0) > 0;

  const signed = points.map((p) => p.signed);
  const cumulative = points.map((p) => p.cumulative);
  const barScale = yScale(signed, box, { includeZero: true, pad: 0.05 });
  const lineScale = yScale(cumulative, box, { includeZero: true });
  const zero = barScale.at(0);
  const n = points.length;
  const barWidth = Math.max(1, (box.right - box.left) / Math.max(1, n) - 0.5);

  return (
    <ChartCard
      title="Order-flow imbalance"
      caption={`Tick-rule signed events · ${FLOW_BIN_SECONDS / 60}m bars · ${windowMinutes}m`}
      headline={`OFI ${fmtSigned(data?.imbalance ?? null, 2)}`}
      size="h-[248px] flex-[509_1_0px]"
      plotSize="h-[150px]"
      plotRef={plotRef}
      dataNodeId="2007:7313"
      hasData={hasData}
      emptyReason={emptyReason(symbolId, error, data !== null, "No mid moves in this window to sign.")}
      footnote={
        data
          ? `PROXY · buy ${fmtInt(data.buy_events)} / sell ${fmtInt(data.sell_events)} · book imbalance ${fmtSigned(data.book_imbalance, 3)}`
          : null
      }
    >
      <Plot box={box} label={`Signed order flow for a ${windowMinutes} minute lookback`}>
        <Axes
          box={box}
          yTicks={valueTicks(barScale, 3, fmtCompact)}
          xTicks={timeTicks(points.map((p) => p.bucket), box, fmtTimeUtc)}
        />
        {points.map((p, i) => {
          const y = barScale.at(p.signed);
          return (
            <rect
              key={p.bucket}
              x={xAt(box, i, n) - barWidth / 2}
              y={Math.min(y, zero)}
              width={barWidth}
              height={Math.max(0.5, Math.abs(zero - y))}
              fill={p.signed >= 0 ? ACCENT : ACCENT_SELL}
              opacity={0.55}
            />
          );
        })}
        <path d={linePath(cumulative, box, lineScale)} fill="none" stroke="#e7edf3" strokeWidth={1.2} />
        <text x={box.right} y={box.top + 8} fill={LABEL} fontSize={9} fontFamily={MONO} textAnchor="end">
          cum {fmtCompact(cumulative[cumulative.length - 1] ?? null)}
        </text>
      </Plot>
    </ChartCard>
  );
}
