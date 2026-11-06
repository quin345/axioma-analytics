import { useCardState } from "../hooks/useCardState";
import { imgChevronDown, imgEllipsis, imgMaximize2 } from "../assets";

export default function ActivityByHourCard() {
  const card = useCardState();
  return (
    <div
      className={`bg-[#11161d] border border-[#29323d] border-solid content-stretch flex flex-col gap-[8px] h-[236px] items-start overflow-clip p-[14px] relative rounded-[6px] shrink-0 w-[509px] ${card.className}`}
      data-node-id="2007:7542"
      data-name="Analytics card"
    >
      <div
        className="content-stretch flex items-center justify-between overflow-clip relative shrink-0 w-full"
        data-node-id="2007:7543"
        data-name="Chart header"
      >
        <div
          className="content-stretch flex gap-[7px] items-center overflow-clip relative shrink-0"
          data-node-id="2007:7544"
          onClick={card.onToggleCollapse}
          data-name="Chart title"
        >
          <div
            className="relative shrink-0 size-[12px]"
            data-node-id="2007:8450"
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
            data-node-id="2007:7546"
          >
            Activity by hour
          </p>
        </div>
        <div
          className="content-stretch flex gap-[12px] items-start overflow-clip relative shrink-0"
          data-node-id="2007:7547"
          data-name="Chart actions"
        >
          <div
            className="relative shrink-0 size-[12px]"
            data-node-id="2007:8453"
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
            data-node-id="2007:8456"
            onClick={card.onToggleHighlight}
            data-name="ellipsis"
          >
            <img alt="" className="absolute block inset-0 max-w-none size-full" src={imgEllipsis} />
          </div>
        </div>
      </div>
      <div
        className="[word-break:break-word] content-stretch flex items-start justify-between leading-[1.4] not-italic overflow-clip relative shrink-0 w-full whitespace-nowrap"
        data-node-id="2007:7550"
        data-name="Chart metadata"
      >
        <p
          className="font-sans font-normal relative shrink-0 text-[#94a3b3] text-[10px]"
          data-node-id="2007:7551"
        >
          09 Oct · events, thousands · full day context
        </p>
        <p
          className="font-mono relative shrink-0 text-[#79d9ee] text-[11px]"
          data-node-id="2007:7552"
        >
          PEAK 54.8k
        </p>
      </div>
      <div
        className="h-[154px] overflow-clip relative shrink-0 w-[481px]"
        data-node-id="2007:7553"
        data-name="Analytical plot"
      >
        <p
          className="[word-break:break-word] absolute font-mono leading-[normal] left-[432px] not-italic text-[#647587] text-[9px] top-[139px] whitespace-nowrap"
          data-node-id="2007:7594"
        >
          24 UTC
        </p>
        <p
          className="[word-break:break-word] absolute font-mono leading-[normal] left-[326.67px] not-italic text-[#647587] text-[9px] top-[139px] whitespace-nowrap"
          data-node-id="2007:7593"
        >
          16
        </p>
        <p
          className="[word-break:break-word] absolute font-mono leading-[normal] left-[186.33px] not-italic text-[#647587] text-[9px] top-[139px] whitespace-nowrap"
          data-node-id="2007:7592"
        >
          08
        </p>
        <p
          className="[word-break:break-word] absolute font-mono leading-[normal] left-[46px] not-italic text-[#647587] text-[9px] top-[139px] whitespace-nowrap"
          data-node-id="2007:7591"
        >
          00
        </p>
        <div
          className="absolute bg-[#79d9ee] h-[0.1px] left-[451.46px] opacity-80 top-[126px] w-[13.542px]"
          data-node-id="2007:7590"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[0.1px] left-[433.92px] opacity-80 top-[126px] w-[13.542px]"
          data-node-id="2007:7589"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[0.1px] left-[416.38px] opacity-80 top-[126px] w-[13.542px]"
          data-node-id="2007:7588"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[0.1px] left-[398.83px] opacity-80 top-[126px] w-[13.542px]"
          data-node-id="2007:7587"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[0.1px] left-[381.29px] opacity-80 top-[126px] w-[13.542px]"
          data-node-id="2007:7586"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[0.1px] left-[363.75px] opacity-80 top-[126px] w-[13.542px]"
          data-node-id="2007:7585"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[0.1px] left-[346.21px] opacity-80 top-[126px] w-[13.542px]"
          data-node-id="2007:7584"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[0.1px] left-[328.67px] opacity-80 top-[126px] w-[13.542px]"
          data-node-id="2007:7583"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[85.728px] left-[311.13px] opacity-80 top-[40.27px] w-[13.542px]"
          data-node-id="2007:7582"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[104.12px] left-[293.58px] opacity-80 top-[21.88px] w-[13.542px]"
          data-node-id="2007:7581"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[83.98px] left-[276.04px] opacity-80 top-[42.02px] w-[13.542px]"
          data-node-id="2007:7580"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[76.38px] left-[258.5px] opacity-80 top-[49.62px] w-[13.542px]"
          data-node-id="2007:7579"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[65.637px] left-[240.96px] opacity-35 top-[60.36px] w-[13.542px]"
          data-node-id="2007:7578"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[58.313px] left-[223.42px] opacity-35 top-[67.69px] w-[13.542px]"
          data-node-id="2007:7577"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[48.782px] left-[205.88px] opacity-35 top-[77.22px] w-[13.542px]"
          data-node-id="2007:7576"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[39.575px] left-[188.33px] opacity-35 top-[86.42px] w-[13.542px]"
          data-node-id="2007:7575"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[32.358px] left-[170.79px] opacity-35 top-[93.64px] w-[13.542px]"
          data-node-id="2007:7574"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[27.606px] left-[153.25px] opacity-35 top-[98.39px] w-[13.542px]"
          data-node-id="2007:7573"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[24.933px] left-[135.71px] opacity-35 top-[101.07px] w-[13.542px]"
          data-node-id="2007:7572"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[23.635px] left-[118.17px] opacity-35 top-[102.36px] w-[13.542px]"
          data-node-id="2007:7571"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[23.089px] left-[100.63px] opacity-35 top-[102.91px] w-[13.542px]"
          data-node-id="2007:7570"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[22.888px] left-[83.08px] opacity-35 top-[103.11px] w-[13.542px]"
          data-node-id="2007:7569"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[22.824px] left-[65.54px] opacity-35 top-[103.18px] w-[13.542px]"
          data-node-id="2007:7568"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[22.806px] left-[48px] opacity-35 top-[103.19px] w-[13.542px]"
          data-node-id="2007:7567"
          data-name="Observation"
        />
        <p
          className="[word-break:break-word] absolute font-mono leading-[normal] left-0 not-italic text-[#647587] text-[9px] top-[121px] whitespace-nowrap"
          data-node-id="2007:7566"
        >
          0
        </p>
        <p
          className="[word-break:break-word] absolute font-mono leading-[normal] left-0 not-italic text-[#647587] text-[9px] top-[83px] whitespace-nowrap"
          data-node-id="2007:7565"
        >
          20k
        </p>
        <p
          className="[word-break:break-word] absolute font-mono leading-[normal] left-0 not-italic text-[#647587] text-[9px] top-[45px] whitespace-nowrap"
          data-node-id="2007:7564"
        >
          40k
        </p>
        <p
          className="[word-break:break-word] absolute font-mono leading-[normal] left-0 not-italic text-[#647587] text-[9px] top-[7px] whitespace-nowrap"
          data-node-id="2007:7563"
        >
          60k
        </p>
        <div
          className="absolute bg-[#202a34] h-[114px] left-[467px] top-[12px] w-px"
          data-node-id="2007:7562"
          data-name="Time rule"
        />
        <div
          className="absolute bg-[#202a34] h-[114px] left-[361.75px] top-[12px] w-px"
          data-node-id="2007:7561"
          data-name="Time rule"
        />
        <div
          className="absolute bg-[#202a34] h-[114px] left-[256.5px] top-[12px] w-px"
          data-node-id="2007:7560"
          data-name="Time rule"
        />
        <div
          className="absolute bg-[#202a34] h-[114px] left-[151.25px] top-[12px] w-px"
          data-node-id="2007:7559"
          data-name="Time rule"
        />
        <div
          className="absolute bg-[#202a34] h-[114px] left-[46px] top-[12px] w-px"
          data-node-id="2007:7558"
          data-name="Time rule"
        />
        <div
          className="absolute bg-[#202a34] h-px left-[46px] top-[126px] w-[421px]"
          data-node-id="2007:7557"
          data-name="Grid rule"
        />
        <div
          className="absolute bg-[#202a34] h-px left-[46px] top-[88px] w-[421px]"
          data-node-id="2007:7556"
          data-name="Grid rule"
        />
        <div
          className="absolute bg-[#202a34] h-px left-[46px] top-[50px] w-[421px]"
          data-node-id="2007:7555"
          data-name="Grid rule"
        />
        <div
          className="absolute bg-[#202a34] h-px left-[46px] top-[12px] w-[421px]"
          data-node-id="2007:7554"
          data-name="Grid rule"
        />
      </div>
    </div>
  );
}
