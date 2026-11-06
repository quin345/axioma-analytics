import { useMarket } from "../context/MarketContext";
import { formatKpis } from "../lib/format";
export default function HeadlineMetrics() {
  const k = formatKpis(useMarket().summary);
  return (
    <div
      className="[word-break:break-word] content-stretch flex gap-[8px] items-start not-italic overflow-clip relative shrink-0 w-full"
      data-node-id="2007:7184"
      data-name="Headline metrics"
    >
      <div
        className="bg-[#11161d] border border-[#29323d] border-solid content-stretch flex flex-[1_0_0] flex-col gap-[8px] items-start min-w-px overflow-clip p-[14px] relative rounded-[4px]"
        data-node-id="2007:7185"
        data-name="KPI tile"
      >
        <p
          className="font-sans font-normal leading-[1.4] relative shrink-0 text-[#94a3b3] text-[11px] whitespace-nowrap"
          data-node-id="2007:7186"
        >
          Ticks
        </p>
        <p
          className="font-mono leading-[normal] min-w-full relative shrink-0 text-[#e7edf3] text-[25px] w-[min-content]"
          data-node-id="2007:7187"
        >
          {k.ticks}
        </p>
        <p
          className="font-mono leading-[1.4] relative shrink-0 text-[#94a3b3] text-[9px] whitespace-nowrap"
          data-node-id="2007:7188"
        >
          100% of book events
        </p>
      </div>
      <div
        className="bg-[#11161d] border border-[#29323d] border-solid content-stretch flex flex-[1_0_0] flex-col gap-[8px] items-start min-w-px overflow-clip p-[14px] relative rounded-[4px]"
        data-node-id="2007:7189"
        data-name="KPI tile"
      >
        <p
          className="font-sans font-normal leading-[1.4] relative shrink-0 text-[#94a3b3] text-[11px] whitespace-nowrap"
          data-node-id="2007:7190"
        >
          Last price
        </p>
        <p
          className="font-mono leading-[normal] min-w-full relative shrink-0 text-[#e7edf3] text-[25px] w-[min-content]"
          data-node-id="2007:7191"
        >
          {k.last}
        </p>
        <p
          className="font-mono leading-[1.4] relative shrink-0 text-[#94a3b3] text-[9px] whitespace-nowrap"
          data-node-id="2007:7192"
        >
          USD per EUR
        </p>
      </div>
      <div
        className="bg-[#11161d] border border-[#29323d] border-solid content-stretch flex flex-[1_0_0] flex-col gap-[8px] items-start min-w-px overflow-clip p-[14px] relative rounded-[4px]"
        data-node-id="2007:7193"
        data-name="KPI tile"
      >
        <p
          className="font-sans font-normal leading-[1.4] relative shrink-0 text-[#94a3b3] text-[11px] whitespace-nowrap"
          data-node-id="2007:7194"
        >
          Change
        </p>
        <p
          className="font-mono leading-[normal] min-w-full relative shrink-0 text-[#8ed6be] text-[25px] w-[min-content]"
          data-node-id="2007:7195"
        >
          {k.change}
        </p>
        <p
          className="font-mono leading-[1.4] relative shrink-0 text-[#94a3b3] text-[9px] whitespace-nowrap"
          data-node-id="2007:7196"
        >
          {k.changeNote}
        </p>
      </div>
      <div
        className="bg-[#11161d] border border-[#29323d] border-solid content-stretch flex flex-[1_0_0] flex-col gap-[8px] items-start min-w-px overflow-clip p-[14px] relative rounded-[4px]"
        data-node-id="2007:7197"
        data-name="KPI tile"
      >
        <p
          className="font-sans font-normal leading-[1.4] relative shrink-0 text-[#94a3b3] text-[11px] whitespace-nowrap"
          data-node-id="2007:7198"
        >
          High / low
        </p>
        <p
          className="font-mono leading-[normal] min-w-full relative shrink-0 text-[#e7edf3] text-[23px] w-[min-content]"
          data-node-id="2007:7199"
        >
          {k.highLow}
        </p>
        <p
          className="font-mono leading-[1.4] relative shrink-0 text-[#94a3b3] text-[9px] whitespace-nowrap"
          data-node-id="2007:7200"
        >
          L 1.08194 · USD/EUR
        </p>
      </div>
      <div
        className="bg-[#11161d] border border-[#29323d] border-solid content-stretch flex flex-[1_0_0] flex-col gap-[8px] items-start min-w-px overflow-clip p-[14px] relative rounded-[4px]"
        data-node-id="2007:7201"
        data-name="KPI tile"
      >
        <p
          className="font-sans font-normal leading-[1.4] relative shrink-0 text-[#94a3b3] text-[11px] whitespace-nowrap"
          data-node-id="2007:7202"
        >
          Realised vol
        </p>
        <p
          className="font-mono leading-[normal] min-w-full relative shrink-0 text-[#e7edf3] text-[25px] w-[min-content]"
          data-node-id="2007:7203"
        >
          {k.vol}
        </p>
        <p
          className="font-mono leading-[1.4] relative shrink-0 text-[#94a3b3] text-[9px] whitespace-nowrap"
          data-node-id="2007:7204"
        >
          Annualised · 1m returns
        </p>
      </div>
      <div
        className="bg-[#11161d] border border-[#29323d] border-solid content-stretch flex flex-[1_0_0] flex-col gap-[8px] items-start min-w-px overflow-clip p-[14px] relative rounded-[4px]"
        data-node-id="2007:7205"
        data-name="KPI tile"
      >
        <p
          className="font-sans font-normal leading-[1.4] relative shrink-0 text-[#94a3b3] text-[11px] whitespace-nowrap"
          data-node-id="2007:7206"
        >
          Avg spread
        </p>
        <p
          className="font-mono leading-[normal] min-w-full relative shrink-0 text-[#e7edf3] text-[25px] w-[min-content]"
          data-node-id="2007:7207"
        >
          {k.spread}
        </p>
        <p
          className="font-mono leading-[1.4] relative shrink-0 text-[#94a3b3] text-[9px] whitespace-nowrap"
          data-node-id="2007:7208"
        >
          pips · 1 pip = 0.0001
        </p>
      </div>
      <div
        className="bg-[#11161d] border border-[#29323d] border-solid content-stretch flex flex-[1_0_0] flex-col gap-[8px] items-start min-w-px overflow-clip p-[14px] relative rounded-[4px]"
        data-node-id="2007:7209"
        data-name="KPI tile"
      >
        <p
          className="font-sans font-normal leading-[1.4] relative shrink-0 text-[#94a3b3] text-[11px] whitespace-nowrap"
          data-node-id="2007:7210"
        >
          Ticks / min
        </p>
        <p
          className="font-mono leading-[normal] min-w-full relative shrink-0 text-[#e7edf3] text-[25px] w-[min-content]"
          data-node-id="2007:7211"
        >
          768
        </p>
        <p
          className="font-mono leading-[1.4] relative shrink-0 text-[#94a3b3] text-[9px] whitespace-nowrap"
          data-node-id="2007:7212"
        >
          4h window average
        </p>
      </div>
      <div
        className="bg-[#11161d] border border-[#29323d] border-solid content-stretch flex flex-[1_0_0] flex-col gap-[8px] items-start min-w-px overflow-clip p-[14px] relative rounded-[4px]"
        data-node-id="2007:7213"
        data-name="KPI tile"
      >
        <p
          className="font-sans font-normal leading-[1.4] relative shrink-0 text-[#94a3b3] text-[11px] whitespace-nowrap"
          data-node-id="2007:7214"
        >
          Flow imbalance
        </p>
        <p
          className="font-mono leading-[normal] min-w-full relative shrink-0 text-[#8ed6be] text-[25px] w-[min-content]"
          data-node-id="2007:7215"
        >
          {k.flow}
        </p>
        <p
          className="font-mono leading-[1.4] relative shrink-0 text-[#94a3b3] text-[9px] whitespace-nowrap"
          data-node-id="2007:7216"
        >
          −1 sell / +1 buy
        </p>
      </div>
    </div>
  );
}
