import PriceVolumeCard from "../panels/PriceVolumeCard";
import OrderFlowImbalanceCard from "../panels/OrderFlowImbalanceCard";

export default function PriceAndFlow() {
  return (
    <div
      className="content-stretch flex gap-[12px] items-start overflow-clip relative shrink-0 w-full"
      data-node-id="2007:7221"
      data-name="Price and flow"
    >
      <PriceVolumeCard />
      <OrderFlowImbalanceCard />
    </div>
  );
}
