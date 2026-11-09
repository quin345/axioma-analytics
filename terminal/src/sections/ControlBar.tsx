import { useMarket } from "../context/MarketContext";
import { imgCalendarDays, imgChevronDown, imgPlay } from "../assets";

type Props = { assetClass: string | null; instrument: string; running: boolean };

export default function ControlBar({ assetClass, instrument, running }: Props) {
  const { actions } = useMarket();
  return (
    <div
      className="content-stretch flex gap-[16px] items-end overflow-clip relative shrink-0 w-full"
      data-node-id="2007:7144"
      data-name="Control bar"
    >
      <div
        className="content-stretch flex flex-col gap-[7px] items-start overflow-clip relative shrink-0 w-[130px]"
        data-node-id="2007:7145"
        data-name="Control field"
      >
        <p
          className="[word-break:break-word] font-sans font-normal leading-[1.4] not-italic relative shrink-0 text-[#94a3b3] text-[10px] whitespace-nowrap"
          data-node-id="2007:7146"
        >
          ASSET CLASS
        </p>
        <div
          className="bg-[#11161d] border border-[#29323d] border-solid content-stretch flex h-[36px] items-center justify-between overflow-clip px-[12px] relative rounded-[4px] shrink-0 w-full"
          onClick={() => actions.cycleAssetClass()}
          data-node-id="2007:7147"
          data-name="Selection"
        >
          <p
            className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#e7edf3] text-[12px] whitespace-nowrap"
            data-node-id="2007:7148"
          >
            {assetClass ?? "All classes"}
          </p>
          <div
            className="relative shrink-0 size-[12px]"
            data-node-id="2007:8384"
            data-name="chevron-down"
          >
            <img
              alt=""
              className="absolute block inset-0 max-w-none size-full"
              src={imgChevronDown}
            />
          </div>
        </div>
      </div>
      <div
        className="content-stretch flex flex-col gap-[7px] items-start overflow-clip relative shrink-0 w-[185px]"
        data-node-id="2007:7150"
        data-name="Control field"
      >
        <p
          className="[word-break:break-word] font-sans font-normal leading-[1.4] not-italic relative shrink-0 text-[#94a3b3] text-[10px] whitespace-nowrap"
          data-node-id="2007:7151"
        >
          INSTRUMENT
        </p>
        <div
          className="bg-[#11161d] border border-[#29323d] border-solid content-stretch flex h-[36px] items-center justify-between overflow-clip px-[12px] relative rounded-[4px] shrink-0 w-full"
          onClick={() => actions.cycleSymbol()}
          data-node-id="2007:7152"
          data-name="Selection"
        >
          <p
            className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#e7edf3] text-[12px] whitespace-nowrap"
            data-node-id="2007:7153"
          >
            {instrument}
          </p>
          <div
            className="relative shrink-0 size-[12px]"
            data-node-id="2007:8387"
            data-name="chevron-down"
          >
            <img
              alt=""
              className="absolute block inset-0 max-w-none size-full"
              src={imgChevronDown}
            />
          </div>
        </div>
      </div>
      <div
        className="content-stretch flex flex-col gap-[7px] items-start overflow-clip relative shrink-0 w-[224px]"
        data-node-id="2007:7155"
        data-name="Rolling timeframe"
      >
        <p
          className="[word-break:break-word] font-sans font-normal leading-[1.4] not-italic relative shrink-0 text-[#94a3b3] text-[10px] whitespace-nowrap"
          data-node-id="2007:7156"
        >
          ROLLING WINDOW
        </p>
        <div
          className="bg-[#11161d] border border-[#29323d] border-solid content-stretch flex gap-[4px] h-[36px] items-start overflow-clip p-[3px] relative rounded-[4px] shrink-0 w-full"
          data-node-id="2007:7157"
          data-name="Window options"
        >
          <div
            className="bg-[#11161d] content-stretch flex flex-[1_0_0] h-full items-center justify-center min-w-px overflow-clip relative rounded-[3px]"
            onClick={() => actions.setWindow("1h")}
            data-node-id="2007:7158"
            data-name="Window option"
          >
            <p
              className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#94a3b3] text-[11px] whitespace-nowrap"
              data-node-id="2007:7159"
            >
              1h
            </p>
          </div>
          <div
            className="bg-[#11161d] content-stretch flex flex-[1_0_0] h-full items-center justify-center min-w-px overflow-clip relative rounded-[3px]"
            onClick={() => actions.setWindow("2h")}
            data-node-id="2007:7160"
            data-name="Window option"
          >
            <p
              className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#94a3b3] text-[11px] whitespace-nowrap"
              data-node-id="2007:7161"
            >
              2h
            </p>
          </div>
          <div
            className="bg-[#11161d] content-stretch flex flex-[1_0_0] h-full items-center justify-center min-w-px overflow-clip relative rounded-[3px]"
            onClick={() => actions.setWindow("3h")}
            data-node-id="2007:7162"
            data-name="Window option"
          >
            <p
              className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#94a3b3] text-[11px] whitespace-nowrap"
              data-node-id="2007:7163"
            >
              3h
            </p>
          </div>
          <div
            className="bg-[#163b47] content-stretch flex flex-[1_0_0] h-full items-center justify-center min-w-px overflow-clip relative rounded-[3px]"
            onClick={() => actions.setWindow("4h")}
            data-node-id="2007:7164"
            data-name="Window option"
          >
            <p
              className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#79d9ee] text-[11px] whitespace-nowrap"
              data-node-id="2007:7165"
            >
              4h
            </p>
          </div>
        </div>
      </div>
      <div
        className="content-stretch flex flex-col gap-[7px] items-start overflow-clip relative shrink-0 w-[245px]"
        data-node-id="2007:7166"
        data-name="Control field"
      >
        <p
          className="[word-break:break-word] font-sans font-normal leading-[1.4] not-italic relative shrink-0 text-[#94a3b3] text-[10px] whitespace-nowrap"
          data-node-id="2007:7167"
        >
          POINT IN TIME · UTC
        </p>
        <div
          className="bg-[#11161d] border border-[#29323d] border-solid content-stretch flex h-[36px] items-center justify-between overflow-clip px-[12px] relative rounded-[4px] shrink-0 w-full"
          data-node-id="2007:7168"
          data-name="Selection"
        >
          <p
            className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#e7edf3] text-[12px] whitespace-pre"
            data-node-id="2007:7169"
          >{`09 Oct 2026  16:00`}</p>
          <div
            className="relative shrink-0 size-[12px]"
            data-node-id="2007:8390"
            data-name="calendar-days"
          >
            <img
              alt=""
              className="absolute block inset-0 max-w-none size-full"
              src={imgCalendarDays}
            />
          </div>
        </div>
      </div>
      <div
        className="content-stretch flex flex-[1_0_0] flex-col gap-[8px] items-start min-w-px overflow-clip relative"
        data-node-id="2007:7171"
        data-name="Time scrubber"
      >
        <div
          className="[word-break:break-word] content-stretch flex font-mono items-start justify-between leading-[1.4] not-italic overflow-clip relative shrink-0 text-[9px] w-full whitespace-nowrap"
          data-node-id="2007:7172"
          data-name="Scrubber labels"
        >
          <p className="relative shrink-0 text-[#94a3b3]" data-node-id="2007:7173">
            12:00
          </p>
          <p className="relative shrink-0 text-[#79d9ee]" data-node-id="2007:7174">
            16:00 · NOW
          </p>
        </div>
        <div
          className="content-stretch flex h-[36px] items-center overflow-clip relative shrink-0 w-full"
          data-node-id="2007:7175"
          data-name="Timeline"
        >
          <div
            className="bg-[#163b47] flex-[1_0_0] h-[3px] min-w-px relative"
            data-node-id="2007:7176"
            data-name="Selected interval"
          />
          <div
            className="bg-[#79d9ee] h-[17px] relative shrink-0 w-[7px]"
            data-node-id="2007:7177"
            data-name="Playhead"
          />
        </div>
      </div>
      <div
        className="bg-[#79d9ee] border border-[#79d9ee] border-solid content-stretch flex gap-[10px] h-[38px] items-center overflow-clip px-[16px] relative rounded-[4px] shrink-0"
        onClick={() => actions.run()}
        data-node-id="2007:7178"
        data-name="Action"
      >
        <p
          className="[word-break:break-word] font-sans font-semibold leading-[1.4] not-italic relative shrink-0 text-[#0b0e12] text-[12px] whitespace-nowrap"
          data-node-id="2007:7179"
        >
          {running ? "Running" : "Run"}
        </p>
        <div className="relative shrink-0 size-[14px]" data-node-id="2007:8393" data-name="play">
          <img alt="" className="absolute block inset-0 max-w-none size-full" src={imgPlay} />
        </div>
      </div>
    </div>
  );
}
