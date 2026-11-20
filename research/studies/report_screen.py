"""Hypothesis-screen sections of the /research/ report. Numbers come from
research/output/screen/ (5-minute) or screen_1m/ (top_hypotheses.json, ranking.csv);
bar size, windows and counts are read from the results, not hard-coded. Only the
method prose and the caveats that describe the first 5-minute run are fixed text."""
import csv
import html
import json
import pathlib

from build_report import num, tone
from pipeline.hypotheses import hypotheses_for
from pipeline.timeframe import TF_1M, TF_5M, span

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


WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
         "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty"]


def _word(n):
    return WORDS[n] if 0 <= n < len(WORDS) else str(n)


def info(s):
    """Timeframe facts of a screen. Results written before timeframes existed carry none: they are 5-minute."""
    top = s["top"]
    legacy = hypotheses_for(TF_5M)
    tf = top.get("timeframe") or {**TF_5M.to_dict(), "hypotheses": len(legacy), "baselines": sum(h.baseline for h in legacy)}
    n, base = tf["hypotheses"], tf["baselines"]
    sp = top["split"]
    return {"key": tf["key"], "slot": int(tf["slot_minutes"]), "table": tf["table"], "n": int(n), "base": int(base),
            "top": sp["top"], "block_hours": sp["block_hours"], "poc": tf["poc_windows"],
            "zwindow": tf["zwindow_bars"], "horizons": tf["horizons_bars"], "embargo": sp["embargo_bars"],
            "label": span(int(tf["slot_minutes"])), "window": span(int(float(sp["block_hours"]) * 60))}


def summary_line(s):
    """One line for the index page."""
    t, picks = info(s), s["top"]["picks"]
    done = sum(p.get("verdict") == "confirmed" for p in picks)
    a, b = coverage_dates(s)
    held = f"{done} of {len(picks)} top picks confirmed on holdout" if picks else "no picks"
    return (f"{t['n']} pre-registered hypotheses on {t['label']} bars with {t['window']} aggregation windows; "
            f"{held}. Data {a} to {b}.")


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
    t = info(s)
    toc = [("screen", f"Top {t['top']} hypotheses", False)]
    toc += [(f"screen-{i}", f"#{i} {p['hypothesis']}", True) for i, p in enumerate(s["top"]["picks"], 1)]
    if t["key"] != "5m":
        toc.append(("screen-changes", "What changed vs the 5-minute screen", False))
    return toc + [("screen-ranking", "Full ranking", False), ("screen-method", "How the picks were made", False),
                  ("screen-data", "Screen downloads", False)]


def hero(s):
    sp, (a, b), t = s["top"]["split"], coverage_dates(s), info(s)
    cov = s["top"].get("coverage") or []
    chips = "".join(f'<span class="chip">{_esc(c["instrument"])}</span>' for c in cov)
    return f"""
<section class="block" id="summary">
  <div class="eyebrow">Research note · Hypothesis screen</div>
  <h1>AXIOMA research - hypothesis screen, {t['label']} bars</h1>
  <p class="lead">{_word(t['n']).capitalize()} pre-registered ideas ({_word(t['n'] - t['base'])} candidates plus {_word(t['base'])}
  known baseline{'s' if t['base'] != 1 else ''}), scored on earlier data, with the best {_word(t['top'])} tested once on
  later data they had never touched.</p>
  <div class="meta">{chips}<span class="chip">{t['label']} bars</span><span class="chip">data {_esc(a)} to {_esc(b)}</span>
  <span class="chip">cut {_ts(sp['cut'])}</span></div>
</section>"""


def _coverage_table(s):
    rows = "".join(f"<tr><td><b>{_esc(c['instrument'])}</b></td><td>{c['bars_with_data']:,}</td>"
                   f"<td>{c['days_with_data']}</td><td>{_ts(c['first'])}</td><td>{_ts(c['last'])}</td></tr>"
                   for c in s["top"].get("coverage") or [])
    return ("<div class='table-wrap'><table><thead><tr><th>Instrument</th><th>Bars with data</th><th>Days</th>"
            f"<th>First</th><th>Last</th></tr></thead><tbody>{rows}</tbody></table></div>")


def _meaning(p):  # noqa: C901
    if p["target"] == "vol":
        r = p.get("vol_ratio_top_vs_bottom")
        return (f"Useful as a risk signal: bars in the top fifth saw {num(r, 2, sign=False)}x the next-bar "
                "volatility of the bottom fifth, at similar current volatility.")
    net = p.get("net_avg_bps")
    if net is not None and net > 0:
        return "Covers the spread in this test."
    return ("Predicts direction, but the edge is smaller than the spread as a "
            f"{p['horizon_minutes']}-minute taker trade.")


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
    sp, t = s["top"]["split"], info(s)
    names = " and ".join(_esc(c["instrument"]) for c in s["top"].get("coverage") or []) or "the instruments"
    return f"""
<section class="block" id="screen">
  <div class="eyebrow">Hypothesis screen</div><h2>Top {t['top']} hypotheses</h2>
  <p>We wrote down {t['n']} hypotheses before looking at results and scored each on {t['label']} data for {names}
  from the KQL feed. Hypotheses were chosen on data up to a single cut; each of the top {sp['top']} picks was then
  tested once on the later data. Rank correlation (IC) measures how well a signal orders what happens next; blocks
  are {sp['block_hours']}-hour windows, and t is the average IC over blocks divided by its standard error.</p>
  <h3 id="screen-data-split">Data and split</h3>
  {_coverage_table(s)}
  <ul class="checks">
    <li class="ok"><b>Cut:</b> {_ts(sp['cut'])}. The first {sp['fraction'] * 100:.0f}% of bars with data are discovery;
    the rest are holdout.</li>
    <li class="ok"><b>Embargo:</b> {sp['embargo_bars']} bars ({sp['embargo_bars'] * t['slot']} min) dropped at the cut.</li>
    <li class="ok"><b>Block size:</b> {sp['block_hours']} hours. <b>FDR:</b> {sp['fdr']}.</li>
  </ul>
</section>"""


def _picks_html(s):
    return "".join(_pick(i, p) for i, p in enumerate(s["top"]["picks"], 1))


def _ranking(s):
    slot = info(s)["slot"]
    rows = []
    for x in s["ranking"]:
        picked = x["picked"] == "True"
        yn = lambda v: "yes" if v == "True" else "no"  # noqa: E731
        ic, hb = _f(x["ic"]), _f(x["horizon_bars"])
        row_attr = ' class="picked" style="background:#22d3ee14"' if picked else ""
        rows.append(
            f"<tr{row_attr}><td>{int(float(x['rank']))}</td>"
            f"<td><code>{_esc(x['hypothesis'])}</code></td><td>{_esc(x['target'])}</td>"
            f"<td>{num(hb * slot, 0, sign=False) if hb is not None else 'n/a'} min</td>"
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


def _changes(s):
    """1-minute page only: the windows that were rescaled, next to the 5-minute values."""
    t = info(s)
    if t["key"] == "5m":
        return ""
    a, b = TF_5M, TF_1M
    hz = lambda tf_: " / ".join(str(tf_.minutes(h)) for h in tf_.horizons) + " min"  # noqa: E731
    rows = [
        ("Source table", f"<code>{a.table}</code>", f"<code>{t['table']}</code>"),
        ("Bar size", f"{a.slot_minutes} min", f"{t['slot']} min"),
        ("Forward horizons (same 1 / 3 / 6 / 12 bars)", hz(a), " / ".join(str(t["slot"] * h) for h in t["horizons"]) + " min"),
        ("Rolling z-score window (spread, activity, tick gap; also the economics signal)",
         f"{a.zwindow} bars = {a.zwindow * a.slot_minutes // 60} h", f"{t['zwindow']} bars = {t['zwindow'] * t['slot'] // 60} h"),
        ("IC block", f"{a.block_hours} h", f"{t['block_hours']} h"),
        ("POC / value-area windows", " / ".join(f"{w} min" for w in a.poc_windows),
         " / ".join(f"{w} min" for w in t["poc"])),
        ("Embargo at the cut", f"{a.embargo_bars} bars = {a.embargo_bars * a.slot_minutes} min",
         f"{t['embargo']} bars = {t['embargo'] * t['slot']} min"),
    ]
    body = "".join(f"<tr><td>{n}</td><td>{x}</td><td>{y}</td></tr>" for n, x, y in rows)
    return f"""
<section class="block" id="screen-changes">
  <div class="eyebrow">Hypothesis screen</div><h2>What changed vs the 5-minute screen</h2>
  <div class="callout"><b>Same ideas, same formulas, rescaled windows.</b>
  <p>Every 4-hour aggregation window became 1 hour. Per-bar counts did not change, so a return over "3 bars" is now
  3 minutes. Thresholds are unchanged: FDR {s['top']['split']['fdr']}, top {t['top']}, 60% split, duplicate cut-off |rho| 0.7,
  at least 12 observations per block. The hypothesis set was fixed before any 1-minute data was screened.</p></div>
  <div class="table-wrap"><table><thead><tr><th>Setting</th><th>5-minute screen</th><th>This screen</th></tr></thead>
  <tbody>{body}</tbody></table></div>
</section>"""


def _method(s):
    sp, t = s["top"]["split"], info(s)
    days = ", ".join(f"{_esc(c['instrument'])} {c['days_with_data']} days" for c in s["top"].get("coverage") or [])
    return f"""
<section class="block" id="screen-method">
  <div class="eyebrow">Hypothesis screen</div><h2>How the picks were made</h2>
  <ol class="steps">
    <li><b>One chronological cut</b> with a {span(sp['embargo_bars'] * t['slot'])} embargo. Everything before it is discovery, everything after is holdout.</li>
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
    {_caveats(t)}
    <li class="warn"><b>Book depth was not testable.</b> The liquidity providers quote fixed, symmetric sizes, so
    book-depth signals carry no information.</li>
    <li class="warn"><b>Robustness checks are post-selection.</b> They were run on the picks after they were chosen.</li>
  </ul>
</section>"""


def _caveats(t):
    if t["key"] != "5m":
        return """<li class="warn"><b>Not independent of the 5-minute screen.</b> Same feed, same days, same instruments:
    a result that shows up in both is one observation seen twice, and no correction is applied across the two screens.</li>
    <li class="warn"><b>Costs weigh more on short bars.</b> The spread is a larger share of a 1-minute move, so a real
    directional edge can still be smaller than the cost of trading it.</li>"""
    return """<li class="warn"><b>The duplicate rule came after the first run.</b> The first run showed the original #1 and #2
    were one effect (rho 0.95); adding the rule moved the 1-hour range reversal into third place.</li>
    <li class="warn"><b>#2 and #3 are related.</b> range_reversal_1h correlates about 0.61 with poc_reversion_4h.</li>
"""


def _downloads(s):
    flag = "" if info(s)["key"] == "5m" else f" --timeframe {info(s)['key']}"
    return f"""
<section class="block" id="screen-data">
  <div class="eyebrow">Hypothesis screen</div><h2>Screen downloads</h2>
  <div class="actions">
    <a class="btn" href="data/screen/top_hypotheses.json" download>top_hypotheses.json</a>
    <a class="btn" href="data/screen/ranking.csv" download>ranking.csv</a>
    <a class="btn" href="data/screen/discovery_scores.csv" download>discovery_scores.csv</a>
    <a class="btn" href="data/screen/report.md" download>report.md</a>
  </div>
  <p>Regenerate with <code>venv/bin/python research/run_screen.py{flag}</code>.</p>
</section>"""


def screen_sections(s, lead=False):
    """(toc, body). lead=True (no study results) prepends the page hero."""
    top = _lead(s)
    # per-pick h3s sit inside the screen section, before the ranking
    body = top.replace("</section>", _picks_html(s) + "\n</section>", 1)
    body = (hero(s) if lead else "") + body + _changes(s) + _ranking(s) + _method(s) + _downloads(s)
    toc = screen_toc(s)
    if lead:
        toc = [("summary", "Summary", False)] + toc
    return toc, body
