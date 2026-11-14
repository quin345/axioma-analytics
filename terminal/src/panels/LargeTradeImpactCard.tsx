import ChartCard, { emptyReason } from "./ChartCard";
import { useElementSize } from "../hooks/useElementSize";
import Axes, { ACCENT, ACCENT_SELL, GRID, LABEL, MONO, Plot } from "../charts/Axes";
import { useMarket } from "../context/MarketContext";
import { useImpact } from "../hooks/useMarketData";
import { fitBox, valueTicks, yScale, type PlotBox } from "../lib/chart";
import { fmtBpsSigned, fmtInt, fmtSigned, fmtTimeUtc } from "../lib/format";

// Left: the mean impact curve. Right: the most recent bursts as a table.
const BOX: PlotBox = { width: 430, height: 144, left: 40, right: 424, top: 8, bottom: 124 };

function horizonLabel(s: number): string {
  return s < 60 ? `${s}s` : `${s / 60}m`;
}

// Large-trade impact, proxied. The feed has no trade sizes, so a "large trade"
// is an activity burst: a 10s bucket in the window's top 5% by depth events,
// signed by the mid move into it. The curve is the mean signed mid response
// (bps of the pre-burst mid) at each horizon - above zero the move persisted,
// below zero it reverted.
export default function LargeTradeImpactCard() {
  // The curve takes the card width left over by the burst table.
  const [plotRef, plotDims] = useElementSize();
  const box = fitBox(BOX, plotDims);
  const { symbolId, windowMinutes } = useMarket();
  const { data, error } = useImpact();
  const curve = (data?.curve ?? []).filter((c) => c.mean_bps !== null);
  const hasData = (data?.bursts ?? 0) > 0 && curve.length > 0;
  const headlineH = data?.headline_horizon_s ?? 60;
  const headline = curve.find((c) => c.horizon_s === headlineH)?.mean_bps ?? null;

  const scale = yScale(curve.map((c) => c.mean_bps as number), box, { includeZero: true });
  const zero = scale.at(0);
  const slot = (box.right - box.left) / Math.max(1, curve.length);
  const barWidth = Math.min(46, slot * 0.6);

  return (
    <ChartCard
      title="Large-trade impact"
      caption={`Mid response after activity bursts · bps · ${windowMinutes}m`}
      headline={`${horizonLabel(headlineH)} ${fmtBpsSigned(headline)} bps`}
      size="h-[226px] flex-[770_1_0px]"
      plotSize="h-[144px]"
      dataNodeId="2007:7687"
      hasData={hasData}
      emptyReason={emptyReason(symbolId, error, data !== null, "No directional bursts in this window.")}
      footnote={
        data
          ? `PROXY · ${fmtInt(data.bursts)} bursts ≥ ${fmtInt(data.threshold)} events / 10s, signed by the tick rule`
          : null
      }
    >
      <div className="flex gap-[12px] h-full w-full">
        <div ref={plotRef} className="h-[144px] flex-1 min-w-0">
          <Plot box={box} label="Mean signed mid response after activity bursts">
            <Axes box={box} yTicks={valueTicks(scale, 4, (v) => fmtSigned(v, 1))} />
            <line x1={box.left} x2={box.right} y1={zero} y2={zero} stroke={LABEL} strokeDasharray="2 3" />
            {curve.map((c, i) => {
              const x = box.left + slot * (i + 0.5);
              const y = scale.at(c.mean_bps as number);
              return (
                <g key={c.horizon_s}>
                  <rect
                    x={x - barWidth / 2}
                    y={Math.min(y, zero)}
                    width={barWidth}
                    height={Math.max(0.5, Math.abs(zero - y))}
                    fill={(c.mean_bps as number) >= 0 ? ACCENT : ACCENT_SELL}
                    opacity={0.7}
                  />
                  <text x={x} y={box.bottom + 13} fill={LABEL} fontSize={9} fontFamily={MONO} textAnchor="middle">
                    +{horizonLabel(c.horizon_s)}
                  </text>
                </g>
              );
            })}
          </Plot>
        </div>
        <div className="flex flex-col gap-[3px] w-[300px] shrink-0 font-mono text-[10px] leading-[1.4]">
          <div className="flex justify-between text-[#647587] border-b pb-[2px]" style={{ borderColor: GRID }}>
            <span className="w-[44px]">TIME</span>
            <span className="w-[30px]">SIDE</span>
            <span className="w-[54px] text-right">EVENTS</span>
            <span className="w-[56px] text-right">MOVE</span>
            <span className="w-[66px] text-right">+{horizonLabel(headlineH)}</span>
          </div>
          {(data?.events ?? []).slice(0, 7).map((e) => (
            <div key={e.bucket} className="flex justify-between text-[#e7edf3]">
              <span className="w-[44px]">{fmtTimeUtc(e.bucket)}</span>
              <span className="w-[30px]" style={{ color: e.direction > 0 ? ACCENT : ACCENT_SELL }}>
                {e.direction > 0 ? "BUY" : "SELL"}
              </span>
              <span className="w-[54px] text-right">{fmtInt(e.events)}</span>
              <span className="w-[56px] text-right">{e.move_bps.toFixed(2)}</span>
              <span className="w-[66px] text-right">{fmtBpsSigned(e.impact_bps)}</span>
            </div>
          ))}
        </div>
      </div>
    </ChartCard>
  );
}
