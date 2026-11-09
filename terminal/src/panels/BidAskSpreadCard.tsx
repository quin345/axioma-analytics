import { useCardState } from "../hooks/useCardState";
import { useMarket } from "../context/MarketContext";
import { useSpread } from "../hooks/useMarketData";
import { buildSpreadChart, SPREAD_PLOT } from "../lib/chart";
import { fmtBps, fmtTimeUtc } from "../lib/format";
import { imgChevronDown, imgEllipsis, imgMaximize2 } from "../assets";

const GRID = "#202a34";
const LABEL = "#647587";
const LINE = "#79d9ee";

export default function BidAskSpreadCard() {
  const card = useCardState();
  const { symbolId, windowMinutes } = useMarket();
  const { data, error } = useSpread(symbolId, windowMinutes);
  const model = buildSpreadChart(data?.series ?? [], fmtBps, fmtTimeUtc);
  // The header reads the series' own mean, so the number matches the line drawn
  // under it rather than the price window's average.
  const avgLabel = fmtBps(data?.mean ?? null);

  return (
    <div
      className={`bg-[#11161d] border border-[#29323d] border-solid content-stretch flex flex-col gap-[8px] h-[248px] items-start overflow-clip p-[14px] relative rounded-[6px] shrink-0 w-[510px] ${card.className}`}
      data-node-id="2007:7451"
      data-name="Analytics card"
    >
      <div
        className="content-stretch flex items-center justify-between overflow-clip relative shrink-0 w-full"
        data-node-id="2007:7452"
        data-name="Chart header"
      >
        <div
          className="content-stretch flex gap-[7px] items-center overflow-clip relative shrink-0"
          data-node-id="2007:7453"
          onClick={card.onToggleCollapse}
          data-name="Chart title"
        >
          <div
            className="relative shrink-0 size-[12px]"
            data-node-id="2007:8432"
            data-name="chevron-down"
          >
            <img
              alt=""
              className="absolute block inset-0 max-w-none size-full"
              src={imgChevronDown}
            />
          </div>
          <p
            className="[word-break:break-word] font-sans font-semibold leading-[1.4] not-italic relative shrink-0 text-[#e7edf3] text-[12px] whitespace-nowrap"
            data-node-id="2007:7455"
          >
            Bid-ask spread
          </p>
        </div>
        <div
          className="content-stretch flex gap-[12px] items-start overflow-clip relative shrink-0"
          data-node-id="2007:7456"
          data-name="Chart actions"
        >
          <div
            className="relative shrink-0 size-[12px]"
            data-node-id="2007:8435"
            onClick={card.onToggleExpand}
            data-name="maximize-2"
          >
            <img
              alt=""
              className="absolute block inset-0 max-w-none size-full"
              src={imgMaximize2}
            />
          </div>
          <div
            className="relative shrink-0 size-[12px]"
            data-node-id="2007:8438"
            onClick={card.onToggleHighlight}
            data-name="ellipsis"
          >
            <img alt="" className="absolute block inset-0 max-w-none size-full" src={imgEllipsis} />
          </div>
        </div>
      </div>
      <div
        className="[word-break:break-word] content-stretch flex items-start justify-between leading-[1.4] not-italic overflow-clip relative shrink-0 w-full whitespace-nowrap"
        data-node-id="2007:7459"
        data-name="Chart metadata"
      >
        <p
          className="font-sans font-normal relative shrink-0 text-[#94a3b3] text-[10px]"
          data-node-id="2007:7460"
        >
          Quoted spread · bps of mid · {windowMinutes}m
        </p>
        <p
          className="font-mono relative shrink-0 text-[#79d9ee] text-[11px]"
          data-node-id="2007:7461"
        >
          AVG {avgLabel}
        </p>
      </div>
      {model.hasSeries ? (
        <div
          className="h-[166px] overflow-clip relative shrink-0 w-[482px]"
          data-node-id="2007:7462"
          data-name="Analytical plot"
        >
          <svg
            viewBox={`0 0 ${SPREAD_PLOT.width} ${SPREAD_PLOT.height}`}
            width="100%"
            height="100%"
            role="img"
            aria-label={`Relative quoted spread for a ${windowMinutes} minute lookback`}
            style={{ display: "block", overflow: "hidden" }}
          >
            {model.yTicks.map((t) => (
              <g key={`y${t.pos}`}>
                <line
                  x1={SPREAD_PLOT.left}
                  x2={SPREAD_PLOT.right}
                  y1={t.pos}
                  y2={t.pos}
                  stroke={GRID}
                />
                <text
                  x={0}
                  y={t.pos + 3}
                  fill={LABEL}
                  fontSize={9}
                  fontFamily="IBM Plex Mono, monospace"
                >
                  {t.label}
                </text>
              </g>
            ))}
            {model.xTicks.map((t, i) => (
              <g key={`x${i}`}>
                <line
                  x1={t.pos}
                  x2={t.pos}
                  y1={SPREAD_PLOT.top}
                  y2={SPREAD_PLOT.bottom}
                  stroke={GRID}
                />
                <text
                  x={t.pos}
                  y={SPREAD_PLOT.bottom + 13}
                  fill={LABEL}
                  fontSize={9}
                  fontFamily="IBM Plex Mono, monospace"
                  textAnchor="middle"
                >
                  {t.label}
                </text>
              </g>
            ))}
            <path d={model.linePath} fill="none" stroke={LINE} strokeWidth={1.5} />
            {model.lastPoint && (
              <circle
                cx={model.lastPoint.x}
                cy={model.lastPoint.y}
                r={2.5}
                fill={LINE}
                data-name="Latest observation"
              />
            )}
          </svg>
        </div>
      ) : (
        <div className="flex flex-1 flex-col gap-[8px] items-center justify-center w-full text-center">
          <p className="font-mono text-[#94a3b3] text-[11px] tracking-[0.12em]">NO DATA</p>
          <p className="font-sans text-[#647587] text-[11px] max-w-[420px]">
            {error
              ? `Bid-ask spread feed unavailable: ${error}`
              : "Needs a two-sided book. Select an instrument to load its spread series."}
          </p>
        </div>
      )}
    </div>
  );
}
