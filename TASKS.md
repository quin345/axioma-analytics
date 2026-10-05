# Tasks

Working checklist for Axioma Analytics. One file, newest work at the top.
Tick a box when the item is done and the change is verified, not just written.

Conventions: `- [ ]` open, `- [x]` done, `- [!]` blocked. Notes go under
**Notes:** so the checklist itself stays scannable.

---

## 2026-10-05 - Dashboard cards, calendar, symbolId

### Expandable cards
- [x] Make every card a click/tap/keyboard disclosure (`data-card`, `aria-expanded`)
- [x] Do not collapse when a click lands inside the expanded body
- [x] Rotating chevron on the card title; open state uses the accent border
- [x] Detail body per card: prose explaining the metric + a label/value table
- [x] Cover all 15 cards: price, ofi, depth, profile, hist, vol, dd, spread,
      iarr, hour, acf, large, ticks, coverage, calendar

### Calendar
- [x] Month grid built from the same OHLCV bars as the price chart
- [x] Day cell sized by tick count, coloured by that day's change
- [x] Prev/next month and Today (Today = most recent day that has data)
- [x] Click a day to load its detail
- [x] Default to the last day with data, not to today

### Readability and placement
- [x] Group cards into Overview / Microstructure / Liquidity / Data sections
- [x] Widen type scale, card min-width 420px, taller charts, content max-width
- [x] Add missing footnotes so no chart needs expanding to read its headline

### Remove symbolId from the UI
- [x] Price-chart tag shows name and asset class only
- [x] Symbol picker label drops the `(8)` suffix; id stays the option value

### Verification
- [x] `node --check app/static/app.js` clean
- [x] `pytest` - 133 passed
- [x] Live run against the gold endpoint; all 15 detail builders render clean
- [x] Calendar aggregation checked across a month boundary
- [x] Empty-report path does not throw in any builder (fixed `dl(null)`)

**Notes:**
- `tests/test_config.py::test_plain_connection_has_no_authentication_when_unset`
  fails in this checkout and is **pre-existing**. It reads credentials from the
  local `.env`, so it fails whenever those are set. Untouched by this work.
- A 24h lookback yields one calendar day. Use `lookback_hours=2160` (the field
  max) for a populated month grid.

---

## 2026-10-05 - Favicon

### Assets
- [x] `favicon.svg` - the logo mark as vector, identical geometry to the header
- [x] `favicon.ico` - 16/32/48/64/128/256 PNG-compressed frames
- [x] `icon-32.png`, `icon-180.png`, `icon-512.png` for bookmarks and iOS
- [x] Link tags in `index.html`: svg, ico, png, apple-touch-icon, mask-icon
- [x] `theme-color` set to the app background

### Generator
- [x] `scripts/make_favicon.py` rasterises the mark with no third-party deps
- [x] Fixed alpha divisor: was `size * SS * SS` (whole canvas) instead of
      `SS * SS` (samples per pixel), which zeroed every alpha at 512px
- [x] Output is byte-for-byte reproducible (fixed zlib level)

### Verification
- [x] PNG signature, chunk CRCs and scanline sizes valid at every size
- [x] ICO header and all 6 directory entries correct; 256 stored as 0
- [x] Rendered ink matches the logo (diamond outline, two ascending bars)
- [x] Served by the app with correct MIME types; `pytest` still 133 passed

**Notes:**
- Rasters are antialiased by 4x4 supersampling; SVG is preferred where
  supported, so the ico/png set is only a fallback.
- Re-run `python scripts/make_favicon.py` after changing the header logo.

---

## 2026-10-05 - Drawdown card overflow

- [x] `dd` card spans a full grid row (`span3`) instead of one column
- [x] Added `.card.span3` with responsive collapse (3 -> 2 at 1400px, -> 1 at 900px)
- [x] `.dl dd` no longer `nowrap`: a wide value wraps inside its own cell
- [x] `.dl dt` pinned with `flex:0 0 auto`, `.dl dd` given `margin-left:auto`
      so a wrapped value still hugs the right edge
- [x] `.dl` min column 230px -> 300px to give wide values more room
- [x] Verified against live data: episode timelines are ~79 chars / ~521px
- [x] `node --check` clean; `pytest` 133 passed

**Notes:**
- Root cause was `white-space:nowrap` on `.dl dd`, not the card width alone.
  Widening alone would have hidden it; both were needed.
- Full-width row avoids an empty grid cell, since price (span2) + hist (span1)
  already fills the row above.
- Other cards benefit from the `.dl` fix too (hour-by-hour, episode timelines).

---

## 2026-10-05 - Overview block re-layout

- [x] `price` takes the full row (`span3`), so the anchor chart is full width
- [x] Row below: `dd` at 2/3 (`span2`), `hist` at 1/3
- [x] Reordered the markup so the grid flows: price, drawdown, return distribution
- [x] Fixed the `span3` breakpoint: was 1400px, correct value is 1339px
- [x] Verified card order and spans served by the app; `pytest` 133 passed

**Notes:**
- The grid keeps 3 columns down to a 1340px viewport (420px min track + 16px
  gaps + 48px padding); below that it drops to 2, so `span3` collapses first.
  At the old 1400px breakpoint, `span3` would have overflowed a 2-column grid
  between 1340 and 1400px.
- The drawdown card stays at 2/3, which keeps its episode timeline readable
  now that `.dl dd` wraps rather than spills.

---

## Open questions

- [ ] Confirm the calendar is right: it is a read-only view of data already in
      the analytics report. If a *task* list is wanted instead (add/edit items,
      persisted), that is a separate feature and needs a decision on storage:
      browser localStorage, or a server endpoint plus a store.