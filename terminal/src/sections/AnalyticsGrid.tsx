import GridToolbar from "./GridToolbar";
import PriceAndFlow from "./PriceAndFlow";
import LiquidityAnalysis from "./LiquidityAnalysis";
import DistributionAndActivity from "./DistributionAndActivity";
import BehaviourAndImpact from "./BehaviourAndImpact";

export default function AnalyticsGrid() {
  return (
    <div
      className="content-stretch flex flex-col gap-[12px] items-start overflow-clip relative shrink-0 w-full"
      data-node-id="2007:7217"
      data-name="Analytics grid"
    >
      <GridToolbar />
      <PriceAndFlow />
      <LiquidityAnalysis />
      <DistributionAndActivity />
      <BehaviourAndImpact />
    </div>
  );
}
