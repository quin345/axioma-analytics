import PriceAutocorrelationCard from "../panels/PriceAutocorrelationCard";
import LargeTradeImpactCard from "../panels/LargeTradeImpactCard";

export default function BehaviourAndImpact() {
  return (
    <div
      className="content-stretch flex gap-[12px] items-start overflow-clip relative shrink-0 w-full"
      data-node-id="2007:7626"
      data-name="Behaviour and impact"
    >
      <PriceAutocorrelationCard />
      <LargeTradeImpactCard />
    </div>
  );
}
