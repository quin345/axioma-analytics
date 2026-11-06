import { useCardState } from "../hooks/useCardState";
import { imgAskDepth, imgBidDepth, imgChevronDown, imgEllipsis, imgMaximize2 } from "../assets";

export default function BookDepthCard() {
  const card = useCardState();
  return (
    <div
      className={`bg-[#11161d] border border-[#29323d] border-solid content-stretch flex flex-col gap-[8px] h-[248px] items-start overflow-clip p-[14px] relative rounded-[6px] shrink-0 w-[509px] ${card.className}`}
      data-node-id="2007:7346"
      data-name="Analytics card"
    >
      <div
        className="content-stretch flex items-center justify-between overflow-clip relative shrink-0 w-full"
        data-node-id="2007:7347"
        data-name="Chart header"
      >
        <div
          className="content-stretch flex gap-[7px] items-center overflow-clip relative shrink-0"
          data-node-id="2007:7348"
          onClick={card.onToggleCollapse}
          data-name="Chart title"
        >
          <div
            className="relative shrink-0 size-[12px]"
            data-node-id="2007:8414"
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
            data-node-id="2007:7350"
          >
            Book depth
          </p>
        </div>
        <div
          className="content-stretch flex gap-[12px] items-start overflow-clip relative shrink-0"
          data-node-id="2007:7351"
          data-name="Chart actions"
        >
          <div
            className="relative shrink-0 size-[12px]"
            data-node-id="2007:8417"
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
            data-node-id="2007:8420"
            onClick={card.onToggleHighlight}
            data-name="ellipsis"
          >
            <img alt="" className="absolute block inset-0 max-w-none size-full" src={imgEllipsis} />
          </div>
        </div>
      </div>
      <div
        className="[word-break:break-word] content-stretch flex items-start justify-between leading-[1.4] not-italic overflow-clip relative shrink-0 w-full whitespace-nowrap"
        data-node-id="2007:7354"
        data-name="Chart metadata"
      >
        <p
          className="font-sans font-normal relative shrink-0 text-[#94a3b3] text-[10px]"
          data-node-id="2007:7355"
        >
          Cumulative liquidity · EUR millions
        </p>
        <p
          className="font-mono relative shrink-0 text-[#79d9ee] text-[11px]"
          data-node-id="2007:7356"
        >
          B 18.4m / A 14.1m
        </p>
      </div>
      <div
        className="h-[166px] overflow-clip relative shrink-0 w-[481px]"
        data-node-id="2007:7357"
        data-name="Analytical plot"
      >
        <p
          className="[word-break:break-word] absolute font-mono leading-[normal] left-[432px] not-italic text-[#647587] text-[9px] top-[151px] whitespace-nowrap"
          data-node-id="2007:7376"
        >
          +5 pips
        </p>
        <p
          className="[word-break:break-word] absolute font-mono leading-[normal] left-[326.67px] not-italic text-[#647587] text-[9px] top-[151px] whitespace-nowrap"
          data-node-id="2007:7375"
        >
          +1.7
        </p>
        <p
          className="[word-break:break-word] absolute font-mono leading-[normal] left-[186.33px] not-italic text-[#647587] text-[9px] top-[151px] whitespace-nowrap"
          data-node-id="2007:7374"
        >
          −1.7
        </p>
        <p
          className="[word-break:break-word] absolute font-mono leading-[normal] left-[46px] not-italic text-[#647587] text-[9px] top-[151px] whitespace-nowrap"
          data-node-id="2007:7373"
        >
          −5 pips
        </p>
        <div
          className="absolute h-[166px] left-0 top-0 w-[481px]"
          data-node-id="2007:7372"
          data-name="Ask depth"
        >
          <div className="absolute inset-[29.62%_2.91%_16.87%_54.2%]">
            <img alt="" className="block max-w-none size-full" src={imgAskDepth} />
          </div>
        </div>
        <div
          className="absolute h-[166px] left-0 top-0 w-[481px]"
          data-node-id="2007:7371"
          data-name="Bid depth"
        >
          <div className="absolute inset-[13.3%_47.55%_16.87%_9.56%]">
            <img alt="" className="block max-w-none size-full" src={imgBidDepth} />
          </div>
        </div>
        <p
          className="[word-break:break-word] absolute font-mono leading-[normal] left-0 not-italic text-[#647587] text-[9px] top-[133px] whitespace-nowrap"
          data-node-id="2007:7370"
        >
          0
        </p>
        <p
          className="[word-break:break-word] absolute font-mono leading-[normal] left-0 not-italic text-[#647587] text-[9px] top-[91px] whitespace-nowrap"
          data-node-id="2007:7369"
        >
          7m
        </p>
        <p
          className="[word-break:break-word] absolute font-mono leading-[normal] left-0 not-italic text-[#647587] text-[9px] top-[49px] whitespace-nowrap"
          data-node-id="2007:7368"
        >
          13m
        </p>
        <p
          className="[word-break:break-word] absolute font-mono leading-[normal] left-0 not-italic text-[#647587] text-[9px] top-[7px] whitespace-nowrap"
          data-node-id="2007:7367"
        >
          20m
        </p>
        <div
          className="absolute bg-[#202a34] h-[126px] left-[467px] top-[12px] w-px"
          data-node-id="2007:7366"
          data-name="Time rule"
        />
        <div
          className="absolute bg-[#202a34] h-[126px] left-[361.75px] top-[12px] w-px"
          data-node-id="2007:7365"
          data-name="Time rule"
        />
        <div
          className="absolute bg-[#202a34] h-[126px] left-[256.5px] top-[12px] w-px"
          data-node-id="2007:7364"
          data-name="Time rule"
        />
        <div
          className="absolute bg-[#202a34] h-[126px] left-[151.25px] top-[12px] w-px"
          data-node-id="2007:7363"
          data-name="Time rule"
        />
        <div
          className="absolute bg-[#202a34] h-[126px] left-[46px] top-[12px] w-px"
          data-node-id="2007:7362"
          data-name="Time rule"
        />
        <div
          className="absolute bg-[#202a34] h-px left-[46px] top-[138px] w-[421px]"
          data-node-id="2007:7361"
          data-name="Grid rule"
        />
        <div
          className="absolute bg-[#202a34] h-px left-[46px] top-[96px] w-[421px]"
          data-node-id="2007:7360"
          data-name="Grid rule"
        />
        <div
          className="absolute bg-[#202a34] h-px left-[46px] top-[54px] w-[421px]"
          data-node-id="2007:7359"
          data-name="Grid rule"
        />
        <div
          className="absolute bg-[#202a34] h-px left-[46px] top-[12px] w-[421px]"
          data-node-id="2007:7358"
          data-name="Grid rule"
        />
      </div>
    </div>
  );
}
