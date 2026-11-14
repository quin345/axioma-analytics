import { useMarket } from "../context/MarketContext";
import { useTicks } from "../hooks/useMarketData";
import { fmtClockMs, fmtInt, fmtPrice } from "../lib/format";
import { emptyReason } from "./ChartCard";

const BUY = "#79d9ee";
const SELL = "#baa2ec";

// Raw tick data, proxied. The feed carries depth-of-market updates, not trade
// prints, so each row is a book update: its touch prices, resting size per
// side, and a side inferred by the tick rule (mid up vs the previous update =
// BUY, down = SELL, unchanged = —).
export default function RawTickDataPanel() {
  const { symbolId, live } = useMarket();
  const { data, error } = useTicks();
  const rows = data ?? [];

  return (
    <div
      className="bg-[#11161d] border border-[#29323d] border-solid content-stretch flex flex-col gap-[10px] items-start overflow-clip p-[14px] relative rounded-[6px] min-w-0 self-stretch flex-[760_1_0px]"
      data-node-id="2007:7720"
      data-name="Raw tick data"
    >
      <div className="flex items-center justify-between w-full leading-[1.4] whitespace-nowrap">
        <p className="font-sans font-semibold text-[#e7edf3] text-[12px]">Raw tick data</p>
        <p className="font-mono text-[#94a3b3] text-[9px]">
          BOOK UPDATES · SIDE BY TICK RULE · {live ? "LIVE" : "LAST SESSION"}
        </p>
      </div>
      {rows.length === 0 ? (
        <div className="flex flex-1 flex-col gap-[8px] items-center justify-center w-full text-center py-[24px]">
          <p className="font-mono text-[#94a3b3] text-[11px] tracking-[0.12em]">NO DATA</p>
          <p className="font-sans text-[#647587] text-[11px]">
            {emptyReason(symbolId, error, data !== null, "No book updates on record.")}
          </p>
        </div>
      ) : (
        <div className="flex flex-col w-full font-mono text-[10px] leading-[1.5]">
          <div className="grid grid-cols-[110px_70px_1fr_1fr_80px_80px_80px] text-[#647587] border-b border-[#202a34] pb-[3px] mb-[2px]">
            <span>TIME (UTC)</span>
            <span>SEQ</span>
            <span className="text-right">BID</span>
            <span className="text-right">ASK</span>
            <span className="text-right">SPREAD</span>
            <span className="text-right">SIZE B/A</span>
            <span className="text-right">SIDE</span>
          </div>
          {rows.map((t, i) => (
            <div
              key={`${t.seq}-${i}`}
              className="grid grid-cols-[110px_70px_1fr_1fr_80px_80px_80px] text-[#e7edf3]"
            >
              <span className="text-[#94a3b3]">{fmtClockMs(t.timestamp)}</span>
              <span className="text-[#94a3b3]">{t.seq ?? "—"}</span>
              <span className="text-right">{fmtPrice(t.bid)}</span>
              <span className="text-right">{fmtPrice(t.ask)}</span>
              <span className="text-right">{fmtPrice(t.spread)}</span>
              <span className="text-right text-[#94a3b3]">
                {fmtInt(t.bid_size)}/{fmtInt(t.ask_size)}
              </span>
              <span
                className="text-right"
                style={{ color: t.side === "buy" ? BUY : t.side === "sell" ? SELL : "#647587" }}
              >
                {t.side === "buy" ? "BUY ▲" : t.side === "sell" ? "SELL ▼" : "—"}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
