"""Content of the /research/ report. Numbers come from results.json; the prose
interprets the 2026-11-05 → 11-15 run and should be reviewed after a re-run."""
from build_report import callout, chart, num, stat_rows, tone, verdict


def _p_str(p):
    stop = f"{p['stop_k']}× value-area width" if p["stop_k"] else "none"
    return (f"{p['window']}-minute profile · enter {p['entry_k']}× value-area width from the POC · "
            f"stop {stop} · max hold {p['hold'] * 5} min · {'limit (maker)' if p['mode'] == 'maker' else 'market (taker)'} entry")


def hero(r):
    d, h1, h2, h3 = r["data"], r["H1_poc"], r["H2_flow"], r["H3_reversal"]
    o1, o2, o3 = h1["out_of_sample"], h2["out_of_sample"], h3["out_of_sample"]
    return f"""
<section class="block" id="summary">
  <div class="eyebrow">Research note 01 · Market microstructure</div>
  <h1>Can the Point of Control be traded?</h1>
  <p class="lead">We tested whether price reliably returns to the <b>Point of Control (POC)</b>, the busiest
  price of the last few hours, and whether that can be harvested by a market maker or a scalper. We also tested
  two order-book factors: <b>order-flow momentum</b> and <b>short-term reversal</b>. Every number
  below is net of the bid/ask spread and measured on data the strategy never saw while it was tuned.</p>
  <div class="meta">
    <span class="chip">{', '.join(d['instruments'])}</span>
    <span class="chip">{d['bars_with_data']:,} five-minute bars</span>
    <span class="chip">{d['start'][:10]} → {d['end'][:10]}</span>
    <span class="chip">train / test split {d['split'][:16]} UTC</span>
  </div>
  <div class="cards">
    <div class="card neg"><div class="tag">H1 · POC reversion</div><div class="big">{num(o1['avg_bps'])}</div>
      <div class="unit">bps per trade, out-of-sample</div>
      <p>Price does not reliably return to the POC. Limit-order (market-making) entries get filled exactly as
      price breaks away. It loses even with zero spread.</p><span class="badge neg">REJECTED</span></div>
    <div class="card warn"><div class="tag">H2 · Order-flow momentum</div><div class="big">{num(o2['avg_bps'])}</div>
      <div class="unit">bps per trade, out-of-sample</div>
      <p>Profitable on unseen data and robust to costs and trend removal, but the edge appeared only in the
      second week and leans on BTC shorts.</p><span class="badge warn">PROMISING · NOT PROVEN</span></div>
    <div class="card neg"><div class="tag">H3 · Short-term reversal</div><div class="big">{num(o3['avg_bps'])}</div>
      <div class="unit">bps per trade, out-of-sample</div>
      <p>A small edge in the tuning period disappeared on new data. Overreactions revert within seconds, faster
      than a 5-minute bar can capture.</p><span class="badge neg">REJECTED</span></div>
  </div>
  {callout('How to read "bps"', 'A basis point (bp) is 0.01% of price. On BTC at $74,000, 1 bp ≈ $7.40 per coin; '
           'on gold at $4,050, 1 bp ≈ $0.40 per ounce. A typical 5-minute move here is 4–6 bps; the spread is about 0.5–0.7 bps.')}
  <div class="actions"><a class="btn primary" href="slides.html">Open the 12-slide summary →</a>
  <a class="btn" href="/terminal/">Back to the terminal</a><a class="btn" href="#data">Download the data</a></div>
</section>"""


def method(r):
    d = r["data"]
    sp, mv = d["median_spread_bps"], d["median_abs_5m_move_bps"]
    rows = "".join(f"<tr><td>{k}</td><td>{sp[k]:.2f} bps</td><td>{mv[k]:.1f} bps</td><td>{mv[k] / sp[k]:.0f}×</td></tr>" for k in sp)
    return f"""
<section class="block" id="method">
  <div class="eyebrow">01 · Method</div><h2>How we tested, so the results can be trusted</h2>
  <p>Backtests are easy to fool. A strategy tuned on the same data it is judged on will almost always look good.
  We used a fixed protocol for every hypothesis:</p>
  <ol class="steps">
    <li><b>Split the history in time.</b> The first half (to {d['split'][:16]} UTC) is the <i>tuning period</i>:
    we try many settings and keep the best. The second half is the <i>test period</i>: those settings are frozen and
    run once on data they have never seen.</li>
    <li><b>No peeking.</b> Every decision uses only information available at the close of a 5-minute bar,
    and orders can fill no earlier than the next bar.</li>
    <li><b>Pay to trade.</b> Market orders pay half the bid/ask spread on entry and on exit. A resting limit order
    only fills if the opposite side of the book trades through its price, not merely touches it.</li>
    <li><b>Assume the worst inside a bar.</b> We only see each bar's high and low. If a bar touches both the stop
    and the target, we assume the stop came first.</li>
    <li><b>Stress-test the winner.</b> We compare against random-direction trades at the same times, rerun with zero
    costs, and remove the market's overall trend, so a short strategy can't look clever just because prices fell.</li>
  </ol>
  <h3 id="data-overview">The data</h3>
  <p>Five-minute bars from AXIOMA's <code>metrics_5m</code> research table: mid price, spread, order-flow imbalance,
  book imbalance and volume-at-price profiles (POC and value area) over 60/120/180/240-minute windows.</p>
  <div class="table-wrap"><table><thead><tr><th>Instrument</th><th>Median spread</th><th>Median 5-min move</th>
  <th>Move ÷ spread</th></tr></thead><tbody>{rows}</tbody></table></div>
  {callout('Why this matters', 'Typical moves are roughly 8–9× the spread, so trading costs are small. A strategy here '
           'wins or loses mainly on whether it gets the <b>direction</b> right, not on cost.')}
</section>"""


def h1(r):
    h = r["H1_poc"]
    sw, mk = h["sweep"], h["maker_variant"]
    mi, mo = mk["in_sample"], mk["out_of_sample"]
    inst = "".join(f"<tr><td>{i['label']}</td><td>{i['trades']}</td><td>{num(i['win_rate'], pct=True)}</td>"
                   f"<td class='{tone(i['avg_bps'])}'>{num(i['avg_bps'], 2)}</td></tr>" for i in h["oos_by_instrument"])
    return f"""
<section class="block" id="h1">
  <div class="eyebrow">02 · Hypothesis 1</div><h2>POC mean reversion: market making and scalping</h2>
  <div class="grid2"><div>
    <h3 id="h1-idea">The idea</h3>
    <p>A <b>volume-at-price profile</b> counts how much activity happened at each price over a lookback window.
    Its peak is the <b>Point of Control</b>, and the band holding 70% of activity is the <b>value area</b>.
    Traders often treat the POC as a magnet: if price wanders away, it should come back.</p>
    <p>We tested two ways to monetise that:</p>
    <ul>
      <li><b>Market maker:</b> rest a buy limit below the POC and a sell limit above it. When filled, take profit
      at the POC. You earn the spread instead of paying it.</li>
      <li><b>Scalper:</b> when price is already far from the POC, buy or sell at market and target the POC.</li>
    </ul>
    <p>We tried <b>{sw['configs']} combinations</b>: profile window (60/120/240 min), entry distance, stop distance,
    maximum hold (15/30/60 min) and order type.</p>
  </div><div>
    {chart(h['charts']['deciles'], 'Every bar is split into 10 groups by how far price sits from the 240-minute POC '
           '(1 = far below, 10 = far above), then we measure the next 30 minutes. If POC reversion worked, the left '
           'bars would be green (bounce up) and the right bars red (fall back). They are not: the pattern is noise.')}
  </div></div>

  <h3 id="h1-results">What happened</h3>
  <div class="grid2"><div>
    {chart(h['charts']['heatmap'], 'Median result across 27 settings for each order type and profile window, in bps '
           'per trade after costs (tuning period). Every market-making cell is red.')}
  </div><div>
    <ul class="checks">
      <li class="no">Only <b>{num(sw['profitable_share_net'], pct=True)}</b> of {sw['configs']} settings made money
      even in the tuning period, where luck is on the strategy's side.</li>
      <li class="no">With trading costs set to <b>zero</b>, still only <b>{num(sw['profitable_share_gross'], pct=True)}</b>
      were profitable. Costs aren't the problem; the idea doesn't hold up.</li>
      <li class="no">Market-making entries were the worst: median <b>{num(sw['by_mode']['maker'], 2)} bps</b> per trade
      vs {num(sw['by_mode']['taker'], 2)} for market orders.</li>
      <li class="no">On the test period, only <b>{num(h['oos_robustness']['oos_profitable_share'], pct=True)}</b> of all
      settings were profitable.</li>
    </ul>
  </div></div>

  <h3 id="h1-maker">Why market-making the POC fails: adverse selection</h3>
  <p>The best market-making setting from the tuning period was: <i>{_p_str(mk['params'])}</i>.</p>
  <div class="table-wrap"><table><thead><tr><th>Period</th><th>Trades</th><th>Win rate</th><th>Avg bps / trade</th>
  <th>Hit stop</th><th>Reached POC</th></tr></thead><tbody>
  <tr><td>Tuning</td><td>{mi['trades']}</td><td>{num(mi['win_rate'], pct=True)}</td><td class="{tone(mi['avg_bps'])}">{num(mi['avg_bps'], 2)}</td>
  <td>{num(mi['stop_share'], pct=True)}</td><td>{num(mi['target_share'], pct=True)}</td></tr>
  <tr><td>Test</td><td>{mo['trades']}</td><td>{num(mo['win_rate'], pct=True)}</td><td class="{tone(mo['avg_bps'])}">{num(mo['avg_bps'], 2)}</td>
  <td>{num(mo['stop_share'], pct=True)}</td><td>{num(mo['target_share'], pct=True)}</td></tr></tbody></table></div>
  {callout('Adverse selection, in plain words', 'A resting buy order 1 value-area-width below the POC only fills '
           'when sellers push price that far, which is usually when the market is <b>breaking out</b> of its range, '
           'not wobbling inside it. So the fills you get are the ones you least want: about 6 in 10 hit the stop, and '
           'fewer than 2 in 10 make it back to the POC.')}

  <h3 id="h1-oos">The best setting on unseen data</h3>
  <p>The best setting overall from the tuning period was a scalper: <i>{_p_str(h['params'])}</i>.</p>
  {stat_rows(h)}
  <div class="grid2"><div>
    {chart(h['charts']['equity'], 'Cumulative profit after costs. Grey = tuning period (where the setting was picked); '
           'blue = test period. The dashed line is the split.')}
  </div><div>
    {chart(h['charts']['benchmark'], f"Grey = 500 runs of the same trades with a random buy/sell direction. The "
           f"strategy did worse than {num(h['random_benchmark_p'], pct=True)} of them, i.e. no better than a coin flip.")}
    <div class="table-wrap"><table><thead><tr><th>Instrument (test)</th><th>Trades</th><th>Win rate</th><th>Avg bps</th></tr></thead>
    <tbody>{inst}</tbody></table></div>
  </div></div>
  {verdict('neg', 'Verdict: rejected.', 'On this data, POC mean reversion is not a profitable market-making or scalping '
           'edge. The best-looking tuning result reversed on new data, and the market-making version suffers from adverse selection.')}
</section>"""


def _ic_table(ic):
    rows = "".join(f"<tr><td>{x['horizon_min']} min</td><td class='{tone(x['rank_ic'])}'>{x['rank_ic']:+.3f}</td>"
                   f"<td>{x['t_stat']:+.2f}</td><td>{'no' if abs(x['t_stat']) < 2 else 'yes'}</td></tr>" for x in ic)
    return ("<div class='table-wrap'><table><thead><tr><th>Horizon</th><th>Rank IC</th><th>t-stat</th>"
            f"<th>Significant?</th></tr></thead><tbody>{rows}</tbody></table></div>")


def h2(r):
    h = r["H2_flow"]
    p = h["params"]
    daily = "".join(f"<tr><td>{k}</td><td class='{tone(v)}'>{num(v, 0)}</td></tr>" for k, v in h.get("daily_bps", {}).items())
    inst = "".join(f"<tr><td>{i['label']}</td><td>{i['trades']}</td><td>{num(i['win_rate'], pct=True)}</td>"
                   f"<td class='{tone(i['avg_bps'])}'>{num(i['avg_bps'], 2)}</td></tr>" for i in h["oos_by_instrument"])
    return f"""
<section class="block" id="h2">
  <div class="eyebrow">03 · Hypothesis 2</div><h2>Order-flow momentum</h2>
  <h3 id="h2-idea">The idea</h3>
  <p>AXIOMA classifies each tick as buyer- or seller-initiated (tick rule) and reports the net balance as
  <b>flow imbalance</b>, from −1 (all selling) to +1 (all buying). If one side has dominated for the last
  15 minutes, informed traders may still be working orders, so price should keep drifting the same way.</p>
  <p><b>Rule (picked in the tuning period, from 16 combinations):</b> average the last three bars' flow imbalance and
  compare it with its own past 4 hours. When it is more than <b>{p['threshold_std']:.1f} standard deviations</b> from
  normal, buy (or sell) at market and exit after <b>{p['hold_bars'] * 5} minutes</b>.</p>
  {stat_rows(h)}
  <div class="grid2"><div>
    {chart(h['charts']['equity'], 'Cumulative profit after costs. Flat in the tuning period, then rising steadily on the '
           'unseen test period.')}
  </div><div>
    {chart(h['charts']['benchmark'], f"Random-direction trades at the same times (grey) vs the strategy. None of the 500 "
           f"random runs did as well (p = {num(h['random_benchmark_p'], 3, sign=False)}).")}
  </div></div>
  {h2_checks(h, daily, inst)}
</section>"""


def h2_checks(h, daily, inst):
    return f"""
  <h3 id="h2-checks">Stress tests</h3>
  <div class="grid2"><div>
    <ul class="checks">
      <li class="ok"><b>Beats chance.</b> Better than all 500 random-direction runs on the same trades.</li>
      <li class="ok"><b>Not just costs.</b> {num(h['out_of_sample']['avg_bps'], 1)} bps after spread vs
      {num(h['out_of_sample_zero_cost']['avg_bps'], 1)} without, so costs take less than 1 bp.</li>
      <li class="ok"><b>Not just the trend.</b> Still {num(h['out_of_sample_drift_adjusted']['avg_bps'], 1)} bps per trade
      after removing each market's average drift.</li>
      <li class="ok"><b>Not one lucky setting.</b> {num(h['oos_robustness']['oos_profitable_share_drift_adjusted'], pct=True)}
      of all 16 combinations were profitable in the test period.</li>
      <li class="warn"><b>Weak in tuning.</b> Only {num(h['in_sample']['avg_bps'], 1)} bps per trade
      (t = {num(h['in_sample']['t_stat'], 2)}) in the first week. The edge appeared in the second week, which looks like
      a change of market regime.</li>
      <li class="warn"><b>Lopsided.</b> Shorts averaged {num(h['oos_short_avg_bps'], 1)} bps vs longs
      {num(h['oos_long_avg_bps'], 1)}; most profit came from shorting BTC while it fell. The top 5 trades are
      {num(h.get('top5_share_of_total'), pct=True)} of total profit.</li>
    </ul>
  </div><div>
    {chart(h['charts']['breakdown'], 'Total test-period profit by instrument and direction. Selling BTC dominates; '
           'buying gold lost money.')}
    <div class="grid2"><div class="table-wrap"><table><thead><tr><th>Day (test)</th><th>Net bps</th></tr></thead>
    <tbody>{daily}</tbody></table></div>
    <div class="table-wrap"><table><thead><tr><th>Instrument</th><th>Trades</th><th>Win</th><th>Avg</th></tr></thead>
    <tbody>{inst}</tbody></table></div></div>
  </div></div>
  <h3 id="h2-ic">Predictive power by horizon</h3>
  {_ic_table(h['ic'])}
  <p class="muted small">Rank IC = rank correlation between the signal now and the price change over the next horizon.
  t-stats account for overlapping horizons. |t| &lt; 2 means it can't be told apart from noise. Note the sign flips:
  slightly negative at 5 minutes, positive at 30–60. The edge is a slow drift, not an instant push.</p>
  {verdict('warn', 'Verdict: promising, not proven.', 'It passes every robustness check we ran on the test week, but it '
           'was flat in the tuning week, and five days of mostly falling BTC is not enough evidence. Next step: paper-trade '
           'it with these frozen settings and re-test as data accumulates. Not ready for capital.')}"""


def h3(r):
    h = r["H3_reversal"]
    p = h["params"]
    return f"""
<section class="block" id="h3">
  <div class="eyebrow">04 · Hypothesis 3</div><h2>Short-term reversal</h2>
  <h3 id="h3-idea">The idea</h3>
  <p>Fast markets overshoot. AXIOMA's 10-second return autocorrelation is negative on average, meaning a move up
  tends to be followed by a small move down. If that carries over to 5-minute bars, fading an unusually large bar
  should pay.</p>
  <p><b>Rule (picked in tuning):</b> when the last 5-minute return is more than <b>{p['threshold_std']:.1f} standard
  deviations</b> from normal, trade the opposite way at market and exit after <b>{p['hold_bars'] * 5} minutes</b>.</p>
  {stat_rows(h)}
  <div class="grid2"><div>{chart(h['charts']['equity'], 'Small gains in the tuning period, then a steady loss on unseen data.')}</div>
  <div>{chart(h['charts']['benchmark'], f"Random-direction trades did better {num(h['random_benchmark_p'], pct=True)} of the time.")}
  {_ic_table(h['ic'])}</div></div>
  <ul class="checks">
    <li class="no">Only {num(h['oos_robustness']['oos_profitable_share_drift_adjusted'], pct=True)} of 16 settings were
    profitable in the test period.</li>
    <li class="warn">Predictive power is strongest at 5–15 minutes and fades by 30: the overreaction is real but small and
    short-lived, and the setting chosen in tuning (60-minute hold) waits too long.</li>
  </ul>
  {verdict('neg', 'Verdict: rejected at 5-minute resolution.', 'Overshoots revert within seconds, faster than a 5-minute '
           'bar can capture. Revisit on 10-second data, where the effect is visible.')}
</section>"""
