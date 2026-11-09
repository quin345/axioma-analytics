import { useMarket } from "../context/MarketContext";
import { fmtInt } from "../lib/format";

// The asset classes and how many collected instruments fall in each. This is
// real now: the counts come from the broker/symbol mapping snapshotted from the
// Fabric `ctrader_metadata` source, joined to the instruments the feed has.
export default function InstrumentCoveragePanel() {
  const { assetClasses } = useMarket();
  const total = assetClasses.reduce((sum, c) => sum + c.count, 0);
  const covered = assetClasses.filter((c) => c.count > 0);

  return (
    <div
      className="bg-[#11161d] border border-[#29323d] border-solid content-stretch flex flex-col gap-[12px] items-start overflow-clip p-[14px] relative rounded-[6px] self-stretch shrink-0 w-[360px]"
      data-node-id="2007:7808"
      data-name="Instrument coverage"
    >
      <p className="[word-break:break-word] font-sans font-medium leading-[normal] not-italic relative shrink-0 text-[#e7edf3] text-[13px] whitespace-nowrap">
        Instrument coverage
      </p>
      {covered.length === 0 ? (
        <p className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#94a3b3] text-[11px]">
          No mapped instruments yet.
        </p>
      ) : (
        <>
          <p className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#94a3b3] text-[10px]">
            {fmtInt(total)} collected · {fmtInt(covered.length)} classes
          </p>
          <div className="content-stretch flex flex-col gap-[6px] items-start relative shrink-0 w-full">
            {covered.map((c) => (
              <div
                key={c.assetClass}
                className="content-stretch flex items-center justify-between relative shrink-0 w-full"
              >
                <p className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#e7edf3] text-[11px] whitespace-nowrap">
                  {c.assetClass}
                </p>
                <p className="[word-break:break-word] font-mono leading-[1.4] not-italic relative shrink-0 text-[#79d9ee] text-[11px] whitespace-nowrap">
                  {fmtInt(c.count)}
                </p>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
