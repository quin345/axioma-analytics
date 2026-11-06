import NoDataPanel from "./NoDataPanel";

export default function OrderFlowImbalanceCard() {
  return (
    <NoDataPanel
      title="Order-flow imbalance"
      reason="Needs signed trade flow. The feed has no trades, and imbalance is always zero on fixed slot sizes."
      className="bg-[#11161d] border border-[#29323d] border-solid content-stretch flex flex-col gap-[8px] h-[248px] items-start overflow-clip p-[14px] relative rounded-[6px] shrink-0 w-[509px]"
      dataNodeId="2007:7313"
    />
  );
}
