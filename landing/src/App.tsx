import { useState } from "react";

const A = "/assets";

const assets = {
  mark: `${A}/ffcfa.svg`,
  arrowDark: `${A}/f4464.svg`,
  arrowLight: `${A}/fa1b6.svg`,
  connected: `${A}/09808.svg`,
  point: `${A}/cf6ae.svg`,
  series: `${A}/dfde1.svg`,
  ask: `${A}/087e4.svg`,
  bid: `${A}/a7ddc.svg`,
};

const assetClasses = [
  ["FX", "EUR/USD"],
  ["Metals", "XAU/USD"],
  ["Energy", "WTI"],
  ["Indices", "SPX"],
  ["Crypto", "BTC/USD"],
  ["Bonds", "US 10Y"],
  ["Futures", "ESZ6"],
  ["Equities", "AAPL"],
  ["ETFs", "SPY"],
];

const analyticBlocks = [
  ["Summary / OHLCV", "Price, range and traded volume"],
  ["Order flow", "Net pressure, posted and taken"],
  ["Liquidity", "Cumulative bid and ask depth"],
  ["Volume at price", "Where trading concentrates"],
  ["Spread", "The cost of crossing the book"],
  ["Distribution", "Tick inter-arrival structure"],
  ["Activity", "Intraday event intensity"],
  ["Volatility", "Rolling realised tape risk"],
  ["Behaviour", "Price-return autocorrelation"],
  ["Impact / drawdown", "Large-trade response and risk"],
];

const events = [
  ["15:59:59.919", "CANCEL", "ASK", "−100,000 EUR"],
  ["15:59:59.940", "ADD", "BID", "+500,000 EUR"],
  ["15:59:59.961", "TRADE", "BUY", "1,200,000 EUR"],
  ["15:59:59.982", "QUOTE", "MID", "1.08426 USD"],
];

function Brand() {
  return (
    <a className="brand" href="#top" aria-label="AXIOMA home">
      <img src={assets.mark} width="30" height="31" alt="" />
      <span>AXIOMA</span>
    </a>
  );
}

function Arrow({ dark = false }: { dark?: boolean }) {
  return <img src={dark ? assets.arrowDark : assets.arrowLight} width="14" height="14" alt="" />;
}

function ActionButton({
  children,
  primary = false,
  onClick,
}: {
  children: React.ReactNode;
  primary?: boolean;
  onClick: () => void;
}) {
  return (
    <button className={`action ${primary ? "action-primary" : ""}`} type="button" onClick={onClick}>
      <span>{children}</span>
      <Arrow dark={primary} />
    </button>
  );
}

function Eyebrow({ children }: { children: React.ReactNode }) {
  return <p className="eyebrow">{children}</p>;
}

function SectionHeading({
  eyebrow,
  children,
  supporting,
}: {
  eyebrow: string;
  children: React.ReactNode;
  supporting?: string;
}) {
  return (
    <div className="section-intro">
      <div>
        <Eyebrow>{eyebrow}</Eyebrow>
        <h2>{children}</h2>
      </div>
      {supporting && <p className="section-support">{supporting}</p>}
    </div>
  );
}

function PriceChart({ window }: { window: number }) {
  const bars = [
    20, 14, 27, 19, 34, 24, 31, 17, 38, 25, 30, 20, 35, 16, 28, 22, 32, 18, 36, 24, 29, 15, 33, 20,
    38, 26, 31, 19, 35, 23, 28, 17, 34, 25, 30, 18, 37, 22, 29, 16, 32, 25, 35, 20, 30, 18, 36, 24,
    31, 17, 34, 22, 29, 16, 33, 24, 37, 19, 30,
  ];
  return (
    <div className="chart-card price-card">
      <div className="chart-caption">
        <span>Price + volume</span>
        <span>USD/EUR · +{(15.5 + window).toFixed(1)} pips</span>
      </div>
      <div className="plot">
        <div className="plot-grid" />
        <img
          className="data-series"
          src={assets.series}
          width="719"
          height="146"
          alt="EUR/USD rising price series"
        />
        <img className="latest-point" src={assets.point} width="6" height="6" alt="" />
        <div className="volume-bars" aria-hidden="true">
          {bars.map((height, i) => (
            <i key={i} style={{ height, background: i % 3 === 2 ? "#baa2ec" : "#163b47" }} />
          ))}
        </div>
        <div className="y-labels">
          <span>1.0850</span>
          <span>1.0838</span>
          <span>1.0826</span>
          <span>1.0814</span>
        </div>
        <div className="x-labels">
          <span>12:00</span>
          <span>13:20</span>
          <span>14:40</span>
          <span>16:00</span>
        </div>
      </div>
    </div>
  );
}

function DepthChart() {
  return (
    <div className="chart-card depth-card">
      <div className="chart-caption">
        <span>Book depth</span>
        <span>EUR millions</span>
      </div>
      <div className="plot">
        <div className="plot-grid" />
        <img className="bid-depth" src={assets.bid} width="177.38" height="156.4" alt="Bid depth" />
        <img
          className="ask-depth"
          src={assets.ask}
          width="177.38"
          height="119.85"
          alt="Ask depth"
        />
        <div className="y-labels">
          <span>20m</span>
          <span>13m</span>
          <span>7m</span>
          <span>0</span>
        </div>
        <div className="x-labels">
          <span>−5 pips</span>
          <span>−1.7</span>
          <span>+1.7</span>
          <span>+5 pips</span>
        </div>
      </div>
    </div>
  );
}

function Terminal({ window }: { window: number }) {
  return (
    <div className="terminal" id="terminal">
      <div className="terminal-head mono">
        <div>
          <strong>AXIOMA / EUR/USD</strong>
          <span>FX · {window}h · 09 OCT 2026 · 12:00–16:00 UTC</span>
        </div>
        <p>
          <img src={assets.connected} width="5" height="5" alt="" /> FEED CONNECTED · UPDATED 16:00
        </p>
      </div>
      <div className="terminal-rule" />
      <div className="metrics mono">
        {[
          ["BOOK EVENTS", (46080 * window).toLocaleString()],
          ["MID-PRICE", "1.08426"],
          ["CHANGE", `+0.${14 + window}%`],
          ["AVG SPREAD", "0.72 pip"],
          ["FLOW IMBALANCE", "+0.24"],
        ].map(([label, value], i) => (
          <div key={label}>
            <small>{label}</small>
            <strong className={i === 2 ? "positive" : ""}>{value}</strong>
          </div>
        ))}
      </div>
      <div className="charts">
        <PriceChart window={window} />
        <DepthChart />
      </div>
      <div className="terminal-note mono">
        <span>PRODUCT VIEW / ILLUSTRATIVE MARKET DATA</span>
        <span>QUOTE → LIQUIDITY → FLOW → BEHAVIOUR</span>
      </div>
    </div>
  );
}

function AccessDialog({ close }: { close: () => void }) {
  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={close}>
      <div
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="dialog-title"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <button className="dialog-close" onClick={close} aria-label="Close dialog">
          ×
        </button>
        <Eyebrow>PRIVATE DESK WALKTHROUGH</Eyebrow>
        <h2 id="dialog-title">Bring your market into focus.</h2>
        <p>
          Tell us where to reach you. We’ll tailor the AXIOMA walkthrough to your desk’s instruments
          and research questions.
        </p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            close();
          }}
        >
          <label>
            Work email
            <input type="email" placeholder="name@institution.com" required autoFocus />
          </label>
          <label>
            Primary market
            <select defaultValue="FX">
              <option>FX</option>
              <option>Equities</option>
              <option>Futures</option>
              <option>Digital assets</option>
              <option>Other</option>
            </select>
          </label>
          <button className="action action-primary dialog-submit" type="submit">
            <span>Request a desk demo</span>
            <Arrow dark />
          </button>
        </form>
      </div>
    </div>
  );
}

export default function App() {
  const [window, setWindow] = useState(4);
  const [selectedBlock, setSelectedBlock] = useState(0);
  const [dialogOpen, setDialogOpen] = useState(false);
  const openDialog = () => setDialogOpen(true);

  return (
    <main id="top">
      <header className="site-header">
        <Brand />
        <nav aria-label="Primary navigation">
          <a href="/terminal/">Terminal</a>
          <a href="#analytics">Analytics</a>
          <a href="#coverage">Coverage</a>
          <a href="#methodology">Methodology</a>
        </nav>
        <ActionButton onClick={openDialog}>Request access</ActionButton>
      </header>

      <section className="hero">
        <div className="hero-kicker">
          <i />
          <Eyebrow>THE MARKET, AT EVENT RESOLUTION.</Eyebrow>
        </div>
        <div className="hero-copy">
          <h1>
            Every tick.
            <br />A clearer picture.
          </h1>
          <div>
            <p>
              Market microstructure analytics for institutional trading desks and quant researchers.
              See what price alone leaves out.
            </p>
            <ActionButton
              primary
              onClick={() => {
                globalThis.location.href = "/terminal/";
              }}
            >
              Explore the terminal
            </ActionButton>
          </div>
        </div>
        <Terminal window={window} />
      </section>

      <section className="stat-strip" aria-label="Platform at a glance">
        {[
          ["Every event", "Tick-level book coverage"],
          ["9", "Asset classes"],
          ["10", "Analytic blocks"],
          ["4 hours", "Maximum look-back"],
          ["5 minutes", "Refresh cadence"],
        ].map(([value, label]) => (
          <div key={label}>
            <strong className="mono">{value}</strong>
            <span>{label}</span>
          </div>
        ))}
      </section>

      <div className="page-sections">
        <section className="event-section section-rule" id="coverage">
          <div className="event-copy">
            <Eyebrow>03 / EVENT RESOLUTION</Eyebrow>
            <h2>
              Not just where price went.
              <br />
              What happened in the book.
            </h2>
            <p>
              Every quote update, trade, liquidity add and cancellation in the selected window.
              Follow price moves alongside the liquidity that made them possible.
            </p>
          </div>
          <div className="event-stream">
            <div className="event-head mono">
              <span>EVENT STREAM / EUR/USD</span>
              <span>100% COVERAGE</span>
            </div>
            {events.map(([time, event, side, amount], i) => (
              <div className="event-row mono" key={time}>
                <span>{time}</span>
                <strong>{event}</strong>
                <b className={i === 0 ? "ask" : "bid"}>{side}</b>
                <span>{amount}</span>
              </div>
            ))}
          </div>
        </section>

        <section className="content-section section-rule">
          <SectionHeading
            eyebrow="04 / CROSS-ASSET CONTEXT"
            supporting="From FX to ETFs. Consistent event-level views."
          >
            Nine asset classes. One analytical language.
          </SectionHeading>
          <div className="asset-grid">
            {assetClasses.map(([name, code]) => (
              <button key={name}>
                <span>{name}</span>
                <strong className="mono">{code}</strong>
              </button>
            ))}
          </div>
        </section>

        <section className="content-section section-rule" id="analytics">
          <SectionHeading
            eyebrow="05 / THE ANALYTICAL TOOLKIT"
            supporting="A modular workspace for the anatomy of a market."
          >
            Ten blocks. No blind spots.
          </SectionHeading>
          <div className="analytics-grid">
            {analyticBlocks.map(([name, copy], i) => (
              <button
                className={selectedBlock === i ? "selected" : ""}
                key={name}
                onClick={() => setSelectedBlock(i)}
              >
                <span className="mono">{String(i + 1).padStart(2, "0")} /</span>
                <strong>{name}</strong>
                <small>{copy}</small>
              </button>
            ))}
          </div>
        </section>

        <section className="content-section section-rule workflow" id="methodology">
          <div className="workflow-head">
            <SectionHeading eyebrow="06 / A REPEATABLE WORKFLOW">
              Choose the context. Run the analysis.
            </SectionHeading>
            <div className="window-switch" aria-label="Rolling analysis window">
              {[1, 2, 3, 4].map((hours) => (
                <button
                  className={window === hours ? "active" : ""}
                  key={hours}
                  onClick={() => setWindow(hours)}
                >
                  {hours}h
                </button>
              ))}
            </div>
          </div>
          <div className="steps">
            {[
              ["Select", "Choose an instrument across nine asset classes."],
              ["Set the window", "Scrub to a point in time. Pick a 1h, 2h, 3h or 4h rolling view."],
              [
                "Run and inspect",
                "Read ten analytic blocks, then trace a result back to raw ticks.",
              ],
            ].map(([title, copy], i) => (
              <div key={title}>
                <p>
                  <span className="mono">0{i + 1}</span>
                  {title}
                </p>
                <small>{copy}</small>
              </div>
            ))}
          </div>
        </section>
      </div>

      <section className="technical">
        <div className="technical-copy">
          <Eyebrow>07 / PRECISE BY DESIGN</Eyebrow>
          <h2>Live data. Deliberate cadence.</h2>
          <p>
            A live trading feed, a bounded research window and explicit units. Know what you are
            measuring—and when it was last updated.
          </p>
        </div>
        <dl>
          {[
            ["Resolution", "Every book event per instrument"],
            ["Look-back", `Up to ${window} hours`],
            ["Rolling windows", "1h / 2h / 3h / 4h"],
            ["Refresh", "Every 5 minutes"],
            ["Traceability", "UTC timestamps + raw-tick table"],
          ].map(([term, detail]) => (
            <div key={term}>
              <dt>{term}</dt>
              <dd className="mono">{detail}</dd>
            </div>
          ))}
        </dl>
      </section>

      <section className="audience">
        <Eyebrow>08 / BUILT FOR PEOPLE WHO READ THE TAPE</Eyebrow>
        <div>
          {[
            [
              "Trading desks",
              "Understand liquidity before crossing it. Monitor spread, depth and the footprint of large trades.",
            ],
            [
              "Quant researchers",
              "Interrogate event distributions, autocorrelation and rolling volatility. Trace hypotheses to individual ticks.",
            ],
            [
              "Market structure teams",
              "Compare book dynamics across instruments using one consistent analytical framework.",
            ],
          ].map(([title, copy]) => (
            <article key={title}>
              <h3>{title}</h3>
              <p>{copy}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="faq">
        <div>
          <Eyebrow>09 / BEFORE YOU CONNECT</Eyebrow>
          <h2>The essentials, plainly.</h2>
        </div>
        <div className="answers">
          {[
            [
              "Is this a trading platform?",
              "No. AXIOMA is an analytics terminal for observing and researching market microstructure.",
            ],
            [
              "Does live mean continuous analytics refresh?",
              "The feed is live. Analytics refresh every five minutes over your selected rolling window.",
            ],
            [
              "Can I inspect the underlying events?",
              "Yes. The raw-tick table preserves every book event in the window for each instrument.",
            ],
          ].map(([q, a]) => (
            <details key={q} open>
              <summary>{q}</summary>
              <p>{a}</p>
            </details>
          ))}
        </div>
      </section>

      <section className="final-cta">
        <div>
          <Eyebrow>10 / SEE THE SIGNAL IN THE STRUCTURE</Eyebrow>
          <h2>Bring the book into focus.</h2>
          <p>Walk through AXIOMA with your desk’s instruments and research questions.</p>
        </div>
        <ActionButton primary onClick={openDialog}>
          Request a desk demo
        </ActionButton>
      </section>

      <footer>
        <Brand />
        <p className="mono">© 2026 AXIOMA · MARKET MICROSTRUCTURE, RESOLVED.</p>
        <nav>
          <a href="/terminal/">Terminal</a>
          <button onClick={openDialog}>Contact</button>
        </nav>
      </footer>
      {dialogOpen && <AccessDialog close={() => setDialogOpen(false)} />}
    </main>
  );
}
