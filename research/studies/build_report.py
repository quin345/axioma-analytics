"""Publish the research report to research/site/, served by the webapp at /research/.

    venv/bin/python research/run_screen.py               # hypothesis screen (leads the report)
    venv/bin/python research/studies/run_studies.py      # optional: older study results + charts
    venv/bin/python research/studies/build_presentation.py
    venv/bin/python research/studies/build_report.py      # -> research/site/

The site is static: index.html, charts/*.png, the slide deck and the raw
results (JSON/CSV). It is rebuilt from scratch on every run.
"""
import html
import json
import pathlib
import shutil
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from study_lib import config  # noqa: E402

OUT = config.OUTPUT_DIR / "studies"
SITE = config.RESEARCH_DIR / "site"
SCREEN = config.OUTPUT_DIR / "screen"


def num(v, d=1, sign=True, pct=False):
    if v is None:
        return "n/a"
    if pct:
        return f"{v * 100:.0f}%"
    return f"{v:+,.{d}f}" if sign else f"{v:,.{d}f}"


def tone(v):
    return "pos" if (v or 0) > 0 else "neg"


def chart(name, caption, alt=None):
    if not name:
        return ""
    return (f'<figure><img loading="lazy" src="charts/{name}" alt="{html.escape(alt or caption)}">'
            f"<figcaption>{caption}</figcaption></figure>")


def stat_rows(r):
    rows = [("Tuning period (in-sample)", r["in_sample"], "Where the settings were chosen. Optimistic by construction."),
            ("Test period (out-of-sample)", r["out_of_sample"], "The honest number: unseen data, frozen settings."),
            ("Test period, zero spread", r["out_of_sample_zero_cost"], "Would it work if trading were free?"),
            ("Test period, trend removed", r["out_of_sample_drift_adjusted"], "Strips out profit from simply riding the market's direction.")]
    body = "".join(
        f"<tr><td><b>{lab}</b><span class='hint'>{hint}</span></td><td>{s['trades']}</td>"
        f"<td>{num(s['win_rate'], pct=True)}</td><td class='{tone(s['avg_bps'])}'>{num(s['avg_bps'], 2)}</td>"
        f"<td class='{tone(s['total_bps'])}'>{num(s['total_bps'], 0)}</td><td>{num(s['t_stat'], 2)}</td>"
        f"<td>{num(s['trades_per_day'], 0, sign=False)}</td></tr>" for lab, s, hint in rows)
    return ("<div class='table-wrap'><table><thead><tr><th>Period</th><th>Trades</th><th>Win rate</th>"
            "<th>Avg bps / trade</th><th>Total bps</th><th>t-stat</th><th>Trades / day</th></tr></thead>"
            f"<tbody>{body}</tbody></table></div>")


def verdict(kind, title, text):
    icon = {"neg": "✕", "warn": "!", "pos": "✓"}[kind]
    return f"<div class='verdict {kind}'><span class='v-icon'>{icon}</span><div><b>{title}</b><p>{text}</p></div></div>"


def callout(title, text):
    return f"<aside class='callout'><b>{title}</b><p>{text}</p></aside>"


def publish(sections_html, toc, r, screen=None, meta=None, out=None, site=None, screen_dir=None):
    """Rebuild the site. r (study results) and screen (report_screen.load_screen) are each optional."""
    out, site, screen_dir = out or OUT, site or SITE, screen_dir or SCREEN
    if site.exists():
        shutil.rmtree(site)
    (site / "charts").mkdir(parents=True)
    (site / "data").mkdir()
    if r is not None:
        for png in out.glob("*.png"):
            shutil.copy2(png, site / "charts" / png.name)
        for f in ["results.json", *[p.name for p in out.glob("*_sweep_in_sample.csv")]]:
            shutil.copy2(out / f, site / "data" / f)
        if (out / "presentation.html").exists():
            shutil.copy2(out / "presentation.html", site / "slides.html")
    if screen is not None:
        (site / "data" / "screen").mkdir()
        from report_screen import FILES
        for f in FILES:
            if (screen_dir / f).exists():
                shutil.copy2(screen_dir / f, site / "data" / "screen" / f)
    from report_shell import CSS_PATH, page
    shutil.copy2(CSS_PATH, site / "report.css")
    built = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    (site / "index.html").write_text(page(sections_html, toc, r, built, meta), encoding="utf-8")
    return site / "index.html"


def build(out=None, site=None, screen_dir=None):
    """Build the site from whatever inputs exist. Raises FileNotFoundError (site untouched) if none do."""
    from report_screen import coverage_dates, load_screen, screen_sections
    out, site, screen_dir = out or OUT, site or SITE, screen_dir or SCREEN
    res_path = out / "results.json"
    r = json.loads(res_path.read_text()) if res_path.exists() else None
    screen = load_screen(screen_dir)
    if r is None and screen is None:
        raise FileNotFoundError(f"nothing to publish: no {res_path} and no {screen_dir / 'top_hypotheses.json'}. "
                                "Run research/run_screen.py (or research/studies/run_studies.py) first.")
    meta = None
    if r is not None:
        from report_sections_end import sections
        toc, body = sections(r)
        if screen is not None:
            s_toc, s_body = screen_sections(screen)
            toc, body = s_toc + toc, s_body + body
    else:
        from report_panel_guide import panel_guide
        toc, body = screen_sections(screen, lead=True)
        toc = toc + [("panel-guide", "Panel guide", False)]
        body += panel_guide()
        a, b = coverage_dates(screen)
        meta = {"title": "Research · Hypothesis screen | AXIOMA",
                "description": "Pre-registered hypothesis screen on AXIOMA 5-minute data, with holdout confirmation.",
                "footer": f"DATA {a} → {b} UTC"}
    return publish(body, toc, r, screen, meta, out, site, screen_dir)


def main():
    try:
        path = build()
    except FileNotFoundError as e:
        sys.exit(f"build_report: {e}")
    print(f"published {path} ({sum(1 for _ in SITE.rglob('*') if _.is_file())} files)")


if __name__ == "__main__":
    main()
