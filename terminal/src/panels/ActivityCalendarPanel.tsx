import { useEffect, useMemo, useState } from "react";
import { imgChevronLeft1, imgChevronRight1 } from "../assets";
import { useMarket } from "../context/MarketContext";
import { CALENDAR_DAYS, useActivityByDay } from "../hooks/useMarketData";
import { heat, isoDay, monthWeeks, MONTHS, shiftMonth, WEEKDAYS } from "../lib/calendar";
import { fmtInt } from "../lib/format";

// Activity by day: a month calendar of book events per UTC day for the
// selected instrument (last six weeks on record). Click a day to read it.
export default function ActivityCalendarPanel() {
  const { symbolLabel, symbolId } = useMarket();
  const { data, error } = useActivityByDay();
  const byDay = useMemo(() => new Map((data ?? []).map((d) => [d.day, d.events])), [data]);
  const max = Math.max(1, ...byDay.values());

  const today = new Date();
  const [cursor, setCursor] = useState({ year: today.getUTCFullYear(), month: today.getUTCMonth() });
  const [picked, setPicked] = useState<string | null>(null);

  // Default the selection to the newest day with data.
  useEffect(() => {
    const days = [...byDay.keys()].sort();
    setPicked(days.length ? days[days.length - 1] : null);
  }, [byDay]);

  const prefix = isoDay(cursor.year, cursor.month, 1).slice(0, 7);
  const monthTotal = [...byDay.entries()]
    .filter(([d]) => d.startsWith(prefix))
    .reduce((s, [, v]) => s + v, 0);

  const status = !symbolId
    ? "Select an instrument"
    : error
      ? `Feed unavailable: ${error}`
      : picked
        ? `${picked} · ${fmtInt(byDay.get(picked) ?? 0)} events`
        : data === null
          ? "Loading…"
          : "No activity on record";

  return (
    <div
      className="bg-[#11161d] border border-[#29323d] border-solid content-stretch flex flex-col gap-[12px] items-start overflow-clip p-[14px] relative rounded-[6px] min-w-0 self-stretch flex-[408_1_0px]"
      data-node-id="2007:7861"
      data-name="Activity calendar"
    >
      <div className="flex items-center justify-between leading-[1.4] w-full whitespace-nowrap">
        <p className="font-sans font-semibold text-[#e7edf3] text-[12px]">Activity by day</p>
        <p className="font-mono text-[#94a3b3] text-[9px]">{symbolLabel}</p>
      </div>
      <div className="flex items-start justify-between w-full">
        <p className="font-mono leading-[1.4] text-[#e7edf3] text-[11px] whitespace-nowrap">
          {MONTHS[cursor.month]} {cursor.year}
          <span className="text-[#647587]"> · {fmtInt(monthTotal)} events</span>
        </p>
        <div className="flex gap-[12px] items-start">
          <div
            className="relative shrink-0 size-[14px] cursor-pointer"
            onClick={() => setCursor((c) => shiftMonth(c, -1))}
            data-name="chevron-left"
          >
            <img alt="Previous month" className="absolute block inset-0 max-w-none size-full" src={imgChevronLeft1} />
          </div>
          <div
            className="relative shrink-0 size-[14px] cursor-pointer"
            onClick={() => setCursor((c) => shiftMonth(c, 1))}
            data-name="chevron-right"
          >
            <img alt="Next month" className="absolute block inset-0 max-w-none size-full" src={imgChevronRight1} />
          </div>
        </div>
      </div>
      <div className="flex flex-col gap-[4px] w-full">
        <div className="flex gap-[4px] w-full">
          {WEEKDAYS.map((d, i) => (
            <p key={i} className="flex-[1_0_0] text-center font-sans text-[#94a3b3] text-[9px] leading-[1.4]">
              {d}
            </p>
          ))}
        </div>
        {monthWeeks(cursor).map((week, w) => (
          <div key={w} className="flex gap-[4px] w-full">
            {week.map((day, i) => {
              if (day === null) return <div key={i} className="flex-[1_0_0] h-[34px]" />;
              const iso = isoDay(cursor.year, cursor.month, day);
              const events = byDay.get(iso) ?? 0;
              return (
                <div
                  key={i}
                  onClick={() => setPicked(iso)}
                  title={`${iso} · ${fmtInt(events)} events`}
                  className="flex-[1_0_0] h-[34px] flex items-center justify-center rounded-[3px] border border-solid cursor-pointer"
                  style={{ background: heat(events / max), borderColor: iso === picked ? "#79d9ee" : "#11161d" }}
                >
                  <p className="font-mono text-[10px] leading-[1.4]" style={{ color: events > 0 ? "#e7edf3" : "#647587" }}>
                    {day}
                  </p>
                </div>
              );
            })}
          </div>
        ))}
      </div>
      <div className="flex flex-col gap-[4px] w-full font-mono text-[9px] leading-[1.4] text-[#94a3b3] whitespace-nowrap">
        <p className="text-[#e7edf3] text-[10px]">{status}</p>
        <p>Low ▂ ▃ ▅ High · last {CALENDAR_DAYS} days · UTC</p>
      </div>
    </div>
  );
}
