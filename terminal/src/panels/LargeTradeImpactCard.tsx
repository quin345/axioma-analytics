import NoDataPanel from "./NoDataPanel";

export default function LargeTradeImpactCard() {
  return (
    <NoDataPanel
      title="Large-trade impact"
      reason="Needs trades above a size threshold. The feed has no trades."
      className="bg-[#11161d] border border-[#29323d] border-solid content-stretch flex flex-col gap-[8px] h-[226px] items-start overflow-clip p-[14px] relative rounded-[6px] shrink-0 w-[770px]"
      dataNodeId="2007:7687"
    />
  );
}
