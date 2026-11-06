import { useCardState } from "../hooks/useCardState";

// Placeholder for panels the feed cannot fill. Keeps the card's size and
// click behaviour (title toggles collapse) so the grid layout does not change.
type Props = {
  title: string;
  reason: string;
  className: string;
  dataNodeId: string;
  dataName?: string;
};

export default function NoDataPanel({
  title,
  reason,
  className,
  dataNodeId,
  dataName = "Analytics card",
}: Props) {
  const card = useCardState();
  return (
    <div
      className={`${className} ${card.className}`}
      data-node-id={dataNodeId}
      data-name={dataName}
    >
      <div
        className="content-stretch flex items-center justify-between relative shrink-0 w-full"
        data-name="Chart title"
        onClick={card.onToggleCollapse}
      >
        <p className="font-sans font-semibold text-[#e7edf3] text-[12px]">{title}</p>
      </div>
      <div className="flex flex-1 flex-col gap-[8px] items-center justify-center w-full text-center">
        <p className="font-mono text-[#94a3b3] text-[11px] tracking-[0.12em]">NO DATA</p>
        <p className="font-sans text-[#647587] text-[11px] max-w-[420px]">{reason}</p>
      </div>
    </div>
  );
}
