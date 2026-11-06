import RawTickDataPanel from "../panels/RawTickDataPanel";
import InstrumentCoveragePanel from "../panels/InstrumentCoveragePanel";
import ActivityCalendarPanel from "../panels/ActivityCalendarPanel";

export default function SupportingPanels() {
  return (
    <div
      className="content-stretch flex gap-[12px] items-start overflow-clip relative shrink-0 w-full"
      data-node-id="2007:7719"
      data-name="Supporting panels"
    >
      <RawTickDataPanel />
      <InstrumentCoveragePanel />
      <ActivityCalendarPanel />
    </div>
  );
}
