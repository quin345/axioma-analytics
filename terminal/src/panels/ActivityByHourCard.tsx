import ChartCard, { emptyReason } from "./ChartCard";
import { useElementSize } from "../hooks/useElementSize";
import Axes, { ACCENT, BAR, LABEL, MONO, Plot } from "../charts/Axes";
import { useMarket } from "../context/MarketContext";
import { ACTIVITY_HOUR_DAYS, useActivityByHour } from "../hooks/useMarketData";
import { valueTicks, yScale, fitBox, type PlotBox } from "../lib/chart";
import { fmtCompact } from "../lib/format";

const BOX: PlotBox = { width: 481, height: 154, left: 40, right: 478, top: 8, bottom: 136 };

// Activity by hour of day (UTC): book events per hour over the instrument's
// last week, averaged per day. The current UTC hour is highlighted.
export default function ActivityByHourCard() {
  const [plotRef, plotDims] = useElementSize();
  const box = fitBox(BOX, plotDims);
  const { symbolId } = useMarket();
  const { data, error } = useActivityByHour();
  const byHour = new Array<number>(24).fill(0);
  for (const r of data ?? []) byHour[r.hour] = r.events / ACTIVITY_HOUR_DAYS;
  const hasData = byHour.some((v) => v > 0);
  const scale = yScale(byHour, box, { includeZero: true, pad: 0.08 });
  const slot = (box.right - box.left) / 24;
  const peak = Math.max(...byHour);
  const peakHour = byHour.indexOf(peak);
  const nowHour = new Date().getUTCHours();

  return (
    <ChartCard
      title="Activity by hour"
      caption={`Events per hour, ${ACTIVITY_HOUR_DAYS}-day average · UTC`}
      headline={`PEAK ${fmtCompact(peak)} @ ${String(peakHour).padStart(2, "0")}h`}
      size="h-[236px] flex-[509_1_0px]"
      plotSize="h-[154px]"
      plotRef={plotRef}
      dataNodeId="2007:7542"
      hasData={hasData}
      emptyReason={emptyReason(symbolId, error, data !== null, "No events in the last week.")}
    >
      <Plot box={box} label="Average book events by hour of day">
        <Axes box={box} yTicks={valueTicks(scale, 4, fmtCompact)} />
        {byHour.map((v, h) => {
          const y = scale.at(v);
          return (
            <rect
              key={h}
              x={box.left + slot * h + 1}
              y={y}
              width={Math.max(1, slot - 2)}
              height={Math.max(0, scale.at(0) - y)}
              fill={h === nowHour ? ACCENT : BAR}
              opacity={h === nowHour ? 0.9 : 1}
            />
          );
        })}
        {[0, 6, 12, 18, 23].map((h) => (
          <text
            key={h}
            x={box.left + slot * (h + 0.5)}
            y={box.bottom + 12}
            fill={LABEL}
            fontSize={9}
            fontFamily={MONO}
            textAnchor="middle"
          >
            {String(h).padStart(2, "0")}
          </text>
        ))}
      </Plot>
    </ChartCard>
  );
}
