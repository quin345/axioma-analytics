import ChartCard, { emptyReason } from "./ChartCard";
import { useElementSize } from "../hooks/useElementSize";
import Axes, { ACCENT, Plot } from "../charts/Axes";
import { useMarket } from "../context/MarketContext";
import { useVolatility, VOL_WINDOW_BUCKETS } from "../hooks/useMarketData";
import { linePath, timeTicks, valueTicks, xAt, yScale, fitBox, type PlotBox } from "../lib/chart";
import { formatKpis, fmtTimeUtc } from "../lib/format";
import { BIN_SECONDS } from "../lib/window";

const BOX: PlotBox = { width: 482, height: 154, left: 40, right: 468, top: 12, bottom: 136 };

// Rolling realised volatility: annualised stdev of 10-second mid log returns
// over a trailing 30-minute window, one point per bucket.
export default function RollingVolatilityCard() {
  const [plotRef, plotDims] = useElementSize();
  const box = fitBox(BOX, plotDims);
  const { symbolId, windowMinutes, summary } = useMarket();
  const k = formatKpis(summary);
  const { data, error } = useVolatility();
  const points = data ?? [];
  const hasData = points.length > 1;
  const values = points.map((p) => p.vol_pct);
  const scale = yScale(values, box);
  const last = points.length - 1;
  const rollMin = (VOL_WINDOW_BUCKETS * BIN_SECONDS) / 60;

  return (
    <ChartCard
      title="Rolling volatility"
      caption={`Annualised % · ${rollMin}m rolling / ${BIN_SECONDS}s returns · ${windowMinutes}m`}
      headline={hasData ? `${values[last].toFixed(2)}% · win ${k.vol}` : k.vol}
      size="h-[236px] flex-[510_1_0px]"
      plotSize="h-[154px]"
      plotRef={plotRef}
      dataNodeId="2007:7595"
      hasData={hasData}
      emptyReason={emptyReason(
        symbolId,
        error,
        data !== null,
        `Needs more than ${rollMin} minutes of quotes in the window.`,
      )}
    >
      <Plot box={box} label="Rolling annualised volatility">
        <Axes
          box={box}
          yTicks={valueTicks(scale, 4, (v) => v.toFixed(1))}
          xTicks={timeTicks(points.map((p) => p.bucket), box, fmtTimeUtc)}
        />
        <path d={linePath(values, box, scale)} fill="none" stroke={ACCENT} strokeWidth={1.5} />
        {hasData && (
          <circle cx={xAt(box, last, points.length)} cy={scale.at(values[last])} r={2.5} fill={ACCENT} />
        )}
      </Plot>
    </ChartCard>
  );
}
