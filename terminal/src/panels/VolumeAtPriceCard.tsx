import { useCardState } from "../hooks/useCardState";
import { useMarket } from "../context/MarketContext";
import { useVolumeAtPrice } from "../hooks/useMarketData";
import { buildVapLadder, VAP_PLOT } from "../lib/chart";
import { fmtPrice, fmtVolume } from "../lib/format";
import { imgChevronDown, imgEllipsis, imgMaximize2 } from "../assets";

const LABEL = "#647587";
const BAR = "#163b47";
const BAR_VA = "#1f5666";
const POC = "#79d9ee";

export default function VolumeAtPriceCard() {
  const card = useCardState();
  const { symbolId, windowMinutes } = useMarket();
  const { data, error } = useVolumeAtPrice(symbolId, windowMinutes);
  const model = buildVapLadder(data, fmtPrice, fmtVolume);
  const hasProfile = model.rowCount > 0;

  return (
    <div
      className={`bg-[#11161d] border border-[#29323d] border-solid content-stretch flex flex-col gap-[8px] h-[248px] items-start overflow-clip p-[14px] relative rounded-[6px] shrink-0 w-[509px] ${card.className}`}
      data-node-id="2007:7377"
      data-name="Analytics card"
    >
      <div
        className="content-stretch flex items-center justify-between overflow-clip relative shrink-0 w-full"
        data-name="Chart header"
      >
        <div
          className="content-stretch flex gap-[7px] items-center overflow-clip relative shrink-0"
          onClick={card.onToggleCollapse}
          data-name="Chart title"
        >
          <div className="relative shrink-0 size-[12px]" data-name="chevron-down">
            <img
              alt=""
              className="absolute block inset-0 max-w-none size-full"
              src={imgChevronDown}
            />
          </div>
          <p className="[word-break:break-word] font-sans font-semibold leading-[1.4] not-italic relative shrink-0 text-[#e7edf3] text-[12px] whitespace-nowrap">
            Volume-at-price profile
          </p>
        </div>
        <div
          className="content-stretch flex gap-[12px] items-start overflow-clip relative shrink-0"
          data-name="Chart actions"
        >
          <div
            className="relative shrink-0 size-[12px]"
            onClick={card.onToggleExpand}
            data-name="maximize-2"
          >
            <img alt="" className="absolute block inset-0 max-w-none size-full" src={imgMaximize2} />
          </div>
          <div
            className="relative shrink-0 size-[12px]"
            onClick={card.onToggleHighlight}
            data-name="ellipsis"
          >
            <img alt="" className="absolute block inset-0 max-w-none size-full" src={imgEllipsis} />
          </div>
        </div>
      </div>

      <div
        className="[word-break:break-word] content-stretch flex items-start justify-between leading-[1.4] not-italic overflow-clip relative shrink-0 w-full whitespace-nowrap"
        data-name="Chart metadata"
      >
        <p className="font-sans font-normal relative shrink-0 text-[#94a3b3] text-[10px]">
          Traded volume by price · {windowMinutes}m lookback
        </p>
        <p className="font-mono relative shrink-0 text-[#79d9ee] text-[11px]">
          {hasProfile ? `POC ${model.pocLabel} · ${model.volumeLabel}` : "NO DATA"}
        </p>
      </div>

      {hasProfile ? (
        <div
          className="h-[166px] overflow-clip relative shrink-0 w-[481px]"
          data-name="Analytical plot"
        >
          <svg
            viewBox={`0 0 ${VAP_PLOT.width} ${VAP_PLOT.height}`}
            width="100%"
            height="100%"
            role="img"
            aria-label={`Volume at price for a ${windowMinutes} minute lookback`}
            style={{ display: "block", overflow: "hidden" }}
          >
            {model.bars.map((b) => (
              <rect
                key={b.price}
                x={VAP_PLOT.left}
                y={b.y}
                width={b.width}
                height={b.height}
                fill={b.inValueArea ? BAR_VA : BAR}
                opacity={b.inValueArea ? 0.95 : 0.6}
              />
            ))}
            {model.pocY !== null && (
              <line
                x1={VAP_PLOT.left}
                x2={VAP_PLOT.right}
                y1={model.pocY}
                y2={model.pocY}
                stroke={POC}
                strokeWidth={1}
                strokeDasharray="3 3"
              />
            )}
            <text
              x={VAP_PLOT.left}
              y={VAP_PLOT.bottom - 4}
              fill={LABEL}
              fontSize={9}
              fontFamily="IBM Plex Mono, monospace"
            >
              {model.valueAreaLabel ? `VA ${model.valueAreaLabel}` : ""}
            </text>
          </svg>
        </div>
      ) : (
        <div className="flex flex-1 flex-col gap-[8px] items-center justify-center w-full text-center">
          <p className="font-mono text-[#94a3b3] text-[11px] tracking-[0.12em]">NO DATA</p>
          <p className="font-sans text-[#647587] text-[11px] max-w-[420px]">
            {error
              ? `Volume-at-price feed unavailable: ${error}`
              : "Needs traded volume by price. Select an instrument to load its profile."}
          </p>
        </div>
      )}
    </div>
  );
}
