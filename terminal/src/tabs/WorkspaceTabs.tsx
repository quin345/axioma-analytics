import { useMarket } from "../context/MarketContext";
export default function WorkspaceTabs() {
  const { actions } = useMarket();
  return (
    <div
      className="[word-break:break-word] content-stretch flex font-sans font-normal gap-[24px] items-start leading-[1.4] not-italic overflow-clip relative shrink-0 text-[12px] whitespace-nowrap"
      data-node-id="2007:7123"
      data-name="Workspace tabs"
    >
      <p
        className="relative shrink-0 text-[#79d9ee]"
        data-node-id="2007:7124"
        onClick={() => actions.setTab("analytics")}
      >
        Analytics
      </p>
      <p
        className="relative shrink-0 text-[#94a3b3]"
        data-node-id="2007:7125"
        onClick={() => actions.setTab("instruments")}
      >
        Instruments
      </p>
      <p
        className="relative shrink-0 text-[#94a3b3]"
        data-node-id="2007:7126"
        onClick={() => actions.setTab("research")}
      >
        Research
      </p>
    </div>
  );
}
