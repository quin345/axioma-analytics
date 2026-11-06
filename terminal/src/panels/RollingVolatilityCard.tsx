import { useCardState } from "../hooks/useCardState";
import { useMarket } from "../context/MarketContext";
import { formatKpis } from "../lib/format";
import {
  imgChevronDown,
  imgDataSeries3,
  imgEllipsis,
  imgLatestObservation1,
  imgMaximize2,
} from "../assets";

export default function RollingVolatilityCard() {
  const card = useCardState();
  const k = formatKpis(useMarket().summary);
  return (
    <div
      className={`bg-[#11161d] border border-[#29323d] border-solid content-stretch flex flex-col gap-[8px] h-[236px] items-start overflow-clip p-[14px] relative rounded-[6px] shrink-0 w-[510px] ${card.className}`}
      data-node-id="2007:7595"
      data-name="Analytics card"
    >
      <div
        className="content-stretch flex items-center justify-between overflow-clip relative shrink-0 w-full"
        data-node-id="2007:7596"
        data-name="Chart header"
      >
        <div
          className="content-stretch flex gap-[7px] items-center overflow-clip relative shrink-0"
          data-node-id="2007:7597"
          onClick={card.onToggleCollapse}
          data-name="Chart title"
        >
          <div
            className="relative shrink-0 size-[12px]"
            data-node-id="2007:8459"
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
            data-node-id="2007:7599"
          >
            Rolling volatility
          </p>
        </div>
        <div
          className="content-stretch flex gap-[12px] items-start overflow-clip relative shrink-0"
          data-node-id="2007:7600"
          data-name="Chart actions"
        >
          <div
            className="relative shrink-0 size-[12px]"
            data-node-id="2007:8462"
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
            data-node-id="2007:8465"
            onClick={card.onToggleHighlight}
            data-name="ellipsis"
          >
            <img alt="" className="absolute block inset-0 max-w-none size-full" src={imgEllipsis} />
          </div>
        </div>
      </div>
      <div
        className="[word-break:break-word] content-stretch flex items-start justify-between leading-[1.4] not-italic overflow-clip relative shrink-0 w-full whitespace-nowrap"
        data-node-id="2007:7603"
        data-name="Chart metadata"
      >
        <p
          className="font-sans font-normal relative shrink-0 text-[#94a3b3] text-[10px]"
          data-node-id="2007:7604"
        >
          Annualised % · 30m rolling / 1m returns
        </p>
        <p
          className="font-mono relative shrink-0 text-[#79d9ee] text-[11px]"
          data-node-id="2007:7605"
        >
          {k.vol}
        </p>
      </div>
      <div
        className="h-[154px] overflow-clip relative shrink-0 w-[482px]"
        data-node-id="2007:7606"
        data-name="Analytical plot"
      >
        <p
          className="[word-break:break-word] absolute font-mono leading-[normal] left-[433px] not-italic text-[#647587] text-[9px] top-[139px] whitespace-nowrap"
          data-node-id="2007:7625"
        >
          16:00
        </p>
        <p
          className="[word-break:break-word] absolute font-mono leading-[normal] left-[327.33px] not-italic text-[#647587] text-[9px] top-[139px] whitespace-nowrap"
          data-node-id="2007:7624"
        >
          14:40
        </p>
        <p
          className="[word-break:break-word] absolute font-mono leading-[normal] left-[186.67px] not-italic text-[#647587] text-[9px] top-[139px] whitespace-nowrap"
          data-node-id="2007:7623"
        >
          13:20
        </p>
        <p
          className="[word-break:break-word] absolute font-mono leading-[normal] left-[46px] not-italic text-[#647587] text-[9px] top-[139px] whitespace-nowrap"
          data-node-id="2007:7622"
        >
          12:00
        </p>
        <div
          className="absolute left-[465px] size-[6px] top-[53.84px]"
          data-node-id="2007:7621"
          data-name="Latest observation"
        >
          <img
            alt=""
            className="absolute block inset-0 max-w-none size-full"
            src={imgLatestObservation1}
          />
        </div>
        <div
          className="absolute h-[154px] left-0 top-0 w-[482px]"
          data-node-id="2007:7620"
          data-name="Data series"
        >
          <div className="absolute inset-[25.5%_2.81%_55.59%_9.46%]">
            <img alt="" className="block max-w-none size-full" src={imgDataSeries3} />
          </div>
        </div>
        <p
          className="[word-break:break-word] absolute font-mono leading-[normal] left-0 not-italic text-[#647587] text-[9px] top-[121px] whitespace-nowrap"
          data-node-id="2007:7619"
        >
          5.0
        </p>
        <p
          className="[word-break:break-word] absolute font-mono leading-[normal] left-0 not-italic text-[#647587] text-[9px] top-[83px] whitespace-nowrap"
          data-node-id="2007:7618"
        >
          6.0
        </p>
        <p
          className="[word-break:break-word] absolute font-mono leading-[normal] left-0 not-italic text-[#647587] text-[9px] top-[45px] whitespace-nowrap"
          data-node-id="2007:7617"
        >
          7.0
        </p>
        <p
          className="[word-break:break-word] absolute font-mono leading-[normal] left-0 not-italic text-[#647587] text-[9px] top-[7px] whitespace-nowrap"
          data-node-id="2007:7616"
        >
          8.0
        </p>
        <div
          className="absolute bg-[#202a34] h-[114px] left-[468px] top-[12px] w-px"
          data-node-id="2007:7615"
          data-name="Time rule"
        />
        <div
          className="absolute bg-[#202a34] h-[114px] left-[362.5px] top-[12px] w-px"
          data-node-id="2007:7614"
          data-name="Time rule"
        />
        <div
          className="absolute bg-[#202a34] h-[114px] left-[257px] top-[12px] w-px"
          data-node-id="2007:7613"
          data-name="Time rule"
        />
        <div
          className="absolute bg-[#202a34] h-[114px] left-[151.5px] top-[12px] w-px"
          data-node-id="2007:7612"
          data-name="Time rule"
        />
        <div
          className="absolute bg-[#202a34] h-[114px] left-[46px] top-[12px] w-px"
          data-node-id="2007:7611"
          data-name="Time rule"
        />
        <div
          className="absolute bg-[#202a34] h-px left-[46px] top-[126px] w-[422px]"
          data-node-id="2007:7610"
          data-name="Grid rule"
        />
        <div
          className="absolute bg-[#202a34] h-px left-[46px] top-[88px] w-[422px]"
          data-node-id="2007:7609"
          data-name="Grid rule"
        />
        <div
          className="absolute bg-[#202a34] h-px left-[46px] top-[50px] w-[422px]"
          data-node-id="2007:7608"
          data-name="Grid rule"
        />
        <div
          className="absolute bg-[#202a34] h-px left-[46px] top-[12px] w-[422px]"
          data-node-id="2007:7607"
          data-name="Grid rule"
        />
      </div>
    </div>
  );
}
