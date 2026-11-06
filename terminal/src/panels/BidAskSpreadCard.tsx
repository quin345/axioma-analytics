import { useCardState } from "../hooks/useCardState";
import { useMarket } from "../context/MarketContext";
import { formatKpis } from "../lib/format";
import {
  imgChevronDown,
  imgDataSeries2,
  imgEllipsis,
  imgLatestObservation,
  imgMaximize2,
} from "../assets";

export default function BidAskSpreadCard() {
  const card = useCardState();
  const k = formatKpis(useMarket().summary);
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
          Quoted spread · pips / 1m mean
        </p>
        <p
          className="font-mono relative shrink-0 text-[#79d9ee] text-[11px]"
          data-node-id="2007:7461"
        >
          AVG {k.spread}
        </p>
      </div>
      <div
        className="h-[166px] overflow-clip relative shrink-0 w-[482px]"
        data-node-id="2007:7462"
        data-name="Analytical plot"
      >
        <p
          className="[word-break:break-word] absolute font-mono leading-[normal] left-[433px] not-italic text-[#647587] text-[9px] top-[151px] whitespace-nowrap"
          data-node-id="2007:7481"
        >
          16:00
        </p>
        <p
          className="[word-break:break-word] absolute font-mono leading-[normal] left-[327.33px] not-italic text-[#647587] text-[9px] top-[151px] whitespace-nowrap"
          data-node-id="2007:7480"
        >
          14:40
        </p>
        <p
          className="[word-break:break-word] absolute font-mono leading-[normal] left-[186.67px] not-italic text-[#647587] text-[9px] top-[151px] whitespace-nowrap"
          data-node-id="2007:7479"
        >
          13:20
        </p>
        <p
          className="[word-break:break-word] absolute font-mono leading-[normal] left-[46px] not-italic text-[#647587] text-[9px] top-[151px] whitespace-nowrap"
          data-node-id="2007:7478"
        >
          12:00
        </p>
        <div
          className="absolute left-[465px] size-[6px] top-[74.52px]"
          data-node-id="2007:7477"
          data-name="Latest observation"
        >
          <img
            alt=""
            className="absolute block inset-0 max-w-none size-full"
            src={imgLatestObservation}
          />
        </div>
        <div
          className="absolute h-[166px] left-0 top-0 w-[482px]"
          data-node-id="2007:7476"
          data-name="Data series"
        >
          <div className="absolute inset-[16.64%_2.86%_44.43%_9.38%]">
            <img alt="" className="block max-w-none size-full" src={imgDataSeries2} />
          </div>
        </div>
        <p
          className="[word-break:break-word] absolute font-mono leading-[normal] left-0 not-italic text-[#647587] text-[9px] top-[133px] whitespace-nowrap"
          data-node-id="2007:7475"
        >
          0.0
        </p>
        <p
          className="[word-break:break-word] absolute font-mono leading-[normal] left-0 not-italic text-[#647587] text-[9px] top-[91px] whitespace-nowrap"
          data-node-id="2007:7474"
        >
          0.5
        </p>
        <p
          className="[word-break:break-word] absolute font-mono leading-[normal] left-0 not-italic text-[#647587] text-[9px] top-[49px] whitespace-nowrap"
          data-node-id="2007:7473"
        >
          1.0
        </p>
        <p
          className="[word-break:break-word] absolute font-mono leading-[normal] left-0 not-italic text-[#647587] text-[9px] top-[7px] whitespace-nowrap"
          data-node-id="2007:7472"
        >
          1.5
        </p>
        <div
          className="absolute bg-[#202a34] h-[126px] left-[468px] top-[12px] w-px"
          data-node-id="2007:7471"
          data-name="Time rule"
        />
        <div
          className="absolute bg-[#202a34] h-[126px] left-[362.5px] top-[12px] w-px"
          data-node-id="2007:7470"
          data-name="Time rule"
        />
        <div
          className="absolute bg-[#202a34] h-[126px] left-[257px] top-[12px] w-px"
          data-node-id="2007:7469"
          data-name="Time rule"
        />
        <div
          className="absolute bg-[#202a34] h-[126px] left-[151.5px] top-[12px] w-px"
          data-node-id="2007:7468"
          data-name="Time rule"
        />
        <div
          className="absolute bg-[#202a34] h-[126px] left-[46px] top-[12px] w-px"
          data-node-id="2007:7467"
          data-name="Time rule"
        />
        <div
          className="absolute bg-[#202a34] h-px left-[46px] top-[138px] w-[422px]"
          data-node-id="2007:7466"
          data-name="Grid rule"
        />
        <div
          className="absolute bg-[#202a34] h-px left-[46px] top-[96px] w-[422px]"
          data-node-id="2007:7465"
          data-name="Grid rule"
        />
        <div
          className="absolute bg-[#202a34] h-px left-[46px] top-[54px] w-[422px]"
          data-node-id="2007:7464"
          data-name="Grid rule"
        />
        <div
          className="absolute bg-[#202a34] h-px left-[46px] top-[12px] w-[422px]"
          data-node-id="2007:7463"
          data-name="Grid rule"
        />
      </div>
    </div>
  );
}
