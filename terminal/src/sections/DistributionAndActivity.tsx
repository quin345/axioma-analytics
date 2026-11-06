import TickInterArrivalCard from "../panels/TickInterArrivalCard";
import ActivityByHourCard from "../panels/ActivityByHourCard";
import RollingVolatilityCard from "../panels/RollingVolatilityCard";

export default function DistributionAndActivity() {
  return (
    <div
      className="content-stretch flex gap-[12px] items-start overflow-clip relative shrink-0 w-full"
      data-node-id="2007:7482"
      data-name="Distribution and activity"
    >
      <TickInterArrivalCard />
      <ActivityByHourCard />
      <RollingVolatilityCard />
    </div>
  );
}
