"""H2 / H3 factor slides and the closing slides."""
from build_presentation import img, num, slide, stat_table, verdict


def factor_slides(r):
    h2, h3 = r["H2_flow"], r["H3_reversal"]
    return [slide(f"""
      <h2>H2 · Order-flow momentum</h2>
      <div class="cols"><div>
      <p><b>Idea:</b> if buyers dominated the last 15 minutes (net buy ticks, scaled against the past
      4 hours), price keeps drifting up, and the same for sellers.</p>
      <p><b>Rule (chosen in-sample):</b> trade when the signal exceeds {h2['params']['threshold_std']:.1f} std,
      hold {h2['params']['hold_bars'] * 5} min, market orders.</p>
      {stat_table(h2)}
      </div><div>{img(h2['charts']['equity'])}</div></div>
    """), slide(f"""
      <h2>H2 · Promising, but read the fine print</h2>
      <div class="cols"><div>{img(h2['charts']['breakdown'])}</div><div><ul>
        <li class="ok">Beat every random-direction simulation (p = {num(h2['random_benchmark_p'], 3, sign=False)}).</li>
        <li class="ok">Still {num(h2['out_of_sample_drift_adjusted']['avg_bps'])} bps/trade after removing the market trend.</li>
        <li class="ok">{num(h2['oos_robustness']['oos_profitable_share_drift_adjusted'], pct=True)} of 16 settings were profitable
            out-of-sample, so it doesn't depend on one lucky setting.</li>
        <li class="warn">Only {num(h2['in_sample']['avg_bps'])} bps/trade (t = {num(h2['in_sample']['t_stat'], 2)}) in-sample.
            The edge appeared in week two, which looks like a regime change.</li>
        <li class="warn">Shorts {num(h2['oos_short_avg_bps'])} vs longs {num(h2['oos_long_avg_bps'])} bps/trade;
            the top 5 trades = {num(h2.get('top5_share_of_total'), pct=True)} of profit.</li>
        <li class="warn">IC is weak and changes sign across horizons. It needs months of data, not days.</li>
      </ul></div></div>
      {verdict('Verdict: worth paper-trading and re-testing as data accumulates. Not ready for capital.', 'warn')}
    """), slide(f"""
      <h2>H3 · Short-term reversal</h2>
      <div class="cols"><div>
      <p><b>Idea:</b> a sharp 5-minute move partly reverses (overreaction). The negative 10-second
      return autocorrelation in the feed hints at this.</p>
      <p><b>Rule (chosen in-sample):</b> fade moves above {h3['params']['threshold_std']:.1f} std,
      hold {h3['params']['hold_bars'] * 5} min.</p>
      {stat_table(h3)}
      <p class="muted">Only {num(h3['oos_robustness']['oos_profitable_share_drift_adjusted'], pct=True)} of settings were profitable out-of-sample.</p>
      </div><div>{img(h3['charts']['equity'])}</div></div>
      {verdict('Verdict: rejected. Micro-reversal exists within seconds, but at 5-minute granularity it is gone.', 'neg')}
    """)]


def closing(r):
    d = r["data"]
    return [slide(f"""
      <h2>Predictive power at a glance</h2>
      <div class="cols"><div>{img(r['charts_common']['ic'])}</div><div>
      <p><b>Rank IC</b> is the correlation between the signal now and the next move. In liquid markets, a
      durable 0.02–0.05 is already useful. Everything here sits inside the noise band.</p>
      <p>Reversal is strongest at 5–15 min and flow at 30–60 min, which is the textbook shape
      (quick overreaction, slower information) but <b>not yet statistically significant</b>.</p>
      </div></div>
    """), slide(f"""
      <h2>Next steps</h2>
      <ol>
        <li><b>More data.</b> Keep <code>export_flat.py</code> running and re-run weekly
            (<code>run_studies.py</code> → <code>build_presentation.py</code>). Target 2–3 months and more instruments.</li>
        <li><b>Paper-trade H2</b> with the frozen settings; compare live vs backtest.</li>
        <li><b>Finer data for market-making.</b> Real quoting needs 10-second / tick data with queue position,
            not 5-minute bars.</li>
        <li><b>Use POC as context, not a trigger.</b> Combine flow momentum with price <i>leaving</i> the value
            area (breakout), the opposite of the reversion idea.</li>
      </ol>
      <p class="muted">Caveats: {len(d['instruments'])} instruments, about 10 days, one train/test split. Fills are simulated from
      5-minute high/low and mean spread, with no slippage, latency, fees or queue position.</p>
    """)]
