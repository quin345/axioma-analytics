import ChartCard, { emptyReason } from "./ChartCard";
import { useElementSize } from "../hooks/useElementSize";
import Axes, { ACCENT, ACCENT_SELL, LABEL, MONO, Plot } from "../charts/Axes";
import { useMarket } from "../context/MarketContext";
import { ACF_MAX_LAG, useAcf } from "../hooks/useMarketData";
import { valueTicks, yScale, fitBox, type PlotBox } from "../lib/chart";
import { fmtInt, fmtSigned } from "../lib/format";

const BOX: PlotBox = { width: 742, height: 144, left: 40, right: 738, top: 8, bottom: 126 };

// Autocorrelation of 10-second mid log returns, lags 1..30, with the 95%
// white-noise band. Negative lag-1 is the bid-ask bounce / mean reversion;
// bars outside the band are significant.
export default function PriceAutocorrelationCard() {
  const [plotRef, plotDims] = useElementSize();
  const box = fitBox(BOX, plotDims);
  const { symbolId, windowMinutes } = useMarket();
  const { data, error } = useAcf();
  const lags = (data?.lags ?? []).filter((l) => l.acf !== null) as { lag: number; acf: number }[];
  const band = data?.band ?? null;
  const hasData = lags.length > 0;
  const extent = Math.max(0.1, band ?? 0, ...lags.map((l) => Math.abs(l.acf)));
  const scale = yScale([-extent, extent], box, { pad: 0.1 });
  const zero = scale.at(0);
  const slot = (box.right - box.left) / Math.max(1, lags.length);
  const lag1 = lags.find((l) => l.lag === 1)?.acf ?? null;

  return (
    <ChartCard
      title="Price-behaviour autocorrelation"
      caption={`10s mid-return ACF · 95% band ±${band === null ? "—" : band.toFixed(3)} · n=${fmtInt(data?.n ?? null)} · ${windowMinutes}m`}
      headline={`LAG 1  ${fmtSigned(lag1, 2)}`}
      size="h-[226px] flex-[770_1_0px]"
      plotSize="h-[144px]"
      plotRef={plotRef}
      dataNodeId="2007:7627"
      hasData={hasData}
      emptyReason={emptyReason(symbolId, error, data !== null, "Too few returns in this window.")}
    >
      <Plot box={box} label={`Autocorrelation of mid returns, ${ACF_MAX_LAG} lags`}>
        <Axes box={box} yTicks={valueTicks(scale, 5, (v) => fmtSigned(v, 2))} />
        {band !== null && (
          <rect
            x={box.left}
            y={scale.at(band)}
            width={box.right - box.left}
            height={scale.at(-band) - scale.at(band)}
            fill={ACCENT}
            opacity={0.07}
          />
        )}
        <line x1={box.left} x2={box.right} y1={zero} y2={zero} stroke={LABEL} />
        {lags.map((l, i) => {
          const y = scale.at(l.acf);
          const significant = band !== null && Math.abs(l.acf) > band;
          return (
            <g key={l.lag}>
              <rect
                x={box.left + slot * i + slot * 0.25}
                y={Math.min(y, zero)}
                width={Math.max(1, slot * 0.5)}
                height={Math.max(0.5, Math.abs(zero - y))}
                fill={l.acf >= 0 ? ACCENT : ACCENT_SELL}
                opacity={significant ? 0.95 : 0.4}
              />
              {(l.lag === 1 || l.lag % 5 === 0) && (
                <text
                  x={box.left + slot * (i + 0.5)}
                  y={box.bottom + 13}
                  fill={LABEL}
                  fontSize={9}
                  fontFamily={MONO}
                  textAnchor="middle"
                >
                  {l.lag}
                </text>
              )}
            </g>
          );
        })}
      </Plot>
    </ChartCard>
  );
}
