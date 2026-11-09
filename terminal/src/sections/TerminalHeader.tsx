import { imgAxiomaMark, imgLiveIndicator, imgSettings2 } from "../assets";
import WorkspaceTabs from "../tabs/WorkspaceTabs";

export default function TerminalHeader() {
  return (
    <div
      className="border-[#29323d] border-b border-solid content-stretch flex h-[68px] items-center justify-between overflow-clip px-[24px] relative shrink-0 w-full"
      data-node-id="2007:7117"
      data-name="Terminal header"
    >
      <div
        className="content-stretch flex gap-[38px] items-center overflow-clip relative shrink-0"
        data-node-id="2007:7118"
        data-name="Identity and navigation"
      >
        <div
          className="content-stretch flex gap-[12px] items-center overflow-clip relative shrink-0"
          data-node-id="2007:7119"
          data-name="Brand"
        >
          <div
            className="relative shrink-0 size-[28px]"
            data-node-id="2007:7120"
            data-name="Axioma mark"
          >
            <img
              alt="AXIOMA"
              className="absolute block inset-0 size-full"
              src={imgAxiomaMark}
            />
          </div>
          <p
            className="[word-break:break-word] font-sans font-semibold leading-[normal] not-italic relative shrink-0 text-[#e7edf3] text-[20px] whitespace-nowrap"
            data-node-id="2007:7121"
          >
            AXIOMA
          </p>
        </div>
        <p
          className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#94a3b3] text-[10px] whitespace-nowrap"
          data-node-id="2007:7122"
        >
          MARKET MICROSTRUCTURE
        </p>
        <WorkspaceTabs />
      </div>
      <div
        className="content-stretch flex gap-[20px] items-center overflow-clip relative shrink-0"
        data-node-id="2007:7127"
        data-name="Feed status"
      >
        <div
          className="content-stretch flex gap-[7px] items-center overflow-clip relative shrink-0"
          data-node-id="2007:7128"
          data-name="Connection"
        >
          <div
            className="relative shrink-0 size-[6px]"
            data-node-id="2007:7129"
            data-name="Live indicator"
          >
            <img
              alt=""
              className="absolute block inset-0 max-w-none size-full"
              src={imgLiveIndicator}
            />
          </div>
          <p
            className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#8ed6be] text-[10px] whitespace-nowrap"
            data-node-id="2007:7130"
          >
            FEED CONNECTED
          </p>
        </div>
        <p
          className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#94a3b3] text-[10px] whitespace-nowrap"
          data-node-id="2007:7131"
        >
          UPDATED 16:00:00 UTC
        </p>
        <div
          className="relative shrink-0 size-[14px]"
          data-node-id="2007:8381"
          data-name="settings-2"
        >
          <img alt="" className="absolute block inset-0 max-w-none size-full" src={imgSettings2} />
        </div>
        <div
          className="bg-[#171e27] content-stretch flex items-start overflow-clip p-[8px] relative rounded-[4px] shrink-0"
          data-node-id="2007:7133"
          data-name="User"
        >
          <p
            className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#e7edf3] text-[11px] whitespace-nowrap"
            data-node-id="2007:7134"
          >
            JD
          </p>
        </div>
      </div>
    </div>
  );
}
