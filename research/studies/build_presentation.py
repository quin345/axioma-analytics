"""Build a self-contained HTML slide deck from research/output/studies/results.json.

    venv/bin/python research/studies/build_presentation.py
Open research/output/studies/presentation.html in a browser; arrow keys / space
to navigate, F for full screen. Images are embedded, so the file can be shared alone.
"""
import base64
import html
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from study_lib import config  # noqa: E402

OUT = config.OUTPUT_DIR / "studies"


def img(name, caption=""):
    if not name:
        return ""
    b64 = base64.b64encode((OUT / name).read_bytes()).decode()
    cap = f"<figcaption>{html.escape(caption)}</figcaption>" if caption else ""
    return f'<figure><img src="data:image/png;base64,{b64}" alt="{html.escape(caption or name)}">{cap}</figure>'


def num(v, d=1, sign=True, pct=False):
    if v is None:
        return "n/a"
    if pct:
        return f"{v * 100:.0f}%"
    return f"{v:+.{d}f}" if sign else f"{v:.{d}f}"


def cls(v):
    return "pos" if (v or 0) > 0 else "neg"


def stat_table(r):
    rows = [("In-sample (tuning)", r["in_sample"]), ("Out-of-sample", r["out_of_sample"]),
            ("OOS, zero spread", r["out_of_sample_zero_cost"]),
            ("OOS, market trend removed", r["out_of_sample_drift_adjusted"])]
    body = "".join(
        f"<tr><td>{lab}</td><td>{s['trades']}</td><td>{num(s['win_rate'], pct=True)}</td>"
        f"<td class='{cls(s['avg_bps'])}'>{num(s['avg_bps'], 2)}</td><td>{num(s['t_stat'], 2)}</td>"
        f"<td>{num(s['trades_per_day'], 0, sign=False)}</td></tr>" for lab, s in rows)
    return ("<table><tr><th></th><th>Trades</th><th>Win rate</th><th>Avg bps / trade</th>"
            f"<th>t-stat</th><th>Trades / day</th></tr>{body}</table>")


def verdict(text, kind):
    return f'<div class="verdict {kind}">{text}</div>'


def slide(inner, cls_=""):
    return f'<section class="slide {cls_}">{inner}</section>'


CSS = """
*{box-sizing:border-box}html,body{margin:0;height:100%;background:#070c17;color:#e6edf7;
font-family:Inter,Segoe UI,Helvetica,Arial,sans-serif}
.slide{display:none;position:absolute;inset:0;padding:48px 64px 64px;overflow:auto;
background:radial-gradient(1200px 600px at 10% -10%,#16264a 0%,#0b1220 55%)}
.slide.active{display:block;animation:fade .35s ease}@keyframes fade{from{opacity:0;transform:translateY(8px)}to{opacity:1}}
.title{display:flex!important;flex-direction:column;justify-content:center}.title:not(.active){display:none!important}
h1{font-size:54px;margin:.2em 0;background:linear-gradient(90deg,#38bdf8,#a78bfa);-webkit-background-clip:text;color:transparent}
h2{font-size:32px;margin:0 0 20px;color:#f1f5f9;border-left:5px solid #38bdf8;padding-left:14px}
.kicker{color:#38bdf8;text-transform:uppercase;letter-spacing:.18em;font-size:14px;font-weight:700}
.lead{font-size:22px;max-width:900px;line-height:1.5;color:#cbd5e1}.muted{color:#8da2c0;font-size:15px}
.center{text-align:center}p,li{font-size:18px;line-height:1.55}li{margin:6px 0}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:36px;align-items:start}
figure{margin:0}img{max-width:100%;border-radius:12px;box-shadow:0 8px 30px #0008}
figcaption{color:#8da2c0;font-size:14px;margin-top:6px;text-align:center}
.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:22px;margin:20px 0 30px}
.card{background:#111a2e;border-radius:16px;padding:24px;border-top:5px solid}
.card.pos{border-color:#22c55e}.card.neg{border-color:#ef4444}.card.warn{border-color:#f59e0b}
.card .tag{color:#8da2c0;font-weight:700;font-size:14px;text-transform:uppercase;letter-spacing:.08em}
.card .big{font-size:44px;font-weight:800;margin:8px 0}.card.pos .big{color:#22c55e}.card.neg .big{color:#ef4444}
table{border-collapse:collapse;width:100%;margin:10px 0;font-size:16px;background:#111a2e;border-radius:10px;overflow:hidden}
th,td{padding:9px 12px;text-align:right;border-bottom:1px solid #22304d}th:first-child,td:first-child{text-align:left}
th{color:#8da2c0;font-weight:600}td.pos{color:#22c55e;font-weight:700}td.neg{color:#ef4444;font-weight:700}
.verdict{margin-top:22px;padding:14px 20px;border-radius:12px;font-size:19px;font-weight:700}
.verdict.neg{background:#ef444422;border:1px solid #ef4444}.verdict.warn{background:#f59e0b22;border:1px solid #f59e0b}
li.ok::marker{content:"✔  ";color:#22c55e}li.warn::marker{content:"⚠  ";color:#f59e0b}
code{background:#1e293b;padding:2px 6px;border-radius:5px;font-size:.9em}
#bar{position:fixed;bottom:0;left:0;height:4px;background:linear-gradient(90deg,#38bdf8,#a78bfa);transition:width .3s}
#nav{position:fixed;bottom:14px;right:24px;color:#8da2c0;font-size:14px;user-select:none}
#nav button{background:#1e293b;color:#e6edf7;border:0;border-radius:8px;padding:6px 12px;margin-left:6px;cursor:pointer}
@media print{.slide{display:block!important;position:relative;page-break-after:always;height:100vh}#nav,#bar{display:none}}
"""

JS = """
const S=[...document.querySelectorAll('.slide')];let i=Math.max(0,Math.min(S.length-1,(+location.hash.slice(1)||1)-1));
function show(n){i=Math.max(0,Math.min(S.length-1,n));S.forEach((s,k)=>s.classList.toggle('active',k===i));
document.getElementById('pg').textContent=(i+1)+' / '+S.length;document.getElementById('bar').style.width=((i+1)/S.length*100)+'%';
history.replaceState(null,'','#'+(i+1));}
document.addEventListener('keydown',e=>{if(['ArrowRight','PageDown',' '].includes(e.key)){e.preventDefault();show(i+1)}
else if(['ArrowLeft','PageUp'].includes(e.key))show(i-1);else if(e.key==='Home')show(0);else if(e.key==='End')show(S.length-1);
else if(e.key==='f')document.documentElement.requestFullscreen?.();});
show(i);
"""


def main():
    from slides_factors import closing, factor_slides
    from slides_h1 import h1_slides, intro

    r = json.loads((OUT / "results.json").read_text())
    slides = intro(r) + h1_slides(r) + factor_slides(r) + closing(r)
    doc = (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>AXIOMA · POC & factor research</title>"
           f"<meta name='viewport' content='width=device-width,initial-scale=1'><style>{CSS}</style></head><body>"
           f"{''.join(slides)}<div id='bar'></div><div id='nav'><span id='pg'></span>"
           f"<button onclick='show(i-1)'>←</button><button onclick='show(i+1)'>→</button></div>"
           f"<script>{JS}</script></body></html>")
    path = OUT / "presentation.html"
    path.write_text(doc, encoding="utf-8")
    print(f"wrote {path} ({len(slides)} slides, {len(doc) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
