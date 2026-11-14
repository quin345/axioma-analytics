import ChartCard, { emptyReason } from "./ChartCard";
import Axes, { ACCENT, LABEL, MONO, Plot } from "../charts/Axes";
import { useMarket } from "../context/MarketContext";
import { useTickGaps } from "../hooks/useMarketData";
import { valueTicks, yScale, type PlotBox } from "../lib/chart";
import { fmtInt } from "../lib/format";

const BOX: PlotBox = { width: 481, height: 154, left: 40, right: 478, top: 8, bottom: 136 };

function edge(ms: number): string {
  return ms >= 1000 ? `${ms / 1000}s` : `${ms}`;
}

// Tick inter-arrival: the distribution of gaps between distinct event
// timestamps, in log-spaced millisecond bins, as a share of all gaps.
export default function TickInterArrivalCard() {
  const { symbolId, windowMinutes } = useMarket();
  const { data, error } = useTickGaps();
  const bins = data?.bins ?? [];
  const total = bins.reduce((s, b) => s + b.count, 0);
  const hasData = total > 0;
  const shares = bins.map((b) => (total ? (b.count / total) * 100 : 0));
  const scale = yScale(shares, BOX, { includeZero: true, pad: 0.08 });
  const slot = (BOX.right - BOX.left) / Math.max(1, bins.length);
  const median = data?.median_gap_ms ?? null;
  const shared = data?.shared_timestamp_share ?? null;

  return (
    <ChartCard
      title="Tick inter-arrival"
      caption={`Event gap distribution · ms · ${windowMinutes}m`}
      headline={`MED ${median === null ? "—" : fmtInt(median)} ms`}
      size="h-[236px] w-[509px]"
      plotSize="h-[154px] w-[481px]"
      dataNodeId="2007:7483"
      hasData={hasData}
      emptyReason={emptyReason(symbolId, error, data !== null, "Fewer than two events in this window.")}
      footnote={
        data
          ? `${fmtInt(data.events)} events · ${fmtInt(data.distinct_timestamps)} distinct stamps · ${shared === null ? "—" : (shared * 100).toFixed(1)}% share a stamp`
          : null
      }
    >
      <Plot box={BOX} label="Distribution of gaps between book events">
        <Axes box={BOX} yTicks={valueTicks(scale, 4, (v) => `${v.toFixed(0)}%`)} />
        {bins.map((b, i) => {
          const y = scale.at(shares[i]);
          const x = BOX.left + slot * i;
          const isMedian =
            median !== null && median >= b.from_ms && (b.to_ms === null || median < b.to_ms);
          return (
            <g key={b.from_ms}>
              <rect
                x={x + 1}
                y={y}
                width={Math.max(1, slot - 2)}
                height={Math.max(0, scale.at(0) - y)}
                fill={ACCENT}
                opacity={isMedian ? 0.95 : 0.55}
              />
              <text
                x={x + slot / 2}
                y={BOX.bottom + 12}
                fill={LABEL}
                fontSize={8}
                fontFamily={MONO}
                textAnchor="middle"
              >
                {b.to_ms === null ? `${edge(b.from_ms)}+` : edge(b.from_ms)}
              </text>
            </g>
          );
        })}
      </Plot>
    </ChartCard>
  );
}
