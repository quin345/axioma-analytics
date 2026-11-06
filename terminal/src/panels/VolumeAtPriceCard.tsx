import NoDataPanel from "./NoDataPanel";

export default function VolumeAtPriceCard() {
  return (
    <NoDataPanel
      title="Volume-at-price profile"
      reason="Needs traded volume by price. The feed has no trades."
      className="bg-[#11161d] border border-[#29323d] border-solid content-stretch flex flex-col gap-[8px] h-[248px] items-start overflow-clip p-[14px] relative rounded-[6px] shrink-0 w-[509px]"
      dataNodeId="2007:7377"
    />
  );
}
