import { useId } from "react";
import type { VolumeAtPriceProfile } from "../api/types";
import { useMarket } from "../context/MarketContext";
import { buildPriceChart, PLOT } from "../lib/chart";
import { BIN_SECONDS } from "../lib/window";
import { useElementSize } from "../hooks/useElementSize";
import { fmtPrice, fmtTimeUtc } from "../lib/format";
import { plotBox } from "./geometry";
import { buildPriceModel, type PriceMode } from "./priceModel";

const GRID = "#202a34";
const LABEL = "#647587";
const LINE = "#79d9ee";
const BAR = "#163b47";
const UP = "#8ed6be";
const DOWN = "#f2a7a7";
const PROFILE = "#2a6f82";
const PROFILE_VA = "#3b8ea3";
const VA_RULE = "#baa2ec";
const MONO = "IBM Plex Mono, monospace";

type Props = {
  // "line" (the default, unchanged view) or OHLC candlesticks.
  mode?: PriceMode;
  // The volume profile to overlay, or null for none.
  profile?: VolumeAtPriceProfile | null;
  // Expanded cards draw at the plot's real pixel size (see charts/geometry).
  expanded?: boolean;
};

export default function PriceChart({ mode = "line", profile = null, expanded = false }: Props) {
  // Fill the plot area: measure it and stretch the design box to its width
  // (and, when the card is expanded, to its height too).
  const [plotRef, plotDims] = useElementSize();
  const box = plotBox(PLOT, plotDims, expanded);
  const { series } = useMarket();
  const clipId = useId();

  // The default view is exactly the original chart; the candle and profile
  // views use the extended model, which keeps the same layout.
  const plain = mode === "line" && !profile;
  const base = plain ? buildPriceChart(series, fmtPrice, fmtTimeUtc, box) : null;
  const model = plain
    ? null
    : buildPriceModel(
        series,
        { mode, profile, binSeconds: BIN_SECONDS, formatPrice: fmtPrice, formatTime: fmtTimeUtc },
        box,
      );
  const yTicks = base?.yTicks ?? model?.yTicks ?? [];
  const xTicks = base?.xTicks ?? model?.xTicks ?? [];
  const bars = base?.bars ?? model?.bars ?? [];
  const linePath = base?.linePath ?? model?.linePath ?? "";

  return (
    <div ref={plotRef} className="size-full">
      <svg
        viewBox={`0 0 ${box.width} ${box.height}`}
        width="100%"
        height="100%"
        role="img"
        aria-label={
          mode === "candles" ? "Mid price candlesticks and depth events" : "Mid price and depth events per bucket"
        }
        data-mode={mode}
        data-profile={profile ? "on" : "off"}
        style={{ display: "block", overflow: "hidden" }}
      >
        {model && (
          <defs>
            <clipPath id={clipId}>
              <rect
                x={box.left}
                y={model.priceTop}
                width={box.right - box.left}
                height={model.priceBottom - model.priceTop}
              />
            </clipPath>
          </defs>
        )}
        {yTicks.map((t) => (
          <g key={`y${t.pos}`}>
            <line x1={box.left} x2={box.right} y1={t.pos} y2={t.pos} stroke={GRID} />
            <text x={0} y={t.pos + 3} fill={LABEL} fontSize={9} fontFamily={MONO}>
              {t.label}
            </text>
          </g>
        ))}
        {xTicks.map((t, i) => (
          <g key={`x${i}`}>
            <line x1={t.pos} x2={t.pos} y1={box.top} y2={box.bottom} stroke={GRID} />
            <text x={t.pos} y={box.bottom + 13} fill={LABEL} fontSize={9} fontFamily={MONO} textAnchor="middle">
              {t.label}
            </text>
          </g>
        ))}

        {model && model.profileBars.length > 0 && (
          <g clipPath={`url(#${clipId})`} data-name="Volume profile">
            {model.profileBars.map((b) => (
              <rect
                key={b.price}
                x={b.x}
                y={b.y}
                width={b.width}
                height={b.height}
                fill={b.inValueArea ? PROFILE_VA : PROFILE}
                opacity={b.inValueArea ? 0.45 : 0.3}
              />
            ))}
          </g>
        )}

        {bars.map((b, i) => (
          <rect key={i} x={b.x} y={b.y} width={b.width} height={b.height} fill={BAR} opacity={0.65} />
        ))}

        {linePath && <path d={linePath} fill="none" stroke={LINE} strokeWidth={1.5} />}

        {model?.candles.map((c) => {
          const color = c.up ? UP : DOWN;
          const bodyTop = Math.min(c.yOpen, c.yClose);
          const bodyHeight = Math.max(1, Math.abs(c.yClose - c.yOpen));
          return (
            <g key={c.start} data-name="Candle">
              <line x1={c.x} x2={c.x} y1={c.yHigh} y2={c.yLow} stroke={color} strokeWidth={1} />
              <rect
                x={c.x - c.width / 2}
                y={bodyTop}
                width={c.width}
                height={bodyHeight}
                fill={c.up ? "#11161d" : color}
                stroke={color}
                strokeWidth={1}
              />
            </g>
          );
        })}

        {model?.profileLevels.map((l) => (
          <g key={l.kind} data-name={`Profile ${l.kind}`}>
            <line
              x1={box.left}
              x2={box.right}
              y1={l.y}
              y2={l.y}
              stroke={l.kind === "POC" ? LINE : VA_RULE}
              strokeWidth={1}
              strokeDasharray={l.kind === "POC" ? "4 3" : "2 3"}
              opacity={0.9}
            />
            <text
              // VAH / VAL sit at the right edge; the POC label sits just left
              // of the profile so the three never stack on each other.
              x={l.kind === "POC" ? box.right - (box.right - box.left) * 0.22 - 6 : box.right - 2}
              y={l.kind === "VAL" ? l.y + 10 : l.y - 3}
              fill={l.kind === "POC" ? LINE : VA_RULE}
              fontSize={9}
              fontFamily={MONO}
              textAnchor="end"
            >
              {`${l.kind} ${fmtPrice(l.price)}`}
            </text>
          </g>
        ))}
      </svg>
    </div>
  );
}
