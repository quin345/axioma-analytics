"""Publish the research report to research/site/, served by the webapp at /research/.

    venv/bin/python research/run_screen.py                    # 5-minute screen -> output/screen
    venv/bin/python research/run_screen.py --timeframe 1m     # 1-minute screen -> output/screen_1m
    venv/bin/python research/studies/run_studies.py           # optional: older study results + charts
    venv/bin/python research/studies/build_presentation.py
    venv/bin/python research/studies/build_report.py          # -> research/site/

The site is static: index.html lists the research items; each item is its own page,
research/site/screen-5m/ and screen-1m/, with its charts, slide deck and raw results
(JSON/CSV). A build rewrites every item whose outputs exist and leaves the others
as they are, then rebuilds the index.
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
SCREEN_1M = config.OUTPUT_DIR / "screen_1m"


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


# Research items, newest idea last. Each lives at /research/<slug>/ and is rebuilt only when
# its own outputs exist; the index at /research/ lists whatever item pages are on disk.
ITEMS = (("screen-5m", "5m"), ("screen-1m", "1m"))
LEGACY_ROOT = ("charts", "data", "slides.html")   # the single-page layout: now owned by screen-5m


def publish_item(slug, sections_html, toc, r, screen=None, meta=None, out=None, site=None, screen_dir=None,
                 title=None, summary=None):
    """Rebuild one item page at <site>/<slug>/. Other items are not touched.
    r (study results) and screen (report_screen.load_screen) are each optional."""
    out, site, screen_dir = out or OUT, site or SITE, screen_dir or SCREEN
    item = site / slug
    if item.exists():
        shutil.rmtree(item)
    (item / "charts").mkdir(parents=True)
    (item / "data").mkdir()
    if r is not None:
        for png in out.glob("*.png"):
            shutil.copy2(png, item / "charts" / png.name)
        for f in ["results.json", *[p.name for p in out.glob("*_sweep_in_sample.csv")]]:
            shutil.copy2(out / f, item / "data" / f)
        if (out / "presentation.html").exists():
            shutil.copy2(out / "presentation.html", item / "slides.html")
    if screen is not None:
        (item / "data" / "screen").mkdir()
        from report_screen import FILES
        for f in FILES:
            if (screen_dir / f).exists():
                shutil.copy2(screen_dir / f, item / "data" / "screen" / f)
    from report_shell import CSS_PATH, page
    site.mkdir(parents=True, exist_ok=True)
    shutil.copy2(CSS_PATH, site / "report.css")
    now = datetime.now(timezone.utc)
    (item / "index.html").write_text(page(sections_html, toc, r, now.strftime("%Y-%m-%d %H:%M UTC"), meta, root="../"),
                                     encoding="utf-8")
    (item / "item.json").write_text(json.dumps({"slug": slug, "title": title, "summary": summary,
                                                "date": now.strftime("%Y-%m-%d")}, indent=2), encoding="utf-8")
    return item / "index.html"


def publish_index(site):
    """/research/ : one card per item page found under <site>, newest first."""
    from report_shell import CSS_PATH, page
    items = []
    for f in sorted(site.glob("*/item.json")):
        meta = json.loads(f.read_text(encoding="utf-8"))
        if (f.parent / "index.html").exists():
            items.append(meta)
    items.sort(key=lambda m: (m["date"], m["slug"]), reverse=True)
    cards = "".join(
        f'<a class="card pos" href="{html.escape(m["slug"])}/index.html" style="display:block;text-decoration:none;color:inherit">'
        f'<div class="tag">{html.escape(m["date"])}</div><h3 style="margin:8px 0 0">{html.escape(m["title"])}</h3>'
        f'<p>{html.escape(m["summary"])}</p></a>' for m in items)
    body = (f'<section class="block" id="summary"><div class="eyebrow">Research</div><h1>AXIOMA research</h1>'
            f'<p class="lead">Pre-registered studies on the AXIOMA quote feed, each tested once on data held out '
            f'from selection.</p><div class="cards">{cards}</div></section>')
    toc = [("summary", "Research", False)] + [(f"{m['slug']}/index.html", m["title"], True) for m in items]
    # old single-page links such as /research/#screen-ranking now live on the 5-minute item page
    legacy = ("<script>if(location.hash&&location.hash!=='#summary')"
              "location.replace('screen-5m/index.html'+location.hash)</script>")
    meta = {"title": "Research | AXIOMA", "description": "Pre-registered research on AXIOMA market data.",
            "footer": f"{len(items)} RESEARCH ITEMS"}
    site.mkdir(parents=True, exist_ok=True)
    shutil.copy2(CSS_PATH, site / "report.css")
    built = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    doc = page(body, toc, None, built, meta)
    (site / "index.html").write_text(doc.replace("</body>", legacy + "</body>"), encoding="utf-8")
    return site / "index.html"


def _item_summary(r, screen):
    """(title, one-line summary) for the index card of one item."""
    from report_screen import info, summary_line
    if screen is None:
        return ("Can the Point of Control be traded?",
                "Out-of-sample tests of POC mean reversion, order-flow momentum and short-term reversal.")
    t = info(screen)
    title = f"Hypothesis screen, {t['label']} bars"
    line = summary_line(screen)
    return title, line + (" Includes the older POC study." if r is not None else "")


def build_item(slug, key, out, site, screen_dir):
    """Build one item page. Returns its path, or None if its outputs do not exist (page left as is)."""
    from report_screen import coverage_dates, info, load_screen, screen_sections
    screen = load_screen(screen_dir)
    res_path = out / "results.json"
    r = json.loads(res_path.read_text()) if key == "5m" and res_path.exists() else None   # older POC study: 5-minute only
    if r is None and screen is None:
        return None
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
        t = info(screen)
        meta = {"title": f"Research · Hypothesis screen, {t['label']} bars | AXIOMA",
                "description": f"Pre-registered hypothesis screen on AXIOMA {t['label']} data, with holdout confirmation.",
                "footer": f"DATA {a} → {b} UTC"}
    title, summary = _item_summary(r, screen)
    return publish_item(slug, body, toc, r, screen, meta, out, site, screen_dir, title, summary)


def build(out=None, site=None, screen_dir=None, screen_1m_dir=None):
    """Build every item whose outputs exist, then the index. Items without outputs are left untouched.
    Raises FileNotFoundError (site untouched) if no item has outputs."""
    out, site = out or OUT, site or SITE
    dirs = {"5m": screen_dir or SCREEN, "1m": screen_1m_dir or SCREEN_1M}
    ready = [(slug, key) for slug, key in ITEMS
             if (dirs[key] / "top_hypotheses.json").exists() or (key == "5m" and (out / "results.json").exists())]
    if not ready:
        raise FileNotFoundError(f"nothing to publish: no {out / 'results.json'}, no {dirs['5m'] / 'top_hypotheses.json'} "
                                f"and no {dirs['1m'] / 'top_hypotheses.json'}. "
                                "Run research/run_screen.py [--timeframe 1m] (or research/studies/run_studies.py) first.")
    for slug, key in ready:
        build_item(slug, key, out, site, dirs[key])
        if slug == "screen-5m":      # the single-page site kept these at the root; they belong to this item now
            for name in LEGACY_ROOT:
                legacy = site / name
                if legacy.is_dir():
                    shutil.rmtree(legacy)
                elif legacy.exists():
                    legacy.unlink()
    return publish_index(site)


def main():
    try:
        path = build()
    except FileNotFoundError as e:
        sys.exit(f"build_report: {e}")
    print(f"published {path} ({sum(1 for _ in SITE.rglob('*') if _.is_file())} files)")


if __name__ == "__main__":
    main()
