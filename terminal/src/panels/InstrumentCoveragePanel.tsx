import NoDataPanel from "./NoDataPanel";

export default function InstrumentCoveragePanel() {
  return (
    <NoDataPanel
      title="Instrument coverage"
      reason="Only one symbol is collected, and asset-class counts are not in the data."
      className="bg-[#11161d] border border-[#29323d] border-solid content-stretch flex flex-col gap-[12px] items-start overflow-clip p-[14px] relative rounded-[6px] self-stretch shrink-0 w-[360px]"
      dataNodeId="2007:7808"
      dataName="Instrument coverage"
    />
  );
}
