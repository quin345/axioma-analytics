"""Intro and H1 (POC) slides."""
from build_presentation import cls, img, num, slide, stat_table, verdict


def p_str(p):
    return (f"{p['window']}m VAP, entry {p['entry_k']}× value-area, "
            f"stop {p['stop_k'] or 'none'}, max {p['hold'] * 5} min, {p['mode']}")


def intro(r):
    d, o1, o2, o3 = r["data"], r["H1_poc"]["out_of_sample"], r["H2_flow"]["out_of_sample"], r["H3_reversal"]["out_of_sample"]
    sp, mv = d["median_spread_bps"], d["median_abs_5m_move_bps"]
    return [slide(f"""
      <div class="kicker">AXIOMA Analytics · Quant research</div>
      <h1>Can the Point of Control be traded?</h1>
      <p class="lead">Testing POC mean reversion as a market-making / scalping strategy, plus two
      order-book factors, on {d['bars_with_data']:,} five-minute bars of {', '.join(d['instruments'])}.</p>
      <p class="muted">{d['start'][:10]} → {d['end'][:10]} · all results net of spread · press → to start</p>
    """, "title"), slide(f"""
      <h2>The answer in one slide</h2>
      <div class="cards">
        <div class="card neg"><div class="tag">H1 · POC reversion</div><div class="big">{num(o1['avg_bps'])} bps</div>
          <p>per trade out-of-sample. <b>Not profitable</b> as maker or taker; it loses even with zero spread.</p></div>
        <div class="card pos"><div class="tag">H2 · Order-flow momentum</div><div class="big">{num(o2['avg_bps'])} bps</div>
          <p>per trade out-of-sample. <b>Promising, not proven</b>: one week, mostly BTC shorts in a falling market.</p></div>
        <div class="card neg"><div class="tag">H3 · Short-term reversal</div><div class="big">{num(o3['avg_bps'])} bps</div>
          <p>per trade out-of-sample. <b>Not profitable</b>; the in-sample edge disappeared.</p></div>
      </div>
      <p class="muted center">1 bp = 0.01% of price. "Out-of-sample" = data the strategy never saw while it was tuned.</p>
    """), slide(f"""
      <h2>How we tested (so the results can be trusted)</h2>
      <div class="cols"><div><ol>
        <li><b>Split in time.</b> Tune on the first half (to {d['split'][:16]} UTC), then freeze the
            settings and test on the second half.</li>
        <li><b>No peeking.</b> Decisions use only data up to the bar close; orders fill on the <i>next</i> bar.</li>
        <li><b>Pay the spread.</b> Market orders cross half the spread on entry and exit. Limit orders fill
            only if the opposite side trades through the price.</li>
        <li><b>Conservative fills.</b> If stop and target are hit in the same bar, we assume the stop.</li>
        <li><b>Sanity checks.</b> Compare with random-direction trades at the same times, with zero costs,
            and with the market trend removed.</li>
      </ol></div><div>
      <table><tr><th>Instrument</th><th>Median spread</th><th>Median 5-min move</th></tr>
      {''.join(f"<tr><td>{k}</td><td>{sp[k]:.2f} bps</td><td>{mv[k]:.1f} bps</td></tr>" for k in sp)}</table>
      <p class="muted">Spreads are tiny relative to price moves, so cost is not the main problem:
      <b>direction</b> is.</p></div></div>
    """), slide(f"""<h2>Scorecard</h2>{img(r['charts_common']['scorecard'],
      'Average net profit per trade on unseen data, using settings frozen from the first half.')}""")]


def h1_slides(r):
    h1 = r["H1_poc"]
    mk, sw = h1["maker_variant"], h1["sweep"]
    mi, mo = mk["in_sample"], mk["out_of_sample"]
    return [slide(f"""
      <h2>H1 · The POC idea</h2>
      <div class="cols"><div>
      <p>The <b>Point of Control</b> is the price with the most trading in the last 60–240 minutes.
      The hypothesis: price gets "pulled back" to it.</p>
      <ul>
        <li><b>Market-maker version:</b> rest a buy limit below the POC and a sell limit above it; take profit at the POC.</li>
        <li><b>Scalper version:</b> when price is far from the POC, enter at market and target the POC.</li>
        <li>{sw['configs']} settings tested: VAP window, entry distance, stop, holding time, order type.</li>
      </ul></div>
      {img(h1['charts']['deciles'], 'If POC reversion worked: green on the left, red on the right. No such pattern.')}
      </div>
    """), slide(f"""
      <h2>H1 · Price does not reliably return to the POC</h2>
      <div class="cols"><div>
      {img(h1['charts']['heatmap'])}
      <p>Only <b>{num(sw['profitable_share_net'], pct=True)}</b> of settings were profitable in-sample, and only
      <b>{num(sw['profitable_share_gross'], pct=True)}</b> even with <b>zero</b> spread. Maker (limit) entries were
      worst (median {num(sw['by_mode']['maker'], 2)} bps/trade).</p>
      </div><div>{img(h1['charts']['equity'], 'Best in-sample setting: ' + p_str(h1['params']))}</div></div>
    """), slide(f"""
      <h2>H1 · Why market-making the POC fails: adverse selection</h2>
      <div class="cols"><div>
      <p>Best in-sample <b>maker</b> setting ({p_str(mk['params'])}):</p>
      <table><tr><th></th><th>Trades</th><th>Win rate</th><th>Avg bps</th><th>Stopped out</th></tr>
      <tr><td>In-sample</td><td>{mi['trades']}</td><td>{num(mi['win_rate'], pct=True)}</td>
        <td class="{cls(mi['avg_bps'])}">{num(mi['avg_bps'], 2)}</td><td>{num(mi['stop_share'], pct=True)}</td></tr>
      <tr><td>Out-of-sample</td><td>{mo['trades']}</td><td>{num(mo['win_rate'], pct=True)}</td>
        <td class="{cls(mo['avg_bps'])}">{num(mo['avg_bps'], 2)}</td><td>{num(mo['stop_share'], pct=True)}</td></tr></table>
      <p>Quotes far from the POC get filled exactly when price is <b>breaking away</b>. The position
      then runs against you, and most fills hit the stop before price reaches the POC.</p>
      </div><div>
      <p>Best overall (scalper) setting, out-of-sample checks:</p>
      {stat_table(h1)}
      <p class="muted">Random-direction trades at the same times did better {num(h1['random_benchmark_p'], pct=True)} of the time.</p>
      </div></div>
      {verdict('Verdict: rejected. POC reversion is not a profitable maker or scalping edge on this data.', 'neg')}
    """)]
