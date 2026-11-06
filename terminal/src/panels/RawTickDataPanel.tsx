import NoDataPanel from "./NoDataPanel";

export default function RawTickDataPanel() {
  return (
    <NoDataPanel
      title="Raw tick data"
      reason="Trade rows need trade data. The feed has no trades, so there are no buy or sell rows to show."
      className="bg-[#11161d] border border-[#29323d] border-solid content-stretch flex flex-col gap-[13px] items-start overflow-clip p-[14px] relative rounded-[6px] self-stretch shrink-0 w-[760px]"
      dataNodeId="2007:7720"
      dataName="Raw tick data"
    />
  );
}
