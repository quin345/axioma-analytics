import { useMarket } from "../context/MarketContext";
import { fmtInt } from "../lib/format";
import ControlBar from "./ControlBar";

type Props = { assetClass: string; instrument: string; running: boolean };

export default function AnalysisControls({ assetClass, instrument, running }: Props) {
  const { ticks, windowLabel, status } = useMarket();
  return (
    <div
      className="content-stretch flex flex-col gap-[18px] items-start overflow-clip relative shrink-0 w-full"
      data-node-id="2007:7136"
      data-name="Analysis controls"
    >
      <div
        className="content-stretch flex items-center justify-between overflow-clip relative shrink-0 w-full"
        data-node-id="2007:7137"
        data-name="Workspace heading"
      >
        <div
          className="content-stretch flex gap-[14px] items-center overflow-clip relative shrink-0"
          data-node-id="2007:7138"
          data-name="Instrument title"
        >
          <p
            className="[word-break:break-word] font-sans font-medium leading-[normal] not-italic relative shrink-0 text-[#e7edf3] text-[24px] whitespace-nowrap"
            data-node-id="2007:7139"
          >
            Market overview
          </p>
          <p
            className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#94a3b3] text-[11px] whitespace-nowrap"
            data-node-id="2007:7140"
          >
            {instrument}
          </p>
          <div
            className="bg-[#163b47] content-stretch flex items-start overflow-clip px-[7px] py-[3px] relative rounded-[3px] shrink-0"
            data-node-id="2007:7141"
            data-name="Data mode"
          >
            <p
              className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#79d9ee] text-[9px] whitespace-nowrap"
              data-node-id="2007:7142"
            >
              LIVE WINDOW
            </p>
          </div>
        </div>
        <p
          className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#94a3b3] text-[10px] whitespace-nowrap"
          data-node-id="2007:7143"
        >
          09 OCT 2026 / 12:00–16:00 UTC / 100% EVENT COVERAGE
        </p>
      </div>
      <ControlBar assetClass={assetClass} instrument={instrument} running={running} />
      <div
        className="[word-break:break-word] content-stretch flex font-mono items-start justify-between leading-[1.4] not-italic overflow-clip relative shrink-0 text-[#94a3b3] text-[10px] w-full whitespace-nowrap"
        data-node-id="2007:7181"
        data-name="Refresh information"
      >
        <p className="relative shrink-0" data-node-id="2007:7182">
          {fmtInt(ticks)} depth updates · {windowLabel} look-back
        </p>
        <p className="relative shrink-0" data-node-id="2007:7183">
          AUTO REFRESH 5s · {status}
        </p>
      </div>
    </div>
  );
}
