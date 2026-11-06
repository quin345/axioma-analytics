import { useCardState } from "../hooks/useCardState";
import { imgChevronDown, imgEllipsis, imgMaximize2 } from "../assets";

export default function TickInterArrivalCard() {
  const card = useCardState();
  return (
    <div
      className={`bg-[#11161d] border border-[#29323d] border-solid content-stretch flex flex-col gap-[8px] h-[236px] items-start overflow-clip p-[14px] relative rounded-[6px] shrink-0 w-[509px] ${card.className}`}
      data-node-id="2007:7483"
      data-name="Analytics card"
    >
      <div
        className="content-stretch flex items-center justify-between overflow-clip relative shrink-0 w-full"
        data-node-id="2007:7484"
        data-name="Chart header"
      >
        <div
          className="content-stretch flex gap-[7px] items-center overflow-clip relative shrink-0"
          data-node-id="2007:7485"
          onClick={card.onToggleCollapse}
          data-name="Chart title"
        >
          <div
            className="relative shrink-0 size-[12px]"
            data-node-id="2007:8441"
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
            data-node-id="2007:7487"
          >
            Tick inter-arrival
          </p>
        </div>
        <div
          className="content-stretch flex gap-[12px] items-start overflow-clip relative shrink-0"
          data-node-id="2007:7488"
          data-name="Chart actions"
        >
          <div
            className="relative shrink-0 size-[12px]"
            data-node-id="2007:8444"
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
            data-node-id="2007:8447"
            onClick={card.onToggleHighlight}
            data-name="ellipsis"
          >
            <img alt="" className="absolute block inset-0 max-w-none size-full" src={imgEllipsis} />
          </div>
        </div>
      </div>
      <div
        className="[word-break:break-word] content-stretch flex items-start justify-between leading-[1.4] not-italic overflow-clip relative shrink-0 w-full whitespace-nowrap"
        data-node-id="2007:7491"
        data-name="Chart metadata"
      >
        <p
          className="font-sans font-normal relative shrink-0 text-[#94a3b3] text-[10px]"
          data-node-id="2007:7492"
        >
          Event gap distribution · milliseconds
        </p>
        <p
          className="font-mono relative shrink-0 text-[#79d9ee] text-[11px]"
          data-node-id="2007:7493"
        >
          MED 18 ms
        </p>
      </div>
      <div
        className="h-[154px] overflow-clip relative shrink-0 w-[481px]"
        data-node-id="2007:7494"
        data-name="Analytical plot"
      >
        <p
          className="[word-break:break-word] absolute font-mono leading-[normal] left-[432px] not-italic text-[#647587] text-[9px] top-[139px] whitespace-nowrap"
          data-node-id="2007:7541"
        >
          150 ms
        </p>
        <p
          className="[word-break:break-word] absolute font-mono leading-[normal] left-[326.67px] not-italic text-[#647587] text-[9px] top-[139px] whitespace-nowrap"
          data-node-id="2007:7540"
        >
          100
        </p>
        <p
          className="[word-break:break-word] absolute font-mono leading-[normal] left-[186.33px] not-italic text-[#647587] text-[9px] top-[139px] whitespace-nowrap"
          data-node-id="2007:7539"
        >
          50
        </p>
        <p
          className="[word-break:break-word] absolute font-mono leading-[normal] left-[46px] not-italic text-[#647587] text-[9px] top-[139px] whitespace-nowrap"
          data-node-id="2007:7538"
        >
          0
        </p>
        <div
          className="absolute bg-[#79d9ee] h-[0.377px] left-[454.97px] opacity-80 top-[125.62px] w-[10.033px]"
          data-node-id="2007:7537"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[0.458px] left-[440.93px] opacity-80 top-[125.54px] w-[10.033px]"
          data-node-id="2007:7536"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[0.555px] left-[426.9px] opacity-80 top-[125.45px] w-[10.033px]"
          data-node-id="2007:7535"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[0.672px] left-[412.87px] opacity-80 top-[125.33px] w-[10.033px]"
          data-node-id="2007:7534"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[0.815px] left-[398.83px] opacity-80 top-[125.19px] w-[10.033px]"
          data-node-id="2007:7533"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[0.987px] left-[384.8px] opacity-80 top-[125.01px] w-[10.033px]"
          data-node-id="2007:7532"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[1.197px] left-[370.77px] opacity-80 top-[124.8px] w-[10.033px]"
          data-node-id="2007:7531"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[1.45px] left-[356.73px] opacity-80 top-[124.55px] w-[10.033px]"
          data-node-id="2007:7530"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[1.758px] left-[342.7px] opacity-80 top-[124.24px] w-[10.033px]"
          data-node-id="2007:7529"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[2.131px] left-[328.67px] opacity-80 top-[123.87px] w-[10.033px]"
          data-node-id="2007:7528"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[2.583px] left-[314.63px] opacity-80 top-[123.42px] w-[10.033px]"
          data-node-id="2007:7527"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[3.13px] left-[300.6px] opacity-80 top-[122.87px] w-[10.033px]"
          data-node-id="2007:7526"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[3.794px] left-[286.57px] opacity-80 top-[122.21px] w-[10.033px]"
          data-node-id="2007:7525"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[4.599px] left-[272.53px] opacity-80 top-[121.4px] w-[10.033px]"
          data-node-id="2007:7524"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[5.574px] left-[258.5px] opacity-80 top-[120.43px] w-[10.033px]"
          data-node-id="2007:7523"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[6.756px] left-[244.47px] opacity-80 top-[119.24px] w-[10.033px]"
          data-node-id="2007:7522"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[8.188px] left-[230.43px] opacity-80 top-[117.81px] w-[10.033px]"
          data-node-id="2007:7521"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[9.924px] left-[216.4px] opacity-80 top-[116.08px] w-[10.033px]"
          data-node-id="2007:7520"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[12.029px] left-[202.37px] opacity-80 top-[113.97px] w-[10.033px]"
          data-node-id="2007:7519"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[14.579px] left-[188.33px] opacity-80 top-[111.42px] w-[10.033px]"
          data-node-id="2007:7518"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[17.671px] left-[174.3px] opacity-80 top-[108.33px] w-[10.033px]"
          data-node-id="2007:7517"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[21.417px] left-[160.27px] opacity-80 top-[104.58px] w-[10.033px]"
          data-node-id="2007:7516"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[25.959px] left-[146.23px] opacity-80 top-[100.04px] w-[10.033px]"
          data-node-id="2007:7515"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[31.463px] left-[132.2px] opacity-80 top-[94.54px] w-[10.033px]"
          data-node-id="2007:7514"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[38.135px] left-[118.17px] opacity-80 top-[87.87px] w-[10.033px]"
          data-node-id="2007:7513"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[46.221px] left-[104.13px] opacity-80 top-[79.78px] w-[10.033px]"
          data-node-id="2007:7512"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[56.022px] left-[90.1px] opacity-80 top-[69.98px] w-[10.033px]"
          data-node-id="2007:7511"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[67.901px] left-[76.07px] opacity-80 top-[58.1px] w-[10.033px]"
          data-node-id="2007:7510"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[82.299px] left-[62.03px] opacity-80 top-[43.7px] w-[10.033px]"
          data-node-id="2007:7509"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[99.75px] left-[48px] opacity-80 top-[26.25px] w-[10.033px]"
          data-node-id="2007:7508"
          data-name="Observation"
        />
        <p
          className="[word-break:break-word] absolute font-mono leading-[normal] left-0 not-italic text-[#647587] text-[9px] top-[121px] whitespace-nowrap"
          data-node-id="2007:7507"
        >
          0%
        </p>
        <p
          className="[word-break:break-word] absolute font-mono leading-[normal] left-0 not-italic text-[#647587] text-[9px] top-[83px] whitespace-nowrap"
          data-node-id="2007:7506"
        >
          7%
        </p>
        <p
          className="[word-break:break-word] absolute font-mono leading-[normal] left-0 not-italic text-[#647587] text-[9px] top-[45px] whitespace-nowrap"
          data-node-id="2007:7505"
        >
          13%
        </p>
        <p
          className="[word-break:break-word] absolute font-mono leading-[normal] left-0 not-italic text-[#647587] text-[9px] top-[7px] whitespace-nowrap"
          data-node-id="2007:7504"
        >
          20%
        </p>
        <div
          className="absolute bg-[#202a34] h-[114px] left-[467px] top-[12px] w-px"
          data-node-id="2007:7503"
          data-name="Time rule"
        />
        <div
          className="absolute bg-[#202a34] h-[114px] left-[361.75px] top-[12px] w-px"
          data-node-id="2007:7502"
          data-name="Time rule"
        />
        <div
          className="absolute bg-[#202a34] h-[114px] left-[256.5px] top-[12px] w-px"
          data-node-id="2007:7501"
          data-name="Time rule"
        />
        <div
          className="absolute bg-[#202a34] h-[114px] left-[151.25px] top-[12px] w-px"
          data-node-id="2007:7500"
          data-name="Time rule"
        />
        <div
          className="absolute bg-[#202a34] h-[114px] left-[46px] top-[12px] w-px"
          data-node-id="2007:7499"
          data-name="Time rule"
        />
        <div
          className="absolute bg-[#202a34] h-px left-[46px] top-[126px] w-[421px]"
          data-node-id="2007:7498"
          data-name="Grid rule"
        />
        <div
          className="absolute bg-[#202a34] h-px left-[46px] top-[88px] w-[421px]"
          data-node-id="2007:7497"
          data-name="Grid rule"
        />
        <div
          className="absolute bg-[#202a34] h-px left-[46px] top-[50px] w-[421px]"
          data-node-id="2007:7496"
          data-name="Grid rule"
        />
        <div
          className="absolute bg-[#202a34] h-px left-[46px] top-[12px] w-[421px]"
          data-node-id="2007:7495"
          data-name="Grid rule"
        />
      </div>
    </div>
  );
}
