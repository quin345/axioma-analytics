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


def page(body, toc, r, built):
    toc_html = "".join(f'<a href="#{i}" class="{"sub" if sub else ""}">{t}</a>' for i, t, sub in toc)
    d = r["data"]
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Research · Can the Point of Control be traded? | AXIOMA</title>
<meta name="description" content="Out-of-sample tests of POC mean reversion, order-flow momentum and short-term reversal on AXIOMA order-book data.">
<link rel="icon" href="/favicon.svg" type="image/svg+xml"><link rel="stylesheet" href="report.css"></head><body>
<header class="top"><a class="brand" href="/">{MARK}AXIOMA<small>/ RESEARCH</small></a>
<nav aria-label="Primary"><a href="/" class="hide-sm">Home</a><a href="/terminal/">Terminal</a>
<a href="/research/" class="on" aria-current="page">Research</a><a href="slides.html" class="hide-sm">Slides</a></nav></header>
<div class="layout"><nav class="toc" aria-label="Contents"><b>CONTENTS</b>{toc_html}</nav><main>{body}</main></div>
<footer><span>© 2026 AXIOMA · RESEARCH NOTE · NOT INVESTMENT ADVICE</span>
<span>DATA {d['start'][:10]} → {d['end'][:10]} UTC · PUBLISHED {built}</span></footer>
<script>{JS}</script></body></html>"""
