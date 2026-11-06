import { useMarket } from "../context/MarketContext";
export default function GridToolbar() {
  const { symbolLabel } = useMarket();
  return (
    <div
      className="[word-break:break-word] content-stretch flex items-start justify-between leading-[0] not-italic overflow-clip relative shrink-0 w-full whitespace-pre"
      data-node-id="2007:7218"
      data-name="Grid toolbar"
    >
      <p
        className="font-sans font-semibold relative shrink-0 text-[#e7edf3] text-[11px]"
        data-node-id="2007:7219"
      >
        <span className="leading-[1.4]">{`ANALYTIC BLOCKS `}</span>
        <span className="leading-[1.4] text-[#647587]">{` / 10 visible`}</span>
      </p>
      <p
        className="font-mono relative shrink-0 text-[#94a3b3] text-[10px]"
        data-node-id="2007:7220"
      >
        <span className="leading-[1.4]">{`${symbolLabel} · Bid / buy `}</span>
        <span className="leading-[1.4] text-[#79d9ee]">━</span>
        <span className="leading-[1.4]">{`  Ask / sell `}</span>
        <span className="leading-[1.4] text-[#baa2ec]">━</span>
        <span className="leading-[1.4]">{`  · All times UTC`}</span>
      </p>
    </div>
  );
}
