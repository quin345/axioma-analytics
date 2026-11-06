import { useCardState } from "../hooks/useCardState";
import { imgChevronDown, imgEllipsis, imgMaximize2 } from "../assets";

export default function PriceAutocorrelationCard() {
  const card = useCardState();
  return (
    <div
      className={`bg-[#11161d] border border-[#29323d] border-solid content-stretch flex flex-col gap-[8px] h-[226px] items-start overflow-clip p-[14px] relative rounded-[6px] shrink-0 w-[770px] ${card.className}`}
      data-node-id="2007:7627"
      data-name="Analytics card"
    >
      <div
        className="content-stretch flex items-center justify-between overflow-clip relative shrink-0 w-full"
        data-node-id="2007:7628"
        data-name="Chart header"
      >
        <div
          className="content-stretch flex gap-[7px] items-center overflow-clip relative shrink-0"
          data-node-id="2007:7629"
          onClick={card.onToggleCollapse}
          data-name="Chart title"
        >
          <div
            className="relative shrink-0 size-[12px]"
            data-node-id="2007:8468"
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
            data-node-id="2007:7631"
          >
            Price-behaviour autocorrelation
          </p>
        </div>
        <div
          className="content-stretch flex gap-[12px] items-start overflow-clip relative shrink-0"
          data-node-id="2007:7632"
          data-name="Chart actions"
        >
          <div
            className="relative shrink-0 size-[12px]"
            data-node-id="2007:8471"
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
            data-node-id="2007:8474"
            onClick={card.onToggleHighlight}
            data-name="ellipsis"
          >
            <img alt="" className="absolute block inset-0 max-w-none size-full" src={imgEllipsis} />
          </div>
        </div>
      </div>
      <div
        className="[word-break:break-word] content-stretch flex items-start justify-between leading-[1.4] not-italic overflow-clip relative shrink-0 w-full"
        data-node-id="2007:7635"
        data-name="Chart metadata"
      >
        <p
          className="font-sans font-normal relative shrink-0 text-[#94a3b3] text-[10px] whitespace-nowrap"
          data-node-id="2007:7636"
        >
          Tick-return ACF · lag in events · 95% confidence ±0.005
        </p>
        <p
          className="font-mono relative shrink-0 text-[#79d9ee] text-[11px] whitespace-pre"
          data-node-id="2007:7637"
        >{`LAG 1  −0.21`}</p>
      </div>
      <div
        className="h-[144px] overflow-clip relative shrink-0 w-[742px]"
        data-node-id="2007:7638"
        data-name="Analytical plot"
      >
        <p
          className="[word-break:break-word] absolute font-mono leading-[normal] left-[693px] not-italic text-[#647587] text-[9px] top-[129px] whitespace-nowrap"
          data-node-id="2007:7686"
        >
          30 lags
        </p>
        <p
          className="[word-break:break-word] absolute font-mono leading-[normal] left-[500.67px] not-italic text-[#647587] text-[9px] top-[129px] whitespace-nowrap"
          data-node-id="2007:7685"
        >
          20
        </p>
        <p
          className="[word-break:break-word] absolute font-mono leading-[normal] left-[273.33px] not-italic text-[#647587] text-[9px] top-[129px] whitespace-nowrap"
          data-node-id="2007:7684"
        >
          10
        </p>
        <p
          className="[word-break:break-word] absolute font-mono leading-[normal] left-[46px] not-italic text-[#647587] text-[9px] top-[129px] whitespace-nowrap"
          data-node-id="2007:7683"
        >
          1
        </p>
        <div
          className="absolute bg-[#baa2ec] h-[1.284px] left-[707.27px] opacity-80 top-[64px] w-[18.733px]"
          data-node-id="2007:7682"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[0.547px] left-[684.53px] opacity-80 top-[63.45px] w-[18.733px]"
          data-node-id="2007:7681"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[0.701px] left-[661.8px] opacity-80 top-[63.3px] w-[18.733px]"
          data-node-id="2007:7680"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#baa2ec] h-[1.839px] left-[639.07px] opacity-80 top-[64px] w-[18.733px]"
          data-node-id="2007:7679"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[2.156px] left-[616.33px] opacity-80 top-[61.84px] w-[18.733px]"
          data-node-id="2007:7678"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#baa2ec] h-[1.256px] left-[593.6px] opacity-80 top-[64px] w-[18.733px]"
          data-node-id="2007:7677"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#baa2ec] h-[0.623px] left-[570.87px] opacity-80 top-[64px] w-[18.733px]"
          data-node-id="2007:7676"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[2.594px] left-[548.13px] opacity-80 top-[61.41px] w-[18.733px]"
          data-node-id="2007:7675"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#baa2ec] h-[3.498px] left-[525.4px] opacity-80 top-[64px] w-[18.733px]"
          data-node-id="2007:7674"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[2.525px] left-[502.67px] opacity-80 top-[61.47px] w-[18.733px]"
          data-node-id="2007:7673"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[0.207px] left-[479.93px] opacity-80 top-[63.79px] w-[18.733px]"
          data-node-id="2007:7672"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#baa2ec] h-[3.494px] left-[457.2px] opacity-80 top-[64px] w-[18.733px]"
          data-node-id="2007:7671"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[5.501px] left-[434.47px] opacity-80 top-[58.5px] w-[18.733px]"
          data-node-id="2007:7670"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#baa2ec] h-[4.702px] left-[411.73px] opacity-80 top-[64px] w-[18.733px]"
          data-node-id="2007:7669"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[0.88px] left-[389px] opacity-80 top-[63.12px] w-[18.733px]"
          data-node-id="2007:7668"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[4.422px] left-[366.27px] opacity-80 top-[59.58px] w-[18.733px]"
          data-node-id="2007:7667"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#baa2ec] h-[8.387px] left-[343.53px] opacity-80 top-[64px] w-[18.733px]"
          data-node-id="2007:7666"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[8.3px] left-[320.8px] opacity-80 top-[55.7px] w-[18.733px]"
          data-node-id="2007:7665"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#baa2ec] h-[3.205px] left-[298.07px] opacity-80 top-[64px] w-[18.733px]"
          data-node-id="2007:7664"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#baa2ec] h-[5.083px] left-[275.33px] opacity-80 top-[64px] w-[18.733px]"
          data-node-id="2007:7663"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[12.379px] left-[252.6px] opacity-80 top-[51.62px] w-[18.733px]"
          data-node-id="2007:7662"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#baa2ec] h-[14.055px] left-[229.87px] opacity-80 top-[64px] w-[18.733px]"
          data-node-id="2007:7661"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[7.704px] left-[207.13px] opacity-80 top-[56.3px] w-[18.733px]"
          data-node-id="2007:7660"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[4.856px] left-[184.4px] opacity-80 top-[59.14px] w-[18.733px]"
          data-node-id="2007:7659"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#baa2ec] h-[17.624px] left-[161.67px] opacity-80 top-[64px] w-[18.733px]"
          data-node-id="2007:7658"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[22.982px] left-[138.93px] opacity-80 top-[41.02px] w-[18.733px]"
          data-node-id="2007:7657"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#baa2ec] h-[15.867px] left-[116.2px] opacity-80 top-[64px] w-[18.733px]"
          data-node-id="2007:7656"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#baa2ec] h-[2.55px] left-[93.47px] opacity-80 top-[64px] w-[18.733px]"
          data-node-id="2007:7655"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#79d9ee] h-[24.018px] left-[70.73px] opacity-80 top-[39.98px] w-[18.733px]"
          data-node-id="2007:7654"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#baa2ec] h-[36.4px] left-[48px] opacity-80 top-[64px] w-[18.733px]"
          data-node-id="2007:7653"
          data-name="Observation"
        />
        <div
          className="absolute bg-[#647587] h-px left-[46px] top-[64px] w-[682px]"
          data-node-id="2007:7652"
          data-name="Zero return baseline"
        />
        <p
          className="[word-break:break-word] absolute font-mono leading-[normal] left-0 not-italic text-[#647587] text-[9px] top-[111px] whitespace-nowrap"
          data-node-id="2007:7651"
        >
          −0.3
        </p>
        <p
          className="[word-break:break-word] absolute font-mono leading-[normal] left-0 not-italic text-[#647587] text-[9px] top-[76.33px] whitespace-nowrap"
          data-node-id="2007:7650"
        >
          −0.1
        </p>
        <p
          className="[word-break:break-word] absolute font-mono leading-[normal] left-0 not-italic text-[#647587] text-[9px] top-[41.67px] whitespace-nowrap"
          data-node-id="2007:7649"
        >
          +0.1
        </p>
        <p
          className="[word-break:break-word] absolute font-mono leading-[normal] left-0 not-italic text-[#647587] text-[9px] top-[7px] whitespace-nowrap"
          data-node-id="2007:7648"
        >
          +0.3
        </p>
        <div
          className="absolute bg-[#202a34] h-[104px] left-[728px] top-[12px] w-px"
          data-node-id="2007:7647"
          data-name="Time rule"
        />
        <div
          className="absolute bg-[#202a34] h-[104px] left-[557.5px] top-[12px] w-px"
          data-node-id="2007:7646"
          data-name="Time rule"
        />
        <div
          className="absolute bg-[#202a34] h-[104px] left-[387px] top-[12px] w-px"
          data-node-id="2007:7645"
          data-name="Time rule"
        />
        <div
          className="absolute bg-[#202a34] h-[104px] left-[216.5px] top-[12px] w-px"
          data-node-id="2007:7644"
          data-name="Time rule"
        />
        <div
          className="absolute bg-[#202a34] h-[104px] left-[46px] top-[12px] w-px"
          data-node-id="2007:7643"
          data-name="Time rule"
        />
        <div
          className="absolute bg-[#202a34] h-px left-[46px] top-[116px] w-[682px]"
          data-node-id="2007:7642"
          data-name="Grid rule"
        />
        <div
          className="absolute bg-[#202a34] h-px left-[46px] top-[81.33px] w-[682px]"
          data-node-id="2007:7641"
          data-name="Grid rule"
        />
        <div
          className="absolute bg-[#202a34] h-px left-[46px] top-[46.67px] w-[682px]"
          data-node-id="2007:7640"
          data-name="Grid rule"
        />
        <div
          className="absolute bg-[#202a34] h-px left-[46px] top-[12px] w-[682px]"
          data-node-id="2007:7639"
          data-name="Grid rule"
        />
      </div>
    </div>
  );
}
