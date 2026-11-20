"""Hypothesis-screen sections of the /research/ report. Numbers come from
research/output/screen/ (top_hypotheses.json, ranking.csv); nothing is hard-coded
except the method prose and the caveats that describe the first run."""
import csv
import html
import json
import pathlib

from build_report import num, tone

FILES = ("top_hypotheses.json", "ranking.csv", "discovery_scores.csv", "report.md")

VERDICT_TONE = {"confirmed": "pos", "right sign, not significant": "warn", "failed": "neg"}


def load_screen(screen_dir):
    """Return {"top": dict, "ranking": [row, ...]} or None if top_hypotheses.json is missing."""
    screen_dir = pathlib.Path(screen_dir)
    top_path = screen_dir / "top_hypotheses.json"
    if not top_path.exists():
        return None
    ranking = []
    rank_path = screen_dir / "ranking.csv"
    if rank_path.exists():
        with rank_path.open(newline="", encoding="utf-8") as f:
            ranking = sorted(csv.DictReader(f), key=lambda x: float(x["rank"]))
    return {"top": json.loads(top_path.read_text()), "ranking": ranking}


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _esc(v):
    return html.escape(str(v if v is not None else ""))


def _ts(v):
    return _esc(str(v)[:16].replace("T", " ")) + " UTC" if v else "n/a"


def _p(v):
    if v is None:
        return "n/a"
    return "&lt;0.0001" if v < 1e-4 else f"{v:.4f}"


def coverage_dates(s):
    cov = s["top"].get("coverage") or []
    if not cov:
        return None, None
    return min(c["first"] for c in cov)[:10], max(c["last"] for c in cov)[:10]


def screen_toc(s):
    toc = [("screen", "Top 3 hypotheses", False)]
    toc += [(f"screen-{i}", f"#{i} {p['hypothesis']}", True) for i, p in enumerate(s["top"]["picks"], 1)]
    return toc + [("screen-ranking", "Full ranking", False), ("screen-method", "How the picks were made", False),
                  ("screen-data", "Screen downloads", False)]


def hero(s):
    sp, (a, b) = s["top"]["split"], coverage_dates(s)
    cov = s["top"].get("coverage") or []
    chips = "".join(f'<span class="chip">{_esc(c["instrument"])}</span>' for c in cov)
    return f"""
<section class="block" id="summary">
  <div class="eyebrow">Research note · Hypothesis screen</div>
  <h1>AXIOMA research - hypothesis screen</h1>
  <p class="lead">Fourteen pre-registered ideas (thirteen candidates plus one known baseline), scored on earlier data, with the best three tested once on
  later data they had never touched.</p>
  <div class="meta">{chips}<span class="chip">data {_esc(a)} to {_esc(b)}</span>
  <span class="chip">cut {_ts(sp['cut'])}</span></div>
</section>"""


def _coverage_table(s):
    rows = "".join(f"<tr><td><b>{_esc(c['instrument'])}</b></td><td>{c['bars_with_data']:,}</td>"
                   f"<td>{c['days_with_data']}</td><td>{_ts(c['first'])}</td><td>{_ts(c['last'])}</td></tr>"
                   for c in s["top"].get("coverage") or [])
    return ("<div class='table-wrap'><table><thead><tr><th>Instrument</th><th>Bars with data</th><th>Days</th>"
            f"<th>First</th><th>Last</th></tr></thead><tbody>{rows}</tbody></table></div>")


def _meaning(p):
    if p["target"] == "vol":
        r = p.get("vol_ratio_top_vs_bottom")
        return (f"Useful as a risk signal: bars in the top fifth saw {num(r, 2, sign=False)}x the next-bar "
                "volatility of the bottom fifth, at similar current volatility.")
    net = p.get("net_avg_bps")
    if net is not None and net > 0:
        return "Covers the spread in this test."
    return "Predicts direction, but the edge is smaller than the spread as a 5-minute taker trade."


def _economics(p):
    if p["target"] == "vol":
        return ("Future volatility: top vs bottom signal quintile within current-vol terciles: "
                f"<b>{num(p.get('vol_ratio_top_vs_bottom'), 2, sign=False)}x</b>.")
    return (f"Taker test on holdout: {p.get('trades') if p.get('trades') is not None else 'n/a'} trades, gross "
            f"<b class='{tone(p.get('gross_avg_bps'))}'>{num(p.get('gross_avg_bps'), 2)} bp</b>, net "
            f"<b class='{tone(p.get('net_avg_bps'))}'>{num(p.get('net_avg_bps'), 2)} bp</b> per trade "
            f"(cost {num(p.get('cost_per_trade_bps'), 2, sign=False)} bp).")


def _robustness(p):
    def ic(v, t):
        return f"{num(v, 3)} (t {num(t, 1)})"
    if p["target"] == "vol":
        return ("Robustness (holdout): IC holding current and hour-of-day volatility fixed "
                f"{ic(p.get('ic_ctrl_vol_and_hour'), p.get('t_ctrl_vol_and_hour'))}.")
    return ("Robustness (holdout): IC holding the last bar's return fixed "
            f"{ic(p.get('ic_ctrl_last_return'), p.get('t_ctrl_last_return'))}; IC skipping one bar "
            f"{ic(p.get('ic_skip_one_bar'), p.get('t_skip_one_bar'))}.")


def _pick(i, p):
    tn = VERDICT_TONE.get(p["verdict"], "neg")
    per = ", ".join(f"{_esc(k)} {num(v, 3)}" for k, v in (p.get("holdout_ic_by_instrument") or {}).items())
    ctrl = f" Controls for <code>{_esc(p['control'])}</code>." if p.get("control") else ""
    return f"""
  <h3 id="screen-{i}">#{i} · <code>{_esc(p['hypothesis'])}</code> <span class="badge {tn}">{_esc(p['verdict'])}</span></h3>
  <p><b>Claim.</b> {_esc(p['statement'])}</p>
  <p><b>Mechanism.</b> {_esc(p['mechanism'])}{ctrl}</p>
  <p class="muted small">Predicts the {'volatility' if p['target'] == 'vol' else 'return'} over the next
  {p['horizon_minutes']} minutes.</p>
  <div class="table-wrap"><table><thead><tr><th></th><th>IC</th><th>t</th><th>p (one-sided)</th><th>Blocks</th></tr></thead>
  <tbody><tr><td><b>Discovery</b></td><td class="{tone(p['ic'])}">{num(p['ic'], 3)}</td><td>{num(p['t'], 2)}</td>
  <td>{_p(p['p'])}</td><td>{p['blocks']}</td></tr>
  <tr><td><b>Holdout</b></td><td class="{tone(p.get('holdout_ic'))}">{num(p.get('holdout_ic'), 3)}</td>
  <td>{num(p.get('holdout_t'), 2)}</td><td>{_p(p.get('holdout_p'))}</td><td>{p.get('holdout_blocks', 'n/a')}</td></tr></tbody></table></div>
  <p>Holdout IC by instrument: {per or 'n/a'}.</p>
  <p>{_economics(p)}</p>
  <p>{_robustness(p)}</p>
  <div class="callout"><b>What it means</b><p>{_meaning(p)}</p></div>"""


def _lead(s):
    sp = s["top"]["split"]
    return f"""
<section class="block" id="screen">
  <div class="eyebrow">Hypothesis screen</div><h2>Top 3 hypotheses</h2>
  <p>We wrote down 14 hypotheses before looking at results and scored each on 5-minute data for XAUUSD and BTCUSD
  from the KQL feed. Hypotheses were chosen on data up to a single cut; each of the top {sp['top']} picks was then
  tested once on the later data. Rank correlation (IC) measures how well a signal orders what happens next; blocks
  are {sp['block_hours']}-hour windows, and t is the average IC over blocks divided by its standard error.</p>
  <h3 id="screen-data-split">Data and split</h3>
  {_coverage_table(s)}
  <ul class="checks">
    <li class="ok"><b>Cut:</b> {_ts(sp['cut'])}. The first {sp['fraction'] * 100:.0f}% of bars with data are discovery;
    the rest are holdout.</li>
    <li class="ok"><b>Embargo:</b> {sp['embargo_bars']} bars ({sp['embargo_bars'] * 5} min) dropped at the cut.</li>
    <li class="ok"><b>Block size:</b> {sp['block_hours']} hours. <b>FDR:</b> {sp['fdr']}.</li>
  </ul>
</section>"""


def _picks_html(s):
    return "".join(_pick(i, p) for i, p in enumerate(s["top"]["picks"], 1))


def _ranking(s):
    rows = []
    for x in s["ranking"]:
        picked = x["picked"] == "True"
        yn = lambda v: "yes" if v == "True" else "no"  # noqa: E731
        ic, hb = _f(x["ic"]), _f(x["horizon_bars"])
        row_attr = ' class="picked" style="background:#22d3ee14"' if picked else ""
        rows.append(
            f"<tr{row_attr}><td>{int(float(x['rank']))}</td>"
            f"<td><code>{_esc(x['hypothesis'])}</code></td><td>{_esc(x['target'])}</td>"
            f"<td>{num(hb * 5, 0, sign=False) if hb is not None else 'n/a'} min</td>"
            f"<td class='{tone(ic)}'>{num(ic, 3)}</td><td>{num(_f(x['t']), 2)}</td><td>{_p(_f(x['q']))}</td>"
            f"<td>{yn(x['sign_consistent'])}</td><td>{yn(x['passed_discovery'])}</td>"
            f"<td>{_esc(x['redundant_with']) or '-'}</td>"
            f"<td>{'<b>picked</b>' if picked else '-'}</td></tr>")
    return f"""
<section class="block" id="screen-ranking">
  <div class="eyebrow">Hypothesis screen</div><h2>Full ranking</h2>
  <p>All hypotheses by discovery rank, before looking at the holdout. Picked rows are highlighted.</p>
  <div class="table-wrap"><table><thead><tr><th>Rank</th><th>Hypothesis</th><th>Target</th><th>Horizon</th>
  <th>Discovery IC</th><th>t</th><th>q</th><th>Same sign on all instruments</th><th>Passed discovery</th>
  <th>Duplicate of</th><th>Picked</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
</section>"""


def _method(s):
    sp = s["top"]["split"]
    days = ", ".join(f"{_esc(c['instrument'])} {c['days_with_data']} days" for c in s["top"].get("coverage") or [])
    return f"""
<section class="block" id="screen-method">
  <div class="eyebrow">Hypothesis screen</div><h2>How the picks were made</h2>
  <ol class="steps">
    <li><b>One chronological cut</b> with a 1-hour embargo. Everything before it is discovery, everything after is holdout.</li>
    <li><b>Spearman IC per {sp['block_hours']}-hour block</b>, with a t-statistic across blocks. Volatility
    hypotheses use a partial IC that controls for current volatility.</li>
    <li><b>Direction fixed in advance</b>, so tests are one-sided.</li>
    <li><b>Pass</b> = Benjamini-Hochberg q &le; {sp['fdr']} and the same sign on every instrument.</li>
    <li><b>Duplicates collapse.</b> Signals correlated at |rho| &ge; 0.7 on discovery count as one effect.</li>
    <li><b>Tested once on holdout</b> for the top {sp['top']}, with a Bonferroni threshold.</li>
    <li><b>Economics and robustness checks</b> on the holdout: a taker-trade test for return picks, a volatility-quintile
    test for volatility picks, and IC with controls.</li>
  </ol>
  <h3 id="screen-caveats">Caveats</h3>
  <ul class="checks">
    <li class="warn"><b>Short history.</b> {days} of data, from one broker's quote feed with no traded volume. These
    are leads, not proven edges.</li>
    <li class="warn"><b>The duplicate rule came after the first run.</b> The first run showed the original #1 and #2
    were one effect (rho 0.95); adding the rule moved the 1-hour range reversal into third place.</li>
    <li class="warn"><b>#2 and #3 are related.</b> range_reversal_1h correlates about 0.61 with poc_reversion_4h.</li>
    <li class="warn"><b>Book depth was not testable.</b> The liquidity providers quote fixed, symmetric sizes, so
    book-depth signals carry no information.</li>
    <li class="warn"><b>Robustness checks are post-selection.</b> They were run on the picks after they were chosen.</li>
  </ul>
</section>"""


def _downloads(s):
    return """
<section class="block" id="screen-data">
  <div class="eyebrow">Hypothesis screen</div><h2>Screen downloads</h2>
  <div class="actions">
    <a class="btn" href="data/screen/top_hypotheses.json" download>top_hypotheses.json</a>
    <a class="btn" href="data/screen/ranking.csv" download>ranking.csv</a>
    <a class="btn" href="data/screen/discovery_scores.csv" download>discovery_scores.csv</a>
    <a class="btn" href="data/screen/report.md" download>report.md</a>
  </div>
  <p>Regenerate with <code>venv/bin/python research/run_screen.py</code>.</p>
</section>"""


def screen_sections(s, lead=False):
    """(toc, body). lead=True (no study results) prepends the page hero."""
    top = _lead(s)
    # per-pick h3s sit inside the screen section, before the ranking
    body = top.replace("</section>", _picks_html(s) + "\n</section>", 1)
    body = (hero(s) if lead else "") + body + _ranking(s) + _method(s) + _downloads(s)
    toc = screen_toc(s)
    if lead:
        toc = [("summary", "Summary", False)] + toc
    return toc, body
