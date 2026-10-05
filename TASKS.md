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

## Open questions

- [ ] Confirm the calendar is right: it is a read-only view of data already in
      the analytics report. If a *task* list is wanted instead (add/edit items,
      persisted), that is a separate feature and needs a decision on storage:
      browser localStorage, or a server endpoint plus a store.