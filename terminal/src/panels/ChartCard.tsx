import type { ReactNode, Ref } from "react";
import { useCardState } from "../hooks/useCardState";
import { imgChevronDown, imgEllipsis, imgMaximize2 } from "../assets";

// The analytics-card shell every chart panel shares, lifted from the Figma
// cards: title with collapse chevron, expand / highlight actions, a one-line
// metadata strip (left caption, right headline value) and the plot area. A
// panel supplies only its numbers and its SVG.
//
// Cards fill the width of their row: each one grows in proportion to its
// design width (the `flex-[509_1_0px]` in `size`), and the plot spans the
// card. Panels measure the plot through `plotRef` and stretch their SVG box
// to match (see fitBox in lib/chart).
type Props = {
  title: string;
  caption: ReactNode;
  headline: ReactNode;
  // Card height and its share of the row, from the design grid
  // (e.g. "h-[248px] flex-[509_1_0px]" for a 509px-wide Figma card).
  size: string;
  // Plot height (e.g. "h-[166px]"). The plot is always full card width.
  plotSize: string;
  // Measures the plot area so the panel can fit its SVG to it.
  plotRef?: Ref<HTMLDivElement>;
  dataNodeId?: string;
  // When false the plot is replaced by the NO DATA state with `emptyReason`.
  hasData: boolean;
  emptyReason: string;
  // Small print shown under the plot - used to name a proxy.
  footnote?: ReactNode;
  children?: ReactNode;
};

export default function ChartCard({
  title,
  caption,
  headline,
  size,
  plotSize,
  plotRef,
  dataNodeId,
  hasData,
  emptyReason,
  footnote,
  children,
}: Props) {
  const card = useCardState();
  return (
    <div
      className={`bg-[#11161d] border border-[#29323d] border-solid content-stretch flex flex-col gap-[8px] items-start min-w-0 overflow-clip p-[14px] relative rounded-[6px] ${size} ${card.className}`}
      data-node-id={dataNodeId}
      data-name="Analytics card"
    >
      <div
        className="content-stretch flex items-center justify-between overflow-clip relative shrink-0 w-full"
        data-name="Chart header"
      >
        <div
          className="content-stretch flex gap-[7px] items-center overflow-clip relative shrink-0"
          onClick={card.onToggleCollapse}
          data-name="Chart title"
        >
          <div className="relative shrink-0 size-[12px]" data-name="chevron-down">
            <img alt="" className="absolute block inset-0 max-w-none size-full" src={imgChevronDown} />
          </div>
          <p className="[word-break:break-word] font-sans font-semibold leading-[1.4] not-italic relative shrink-0 text-[#e7edf3] text-[12px] whitespace-nowrap">
            {title}
          </p>
        </div>
        <div
          className="content-stretch flex gap-[12px] items-start overflow-clip relative shrink-0"
          data-name="Chart actions"
        >
          <div className="relative shrink-0 size-[12px]" onClick={card.onToggleExpand} data-name="maximize-2">
            <img alt="" className="absolute block inset-0 max-w-none size-full" src={imgMaximize2} />
          </div>
          <div className="relative shrink-0 size-[12px]" onClick={card.onToggleHighlight} data-name="ellipsis">
            <img alt="" className="absolute block inset-0 max-w-none size-full" src={imgEllipsis} />
          </div>
        </div>
      </div>
      <div
        className="[word-break:break-word] content-stretch flex items-start justify-between leading-[1.4] not-italic overflow-clip relative shrink-0 w-full whitespace-nowrap"
        data-name="Chart metadata"
      >
        <p className="font-sans font-normal relative shrink-0 text-[#94a3b3] text-[10px]">{caption}</p>
        <p className="font-mono relative shrink-0 text-[#79d9ee] text-[11px]">{hasData ? headline : "NO DATA"}</p>
      </div>
      {hasData ? (
        <div
          ref={plotRef}
          className={`overflow-clip relative shrink-0 w-full ${plotSize}`}
          data-name="Analytical plot"
        >
          {children}
        </div>
      ) : (
        <div className="flex flex-1 flex-col gap-[8px] items-center justify-center w-full text-center">
          <p className="font-mono text-[#94a3b3] text-[11px] tracking-[0.12em]">NO DATA</p>
          <p className="font-sans text-[#647587] text-[11px] max-w-[420px]">{emptyReason}</p>
        </div>
      )}
      {footnote && hasData ? (
        <p className="font-mono leading-[1.4] relative shrink-0 text-[#647587] text-[9px] whitespace-nowrap">
          {footnote}
        </p>
      ) : null}
    </div>
  );
}

// The empty-state sentence for a card: loading, an error, or nothing selected.
export function emptyReason(
  symbolId: string | null,
  error: string | null,
  loaded: boolean,
  idle: string,
): string {
  if (!symbolId) return "Select an instrument to load this panel.";
  if (error) return `Feed unavailable: ${error}`;
  if (!loaded) return "Loading…";
  return idle;
}
