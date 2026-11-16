import { useState, type ReactNode } from "react";
import { useCardState } from "../hooks/useCardState";
import { useVolumeProfiles } from "../hooks/useMarketData";
import PriceChart from "../charts/PriceChart";
import { CANDLE_TARGET, candlePeriod, type PriceMode } from "../charts/priceModel";
import { useMarket } from "../context/MarketContext";
import { formatKpis, fmtTimeUtc } from "../lib/format";
import { BIN_SECONDS } from "../lib/window";
import { imgChevronDown, imgEllipsis, imgMaximize2 } from "../assets";

// Lookbacks the server records a volume profile for (minutes).
const PROFILE_WINDOWS = [60, 120, 180, 240];

// A small segmented-control button for the card header.
function Toggle({
  active,
  onClick,
  title,
  name,
  children,
}: {
  active: boolean;
  onClick: () => void;
  title: string;
  name: string;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      title={title}
      aria-pressed={active}
      data-name={name}
      onClick={onClick}
      className={`font-mono text-[9px] leading-none tracking-[0.06em] px-[6px] h-[16px] rounded-[3px] border border-solid transition-colors ${
        active
          ? "bg-[#163b47] border-[#2a6f82] text-[#79d9ee]"
          : "bg-transparent border-[#29323d] text-[#94a3b3] hover:text-[#e7edf3]"
      }`}
    >
      {children}
    </button>
  );
}

export default function PriceVolumeCard() {
  const card = useCardState();
  const { summary, series, windowMinutes } = useMarket();
  const k = formatKpis(summary);
  // Line is the default view; candles and the profile overlay are opt-in.
  const [mode, setMode] = useState<PriceMode>("line");
  const [showProfile, setShowProfile] = useState(false);
  // null = follow the control bar's lookback.
  const [profileWindow, setProfileWindow] = useState<number | null>(null);
  const vpWindow = profileWindow ?? windowMinutes;
  const profiles = useVolumeProfiles(showProfile);
  const profile = showProfile ? (profiles.data?.[String(vpWindow)] ?? null) : null;

  const candleMinutes =
    mode === "candles" && series.length > 1 ? candlePeriod(series, BIN_SECONDS, CANDLE_TARGET) / 60 : null;
  let caption = "Mid price · depth events per 10s bucket";
  if (mode === "candles") {
    caption = "Mid price candles (OHLC of 10s bucket mids) · depth events per candle";
  }
  let profileNote: string | null = null;
  if (showProfile) {
    if (profile) {
      profileNote = `VP ${vpWindow / 60}h as of ${fmtTimeUtc(profile.as_of)} UTC`;
    } else if (profiles.error) {
      profileNote = "VP unavailable";
    } else if (profiles.data) {
      profileNote = `No recorded ${vpWindow / 60}h profile`;
    } else {
      profileNote = "VP loading…";
    }
  }

  return (
    <div
      className={`bg-[#11161d] border border-[#29323d] border-solid content-stretch flex flex-col gap-[8px] h-[248px] items-start overflow-clip p-[14px] min-w-0 relative rounded-[6px] flex-[1024_1_0px] ${card.className}`}
      data-node-id="2007:7222"
      data-name="Analytics card"
    >
      <div
        className="content-stretch flex items-center justify-between overflow-clip relative shrink-0 w-full"
        data-node-id="2007:7223"
        data-name="Chart header"
      >
        <div
          className="content-stretch flex gap-[7px] items-center overflow-clip relative shrink-0"
          data-node-id="2007:7224"
          onClick={card.onToggleCollapse}
          data-name="Chart title"
        >
          <div
            className="relative shrink-0 size-[12px]"
            data-node-id="2007:8396"
            data-name="chevron-down"
          >
            <img
              alt=""
              className="absolute block inset-0 max-w-none size-full"
              src={imgChevronDown}
            />
          </div>
          <p
            className="[word-break:break-word] font-sans font-semibold leading-[1.4] not-italic relative shrink-0 text-[#e7edf3] text-[12px] whitespace-nowrap"
            data-node-id="2007:7226"
          >
            Price + volume
          </p>
        </div>
        <div
          className="content-stretch flex gap-[12px] items-center overflow-clip relative shrink-0"
          data-node-id="2007:7227"
          data-name="Chart actions"
        >
          <div className="flex gap-[4px] items-center" data-name="Price view">
            <Toggle name="View line" title="Mid price line" active={mode === "line"} onClick={() => setMode("line")}>
              LINE
            </Toggle>
            <Toggle
              name="View candles"
              title="OHLC candlesticks of the bucket mids"
              active={mode === "candles"}
              onClick={() => setMode("candles")}
            >
              CANDLES
            </Toggle>
          </div>
          <div className="flex gap-[4px] items-center" data-name="Volume profile controls">
            {/* The window picker opens to the left, so the toggle itself does not
                move under the pointer when it is switched on. */}
            {showProfile &&
              PROFILE_WINDOWS.map((w) => (
                <Toggle
                  key={w}
                  name={`Profile window ${w}`}
                  title={`${w / 60}-hour profile`}
                  active={vpWindow === w}
                  onClick={() => setProfileWindow(w)}
                >
                  {`${w / 60}H`}
                </Toggle>
              ))}
            <Toggle
              name="Volume profile toggle"
              title="Overlay the recorded volume-at-price profile (POC, value area)"
              active={showProfile}
              onClick={() => setShowProfile((v) => !v)}
            >
              VOL PROFILE
            </Toggle>
          </div>
          <div
            className="relative shrink-0 size-[12px]"
            data-node-id="2007:8399"
            onClick={card.onToggleExpand}
            data-name="maximize-2"
          >
            <img
              alt=""
              className="absolute block inset-0 max-w-none size-full"
              src={imgMaximize2}
            />
          </div>
          <div
            className="relative shrink-0 size-[12px]"
            data-node-id="2007:8402"
            onClick={card.onToggleHighlight}
            data-name="ellipsis"
          >
            <img alt="" className="absolute block inset-0 max-w-none size-full" src={imgEllipsis} />
          </div>
        </div>
      </div>
      <div
        className="[word-break:break-word] content-stretch flex items-start justify-between leading-[1.4] not-italic overflow-clip relative shrink-0 w-full"
        data-node-id="2007:7230"
        data-name="Chart metadata"
      >
        <p
          className="font-sans font-normal relative shrink-0 text-[#94a3b3] text-[10px] whitespace-nowrap"
          data-node-id="2007:7231"
        >
          {caption}
          {candleMinutes !== null ? ` · ${candleMinutes < 1 ? `${candleMinutes * 60}s` : `${candleMinutes}m`}` : ""}
          {profileNote ? ` · ${profileNote}` : ""}
        </p>
        <p
          className="font-mono relative shrink-0 text-[#79d9ee] text-[11px] whitespace-pre"
          data-node-id="2007:7232"
        >{`${k.last}  /  ${k.change}`}</p>
      </div>
      <div
        className={`overflow-clip relative w-full ${card.expanded ? "flex-1 min-h-0" : "h-[166px] shrink-0"}`}
        data-node-id="2007:7233"
        data-name="Analytical plot"
      >
        <PriceChart mode={mode} profile={profile} expanded={card.expanded} />
      </div>
    </div>
  );
}
