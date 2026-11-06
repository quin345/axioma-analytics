import BookDepthCard from "../panels/BookDepthCard";
import VolumeAtPriceCard from "../panels/VolumeAtPriceCard";
import BidAskSpreadCard from "../panels/BidAskSpreadCard";

export default function LiquidityAnalysis() {
  return (
    <div
      className="content-stretch flex gap-[12px] items-start overflow-clip relative shrink-0 w-full"
      data-node-id="2007:7345"
      data-name="Liquidity analysis"
    >
      <BookDepthCard />
      <VolumeAtPriceCard />
      <BidAskSpreadCard />
    </div>
  );
}
