"""HTML shell for the /research/ report. Styles live in assets/report.css."""
import pathlib

CSS_PATH = pathlib.Path(__file__).resolve().parent / "assets" / "report.css"

JS = """
const links=[...document.querySelectorAll('.toc a')];
const obs=new IntersectionObserver(es=>es.forEach(e=>{if(e.isIntersecting){
  links.forEach(a=>a.classList.toggle('on',a.getAttribute('href')==='#'+e.target.id));}}),
  {rootMargin:'-25% 0px -65% 0px'});
document.querySelectorAll('section[id],h3[id]').forEach(s=>obs.observe(s));
"""

# The AXIOMA mark is served by the landing page at /favicon.svg.
MARK = '<img src="/favicon.svg" width="26" height="26" alt="">'


def page(body, toc, r, built, meta=None):
    toc_html = "".join(f'<a href="#{i}" class="{"sub" if sub else ""}">{t}</a>' for i, t, sub in toc)
    meta = meta or {}
    slides = '<a href="slides.html" class="hide-sm">Slides</a>' if r is not None else ""
    title = meta.get("title", "Research · Can the Point of Control be traded? | AXIOMA")
    desc = meta.get("description", "Out-of-sample tests of POC mean reversion, order-flow momentum and short-term reversal on AXIOMA order-book data.")
    foot = meta.get("footer") or f"DATA {r['data']['start'][:10]} → {r['data']['end'][:10]} UTC"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<meta name="description" content="{desc}">
<link rel="icon" href="/favicon.svg" type="image/svg+xml"><link rel="stylesheet" href="report.css"></head><body>
<header class="top"><a class="brand" href="/">{MARK}AXIOMA<small>/ RESEARCH</small></a>
<nav aria-label="Primary"><a href="/" class="hide-sm">Home</a><a href="/terminal/">Terminal</a>
<a href="/research/" class="on" aria-current="page">Research</a>{slides}</nav></header>
<div class="layout"><nav class="toc" aria-label="Contents"><b>CONTENTS</b>{toc_html}</nav><main>{body}</main></div>
<footer><span>© 2026 AXIOMA · RESEARCH NOTE · NOT INVESTMENT ADVICE</span>
<span>{foot} · PUBLISHED {built}</span></footer>
<script>{JS}</script></body></html>"""
