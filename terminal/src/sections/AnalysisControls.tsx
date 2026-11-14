import { useMarket } from "../context/MarketContext";
import { fmtDateUtc, fmtInt, fmtTimeUtc } from "../lib/format";
import ControlBar from "./ControlBar";

type Props = { assetClass: string | null; instrument: string; running: boolean };

export default function AnalysisControls({ assetClass, instrument, running }: Props) {
  const { ticks, windowLabel, status, broker, account, assetClasses, summary, live } = useMarket();
  const start = summary?.firstBucket ?? null;
  const end = summary?.lastBucket ?? null;
  const span =
    start && end
      ? `${fmtDateUtc(end)} / ${fmtTimeUtc(start)}–${fmtTimeUtc(end)} UTC / ${live ? "LIVE" : "MARKET CLOSED · LAST SESSION"}`
      : "NO DATA IN WINDOW";
  // The selected class's instrument count, from the mapping-derived list.
  const classCount = assetClasses.find((c) => c.assetClass === assetClass)?.count;
  // Where the instrument came from: broker / account, when the mapping knows.
  const provenance = [broker, account].filter(Boolean).join(" · ");
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
          {provenance ? (
            <p
              className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#94a3b3] text-[11px] whitespace-nowrap"
              data-name="Instrument provenance"
            >
              {provenance}
            </p>
          ) : null}
          <div
            className="bg-[#163b47] content-stretch flex items-start overflow-clip px-[7px] py-[3px] relative rounded-[3px] shrink-0"
            data-node-id="2007:7141"
            data-name="Data mode"
          >
            <p
              className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#79d9ee] text-[9px] whitespace-nowrap"
              data-node-id="2007:7142"
            >
              {assetClass ?? "ALL CLASSES"}
              {classCount === undefined ? "" : ` · ${fmtInt(classCount)}`}
            </p>
          </div>
        </div>
        <p
          className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#94a3b3] text-[10px] whitespace-nowrap"
          data-node-id="2007:7143"
        >
          {span}
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
