"""Closing sections of the /research/ report and the section/TOC assembly."""
from build_report import chart, num, tone
from report_glossary import glossary_data
from report_panel_guide import panel_guide
from report_sections import h1, h2, h3, hero, method


def compare(r):
    rows = []
    for key, name in (("H1_poc", "POC reversion"), ("H2_flow", "Order-flow momentum"), ("H3_reversal", "Short-term reversal")):
        h = r[key]
        i, o, d = h["in_sample"], h["out_of_sample"], h["out_of_sample_drift_adjusted"]
        rows.append(f"<tr><td><b>{name}</b></td><td class='{tone(i['avg_bps'])}'>{num(i['avg_bps'], 2)}</td>"
                    f"<td class='{tone(o['avg_bps'])}'>{num(o['avg_bps'], 2)}</td><td>{num(d['avg_bps'], 2)}</td>"
                    f"<td>{num(o['win_rate'], pct=True)}</td><td>{o['trades']}</td>"
                    f"<td>{num(h['random_benchmark_p'], 2, sign=False)}</td></tr>")
    return f"""
<section class="block" id="compare">
  <div class="eyebrow">05 · Side by side</div><h2>All three hypotheses compared</h2>
  <div class="table-wrap"><table><thead><tr><th>Strategy</th><th>Tuning bps/trade</th><th>Test bps/trade</th>
  <th>Test, trend removed</th><th>Test win rate</th><th>Test trades</th><th>Random-beat p</th></tr></thead>
  <tbody>{''.join(rows)}</tbody></table></div>
  <p class="muted small">"Random-beat p" = share of random-direction simulations that did at least as well. Lower is
  better; below 0.05 is conventionally significant.</p>
  <div class="grid2"><div>{chart(r['charts_common']['scorecard'], 'Average net profit per trade on the unseen test period.')}</div>
  <div>{chart(r['charts_common']['ic'], 'Rank IC by horizon for the two factors. The shaded band is roughly where results '
              'are indistinguishable from noise with this much data.')}</div></div>
</section>"""


def limits_next(r):
    d = r["data"]
    return f"""
<section class="block" id="limits">
  <div class="eyebrow">06 · Limitations</div><h2>What this study can and cannot tell you</h2>
  <ul class="checks">
    <li class="warn"><b>Short history.</b> {len(d['instruments'])} instruments over about 10 days, with one train/test split.
    One market regime can dominate everything; results can flip as data grows.</li>
    <li class="warn"><b>Bar-level fills.</b> Fills are simulated from 5-minute high/low and the average spread. Real market
    making depends on queue position, latency and fills inside the bar, none of which are modelled here.</li>
    <li class="warn"><b>No fees or slippage</b> beyond the spread. Commissions and market impact would lower every result.</li>
    <li class="warn"><b>Flow is inferred.</b> The feed has quotes, not trades; buy/sell flow is a tick-rule estimate.</li>
    <li class="ok"><b>What it does show:</b> the POC is not a reliable magnet at these horizons, and resting orders
    around it suffer adverse selection. That conclusion holds across all {r['H1_poc']['sweep']['configs']} settings.</li>
  </ul>
</section>
<section class="block" id="next">
  <div class="eyebrow">07 · Next steps</div><h2>Where we go from here</h2>
  <ol class="steps">
    <li><b>Keep collecting.</b> The research table grows every 5 minutes. Re-run this study weekly; aim for 2–3 months
    and more instruments before trusting any edge.</li>
    <li><b>Paper-trade order-flow momentum</b> with the frozen settings and compare live results with the backtest.</li>
    <li><b>Flip the POC idea.</b> Use the value area as <i>context</i>: take flow-momentum trades only when price has
    <i>left</i> the value area (a breakout), the opposite of the reversion bet that failed.</li>
    <li><b>Go finer for market making.</b> Test quoting on 10-second or tick data with queue position, where both the
    reversal effect and real maker economics live.</li>
  </ol>
</section>"""


def sections(r):
    toc = [("summary", "Summary", False), ("method", "Method", False), ("data-overview", "The data", True),
           ("h1", "H1 · POC reversion", False), ("h1-idea", "The idea", True), ("h1-results", "What happened", True),
           ("h1-maker", "Adverse selection", True), ("h1-oos", "Unseen data", True),
           ("h2", "H2 · Flow momentum", False), ("h2-checks", "Stress tests", True), ("h2-ic", "By horizon", True),
           ("h3", "H3 · Reversal", False), ("compare", "Side by side", False), ("limits", "Limitations", False),
           ("next", "Next steps", False), ("panel-guide", "Panel guide", False),
           ("glossary", "Glossary", False), ("data", "Data & re-run", False)]
    body = (hero(r) + method(r) + h1(r) + h2(r) + h3(r) + compare(r) + limits_next(r) + panel_guide()
            + glossary_data(r))
    return toc, body
