import { useCardState } from "../hooks/useCardState";
import PriceChart from "../charts/PriceChart";
import { useMarket } from "../context/MarketContext";
import { formatKpis } from "../lib/format";
import { imgChevronDown, imgEllipsis, imgMaximize2 } from "../assets";

export default function PriceVolumeCard() {
  const card = useCardState();
  const k = formatKpis(useMarket().summary);
  return (
    <div
      className={`bg-[#11161d] border border-[#29323d] border-solid content-stretch flex flex-col gap-[8px] h-[312px] items-start overflow-clip p-[14px] relative rounded-[6px] shrink-0 w-[1024px] ${card.className}`}
      data-node-id="2007:7222"
      data-name="Analytics card"
    >
      <div
        className="content-stretch flex items-center justify-between overflow-clip relative shrink-0 w-full"
        data-node-id="2007:7223"
        data-name="Chart header"
      >
        <div
          className="content-stretch flex gap-[7px] items-center overflow-clip relative shrink-0"
          data-node-id="2007:7224"
          onClick={card.onToggleCollapse}
          data-name="Chart title"
        >
          <div
            className="relative shrink-0 size-[12px]"
            data-node-id="2007:8396"
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
            data-node-id="2007:7226"
          >
            Price + volume
          </p>
        </div>
        <div
          className="content-stretch flex gap-[12px] items-start overflow-clip relative shrink-0"
          data-node-id="2007:7227"
          data-name="Chart actions"
        >
          <div
            className="relative shrink-0 size-[12px]"
            data-node-id="2007:8399"
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
            data-node-id="2007:8402"
            onClick={card.onToggleHighlight}
            data-name="ellipsis"
          >
            <img alt="" className="absolute block inset-0 max-w-none size-full" src={imgEllipsis} />
          </div>
        </div>
      </div>
      <div
        className="[word-break:break-word] content-stretch flex items-start justify-between leading-[1.4] not-italic overflow-clip relative shrink-0 w-full"
        data-node-id="2007:7230"
        data-name="Chart metadata"
      >
        <p
          className="font-sans font-normal relative shrink-0 text-[#94a3b3] text-[10px] whitespace-nowrap"
          data-node-id="2007:7231"
        >
          Mid price · depth events per 10s bucket
        </p>
        <p
          className="font-mono relative shrink-0 text-[#79d9ee] text-[11px] whitespace-pre"
          data-node-id="2007:7232"
        >{`${k.last}  /  ${k.change}`}</p>
      </div>
      <div
        className="h-[230px] overflow-clip relative shrink-0 w-[996px]"
        data-node-id="2007:7233"
        data-name="Analytical plot"
      >
        <PriceChart />
      </div>
    </div>
  );
}
