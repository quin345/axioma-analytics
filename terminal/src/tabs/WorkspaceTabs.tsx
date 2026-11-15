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
      {/* Opens the published research report (served by Flask at /research/). */}
      <a
        className="relative shrink-0 text-[#94a3b3] no-underline"
        data-node-id="2007:7126"
        href="/research/"
        title="Research notes: strategy and factor studies"
      >
        Research
      </a>
    </div>
  );
}
